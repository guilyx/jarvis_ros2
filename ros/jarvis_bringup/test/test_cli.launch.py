# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""`ros2 jarvis` (jarvis_cli) against a running Jarvis (event hub, fault manager, rule engine,
recovery). Here rather than in jarvis_cli: the gateway uses jarvis_cli, and the bringup the gateway."""

import pathlib
import subprocess
import tempfile
import time
import unittest

import launch
import launch_testing
import launch_testing.actions
import pytest
from ament_index_python.packages import get_package_share_directory
from launch_ros.actions import ComposableNodeContainer
from launch_ros.descriptions import ComposableNode

CONFIG = pathlib.Path(get_package_share_directory("jarvis_bringup")) / "config"
STATE_DIR = tempfile.mkdtemp(prefix="jarvis_cli_")


@pytest.mark.launch_test
def generate_test_description():
    common = {"robot_id": "test-bot", "state_dir": STATE_DIR, "autostart": True}
    nodes = [
        ("EventHubNode", {}),
        ("FaultManagerNode", {"catalog_path": str(CONFIG / "faults.yaml")}),
        ("RuleEngineNode", {"rules_dir": str(CONFIG / "rules")}),
        ("RecoveryNode", {"plans_dir": str(CONFIG / "plans")}),
    ]
    container = ComposableNodeContainer(
        name="jarvis_container",
        namespace="jarvis",
        package="rclcpp_components",
        executable="component_container",
        composable_node_descriptions=[
            ComposableNode(package="jarvis_ros", plugin=f"jarvis_ros::{p}", parameters=[{**common, **extra}])
            for p, extra in nodes
        ],
        output="screen",
    )
    return launch.LaunchDescription([container, launch_testing.actions.ReadyToTest()])


def jarvis(*args, check=True):
    out = subprocess.run(["ros2", "jarvis", *args], capture_output=True, text=True, timeout=60)
    if check and out.returncode != 0:
        raise AssertionError(f"ros2 jarvis {' '.join(args)} -> {out.returncode}\n{out.stdout}\n{out.stderr}")
    return out


class TestCli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        deadline = time.time() + 60
        while time.time() < deadline:
            if jarvis("mode", check=False).returncode == 0:
                return
            time.sleep(0.5)
        raise AssertionError("Jarvis never came up")

    def test_1_rules_list_shows_the_shipped_rule(self):
        out = jarvis("rules").stdout
        self.assertIn("escalation-notify  [on]", out)

    def test_2_a_dry_run_recovery_goes_through_the_gates(self):
        out = jarvis(
            "recover",
            "restart-lifecycle-node",
            "-a",
            "node=/somewhere",
            "-a",
            "component=thing",
            "--dry-run",
            "--rationale",
            "cli test",
        ).stdout
        self.assertIn("SUCCEEDED: all steps succeeded", out)
        bad = jarvis("recover", "restart-lifecycle-node", "-a", "node=relative", check=False)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("REJECTED: missing required parameter 'component'", bad.stdout)

    def test_3_executions_show_what_ran(self):
        out = jarvis("executions", "--steps").stdout
        self.assertIn("restart-lifecycle-node  SUCCEEDED (dry run)", out)
        self.assertIn("by OPERATOR", out)
        self.assertIn("- verify (wait_for) SUCCEEDED", out)

    def test_4_the_kill_switch(self):
        self.assertIn("ENABLED -> DISABLED", jarvis("mode", "disabled", "--reason", "cli test").stdout)
        self.assertEqual(jarvis("mode").stdout.strip(), "DISABLED")
        out = jarvis("recover", "restart-lifecycle-node", "-a", "node=/x", "-a", "component=x", check=False)
        self.assertIn("REJECTED: recovery is DISABLED", out.stdout)
        jarvis("mode", "ENABLED")

    def test_4b_maintenance(self):
        out = jarvis("maintenance", "thing", "30m", "--reason", "recalibrating", "--operator", "dave")
        self.assertIn("thing under maintenance until", out.stdout)
        listed = jarvis("maintenance").stdout
        self.assertIn("thing", listed)
        self.assertIn("dave: recalibrating", listed)
        self.assertIn("maintenance of thing ended", jarvis("maintenance", "thing", "--end").stdout)
        self.assertIn("nothing under maintenance", jarvis("maintenance").stdout)
        self.assertEqual(jarvis("maintenance", "thing", "--end", check=False).returncode, 1)

    def test_5_services_and_errors(self):
        self.assertIn("rules are valid", jarvis("rules", "reload", "--check").stdout)
        self.assertIn("plans are valid", jarvis("plans", "reload", "--check").stdout)
        listed = jarvis("plans").stdout
        self.assertIn("restart-lifecycle-node(component, fault?, node)", listed)
        self.assertIn("recovery mode:", listed)
        self.assertIn("no open faults", jarvis("faults").stdout)
        self.assertIn("unknown rule 'nope'", jarvis("rules", "disable", "nope", check=False).stdout)
        out = jarvis("rules", "dry-run", "escalation-notify", "--operator", "carol", "--reason", "test")
        self.assertIn("escalation-notify: updated", out.stdout)
        self.assertIn("dry run", jarvis("rules", "list", "escalation-notify").stdout)
        # Options after the ids: before Python 3.12.7, argparse rejects ids that follow an option.
        jarvis("rules", "dry-run", "escalation-notify", "--off", "--operator", "carol")
        self.assertIn("unknown or finished execution", jarvis("approve", "nope", check=False).stdout)
        out = jarvis("lint", "--rules", str(CONFIG / "rules"), "--plans", str(CONFIG / "plans"))
        self.assertIn("0 error(s)", out.stdout)
