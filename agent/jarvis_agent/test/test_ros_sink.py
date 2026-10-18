# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The agent on the ROS graph: INSIGHT events on /jarvis/ingest, proposals as ExecuteRecovery goals
(skipped without rclpy)."""

import json
import threading
import time

import pytest
from conftest import STALL
from jarvis_agent.core.agent import Agent

DOMAIN = 73  # apart from the launch tests (61-68) and a local demo (71)


def test_the_insight_is_published_as_an_event(query):
    rclpy = pytest.importorskip("rclpy")
    pytest.importorskip("jarvis_interfaces")
    from jarvis_agent.adapters.ros2 import RosInsightSink
    from jarvis_interfaces.msg import Event
    from rclpy.qos import QoSProfile, ReliabilityPolicy

    sink = RosInsightSink(node_name="agent_test", domain_id=DOMAIN)
    context = rclpy.Context()
    rclpy.init(context=context, domain_id=DOMAIN)
    node = rclpy.create_node("ingest_listener", context=context)
    received = []
    reliable = QoSProfile(depth=100, reliability=ReliabilityPolicy.RELIABLE)
    node.create_subscription(Event, "/jarvis/ingest", received.append, reliable)
    executor = rclpy.executors.SingleThreadedExecutor(context=context)
    executor.add_node(node)
    try:
        insight = Agent(query).explain(STALL)
        deadline = time.time() + 20
        mine = []
        while time.time() < deadline and not mine:
            sink.publish(insight, correlation_id=STALL, causation_id="e1")
            executor.spin_once(timeout_sec=0.2)
            mine = [m for m in received if insight.insight_id in m.payload]
        assert mine, "the insight never arrived"
        m = mine[0]
        assert (m.kind, m.code) == (Event.KIND_INSIGHT, "jarvis.insight")
        assert m.payload_schema == "jarvis.insight/v1"
        assert (m.correlation_id, m.causation_id, m.message) == (STALL, "e1", insight.title)
        assert m.source.node == "/jarvis/agent_test"
        assert json.loads(m.payload)["hypotheses"][0]["cause"] == insight.hypotheses[0].cause
        attrs = {a.key: a for a in m.attrs}
        assert attrs["analyst"].string_value == "heuristic"
        assert attrs["confidence"].double_value == pytest.approx(insight.confidence)
    finally:
        node.destroy_node()
        context.try_shutdown()
        sink.close()


def test_proposals_go_to_the_one_gate_as_the_agent():
    rclpy = pytest.importorskip("rclpy")
    pytest.importorskip("jarvis_interfaces")
    from jarvis_agent.adapters.ros2 import RosAgent
    from jarvis_interfaces.action import ExecuteRecovery
    from rclpy.action import ActionServer

    context = rclpy.Context()
    rclpy.init(context=context, domain_id=DOMAIN + 1)
    node = rclpy.create_node("orchestrator_stub", context=context)
    goals = []

    def execute(handle):  # what the orchestrator does with a supervised agent proposal
        goals.append(handle.request)
        feedback = ExecuteRecovery.Feedback(
            execution_id="x-1", state=ExecuteRecovery.Feedback.STATE_PENDING_APPROVAL, message="waiting"
        )
        handle.publish_feedback(feedback)
        time.sleep(0.5)
        handle.succeed()
        return ExecuteRecovery.Result(
            execution_id="x-1", outcome=ExecuteRecovery.Result.OUTCOME_REJECTED, reason="rejected by bob"
        )

    ActionServer(node, ExecuteRecovery, "/jarvis/recovery/execute", execute)
    executor = rclpy.executors.MultiThreadedExecutor(context=context)
    executor.add_node(node)
    spinner = threading.Thread(target=executor.spin, daemon=True)
    spinner.start()
    agent = RosAgent(node_name="agent_test", domain_id=DOMAIN + 1)
    try:
        got = agent.submit(
            "restart-lifecycle-node",
            {"node": "/demo/scan_relay", "component": "lidar"},
            rationale="the relay hangs (insight i-1)",
            confidence=0.8,
            correlation_id="inc-1",
            causation_id="e-1",
        )
        assert (got.execution_id, got.state, got.reason) == ("x-1", "PENDING_APPROVAL", "waiting")
        (goal,) = goals
        assert goal.requester_type == ExecuteRecovery.Goal.REQUESTER_AGENT
        assert goal.requester_id == "/jarvis/agent_test"
        assert (goal.plan_id, goal.rationale, goal.correlation_id, goal.causation_id) == (
            "restart-lifecycle-node",
            "the relay hangs (insight i-1)",
            "inc-1",
            "e-1",
        )
        assert goal.confidence == pytest.approx(0.8) and not goal.dry_run
        assert {a.key: a.string_value for a in goal.args} == {
            "node": "/demo/scan_relay",
            "component": "lidar",
        }
    finally:
        agent.close()
        executor.shutdown()
        node.destroy_node()
        context.try_shutdown()

    lonely = RosAgent(node_name="agent_alone", domain_id=DOMAIN + 2)
    try:
        got = lonely.submit("p", {}, rationale="r", confidence=0.9, timeout_s=0.5)
        assert (got.execution_id, got.state) == ("", "UNAVAILABLE")
    finally:
        lonely.close()


def test_questions_are_answered_over_the_analyze_action(query):
    rclpy = pytest.importorskip("rclpy")
    pytest.importorskip("jarvis_interfaces")
    from action_msgs.msg import GoalStatus
    from jarvis_agent.adapters.ros2 import RosAgent
    from jarvis_interfaces.action import Analyze
    from rclpy.action import ActionClient

    agent = RosAgent(node_name="agent_answering", domain_id=DOMAIN + 3)
    agent.serve(lambda incident_id, question, progress: Agent(query).ask(incident_id, question, progress))
    context = rclpy.Context()
    rclpy.init(context=context, domain_id=DOMAIN + 3)
    node = rclpy.create_node("asker", context=context)
    client = ActionClient(node, Analyze, "/jarvis/agent/analyze")
    executor = rclpy.executors.SingleThreadedExecutor(context=context)
    executor.add_node(node)

    def ask(**goal):
        stages = []
        sent = client.send_goal_async(
            Analyze.Goal(requester="test", **goal),
            feedback_callback=lambda m: stages.append(m.feedback.stage),
        )
        executor.spin_until_future_complete(sent, timeout_sec=20)
        assert sent.done(), "the goal was not taken"
        handle = sent.result()
        # Nothing orders feedback (a topic) and the result (a service reply), and rclpy's client
        # drops a goal's feedback once its result is in: hear the last stage, then ask for it.
        ended = (GoalStatus.STATUS_ABORTED, GoalStatus.STATUS_CANCELED)
        deadline = time.monotonic() + 30.0
        while stages[-1:] != ["done"] and handle.status not in ended and time.monotonic() < deadline:
            executor.spin_once(timeout_sec=0.1)
        result = handle.get_result_async()
        executor.spin_until_future_complete(result, timeout_sec=30)
        assert result.done(), "no result"
        return result.result().result, stages

    try:
        assert client.wait_for_server(timeout_sec=30)
        r, stages = ask(incident_id=STALL)
        assert r.success, r.message
        assert json.loads(r.insight_json)["window"]["correlation_id"] == STALL
        assert stages[0] == "window" and stages[-1] == "done"
        r, _ = ask(incident_id="nope")
        assert (r.success, r.message) == (False, "no incident 'nope'")
    finally:
        node.destroy_node()
        context.try_shutdown()
        agent.close()
