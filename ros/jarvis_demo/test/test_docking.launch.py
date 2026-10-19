# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""End-to-end docking demo: a low battery, and Jarvis docks the robot through Nav2's DockRobot action
and AMCL's SetInitialPose service, with the generic actuators (action.goal, service.call)."""

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
from jarvis_interfaces.srv import ListPlans
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue
from rcl_interfaces.srv import SetParameters
from rclpy.qos import QoSProfile, ReliabilityPolicy

DATA_DIR = tempfile.mkdtemp(prefix="jarvis_docking_")
PLAN = "dock-and-charge"


@pytest.mark.launch_test
def generate_test_description():
    docking = pathlib.Path(get_package_share_directory("jarvis_demo")) / "launch" / "docking.launch.py"
    return launch.LaunchDescription(
        [
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(str(docking)),
                launch_arguments={"data_dir": DATA_DIR, "gateway": "false", "agent": "false"}.items(),
            ),
            launch_testing.actions.ReadyToTest(),
        ]
    )


def attr(event, key):
    return next((a.string_value for a in event.attrs if a.key == key), None)


class TestDocking(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.node = rclpy.create_node("docking_test")
        cls.events = []
        stream = QoSProfile(depth=1000, reliability=ReliabilityPolicy.RELIABLE)
        cls.node.create_subscription(Event, "/jarvis/events", cls.events.append, stream)
        cls.set_params = cls.node.create_client(SetParameters, "/demo/base/set_parameters")
        list_plans = cls.node.create_client(ListPlans, "/jarvis/recovery/list_plans")
        for client in (cls.set_params, list_plans):
            assert client.wait_for_service(timeout_sec=60.0), client.srv_name
        # The plan loaded: its types were found and its steps checked against them.
        deadline = time.time() + 60.0
        plans = []
        while time.time() < deadline and PLAN not in plans:
            future = list_plans.call_async(ListPlans.Request())
            rclpy.spin_until_future_complete(cls.node, future, timeout_sec=5.0)
            plans = [p.id for p in future.result().plans] if future.done() and future.result() else []
            time.sleep(0.5)
        assert PLAN in plans, plans

    @classmethod
    def tearDownClass(cls):
        cls.node.destroy_node()
        rclpy.shutdown()

    def wait_for(self, predicate, what, timeout=45.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.05)
            found = next((e for e in self.events if predicate(e)), None)
            if found:
                return found
        self.fail(f"timed out waiting for {what}")

    def inject(self, value):
        p = Parameter(
            name="inject", value=ParameterValue(type=ParameterType.PARAMETER_STRING, string_value=value)
        )
        future = self.set_params.call_async(SetParameters.Request(parameters=[p]))
        rclpy.spin_until_future_complete(self.node, future, timeout_sec=10.0)
        self.assertTrue(future.result().results[0].successful, value)

    def recovery(self, phase, step=None, requester=None):
        return lambda e: (
            e.kind == Event.KIND_RECOVERY
            and attr(e, "plan") == PLAN
            and attr(e, "phase") == phase
            and (step is None or attr(e, "step") == step)
            and (requester is None or attr(e, "requester") == requester)
        )

    def test_1_a_low_battery_docks_the_robot(self):
        self.inject("drain")
        self.wait_for(
            lambda e: (
                e.kind == Event.KIND_FAULT_STATE
                and e.code == "power.battery.low"
                and attr(e, "state") == "ACTIVE"
            ),
            "power.battery.low ACTIVE",
        )
        self.wait_for(self.recovery("SUCCEEDED", requester="RULE"), "the robot docked")
        dock = self.wait_for(self.recovery("STEP_SUCCEEDED", "dock"), "the DockRobot step")
        self.assertIn('/dock_robot: {"error_code":0', dock.message)
        relocalize = self.wait_for(self.recovery("STEP_SUCCEEDED", "relocalize"), "the SetInitialPose step")
        self.assertEqual(relocalize.message, "step relocalize: /set_initial_pose: done")

    def test_2_a_dock_that_is_not_found_fails_the_step(self):
        self.inject("no_dock")
        cmd = [
            "ros2",
            "jarvis",
            "recover",
            PLAN,
            "-a",
            "dock=home",
            "--operator",
            "tester",
            "--timeout",
            "60",
        ]
        subprocess.run(cmd, timeout=90, check=False)
        failed = self.wait_for(self.recovery("STEP_FAILED", "dock", "OPERATOR"), "the dock step failing")
        self.assertIn("/dock_robot: the goal was aborted: dock 'home' not detected", failed.message)
        self.wait_for(self.recovery("ESCALATED", requester="OPERATOR"), "escalated")


@launch_testing.post_shutdown_test()
class TestShutdown(unittest.TestCase):
    def test_exit_code(self, proc_info):
        launch_testing.asserts.assertExitCodes(proc_info, allowable_exit_codes=[0, -2, -15])
