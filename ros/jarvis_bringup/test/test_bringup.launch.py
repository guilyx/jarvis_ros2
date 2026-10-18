# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""End-to-end: jarvis.launch.py with the default catalog, driven by standard ROS 2 traffic.

Checks M1's exit criteria without a simulator: faults from rosout, aborted goals and diagnostics go
through debounce, heal and correlation, trigger the black box, and end up recorded in MCAP.
"""

import pathlib
import tempfile
import time
import unittest
import uuid

import launch
import launch_testing
import launch_testing.actions
import pytest
import rclpy
import yaml
from action_msgs.msg import GoalStatus, GoalStatusArray
from ament_index_python.packages import get_package_share_directory
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus
from jarvis_interfaces.msg import Event
from jarvis_interfaces.srv import GetFaults
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

DATA_DIR = pathlib.Path(tempfile.mkdtemp(prefix="jarvis_bringup_"))


@pytest.mark.launch_test
def generate_test_description():
    launch_file = pathlib.Path(get_package_share_directory("jarvis_bringup")) / "launch" / "jarvis.launch.py"
    jarvis = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(str(launch_file)),
        launch_arguments={"robot_id": "test-bot", "data_dir": str(DATA_DIR), "gateway": "false"}.items(),
    )
    return launch.LaunchDescription([jarvis, launch_testing.actions.ReadyToTest()])


def fault_state(event):
    return next((a.string_value for a in event.attrs if a.key == "state"), None)


class TestBringup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.node = rclpy.create_node("bringup_test")
        cls.events = []
        stream = QoSProfile(depth=1000, reliability=ReliabilityPolicy.RELIABLE)
        cls.node.create_subscription(Event, "/jarvis/events", cls.events.append, stream)
        cls.diagnostics = cls.node.create_publisher(DiagnosticArray, "/diagnostics", 10)
        latched = QoSProfile(
            depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL
        )
        status_topic = "/navigate_to_pose/_action/status"
        cls.nav_status = cls.node.create_publisher(GoalStatusArray, status_topic, latched)
        cls.get_faults = cls.node.create_client(GetFaults, "/jarvis/fault_manager/get_faults")
        assert cls.get_faults.wait_for_service(timeout_sec=60.0), "fault manager never came up"
        # The first capture comes right after startup: wait until the black box records the stream,
        # or its ring buffer may still be empty when it is dumped (discovery is slower under load).
        deadline = time.time() + 60.0
        while time.time() < deadline and not any(
            info.node_name == "blackbox"
            for info in cls.node.get_subscriptions_info_by_topic("/jarvis/event_batches")
        ):
            rclpy.spin_once(cls.node, timeout_sec=0.1)

    @classmethod
    def tearDownClass(cls):
        cls.node.destroy_node()
        rclpy.shutdown()

    def find(self, predicate):
        return next((e for e in self.events if predicate(e)), None)

    def keep_trying(self, step, predicate, what, timeout=30.0):
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

    def became(self, code, state):
        return lambda e: e.kind == Event.KIND_FAULT_STATE and e.code == code and fault_state(e) == state

    def test_sustained_rosout_errors_become_a_fault(self):
        logger = self.node.get_logger()
        active = self.keep_trying(
            lambda: logger.error("costmap update failed"), self.became("ros.log.error", "ACTIVE"), "ACTIVE"
        )
        self.assertEqual(active.source.component, "bringup_test")
        raises = [e for e in self.events if e.code == "ros.log.error" and e.kind == Event.KIND_FAULT_RAISE]
        self.assertGreaterEqual(len(raises), 3, "count debounce: 3 reports within 10 s")

    def test_aborted_goal_captures_the_black_box_and_success_clears(self):
        def publish(status):
            g = GoalStatus(status=status)
            g.goal_info.goal_id.uuid = list(uuid.uuid4().bytes)
            self.nav_status.publish(GoalStatusArray(status_list=[g]))
            return g

        def abort_a_goal():
            g = publish(GoalStatus.STATUS_EXECUTING)
            time.sleep(0.05)
            g.status = GoalStatus.STATUS_ABORTED
            self.nav_status.publish(GoalStatusArray(status_list=[g]))

        code = "ros.action.navigate_to_pose.aborted"
        self.keep_trying(abort_a_goal, self.became(code, "ACTIVE"), "aborted goal ACTIVE")
        captured = self.keep_trying(
            lambda: None, lambda e: e.code.startswith("jarvis.blackbox."), "black-box outcome", timeout=10.0
        )
        self.assertEqual(captured.code, "jarvis.blackbox.captured", captured.message)

        self.keep_trying(
            lambda: publish(GoalStatus.STATUS_SUCCEEDED), self.became(code, "CLEARED"), "cleared on success"
        )

    def test_diagnostics_fault_round_trip_without_loop(self):
        def publish(level):
            status = DiagnosticStatus(level=level, name="test_sensor: driver", hardware_id="sensor0")
            self.diagnostics.publish(DiagnosticArray(status=[status]))

        code = "diag.test_sensor.driver"
        self.keep_trying(lambda: publish(DiagnosticStatus.ERROR), self.became(code, "ACTIVE"), "ACTIVE")
        # The fault manager now exports it on /diagnostics as hardware_id "jarvis": not bridged back.
        time.sleep(1.5)
        rclpy.spin_once(self.node, timeout_sec=0.1)
        self.keep_trying(lambda: publish(DiagnosticStatus.OK), self.became(code, "CLEARED"), "CLEARED")
        looped = [e for e in self.events if e.code.startswith("diag.") and e.source.component == "jarvis"]
        self.assertEqual(looped, [])

    def test_robot_id_is_stamped(self):
        self.keep_trying(lambda: None, lambda e: e.source.robot, "any event")
        self.assertEqual({e.source.robot for e in self.events}, {"test-bot"})


def bag_topics(directory):
    """Topic name -> message count from a closed bag's metadata.yaml."""
    meta = yaml.safe_load((directory / "metadata.yaml").read_text())["rosbag2_bagfile_information"]
    return {t["topic_metadata"]["name"]: t["message_count"] for t in meta["topics_with_message_count"]}


@launch_testing.post_shutdown_test()
class TestRecorded(unittest.TestCase):
    def test_exit_code(self, proc_info):
        launch_testing.asserts.assertExitCodes(proc_info, allowable_exit_codes=[0, -2, -15])

    def test_jarvis_topics_are_recorded_to_mcap(self):
        (bag,) = (DATA_DIR / "bags").iterdir()
        self.assertTrue(list(bag.glob("*.mcap")))
        topics = bag_topics(bag)
        self.assertGreater(topics.get("/jarvis/event_batches", 0), 3)
        self.assertGreater(topics.get("/jarvis/faults", 0), 0)
        self.assertIn("/diagnostics", topics)
        self.assertIn("/rosout", topics)
        for other in ("/jarvis/ingest", "/jarvis/ingest_batches", "/jarvis/events"):
            self.assertNotIn(other, topics, "only the canonical stream is recorded")
        self.assertFalse([t for t in topics if "write_split" in t], "recorder notices are not recorded")

    def test_black_box_snapshot_was_written(self):
        (box,) = (DATA_DIR / "blackbox").iterdir()
        self.assertGreater(bag_topics(box).get("/jarvis/event_batches", 0), 0)

    def test_state_survives_on_disk(self):
        self.assertTrue((DATA_DIR / "state" / "fault_manager.json").exists())
        self.assertTrue((DATA_DIR / "state" / "event_hub.json").exists())
