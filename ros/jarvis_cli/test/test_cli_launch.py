# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""`ros2 jarvis` against a running Jarvis (event hub, fault manager, rule engine, recovery)."""

import os
import pathlib
import subprocess
import tempfile
import time
import unittest

os.environ["ROS_DOMAIN_ID"] = "66"  # packages are tested in parallel; own domain, set before launch

import launch  # noqa: E402
import launch_testing  # noqa: E402
import launch_testing.actions  # noqa: E402
import pytest  # noqa: E402
from ament_index_python.packages import get_package_share_directory  # noqa: E402
from launch_ros.actions import ComposableNodeContainer  # noqa: E402
from launch_ros.descriptions import ComposableNode  # noqa: E402

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

    def test_5_services_and_errors(self):
        self.assertIn("rules are valid", jarvis("rules", "reload", "--check").stdout)
        self.assertIn("plans are valid", jarvis("plans", "reload", "--check").stdout)
        self.assertIn("no open faults", jarvis("faults").stdout)
        self.assertIn("unknown rule 'nope'", jarvis("rules", "disable", "nope", check=False).stdout)
        self.assertIn("unknown or finished execution", jarvis("approve", "nope", check=False).stdout)
        out = jarvis("lint", "--rules", str(CONFIG / "rules"), "--plans", str(CONFIG / "plans"))
        self.assertIn("0 error(s)", out.stdout)
