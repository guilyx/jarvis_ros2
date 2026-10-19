# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""End-to-end behavior-tree demo: the lidar relay hangs, and Jarvis restarts it with jarvis_bt's
restart_lifecycle_node.xml through `bt.run`. Reactivating does not cure a wedge; the tree's second
branch (cleanup, configure, activate) does, and the step's message says so."""

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
from jarvis_interfaces.msg import Event
from jarvis_interfaces.srv import ListPlans
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue
from rcl_interfaces.srv import SetParameters
from rclpy.qos import QoSProfile, ReliabilityPolicy

DATA_DIR = tempfile.mkdtemp(prefix="jarvis_trees_")
PLAN = "restart-with-tree"


@pytest.mark.launch_test
def generate_test_description():
    trees = pathlib.Path(get_package_share_directory("jarvis_demo")) / "launch" / "trees.launch.py"
    return launch.LaunchDescription(
        [
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(str(trees)),
                launch_arguments={"data_dir": DATA_DIR, "gateway": "false", "agent": "false"}.items(),
            ),
            launch_testing.actions.ReadyToTest(),
        ]
    )


def attr(event, key):
    return next((a.string_value for a in event.attrs if a.key == key), None)


class TestTrees(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.node = rclpy.create_node("trees_test")
        cls.events = []
        stream = QoSProfile(depth=1000, reliability=ReliabilityPolicy.RELIABLE)
        cls.node.create_subscription(Event, "/jarvis/events", cls.events.append, stream)
        cls.set_params = cls.node.create_client(SetParameters, "/demo/scan_relay/set_parameters")
        list_plans = cls.node.create_client(ListPlans, "/jarvis/recovery/list_plans")
        for client in (cls.set_params, list_plans):
            if not client.wait_for_service(timeout_sec=60.0):
                # What this node's participant discovered: a lost discovery, or a node that is gone.
                nodes = sorted(f"{ns.rstrip('/')}/{n}" for n, ns in cls.node.get_node_names_and_namespaces())
                services = sorted(name for name, _ in cls.node.get_service_names_and_types())
                raise AssertionError(f"{client.srv_name} not found; nodes {nodes}; services {services}")
        # The plan loaded: bt.run found the tree and every node in it.
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

    def wait_for(self, predicate, what, timeout=60.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.05)
            found = next((e for e in self.events if predicate(e)), None)
            if found:
                return found
        self.fail(f"timed out waiting for {what}")

    def recovery(self, phase, step=None):
        return lambda e: (
            e.kind == Event.KIND_RECOVERY
            and attr(e, "plan") == PLAN
            and attr(e, "phase") == phase
            and (step is None or attr(e, "step") == step)
        )

    def test_a_wedged_relay_is_restarted_by_the_tree(self):
        time.sleep(5.0)  # the relay has been relaying a while
        p = Parameter(
            name="inject", value=ParameterValue(type=ParameterType.PARAMETER_STRING, string_value="wedge")
        )
        future = self.set_params.call_async(SetParameters.Request(parameters=[p]))
        rclpy.spin_until_future_complete(self.node, future, timeout_sec=10.0)
        self.assertTrue(future.result().results[0].successful)
        self.wait_for(
            lambda e: (
                e.kind == Event.KIND_FAULT_STATE
                and e.code == "sensor.scan.stale"
                and attr(e, "state") == "ACTIVE"
            ),
            "sensor.scan.stale ACTIVE",
        )
        tree = self.wait_for(self.recovery("STEP_SUCCEEDED", "tree"), "the tree step")
        self.assertIn("RestartLifecycleNode succeeded in ", tree.message)
        self.assertIn(
            "Reactivate FAILURE (sensor.scan.stale (lidar): ACTIVE, still, after 4 s)", tree.message
        )
        self.assertIn("Reconfigure SUCCESS", tree.message)
        self.wait_for(self.recovery("SUCCEEDED"), "the plan succeeding")


@launch_testing.post_shutdown_test()
class TestShutdown(unittest.TestCase):
    def test_exit_code(self, proc_info):
        launch_testing.asserts.assertExitCodes(proc_info, allowable_exit_codes=[0, -2, -15])
