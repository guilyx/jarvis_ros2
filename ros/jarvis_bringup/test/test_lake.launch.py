# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The lake in the running stack: splits close every 3 s, the lake converts each one as it is told."""

import pathlib
import tempfile
import time
import unittest

import launch
import launch_testing
import launch_testing.actions
import pytest
import rclpy
from ament_index_python.packages import get_package_share_directory
from diagnostic_msgs.msg import DiagnosticArray
from jarvis_interfaces.msg import Event
from jarvis_lake.lake import Lake
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from rclpy.qos import QoSProfile, ReliabilityPolicy

DATA_DIR = tempfile.mkdtemp(prefix="jarvis_lake_")
BRINGUP = pathlib.Path(get_package_share_directory("jarvis_bringup")) / "launch" / "jarvis.launch.py"


@pytest.mark.launch_test
def generate_test_description():
    jarvis = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(str(BRINGUP)),
        launch_arguments={
            "robot_id": "lake-bot",
            "data_dir": DATA_DIR,
            "split_duration_s": "3",
            "bridges": "false",
            "recovery": "false",
            "blackbox": "false",
            "gateway": "false",
        }.items(),
    )
    return launch.LaunchDescription([jarvis, launch_testing.actions.ReadyToTest()])


class TestLake(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.node = rclpy.create_node("lake_test")
        cls.diagnostics = []
        cls.node.create_subscription(
            DiagnosticArray,
            "/diagnostics",
            lambda m: cls.diagnostics.extend(s for s in m.status if s.name == "jarvis: lake"),
            10,
        )
        reliable = QoSProfile(depth=100, reliability=ReliabilityPolicy.RELIABLE)
        cls.ingest = cls.node.create_publisher(Event, "/jarvis/ingest", reliable)

    @classmethod
    def tearDownClass(cls):
        cls.node.destroy_node()
        rclpy.shutdown()

    def spin(self, seconds):
        until = time.time() + seconds
        while time.time() < until:
            rclpy.spin_once(self.node, timeout_sec=0.05)

    def test_closed_splits_are_converted_as_the_recorder_announces_them(self):
        deadline = time.time() + 60
        while self.ingest.get_subscription_count() == 0 and time.time() < deadline:
            self.spin(0.1)
        self.assertGreater(self.ingest.get_subscription_count(), 0, "the event hub never came up")
        self.spin(1.0)

        # Events keep the recorder writing, so it splits every 3 s and announces each split.
        lake = Lake(DATA_DIR)
        seen = 0
        for i in range(60):
            e = Event(kind=Event.KIND_LOG, code="test.lake.tick", severity=Event.SEVERITY_INFO)
            e.message = f"tick {i}"
            e.source.node, e.source.component = "/lake_test", "lake_test"
            e.stamp = self.node.get_clock().now().to_msg()
            self.ingest.publish(e)
            self.spin(0.25)
            status = lake.layout.status
            if status.exists() and i > 20:
                con = Lake(DATA_DIR).connect()
                seen = con.execute("SELECT count(*) FROM events WHERE code = 'test.lake.tick'").fetchone()[0]
                if seen:
                    break
        self.assertGreater(seen, 0, f"no converted tick in {DATA_DIR}/lake")
        # It reports after each conversion pass: keep the recorder splitting until a report arrives.
        deadline = time.time() + 30
        while not self.diagnostics and time.time() < deadline:
            e = Event(kind=Event.KIND_LOG, code="test.lake.tick", severity=Event.SEVERITY_INFO)
            e.source.node, e.source.component = "/lake_test", "lake_test"
            e.stamp = self.node.get_clock().now().to_msg()
            self.ingest.publish(e)
            self.spin(0.25)
        self.assertTrue(self.diagnostics, "the lake reports on /diagnostics")
        self.assertEqual(self.diagnostics[-1].hardware_id, "jarvis")


@launch_testing.post_shutdown_test()
class TestShutdown(unittest.TestCase):
    def test_exit_code(self, proc_info):
        launch_testing.asserts.assertExitCodes(proc_info, allowable_exit_codes=[0, -2, -15])
