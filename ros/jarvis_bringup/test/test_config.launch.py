# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Rules and plans from the fleet in the running stack (config_from:=file://...): the robot starts on
its own, loads the fleet's when they come, refuses a broken revision (raising jarvis.config.rejected
and keeping what runs), and takes the fixed one."""

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
from ament_index_python.packages import get_package_share_directory
from jarvis_interfaces.msg import FaultArray
from jarvis_interfaces.srv import ListRules
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

DATA_DIR = pathlib.Path(tempfile.mkdtemp(prefix="jarvis_config_"))
FLEET_DIR = pathlib.Path(tempfile.mkdtemp(prefix="jarvis_fleet_config_"))
BRINGUP = pathlib.Path(get_package_share_directory("jarvis_bringup")) / "launch" / "jarvis.launch.py"
STATUS = DATA_DIR / "config" / "status.json"

RULE = """schema: jarvis.rules/v1
rules:
  - id: {id}
    description: from the fleet
    when:
      event: {{ kind: FAULT_STATE, code: sensor.scan.stale, attrs.state: ACTIVE }}
    then:
      - recover:
          plan: {plan}
          args: {{ node: /scan_relay, component: lidar }}
"""


@pytest.mark.launch_test
def generate_test_description():
    jarvis = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(str(BRINGUP)),
        launch_arguments={
            "robot_id": "config-bot",
            "data_dir": str(DATA_DIR),
            "config_from": f"file://{FLEET_DIR}",
            "config_period_s": "1",
            "bridges": "false",
            "record": "false",
            "blackbox": "false",
            "lake": "false",
            "gateway": "false",
        }.items(),
    )
    return launch.LaunchDescription([jarvis, launch_testing.actions.ReadyToTest()])


class TestFleetConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.node = rclpy.create_node("config_test")
        cls.faults = []
        latched = QoSProfile(
            depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL
        )
        cls.node.create_subscription(
            FaultArray, "/jarvis/faults", lambda m: cls.faults.append([f.code for f in m.faults]), latched
        )
        cls.rules = cls.node.create_client(ListRules, "/jarvis/rule_engine/list_rules")

    @classmethod
    def tearDownClass(cls):
        cls.node.destroy_node()
        rclpy.shutdown()

    def spin(self, seconds):
        until = time.time() + seconds
        while time.time() < until:
            rclpy.spin_once(self.node, timeout_sec=0.05)

    def status(self):
        try:
            return json.loads(STATUS.read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def wait_status(self, check, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.status()
            if status and check(status):
                return status
            self.spin(0.2)
        self.fail(f"config_sync never got there: {self.status()}")

    def rule_ids(self):
        self.assertTrue(self.rules.wait_for_service(timeout_sec=30.0), "the rule engine is not up")
        future = self.rules.call_async(ListRules.Request())
        rclpy.spin_until_future_complete(self.node, future, timeout_sec=10.0)
        return sorted(r.id for r in future.result().rules)

    def open_faults(self):
        return self.faults[-1] if self.faults else []

    def test_rules_from_the_fleet_are_loaded_and_a_broken_revision_is_refused(self):
        # Before the fleet has anything: the robot's own (the launch's), seeded as the first version.
        seeded = self.wait_status(lambda s: s["state"] == "empty")["revision"]
        self.assertIn("escalation-notify", self.rule_ids())
        self.assertTrue((DATA_DIR / "config" / "active" / "rules" / "core.yaml").is_file())

        # The fleet's rules (using the robot's own plans), over the robot's: settled, linted, loaded.
        (FLEET_DIR / "rules").mkdir()
        (FLEET_DIR / "rules" / "lidar.yaml").write_text(
            RULE.format(id="fleet-lidar", plan="restart-lifecycle-node")
        )
        applied = self.wait_status(lambda s: s["state"] in ("applied", "in-sync") and s["revision"] != seeded)
        self.assertEqual(self.rule_ids(), ["escalation-notify", "fleet-lidar"])
        self.assertEqual(applied["from_fleet"], {"rules": ["lidar.yaml"], "plans": []})
        good = applied["revision"]

        # A rule asking for a plan nobody has: refused, the fault raised, the rules unchanged.
        (FLEET_DIR / "rules" / "lidar.yaml").write_text(RULE.format(id="fleet-lidar-2", plan="no-such-plan"))
        rejected = self.wait_status(lambda s: s["state"] == "rejected")
        self.assertEqual(rejected["revision"], good)
        self.assertIn("no-such-plan", rejected["rejected"]["errors"][0])
        self.assertTrue(rejected["rejected"]["errors"][0].startswith("rules/lidar.yaml: "))
        deadline = time.time() + 20
        while "jarvis.config.rejected" not in self.open_faults() and time.time() < deadline:
            self.spin(0.2)
        self.assertIn("jarvis.config.rejected", self.open_faults())
        self.assertEqual(self.rule_ids(), ["escalation-notify", "fleet-lidar"])

        # Fixed at the source: loaded, the fault cleared.
        (FLEET_DIR / "rules" / "lidar.yaml").write_text(
            RULE.format(id="fleet-lidar-2", plan="restart-lifecycle-node")
        )
        fixed = (good, seeded)
        self.wait_status(lambda s: s["state"] in ("applied", "in-sync") and s["revision"] not in fixed)
        self.assertEqual(self.rule_ids(), ["escalation-notify", "fleet-lidar-2"])
        deadline = time.time() + 20
        while "jarvis.config.rejected" in self.open_faults() and time.time() < deadline:
            self.spin(0.2)
        self.assertNotIn("jarvis.config.rejected", self.open_faults())


@launch_testing.post_shutdown_test()
class TestShutdown(unittest.TestCase):
    def test_processes_exit_cleanly(self, proc_info):
        launch_testing.asserts.assertExitCodes(proc_info, allowable_exit_codes=[0, -2, -15])
