# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""End-to-end: event hub + fault manager composed in one container, driven over real topics.

Covers docs/design/04-data-flow.md §1-2: ingest -> sequence -> fault lifecycle -> latched snapshot,
operator services, rejection notices, recent-event queries and state persistence.
"""

import json
import pathlib
import tempfile
import time
import unittest

import launch
import launch_testing
import launch_testing.actions
import pytest
import rclpy
from jarvis_interfaces.msg import Event, Fault, FaultArray
from jarvis_interfaces.srv import ClearFault, GetFaults, QueryEvents
from launch_ros.actions import ComposableNodeContainer
from launch_ros.descriptions import ComposableNode
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rosbag2_interfaces.srv import Snapshot

HERE = pathlib.Path(__file__).resolve().parent
STATE_DIR = tempfile.mkdtemp(prefix="jarvis_flow_")


@pytest.mark.launch_test
def generate_test_description():
    common = {"robot_id": "test-bot", "state_dir": STATE_DIR, "autostart": True}
    container = ComposableNodeContainer(
        name="jarvis_container",
        namespace="jarvis",
        package="rclcpp_components",
        executable="component_container_mt",
        composable_node_descriptions=[
            ComposableNode(
                package="jarvis_ros",
                plugin="jarvis_ros::EventHubNode",
                parameters=[common],
                extra_arguments=[{"use_intra_process_comms": True}],
            ),
            ComposableNode(
                package="jarvis_ros",
                plugin="jarvis_ros::FaultManagerNode",
                parameters=[
                    {**common, "catalog_path": str(HERE / "data" / "faults.yaml"), "blackbox_wait_s": 1.0}
                ],
                extra_arguments=[{"use_intra_process_comms": True}],
            ),
        ],
        output="screen",
    )
    return launch.LaunchDescription([container, launch_testing.actions.ReadyToTest()])


class TestFaultFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.node = rclpy.create_node("fault_flow_test")
        stream = QoSProfile(depth=1000, reliability=ReliabilityPolicy.RELIABLE)
        latched = QoSProfile(
            depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL
        )
        cls.events = []
        cls.faults = None
        cls.ingest = cls.node.create_publisher(Event, "/jarvis/ingest", stream)
        cls.node.create_subscription(Event, "/jarvis/events", cls.events.append, stream)
        cls.node.create_subscription(FaultArray, "/jarvis/faults", cls._on_faults, latched)
        cls.get_faults = cls.node.create_client(GetFaults, "/jarvis/fault_manager/get_faults")
        cls.clear_fault = cls.node.create_client(ClearFault, "/jarvis/fault_manager/clear_fault")
        cls.query = cls.node.create_client(QueryEvents, "/jarvis/event_hub/query_events")
        for client in (cls.get_faults, cls.clear_fault, cls.query):
            assert client.wait_for_service(timeout_sec=30.0), client.srv_name
        # Wait until both sides are active and matched with our endpoints.
        deadline = time.time() + 30.0
        while time.time() < deadline and (cls.ingest.get_subscription_count() == 0 or cls.faults is None):
            rclpy.spin_once(cls.node, timeout_sec=0.1)
        assert cls.ingest.get_subscription_count() > 0, "event hub never subscribed to /jarvis/ingest"

    @classmethod
    def tearDownClass(cls):
        cls.node.destroy_node()
        rclpy.shutdown()

    @classmethod
    def _on_faults(cls, msg):
        cls.faults = msg

    # -- helpers ---------------------------------------------------------------------------------

    def send(self, kind, code, severity=Event.SEVERITY_ERROR, component="sensor"):
        msg = Event()
        msg.kind = kind
        msg.code = code
        msg.severity = severity
        msg.source.node = "/test/reporter"
        msg.source.component = component
        msg.message = f"test {code}"
        self.ingest.publish(msg)

    def wait_for(self, predicate, timeout=10.0, what="condition"):
        deadline = time.time() + timeout
        while time.time() < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.05)
            result = predicate()
            if result:
                return result
        self.fail(f"timed out waiting for {what}")

    def states(self, code):
        return [
            next(a.string_value for a in e.attrs if a.key == "state")
            for e in self.events
            if e.kind == Event.KIND_FAULT_STATE and e.code == code
        ]

    def call(self, client, request):
        future = client.call_async(request)
        rclpy.spin_until_future_complete(self.node, future, timeout_sec=10.0)
        self.assertTrue(future.done(), client.srv_name)
        return future.result()

    # -- tests (run in order) --------------------------------------------------------------------

    def test_1_debounced_fault_confirms_and_clears(self):
        self.send(Event.KIND_FAULT_RAISE, "demo.sensor.stale")
        self.wait_for(lambda: "ACTIVE" in self.states("demo.sensor.stale"), what="ACTIVE")
        self.assertEqual(self.states("demo.sensor.stale")[:2], ["PENDING", "ACTIVE"])

        raise_evt = next(e for e in self.events if e.kind == Event.KIND_FAULT_RAISE)
        self.assertEqual(raise_evt.source.robot, "test-bot", "hub fills the robot id")
        self.assertGreater(raise_evt.seq, 0)
        active = [e for e in self.events if e.kind == Event.KIND_FAULT_STATE][-1]
        self.assertEqual(active.source.node, "/jarvis/fault_manager")
        self.assertTrue(active.correlation_id, "an incident was opened")
        seqs = [e.seq for e in self.events]
        self.assertEqual(seqs, sorted(seqs), "canonical stream is in seq order")

        self.wait_for(
            lambda: self.faults and any(f.code == "demo.sensor.stale" for f in self.faults.faults),
            what="latched /jarvis/faults snapshot",
        )
        listed = self.call(self.get_faults, GetFaults.Request()).faults
        self.assertEqual([f.code for f in listed], ["demo.sensor.stale"])
        self.assertEqual(listed[0].state, Fault.STATE_ACTIVE)

        self.send(Event.KIND_FAULT_CLEAR, "demo.sensor.stale", Event.SEVERITY_INFO)
        self.wait_for(lambda: "CLEARED" in self.states("demo.sensor.stale"), what="CLEARED")
        self.wait_for(lambda: self.faults is not None and not self.faults.faults, what="empty snapshot")
        # The snapshot and the incident event travel on different topics: no ordering between them.
        self.wait_for(
            lambda: any(e.code == "jarvis.incident.resolved" for e in self.events), what="incident resolved"
        )

    def test_2_latched_fault_needs_an_operator(self):
        self.send(Event.KIND_FAULT_RAISE, "safety.estop.engaged", Event.SEVERITY_CRITICAL, "base")
        self.wait_for(lambda: "ACTIVE" in self.states("safety.estop.engaged"), what="estop ACTIVE")
        self.send(Event.KIND_FAULT_CLEAR, "safety.estop.engaged", Event.SEVERITY_INFO, "base")
        self.wait_for(lambda: "LATCHED" in self.states("safety.estop.engaged"), what="LATCHED")

        fault = self.wait_for(
            lambda: next((f for f in (self.faults.faults if self.faults else []) if f.latch), None),
            what="latched fault in snapshot",
        )
        self.assertEqual(fault.state, Fault.STATE_LATCHED)
        reply = self.call(
            self.clear_fault,
            ClearFault.Request(fault_id=fault.fault_id, operator_name="alice", reason="area checked"),
        )
        self.assertTrue(reply.success, reply.message)
        self.wait_for(lambda: "CLEARED" in self.states("safety.estop.engaged"), what="CLEARED")
        self.assertTrue(
            any(e.kind == Event.KIND_OPERATOR and e.code == "fault.clear" for e in self.events),
            "operator action is on the stream",
        )

    def test_3_invalid_events_are_rejected_with_a_notice(self):
        self.send(Event.KIND_LOG, "Not.A.Valid.Code")
        self.wait_for(
            lambda: any(e.code == "jarvis.ingest.rejected" for e in self.events), what="rejection notice"
        )

    def test_4_recent_events_are_queryable(self):
        request = QueryEvents.Request(codes=["safety.*"], limit=100)
        reply = self.call(self.query, request)
        self.assertTrue(reply.complete)
        codes = {e.code for e in reply.events}
        self.assertEqual(codes, {"safety.estop.engaged"})
        self.assertGreaterEqual(len(reply.events), 4)  # raise, ACTIVE, clear, LATCHED, CLEARED...

    def test_5_state_is_persisted(self):
        hub = pathlib.Path(STATE_DIR) / "event_hub.json"
        manager = pathlib.Path(STATE_DIR) / "fault_manager.json"
        self.assertEqual(json.loads(hub.read_text())["boot_count"], 1)

        # Saves are coalesced (100 ms), so wait for the one that follows the last clear.
        def saved_state():
            if not manager.exists():
                return None
            state = json.loads(manager.read_text())
            return state if not state["faults"] else None

        state = self.wait_for(saved_state, what="fault manager state with every fault cleared")
        self.assertEqual(state["schema"], "jarvis.fault_manager.state/v1")

    def test_6_black_box_capture_waits_for_a_late_recorder(self):
        def outcome(fault_id):
            return lambda: next(
                (
                    e.code
                    for e in self.events
                    if e.code.startswith("jarvis.blackbox.")
                    and any(a.key == "fault_id" and a.string_value == fault_id for a in e.attrs)
                ),
                None,
            )

        def raise_and_get_id(component):
            self.send(Event.KIND_FAULT_RAISE, "demo.capture.me", component=component)
            return self.wait_for(
                lambda: next(
                    (
                        f.fault_id
                        for f in (self.faults.faults if self.faults else [])
                        if f.source.component == component
                    ),
                    None,
                ),
                what=f"fault on {component}",
            )

        # No recorder yet when the fault confirms; it comes up 300 ms later and still gets the request.
        first = raise_and_get_id("late_recorder")
        until = time.time() + 0.3
        while time.time() < until:
            rclpy.spin_once(self.node, timeout_sec=0.05)
        service = self.node.create_service(
            Snapshot, "/jarvis/blackbox/snapshot", lambda req, res: setattr(res, "success", True) or res
        )
        self.assertEqual(self.wait_for(outcome(first), what="capture"), "jarvis.blackbox.captured")

        # Without a recorder, the capture gives up after blackbox_wait_s and says so.
        self.node.destroy_service(service)
        second = raise_and_get_id("no_recorder")
        self.assertEqual(self.wait_for(outcome(second), what="give up"), "jarvis.blackbox.unavailable")


@launch_testing.post_shutdown_test()
class TestShutdown(unittest.TestCase):
    def test_exit_code(self, proc_info):
        launch_testing.asserts.assertExitCodes(proc_info, allowable_exit_codes=[0, -2, -15])
