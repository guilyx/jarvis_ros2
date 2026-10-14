# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""A managed node for the recovery tests: /test/victim.

It reports each lifecycle transition as a LOG event (`test.victim.transition`) and, on activation,
clears its `test.victim.stuck` fault, like a node whose restart fixes it.
"""

import contextlib

import rclpy
from jarvis_interfaces.msg import Attribute, Event
from rclpy.executors import ExternalShutdownException
from rclpy.lifecycle import LifecycleNode, TransitionCallbackReturn
from rclpy.qos import QoSProfile, ReliabilityPolicy


class Victim(LifecycleNode):
    def __init__(self):
        super().__init__("victim", namespace="test")
        qos = QoSProfile(depth=100, reliability=ReliabilityPolicy.RELIABLE)
        self._ingest = self.create_publisher(Event, "/jarvis/ingest", qos)

    def _send(self, kind, code, message, transition=None):
        e = Event(kind=kind, code=code, message=message, severity=Event.SEVERITY_INFO)
        e.source.node = "/test/victim"
        e.source.component = "victim"
        if transition:
            e.attrs = [Attribute(key="transition", type=Attribute.TYPE_STRING, string_value=transition)]
        self._ingest.publish(e)

    def _note(self, transition):
        self._send(Event.KIND_LOG, "test.victim.transition", transition, transition)
        return TransitionCallbackReturn.SUCCESS

    def on_configure(self, state):
        return self._note("configure")

    def on_activate(self, state):
        self._note("activate")
        self._send(Event.KIND_FAULT_CLEAR, "test.victim.stuck", "unstuck by the restart")
        return TransitionCallbackReturn.SUCCESS

    def on_deactivate(self, state):
        return self._note("deactivate")

    def on_cleanup(self, state):
        return self._note("cleanup")


def main():
    rclpy.init()
    node = Victim()
    node.trigger_configure()
    node.trigger_activate()
    with contextlib.suppress(KeyboardInterrupt, ExternalShutdownException):
        rclpy.spin(node)


if __name__ == "__main__":
    main()
