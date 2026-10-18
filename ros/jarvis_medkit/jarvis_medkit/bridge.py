# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""medkit_bridge: the faults of ros2_medkit's fault manager, as Jarvis faults (roadmap 6.4).

A robot that runs ros2_medkit keeps it: medkit confirms its faults (its own debounce), the bridge
reports each one to Jarvis on /jarvis/ingest, and Jarvis's rules, recovery and agent work on them.

- medkit confirms a fault, or sees it again: a FAULT_RAISE per reporting source (medkit counts a
  code once; Jarvis counts a code per component, the last segment of the source's name).
- medkit clears it (an operator) or it heals: a FAULT_CLEAR for each source, and for the symptoms
  medkit cleared with it.
- medkit's events are not replayed to late joiners: at start, then every `resync_period_s`, the
  bridge reads `list_faults` and makes Jarvis agree, raising what it missed and clearing what is gone.
  At start it also reads Jarvis's open faults under `code_prefix`: those medkit no longer confirms
  (cleared while the bridge was down) are cleared, those it still does are not raised again.

Codes become Jarvis codes under `code_prefix`: MOTOR_OVERHEAT is medkit.motor_overheat, the original
in the attribute medkit.fault_code. Muted symptoms are left to medkit unless `include_muted`.
Clearing a fault in Jarvis does not clear it in medkit: clear it there.
"""

import re
import sys

import rclpy
from jarvis_interfaces.msg import Attribute, Event, Fault
from jarvis_interfaces.srv import GetFaults
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from ros2_medkit_msgs.msg import FaultEvent
from ros2_medkit_msgs.srv import ListFaults

SEVERITY = {
    0: Event.SEVERITY_INFO,
    1: Event.SEVERITY_WARN,
    2: Event.SEVERITY_ERROR,
    3: Event.SEVERITY_CRITICAL,
}
CONFIRMED = ("fault_confirmed", "fault_updated")
INGEST = QoSProfile(depth=1000, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.VOLATILE)
EVENTS = QoSProfile(depth=100, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.VOLATILE)


def jarvis_code(code, prefix="medkit"):
    """A medkit fault code as a Jarvis code: dotted, lowercase (`^[a-z0-9_]+(\\.[a-z0-9_]+)*$`)."""
    parts = [re.sub(r"[^a-z0-9_]+", "_", p).strip("_") for p in f"{prefix}.{code}".lower().split(".")]
    return ".".join(p for p in parts if p) or prefix


def component_of(source):
    """The component of a reporting source: the last segment of the node's name."""
    return source.rstrip("/").rsplit("/", 1)[-1] or "medkit"


def _attr(key, value):
    if isinstance(value, int):
        return Attribute(key=key, type=Attribute.TYPE_INT, int_value=value)
    return Attribute(key=key, type=Attribute.TYPE_STRING, string_value=str(value))


class Bridge(Node):
    def __init__(self, **kwargs):
        super().__init__("medkit", namespace="/jarvis/bridges", **kwargs)
        fm = self.declare_parameter("fault_manager", "/fault_manager").value.rstrip("/")
        jarvis = self.declare_parameter("jarvis_fault_manager", "/jarvis/fault_manager").value.rstrip("/")
        self.prefix = self.declare_parameter("code_prefix", "medkit").value
        self.include_muted = self.declare_parameter("include_muted", False).value
        period = self.declare_parameter("resync_period_s", 10.0).value
        self.raised = {}  # (medkit code, source) -> the fault as last reported
        self.ingest = self.create_publisher(Event, "/jarvis/ingest", INGEST)
        self.create_subscription(FaultEvent, f"{fm}/events", self.on_event, EVENTS)
        self.faults = self.create_client(ListFaults, f"{fm}/list_faults")
        self.jarvis = self.create_client(GetFaults, f"{jarvis}/get_faults")
        self.pending = None
        self.synced = False
        # Every second until the first sync, then every resync_period_s.
        self.first = self.create_timer(1.0, self.resync)
        self.create_timer(period, self.resync)
        self.get_logger().info(f"bridging {fm} to /jarvis/ingest as {self.prefix}.*")

    # -- medkit -> Jarvis ----------------------------------------------------------------------------

    def on_event(self, e):
        f = e.fault
        if e.event_type in CONFIRMED:
            for source in f.reporting_sources or [""]:
                self.raise_fault(f, source)
        elif e.event_type == "fault_cleared":
            reason = "cleared in ros2_medkit" if f.status == "CLEARED" else "healed in ros2_medkit"
            for code in [f.fault_code, *e.auto_cleared_codes]:
                self.clear_code(code, reason if code == f.fault_code else f"cleared with {f.fault_code}")

    def resync(self):
        """Asks medkit for its confirmed faults (and, the first time, Jarvis for its medkit faults);
        the answers make Jarvis agree."""
        if self.pending is not None and not self.pending.done():
            return
        if not self.faults.service_is_ready() or not (self.synced or self.jarvis.service_is_ready()):
            return
        request = ListFaults.Request(statuses=["CONFIRMED"], include_muted=self.include_muted)
        self.pending = self.faults.call_async(request)
        self.pending.add_done_callback(self.on_list)

    def on_list(self, future):
        response = future.result()
        if response is None:
            return
        current = {}
        for f in response.faults:
            for source in f.reporting_sources or [""]:
                current[(f.fault_code, source)] = f
        if self.synced:
            self.agree(current)
            return
        request = GetFaults.Request(codes=[f"{self.prefix}.*"], include_muted=True)
        self.pending = self.jarvis.call_async(request)
        self.pending.add_done_callback(lambda done: self.on_first_sync(current, done.result()))

    def on_first_sync(self, current, response):
        """Jarvis keeps its faults across restarts, the bridge does not: what Jarvis has open under
        the prefix and medkit still confirms is already raised; the rest is gone."""
        if response is None:
            return
        open_in_jarvis = {
            (f.code, f.source.component): f for f in response.faults if f.state != Fault.STATE_CLEARED
        }
        confirmed = set()
        for code, source in current:
            key = (jarvis_code(code, self.prefix), component_of(source))
            confirmed.add(key)
            if key in open_in_jarvis:
                self.raised[(code, source)] = current[(code, source)]
        for (code, component), f in open_in_jarvis.items():
            if (code, component) not in confirmed:
                attrs = [_attr("medkit.fault_code", f.code)]
                self.publish(
                    Event.KIND_FAULT_CLEAR,
                    code,
                    f.source.node,
                    component,
                    Event.SEVERITY_INFO,
                    "no longer confirmed in ros2_medkit",
                    attrs,
                )
        self.synced = True
        self.first.cancel()
        self.get_logger().info(
            f"in sync with ros2_medkit: {len(current)} confirmed, {len(open_in_jarvis)} already in Jarvis"
        )
        self.agree(current)

    def agree(self, current):
        for key, f in current.items():
            if key not in self.raised:
                self.raise_fault(f, key[1])
        for code, source in [k for k in self.raised if k not in current]:
            self.clear(code, source, "no longer confirmed in ros2_medkit")

    # -- Jarvis ----------------------------------------------------------------------------------------

    def raise_fault(self, f, source):
        self.raised[(f.fault_code, source)] = f
        attrs = [
            _attr("medkit.fault_code", f.fault_code),
            _attr("medkit.status", f.status),
            _attr("medkit.occurrences", int(f.occurrence_count)),
        ]
        severity = SEVERITY.get(f.severity, Event.SEVERITY_ERROR)
        self.publish(
            Event.KIND_FAULT_RAISE,
            self.code(f.fault_code),
            source,
            component_of(source),
            severity,
            f.description,
            attrs,
        )

    def clear_code(self, code, reason):
        for c, source in [k for k in self.raised if k[0] == code]:
            self.clear(c, source, reason)

    def clear(self, code, source, reason):
        self.raised.pop((code, source), None)
        attrs = [_attr("medkit.fault_code", code)]
        self.publish(
            Event.KIND_FAULT_CLEAR,
            self.code(code),
            source,
            component_of(source),
            Event.SEVERITY_INFO,
            reason,
            attrs,
        )

    def code(self, medkit_code):
        return jarvis_code(medkit_code, self.prefix)

    def publish(self, kind, code, node, component, severity, message, attrs):
        e = Event(kind=kind, code=code, severity=severity, message=message)
        e.stamp = self.get_clock().now().to_msg()
        e.source.node = node or "/fault_manager"
        e.source.component = component
        e.attrs = attrs
        e.tags = ["medkit"]
        self.ingest.publish(e)


def main(argv=None):
    rclpy.init(args=argv if argv is not None else sys.argv)
    node = Bridge()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
    return 0
