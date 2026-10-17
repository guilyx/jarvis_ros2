# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""End-to-end demo without a simulator: inject faults with the real CLI and watch Jarvis handle them.

Covers M1's exit criteria (docs/design/09-roadmap.md): debounce, correlation, black box, latch and
operator clear, and open faults surviving a fault manager restart. And M2's demo scenarios: a wedged
relay restarted by a rule (S1), restarts that keep being needed suppressed and escalated (S2).
"""

import pathlib
import subprocess
import tempfile
import time
import unittest

import launch
import launch_testing
import launch_testing.actions
import pytest
import rclpy
from ament_index_python.packages import get_package_share_directory
from jarvis_interfaces.msg import Event
from jarvis_interfaces.srv import ClearFault, GetFaults
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from lifecycle_msgs.msg import Transition
from lifecycle_msgs.srv import ChangeState
from rclpy.qos import QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import LaserScan

DATA_DIR = tempfile.mkdtemp(prefix="jarvis_demo_")


@pytest.mark.launch_test
def generate_test_description():
    demo = pathlib.Path(get_package_share_directory("jarvis_demo")) / "launch" / "demo.launch.py"
    return launch.LaunchDescription(
        [
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(str(demo)),
                launch_arguments={"data_dir": DATA_DIR, "gateway": "false"}.items(),
            ),
            launch_testing.actions.ReadyToTest(),
        ]
    )


def attr(event, key):
    return next((a.string_value for a in event.attrs if a.key == key), None)


def inject(*args, wait=True, check=True):
    cmd = ["ros2", "run", "jarvis_demo", "inject", *args]
    if wait:
        return subprocess.run(cmd, timeout=60, check=check)
    return subprocess.Popen(cmd)


RESTART_RULE = "lidar-stale-restart"
RELAY = "/demo/scan_relay"


class TestDemo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.node = rclpy.create_node("demo_test")
        cls.events = []
        cls.scans = 0
        stream = QoSProfile(depth=1000, reliability=ReliabilityPolicy.RELIABLE)
        cls.node.create_subscription(Event, "/jarvis/events", cls.events.append, stream)
        cls.node.create_subscription(LaserScan, "/scan", cls._on_scan, qos_profile_sensor_data)
        cls.get_faults = cls.node.create_client(GetFaults, "/jarvis/fault_manager/get_faults")
        cls.clear_fault = cls.node.create_client(ClearFault, "/jarvis/fault_manager/clear_fault")
        cls.change_state = cls.node.create_client(ChangeState, "/jarvis/fault_manager/change_state")
        for client in (cls.get_faults, cls.clear_fault, cls.change_state):
            assert client.wait_for_service(timeout_sec=60.0), client.srv_name
        deadline = time.time() + 30.0
        while cls.scans == 0 and time.time() < deadline:
            rclpy.spin_once(cls.node, timeout_sec=0.1)
        assert cls.scans, "the relay never published /scan"

    @classmethod
    def _on_scan(cls, _msg):
        cls.scans += 1

    @classmethod
    def tearDownClass(cls):
        cls.node.destroy_node()
        rclpy.shutdown()

    def wait_for(self, predicate, what, timeout=30.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.05)
            found = next((e for e in self.events if predicate(e)), None)
            if found:
                return found
        self.fail(f"timed out waiting for {what}")

    def became(self, code, state):
        return lambda e: e.kind == Event.KIND_FAULT_STATE and e.code == code and attr(e, "state") == state

    def spin(self, seconds):
        until = time.time() + seconds
        while time.time() < until:
            rclpy.spin_once(self.node, timeout_sec=0.05)

    def matches(self, rule):
        return [e for e in self.events if e.kind == Event.KIND_RULE_MATCH and attr(e, "rule") == rule]

    def restart(self, phase, requester="RULE"):
        """Predicate: an event of the restart plan reaching `phase`, asked for by `requester`."""
        return lambda e: (
            e.kind == Event.KIND_RECOVERY
            and attr(e, "phase") == phase
            and attr(e, "plan") == "restart-lifecycle-node"
            and attr(e, "requester") == requester
        )

    def recoveries(self, phase, requester="RULE"):
        return [e for e in self.events if self.restart(phase, requester)(e)]

    def call(self, client, request):
        future = client.call_async(request)
        rclpy.spin_until_future_complete(self.node, future, timeout_sec=15.0)
        self.assertTrue(future.done(), client.srv_name)
        return future.result()

    def transition(self, transition_id):
        reply = self.call(self.change_state, ChangeState.Request(transition=Transition(id=transition_id)))
        self.assertTrue(reply.success, f"transition {transition_id}")

    def test_0_a_healthy_start_raises_no_fault(self):
        # The relay's rate window fills and its first scan arrives without any fault on the way.
        until = time.time() + 3.0
        while time.time() < until:
            rclpy.spin_once(self.node, timeout_sec=0.05)
        raised = [e.code for e in self.events if e.kind == Event.KIND_FAULT_RAISE]
        self.assertEqual(raised, [], "no fault while everything is healthy")

    def test_1_scan_stall_is_one_fault_with_its_symptom_muted(self):
        # Long enough to outlast the relay's restart (Jarvis tries one) and its startup grace, so the
        # rate status reports the collapse too.
        proc = inject("scan-stall", "--seconds", "8", wait=False)
        try:
            self.wait_for(self.became("sensor.scan.stale", "PENDING"), "PENDING (persist debounce)")
            active = self.wait_for(self.became("sensor.scan.stale", "ACTIVE"), "ACTIVE")
            self.assertEqual(active.source.component, "lidar")
            self.wait_for(lambda e: e.code == "jarvis.blackbox.captured", "black-box snapshot")
            muted = self.wait_for(lambda e: e.code == "jarvis.fault.muted", "rate status muted")
            self.assertIn("diag.scan_relay.output_rate", muted.message)
            self.assertEqual(proc.wait(timeout=30), 0)
        finally:
            proc.kill()
        self.wait_for(self.became("sensor.scan.stale", "CLEARED"), "CLEARED once scans flow")

    def test_2_latched_estop_survives_a_restart_and_needs_an_operator(self):
        inject("estop", "--seconds", "1")
        self.wait_for(self.became("safety.estop.engaged", "LATCHED"), "LATCHED after release")
        (before,) = [f for f in self.call(self.get_faults, GetFaults.Request()).faults if f.latch]

        # Restart the fault manager through its lifecycle: cleanup drops it, configure restores it.
        for t in (Transition.TRANSITION_DEACTIVATE, Transition.TRANSITION_CLEANUP):
            self.transition(t)
        for t in (Transition.TRANSITION_CONFIGURE, Transition.TRANSITION_ACTIVATE):
            self.transition(t)
        self.wait_for(lambda e: e.code == "jarvis.fault_manager.restored", "restored notice")
        (after,) = [f for f in self.call(self.get_faults, GetFaults.Request()).faults if f.latch]
        self.assertEqual((after.fault_id, after.state), (before.fault_id, before.state))

        reply = self.call(
            self.clear_fault,
            ClearFault.Request(fault_id=after.fault_id, operator_name="tester", reason="area checked"),
        )
        self.assertTrue(reply.success, reply.message)
        self.wait_for(self.became("safety.estop.engaged", "CLEARED"), "CLEARED by the operator")

    def test_3_sustained_node_errors_become_a_fault(self):
        inject("rosout-error", "--count", "10", "--period", "0.4")
        active = self.wait_for(self.became("ros.log.error", "ACTIVE"), "ros.log.error ACTIVE")
        self.assertEqual(active.source.component, "demo.fault_injector")

    def test_4_jarvis_restarts_a_wedged_relay_on_its_own(self):
        before = len(self.recoveries("SUCCEEDED"))
        inject("scan-wedge", "--timeout", "30")  # returns once the relay was restarted and recovery settled
        self.spin(1.0)
        succeeded = self.recoveries("SUCCEEDED")
        self.assertEqual(len(succeeded), before + 1, "one more rule-driven restart succeeded")
        self.assertEqual(attr(succeeded[-1], "requester_id"), RESTART_RULE)
        # The check passed because scans flowed again, after the restart, not because of it.
        cleared = [e for e in self.events if self.became("sensor.scan.stale", "CLEARED")(e)]
        self.assertTrue(cleared and cleared[-1].seq < succeeded[-1].seq)
        scans = self.scans
        self.spin(1.0)
        self.assertGreater(self.scans, scans, "the relay publishes /scan again")

    def test_5_restarts_that_keep_being_needed_are_suppressed_and_escalated(self):
        # The rule may fire 3 times in 10 minutes (earlier tests used some of them): wedge until it's
        # used up, then once more. The policy's 2 s cooldown separates the wedges.
        for _ in range(3):
            self.spin(2.5)  # take in what the last wedge did; also past the cooldown
            if len(self.matches(RESTART_RULE)) >= 3:
                break
            inject("scan-wedge", "--timeout", "30")
        self.spin(2.5)
        self.assertEqual(len(self.matches(RESTART_RULE)), 3)
        stuck = inject("scan-wedge", "--timeout", "6", check=False)
        self.assertEqual(stuck.returncode, 1, "nobody restarts the relay a fourth time")

        suppressed = self.wait_for(lambda e: e.code == "jarvis.rule.suppressed", "the rule suppressed")
        self.assertEqual(attr(suppressed, "rule"), RESTART_RULE)
        self.wait_for(lambda e: attr(e, "rule") == "lidar-restarts-exhausted", "the escalation rule")
        notify = self.wait_for(lambda e: e.code == "jarvis.notify" and "keeps hanging" in e.message, "notify")
        self.assertEqual(attr(notify, "channel"), "operators")
        self.assertEqual(len(self.matches(RESTART_RULE)), 3, "suppressed, not fired")

        # An operator restarts it by hand, through the same gates.
        restart = f"ros2 jarvis recover restart-lifecycle-node -a node={RELAY} -a component=lidar"
        subprocess.run(
            [*restart.split(), "-a", "fault=sensor.scan.stale", "--operator", "tester"],
            timeout=60,
            check=True,
        )
        self.wait_for(self.restart("SUCCEEDED", requester="OPERATOR"), "the operator's restart")


@launch_testing.post_shutdown_test()
class TestShutdown(unittest.TestCase):
    def test_exit_code(self, proc_info):
        launch_testing.asserts.assertExitCodes(proc_info, allowable_exit_codes=[0, -2, -15])
