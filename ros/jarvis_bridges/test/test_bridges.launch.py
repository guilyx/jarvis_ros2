# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""End-to-end: the three bridges composed with the event hub, fed by real ROS 2 traffic.

Each scenario keeps producing fresh input until the expected event shows up on /jarvis/events, so it
doesn't depend on discovery timing or on which latched sample a bridge saw first.
"""

import time
import unittest
import uuid

import launch
import launch_testing
import launch_testing.actions
import pytest
import rclpy
from action_msgs.msg import GoalStatus, GoalStatusArray
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from jarvis_interfaces.msg import Event
from launch_ros.actions import ComposableNodeContainer
from launch_ros.descriptions import ComposableNode
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy


@pytest.mark.launch_test
def generate_test_description():
    def bridge(plugin, **params):
        return ComposableNode(
            package="jarvis_bridges",
            plugin=f"jarvis_bridges::{plugin}",
            parameters=[{"autostart": True, **params}],
        )

    container = ComposableNodeContainer(
        name="jarvis_container",
        namespace="jarvis",
        package="rclcpp_components",
        executable="component_container",
        composable_node_descriptions=[
            ComposableNode(
                package="jarvis_ros",
                plugin="jarvis_ros::EventHubNode",
                parameters=[{"robot_id": "test-bot", "autostart": True}],
            ),
            bridge("DiagnosticsBridge"),
            bridge("RosoutBridge"),
            bridge("ActionStatusBridge", discovery_period_s=0.2),
        ],
        output="screen",
    )
    return launch.LaunchDescription([container, launch_testing.actions.ReadyToTest()])


class TestBridges(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.node = rclpy.create_node("bridges_test")
        cls.events = []
        stream = QoSProfile(depth=1000, reliability=ReliabilityPolicy.RELIABLE)
        cls.node.create_subscription(Event, "/jarvis/events", cls.events.append, stream)
        cls.diagnostics = cls.node.create_publisher(DiagnosticArray, "/diagnostics", 10)
        latched = QoSProfile(
            depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL
        )
        cls.action_status = cls.node.create_publisher(GoalStatusArray, "/fake_nav/_action/status", latched)

    @classmethod
    def tearDownClass(cls):
        cls.node.destroy_node()
        rclpy.shutdown()

    def find(self, predicate):
        return next((e for e in self.events if predicate(e)), None)

    def keep_trying(self, step, predicate, what, timeout=20.0):
        """Runs `step` every 0.2 s until an event matches `predicate`."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            step()
            until = time.time() + 0.2
            while time.time() < until:
                rclpy.spin_once(self.node, timeout_sec=0.02)
            found = self.find(predicate)
            if found:
                return found
        self.fail(f"timed out waiting for {what}")

    def publish_diagnostic(self, level, hardware_id="sensor0", name="test_sensor: driver"):
        status = DiagnosticStatus(level=level, name=name, hardware_id=hardware_id, message="driver state")
        status.values = [KeyValue(key="Temperature (C)", value="81.5")]
        self.diagnostics.publish(DiagnosticArray(status=[status]))

    def test_diagnostics_level_changes_become_faults(self):
        raised = self.keep_trying(
            lambda: self.publish_diagnostic(DiagnosticStatus.ERROR),
            lambda e: e.code == "diag.test_sensor.driver" and e.kind == Event.KIND_FAULT_RAISE,
            "diagnostics raise",
        )
        self.assertEqual(raised.severity, Event.SEVERITY_ERROR)
        self.assertEqual(raised.source.component, "sensor0")
        self.assertEqual(raised.source.node, "/jarvis/bridges/diagnostics")
        self.assertEqual(raised.source.robot, "test-bot", "the hub fills the robot id")
        temperature = next(a for a in raised.attrs if a.key == "temperature_c")
        self.assertAlmostEqual(temperature.double_value, 81.5)

        self.keep_trying(
            lambda: self.publish_diagnostic(DiagnosticStatus.OK),
            lambda e: e.code == "diag.test_sensor.driver" and e.kind == Event.KIND_FAULT_CLEAR,
            "diagnostics clear",
        )
        code = "diag.test_sensor.driver"
        raises = [e for e in self.events if e.code == code and e.kind == Event.KIND_FAULT_RAISE]
        self.assertEqual(len(raises), 1, "a steady level is not re-sent")

    def test_diagnostics_ignore_jarvis_own_export(self):
        # The probe rides in the same array: once it is through, the jarvis status was seen too.
        def publish_both():
            own = DiagnosticStatus(level=DiagnosticStatus.ERROR, name="own: export", hardware_id="jarvis")
            probe = DiagnosticStatus(level=DiagnosticStatus.WARN, name="probe: alive", hardware_id="probe")
            self.diagnostics.publish(DiagnosticArray(status=[own, probe]))

        self.keep_trying(publish_both, lambda e: e.code == "diag.probe.alive", "probe status")
        self.assertIsNone(self.find(lambda e: e.code == "diag.own.export"))

    def test_rosout_errors_become_faults_and_warnings_logs(self):
        logger = self.node.get_logger()
        error = self.keep_trying(
            lambda: logger.error("planner exploded"),
            lambda e: e.code == "ros.log.error",
            "rosout fault",
        )
        self.assertEqual(error.kind, Event.KIND_FAULT_RAISE)
        self.assertEqual(error.source.component, "bridges_test")
        self.assertEqual(error.message, "planner exploded")
        warn = self.keep_trying(
            lambda: logger.warning("planner is slow"), lambda e: e.code == "ros.log.warn", "rosout log"
        )
        self.assertEqual(warn.kind, Event.KIND_LOG)
        self.assertIsNone(
            self.find(lambda e: e.code.startswith("ros.log") and e.source.component.startswith("jarvis.")),
            "Jarvis' own logs are not bridged",
        )

    def test_aborted_goal_raises_and_success_clears(self):
        def goal(status):
            g = GoalStatus(status=status)
            g.goal_info.goal_id.uuid = list(uuid.uuid4().bytes)
            return g

        def abort_a_goal():
            g = goal(GoalStatus.STATUS_EXECUTING)
            self.action_status.publish(GoalStatusArray(status_list=[g]))
            time.sleep(0.05)
            g.status = GoalStatus.STATUS_ABORTED
            self.action_status.publish(GoalStatusArray(status_list=[g]))

        aborted = self.keep_trying(
            abort_a_goal, lambda e: e.code == "ros.action.fake_nav.aborted", "aborted goal fault"
        )
        self.assertEqual(aborted.kind, Event.KIND_FAULT_RAISE)
        self.assertEqual(aborted.source.component, "/fake_nav")
        state = self.find(lambda e: e.code == "ros.action.fake_nav.status")
        self.assertEqual(state.kind, Event.KIND_STATE)

        def succeed_a_goal():
            self.action_status.publish(GoalStatusArray(status_list=[goal(GoalStatus.STATUS_SUCCEEDED)]))

        self.keep_trying(
            succeed_a_goal,
            lambda e: e.code == "ros.action.fake_nav.aborted" and e.kind == Event.KIND_FAULT_CLEAR,
            "clear on success",
        )


@launch_testing.post_shutdown_test()
class TestShutdown(unittest.TestCase):
    def test_exit_code(self, proc_info):
        launch_testing.asserts.assertExitCodes(proc_info, allowable_exit_codes=[0, -2, -15])
