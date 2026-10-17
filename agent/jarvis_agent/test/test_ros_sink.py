# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Insights on the ROS graph: an INSIGHT event on /jarvis/ingest (skipped without rclpy)."""

import json
import time

import pytest
from conftest import STALL
from jarvis_agent.core.agent import Agent


def test_the_insight_is_published_as_an_event(query):
    rclpy = pytest.importorskip("rclpy")
    pytest.importorskip("jarvis_interfaces")
    from jarvis_agent.adapters.ros2 import RosInsightSink
    from jarvis_interfaces.msg import Event
    from rclpy.qos import QoSProfile, ReliabilityPolicy

    sink = RosInsightSink(node_name="agent_test")
    context = rclpy.Context()
    rclpy.init(context=context)
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
