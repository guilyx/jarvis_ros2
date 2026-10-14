# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""End-to-end: rule engine + recovery orchestrator on top of the event hub and fault manager.

Covers docs/design/04-data-flow.md §3 over real topics: a fault fires a rule, the rule requests a
plan, the orchestrator restarts a managed node through its lifecycle services, verifies the fault
clears and the incident resolves. Then the operator side: the ExecuteRecovery action, dry runs,
approvals, the kill switch, and the rule services.
"""

import json
import math
import pathlib
import sys
import tempfile
import time
import unittest

import launch
import launch_testing
import launch_testing.actions
import pytest
import rclpy
from jarvis_interfaces.action import ExecuteRecovery
from jarvis_interfaces.msg import Attribute, Event, Execution, ExecutionArray
from jarvis_interfaces.srv import DecideApproval, ListRules, ReloadConfig, SetRecoveryMode, SetRuleState
from launch.actions import ExecuteProcess
from launch_ros.actions import ComposableNodeContainer
from launch_ros.descriptions import ComposableNode
from lifecycle_msgs.srv import GetState
from rclpy.action import ActionClient
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_srvs.srv import Trigger

HERE = pathlib.Path(__file__).resolve().parent
STATE_DIR = tempfile.mkdtemp(prefix="jarvis_recovery_")


@pytest.mark.launch_test
def generate_test_description():
    common = {"robot_id": "test-bot", "state_dir": STATE_DIR, "autostart": True}
    fast = [{"use_intra_process_comms": True}]
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
                extra_arguments=fast,
            ),
            ComposableNode(
                package="jarvis_ros",
                plugin="jarvis_ros::FaultManagerNode",
                parameters=[{**common, "catalog_path": str(HERE / "data" / "faults.yaml")}],
                extra_arguments=fast,
            ),
            ComposableNode(
                package="jarvis_ros",
                plugin="jarvis_ros::RuleEngineNode",
                parameters=[{**common, "rules_dir": str(HERE / "data" / "rules")}],
                extra_arguments=fast,
            ),
            ComposableNode(
                package="jarvis_ros",
                plugin="jarvis_ros::RecoveryNode",
                parameters=[{**common, "plans_dir": str(HERE / "data" / "plans")}],
                extra_arguments=fast,
            ),
        ],
        output="screen",
    )
    victim = ExecuteProcess(cmd=[sys.executable, str(HERE / "victim.py")], output="screen")
    return launch.LaunchDescription([container, victim, launch_testing.actions.ReadyToTest()])


def attr(event, key):
    for a in event.attrs:
        if a.key == key:
            return {0: a.string_value, 1: a.int_value, 2: a.double_value, 3: a.bool_value}[a.type]
    return None


class TestRecoveryFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.node = rclpy.create_node("recovery_flow_test")
        stream = QoSProfile(depth=1000, reliability=ReliabilityPolicy.RELIABLE)
        latched = QoSProfile(
            depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL
        )
        cls.events = []
        cls.executions = None
        cls.pokes = 0
        cls.ingest = cls.node.create_publisher(Event, "/jarvis/ingest", stream)
        cls.node.create_subscription(Event, "/jarvis/events", cls.events.append, stream)
        cls.node.create_subscription(ExecutionArray, "/jarvis/executions", cls._on_executions, latched)
        cls.node.create_service(Trigger, "/test/poke", cls._on_poke)
        cls.execute = ActionClient(cls.node, ExecuteRecovery, "/jarvis/recovery/execute")
        cls.set_mode = cls.node.create_client(SetRecoveryMode, "/jarvis/recovery/set_mode")
        cls.decide = cls.node.create_client(DecideApproval, "/jarvis/recovery/decide")
        cls.reload_plans = cls.node.create_client(ReloadConfig, "/jarvis/recovery/reload")
        cls.list_rules = cls.node.create_client(ListRules, "/jarvis/rule_engine/list_rules")
        cls.set_rule = cls.node.create_client(SetRuleState, "/jarvis/rule_engine/set_rule_state")
        for client in (cls.set_mode, cls.decide, cls.reload_plans, cls.list_rules, cls.set_rule):
            assert client.wait_for_service(timeout_sec=60.0), client.srv_name
        assert cls.execute.wait_for_server(timeout_sec=60.0), "no ExecuteRecovery server"
        deadline = time.time() + 30.0
        while time.time() < deadline and cls.ingest.get_subscription_count() == 0:
            rclpy.spin_once(cls.node, timeout_sec=0.1)

    @classmethod
    def tearDownClass(cls):
        cls.node.destroy_node()
        rclpy.shutdown()

    @classmethod
    def _on_executions(cls, msg):
        cls.executions = msg

    @classmethod
    def _on_poke(cls, request, response):
        cls.pokes += 1
        response.success = True
        response.message = "poked"
        return response

    # -- helpers ---------------------------------------------------------------------------------

    def wait_for(self, predicate, timeout=20.0, what="condition"):
        deadline = time.time() + timeout
        while time.time() < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.05)
            result = predicate()
            if result:
                return result
        self.fail(f"timed out waiting for {what}")

    def call(self, client, request):
        future = client.call_async(request)
        rclpy.spin_until_future_complete(self.node, future, timeout_sec=10.0)
        self.assertTrue(future.done(), client.srv_name)
        return future.result()

    def recovery(self, phase, plan):
        return [
            e
            for e in self.events
            if e.kind == Event.KIND_RECOVERY and attr(e, "phase") == phase and attr(e, "plan") == plan
        ]

    def goal(self, plan, args=None, dry_run=False):
        goal = ExecuteRecovery.Goal(
            plan_id=plan,
            requester_type=ExecuteRecovery.Goal.REQUESTER_OPERATOR,
            requester_id="alice",
            dry_run=dry_run,
            confidence=math.nan,
        )
        goal.args = [
            Attribute(key=k, type=Attribute.TYPE_STRING, string_value=v) for k, v in (args or {}).items()
        ]
        feedback = []
        sent = self.execute.send_goal_async(goal, feedback_callback=lambda f: feedback.append(f.feedback))
        rclpy.spin_until_future_complete(self.node, sent, timeout_sec=10.0)
        handle = sent.result()
        self.assertTrue(handle.accepted, "the server accepts every goal; the gates decide")
        return handle, feedback

    def result(self, handle, timeout=20.0):
        future = handle.get_result_async()
        rclpy.spin_until_future_complete(self.node, future, timeout_sec=timeout)
        self.assertTrue(future.done(), "no result")
        return future.result().result

    # -- tests (run in order) --------------------------------------------------------------------

    def test_1_a_rule_restarts_the_node_and_the_fault_clears(self):
        victim = self.node.create_client(GetState, "/test/victim/get_state")
        self.assertTrue(victim.wait_for_service(timeout_sec=30.0), "the victim never came up")
        raised = Event(kind=Event.KIND_FAULT_RAISE, code="test.victim.stuck", severity=Event.SEVERITY_ERROR)
        raised.source.node = "/test/watchdog"
        raised.source.component = "victim"
        raised.message = "victim stopped answering"
        self.ingest.publish(raised)

        plan = "restart-lifecycle-node"
        succeeded = self.wait_for(lambda: self.recovery("SUCCEEDED", plan), what="SUCCEEDED")[0]
        transitions = [e.message for e in self.events if e.code == "test.victim.transition"]
        self.assertEqual(transitions[-4:], ["deactivate", "cleanup", "configure", "activate"])

        started = self.recovery("STARTED", plan)[0]
        self.assertEqual(attr(started, "requester"), "RULE")
        self.assertEqual(attr(started, "requester_id"), "victim-restart")
        match = next(e for e in self.events if e.code == "jarvis.rule.matched")
        self.assertEqual(started.causation_id, match.id, "the plan was caused by the rule match")
        self.assertTrue(started.correlation_id, "in the fault's incident")
        self.assertEqual(succeeded.correlation_id, started.correlation_id)
        incident = started.correlation_id
        self.wait_for(
            lambda: any(
                e.code == "jarvis.incident.resolved" and e.correlation_id == incident for e in self.events
            ),
            what="incident resolved",
        )
        codes = [e.code for e in self.events if e.correlation_id == incident]
        self.assertIn("jarvis.incident.recovering", codes)

        def recent():
            done = [x for x in (self.executions.recent if self.executions else []) if x.plan_id == plan]
            return done and done[-1]

        x = self.wait_for(recent, what="/jarvis/executions")
        self.assertEqual(x.state, Execution.STATE_SUCCEEDED)
        self.assertEqual(x.requester_type, Execution.REQUESTER_RULE)
        self.assertEqual([s.id for s in x.steps], ["restart", "verify"])
        self.assertEqual(x.steps[0].message, "/test/victim: deactivate -> cleanup -> configure -> activate")
        self.assertEqual(self.executions.mode, "ENABLED")

    def test_2_operators_run_plans_through_the_action(self):
        handle, feedback = self.goal("poke", {"service": "/test/poke"})
        result = self.result(handle)
        self.assertEqual(result.outcome, ExecuteRecovery.Result.OUTCOME_SUCCEEDED, result.reason)
        self.assertEqual(self.pokes, 1)
        self.assertTrue(feedback and feedback[0].state == ExecuteRecovery.Feedback.STATE_RUNNING)
        self.wait_for(lambda: any(e.code == "test.poked" for e in self.events), what="emitted event")

        handle, _ = self.goal("poke", {"service": "/test/poke"}, dry_run=True)
        result = self.result(handle)
        self.assertEqual(result.outcome, ExecuteRecovery.Result.OUTCOME_SUCCEEDED)
        self.assertEqual(self.pokes, 1, "a dry run calls nothing")

        handle, _ = self.goal("poke", {"service": "relative"})
        self.assertEqual(self.result(handle).outcome, ExecuteRecovery.Result.OUTCOME_FAILED)
        handle, _ = self.goal("nope")
        result = self.result(handle)
        self.assertEqual(result.outcome, ExecuteRecovery.Result.OUTCOME_REJECTED)
        self.assertEqual(result.reason, "unknown plan 'nope'")

    def test_3_approvals(self):
        handle, feedback = self.goal("gated")
        self.wait_for(
            lambda: feedback and feedback[-1].state == ExecuteRecovery.Feedback.STATE_PENDING_APPROVAL,
            what="PENDING_APPROVAL feedback",
        )
        request = DecideApproval.Request(
            execution_id=feedback[-1].execution_id, approve=True, operator_name="bob"
        )
        reply = self.call(self.decide, request)
        self.assertTrue(reply.success, reply.message)
        self.assertEqual(self.result(handle).outcome, ExecuteRecovery.Result.OUTCOME_SUCCEEDED)
        approved = self.wait_for(lambda: self.recovery("APPROVED", "gated"), what="APPROVED event")[0]
        self.assertEqual(attr(approved, "approved_by"), "bob")

    def test_4_the_kill_switch(self):
        mode = SetRecoveryMode.Request
        reply = self.call(self.set_mode, mode(mode="DISABLED", operator_name="alice", reason="test"))
        self.assertTrue(reply.success, reply.message)
        self.assertEqual(reply.previous_mode, "ENABLED")
        handle, _ = self.goal("poke", {"service": "/test/poke"})
        result = self.result(handle)
        self.assertEqual(result.outcome, ExecuteRecovery.Result.OUTCOME_REJECTED)
        self.assertEqual(result.reason, "recovery is DISABLED")
        self.wait_for(lambda: self.executions and self.executions.mode == "DISABLED", what="mode published")
        self.assertFalse(self.call(self.set_mode, mode(mode="OFF")).success)
        self.assertTrue(self.call(self.set_mode, mode(mode="ENABLED", operator_name="alice")).success)

    def test_5_rule_and_plan_services(self):
        rules = self.call(self.list_rules, ListRules.Request()).rules
        self.assertEqual([r.id for r in rules], ["victim-restart"])
        self.assertGreaterEqual(rules[0].fires, 1)
        self.assertTrue(rules[0].file.endswith("test.yaml"))
        shadow = SetRuleState.Request(rule_id="victim-restart", dry_run=SetRuleState.Request.SET_TRUE)
        reply = self.call(self.set_rule, shadow)
        self.assertTrue(reply.success)
        self.assertTrue(self.call(self.list_rules, ListRules.Request(ids=["victim-*"])).rules[0].dry_run)
        self.assertFalse(self.call(self.set_rule, SetRuleState.Request(rule_id="nope")).success)

        reply = self.call(self.reload_plans, ReloadConfig.Request(validate_only=True))
        self.assertTrue(reply.success, reply.errors)
        self.assertEqual(reply.message, "3 plans are valid")
        self.assertEqual(len(reply.revision), 12)

    def test_6_state_is_persisted(self):
        def saved():
            files = {p.name for p in pathlib.Path(STATE_DIR).glob("*.json")}
            return {"rule_engine.json", "recovery.json"} <= files

        self.wait_for(saved, what="state files")
        recovery = json.loads((pathlib.Path(STATE_DIR) / "recovery.json").read_text())
        self.assertEqual(recovery["schema"], "jarvis.recovery.state/v1")
        self.assertEqual(recovery["mode"], "ENABLED")


@launch_testing.post_shutdown_test()
class TestShutdown(unittest.TestCase):
    def test_exit_code(self, proc_info):
        launch_testing.asserts.assertExitCodes(proc_info, allowable_exit_codes=[0, -2, -15])
