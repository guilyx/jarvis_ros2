# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Insights on the ROS graph: INSIGHT events on /jarvis/ingest (the event hub records them, the
dashboard shows them, the lake keeps them). rclpy is imported only here."""

import json

import rclpy
from jarvis_interfaces.msg import Attribute, Event
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.signals import SignalHandlerOptions


class RosInsightSink:
    def __init__(self, namespace="/jarvis", node_name="agent"):
        self.context = rclpy.Context()
        rclpy.init(context=self.context, signal_handler_options=SignalHandlerOptions.NO)
        self.node = rclpy.create_node(node_name, namespace=namespace, context=self.context)
        qos = QoSProfile(
            depth=100, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.VOLATILE
        )
        self.publisher = self.node.create_publisher(Event, f"{namespace.rstrip('/')}/ingest", qos)

    def publish(self, insight, correlation_id="", causation_id=""):
        e = Event(kind=Event.KIND_INSIGHT, code="jarvis.insight", severity=Event.SEVERITY_INFO)
        e.source.node = self.node.get_fully_qualified_name()
        e.message = insight.title
        e.payload = json.dumps(insight.to_json(), separators=(",", ":"))
        e.payload_schema = "jarvis.insight/v1"
        e.correlation_id = correlation_id
        e.causation_id = causation_id
        e.attrs = [
            Attribute(key="insight_id", type=Attribute.TYPE_STRING, string_value=insight.insight_id),
            Attribute(key="analyst", type=Attribute.TYPE_STRING, string_value=insight.analyst),
            Attribute(key="confidence", type=Attribute.TYPE_DOUBLE, double_value=float(insight.confidence)),
            Attribute(key="degraded", type=Attribute.TYPE_BOOL, bool_value=insight.degraded),
        ]
        self.publisher.publish(e)

    def close(self):
        self.node.destroy_node()
        self.context.try_shutdown()
