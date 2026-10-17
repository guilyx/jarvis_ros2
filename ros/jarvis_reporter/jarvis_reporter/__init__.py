# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""One-line fault and event reporting for any rclpy node.

    from jarvis_reporter import Reporter, Severity

    jarvis = Reporter(node, "lidar")
    jarvis.fault("sensor.lidar.stale", Severity.ERROR, "no scan for 1.2 s", {"silence_ms": 1200})
    jarvis.clear("sensor.lidar.stale")
    jarvis.state("robot.mode", "AUTONOMOUS")

Same semantics as the C++ header (include/jarvis_reporter/reporter.hpp): a fault is sent when it
starts, then at most once per ``repeat_period`` while it lasts; a clear is sent only if the fault was
raised; a state only when its value changes.
"""

import threading
import time
from collections.abc import Mapping
from enum import IntEnum

from jarvis_interfaces.msg import Attribute, Event
from rclpy.duration import Duration
from rclpy.qos import QoSProfile, ReliabilityPolicy

__all__ = ["Reporter", "Severity"]


class Severity(IntEnum):
    DEBUG = Event.SEVERITY_DEBUG
    INFO = Event.SEVERITY_INFO
    WARN = Event.SEVERITY_WARN
    ERROR = Event.SEVERITY_ERROR
    CRITICAL = Event.SEVERITY_CRITICAL


def _attribute(key, value):
    a = Attribute(key=key)
    # bool before int: bool is a subclass of int.
    if isinstance(value, bool):
        a.type, a.bool_value = Attribute.TYPE_BOOL, value
    elif isinstance(value, int):
        a.type, a.int_value = Attribute.TYPE_INT, value
    elif isinstance(value, float):
        a.type, a.double_value = Attribute.TYPE_DOUBLE, value
    else:
        a.type, a.string_value = Attribute.TYPE_STRING, str(value)
    return a


class Reporter:
    """Reports faults, states and logs to the Jarvis event hub. Thread-safe."""

    def __init__(self, node, component="", *, topic="/jarvis/ingest", repeat_period=1.0, queue_depth=1000):
        self._node = node
        self._component = component
        self._repeat = Duration(nanoseconds=int(repeat_period * 1e9))
        self._publisher = node.create_publisher(
            Event, topic, QoSProfile(depth=queue_depth, reliability=ReliabilityPolicy.RELIABLE)
        )
        self._lock = threading.Lock()
        self._raised = {}  # code -> rclpy.time.Time of the last report
        self._states = {}

    def fault(self, code, severity=Severity.ERROR, message="", attrs=None):
        """Reports a fault condition (FAULT_RAISE). Returns True if a message was published."""
        # Publishing under the lock keeps a raise and a clear from two threads in order.
        with self._lock:
            now = self._node.get_clock().now()
            last = self._raised.get(code)
            if last is not None:
                elapsed = (now - last).nanoseconds
                # A negative gap means sim time went backwards (bag loop): treat it as a fresh period.
                if 0 <= elapsed < self._repeat.nanoseconds:
                    return False
            self._raised[code] = now
            self._publish(Event.KIND_FAULT_RAISE, code, severity, message, attrs, now)
            return True

    def clear(self, code, message="", attrs=None, *, force=False):
        """Reports that a fault condition is gone (FAULT_CLEAR). Only sent if the fault was raised,
        or with ``force``: after a restart, the fault manager may still hold a fault this process
        raised in its previous life (it restores open faults), and only a clear heals it."""
        with self._lock:
            if self._raised.pop(code, None) is None and not force:
                return False
            self._publish(Event.KIND_FAULT_CLEAR, code, Severity.INFO, message, attrs)
            return True

    def state(self, code, value, message=""):
        """Latest-value-wins state (STATE), e.g. ``state("robot.mode", "AUTONOMOUS")``. Sent on change."""
        with self._lock:
            # Compare with the type, so 1 and True (or 1 and 1.0) are different values.
            if code in self._states and self._states[code] == (type(value), value):
                return False
            self._states[code] = (type(value), value)
            self._publish(Event.KIND_STATE, code, Severity.INFO, message, {"value": value})
            return True

    def log(self, code, severity, message, attrs=None):
        """Something noteworthy happened (LOG). Never debounced: the event hub rate-limits floods."""
        self._publish(Event.KIND_LOG, code, severity, message, attrs)

    def wait_for_hub(self, timeout=5.0):
        """Waits until the event hub (any reader of the ingest topic) is matched.

        DDS drops what is published before discovery completes, so short-lived tools call this
        before their first report.
        """
        deadline = time.monotonic() + timeout
        while self._publisher.get_subscription_count() == 0:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
        return True

    def flush(self, timeout=5.0):
        """Waits until every report was acknowledged by its readers. Call it before a process exits."""
        return self._publisher.wait_for_all_acked(Duration(nanoseconds=int(timeout * 1e9)))

    def is_raised(self, code):
        with self._lock:
            return code in self._raised

    def _publish(self, kind, code, severity, message, attrs, now=None):
        if attrs is not None and not isinstance(attrs, Mapping):
            raise TypeError("attrs must be a mapping of str to str, int, float or bool")
        msg = Event(kind=kind, code=code, severity=int(severity), message=message)
        msg.stamp = (now or self._node.get_clock().now()).to_msg()
        msg.source.node = self._node.get_fully_qualified_name()
        msg.source.component = self._component
        msg.attrs = [_attribute(k, v) for k, v in (attrs or {}).items()]
        self._publisher.publish(msg)
