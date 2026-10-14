# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Lidar relay between the simulator and Nav2, with fault injection and self-monitoring.

    /scan_raw -> scan_relay -> /scan

It reports `sensor.scan.stale` through jarvis_reporter when its output goes quiet, and its output rate
on /diagnostics (bridged to `diag.scan_relay.output_rate`). Faults are injected at runtime:

    ros2 param set /demo/scan_relay inject stall   # stop relaying  -> sensor.scan.stale
    ros2 param set /demo/scan_relay inject slow    # half the rate  -> diag.scan_relay.output_rate
    ros2 param set /demo/scan_relay inject none
"""

import collections

import rclpy
from diagnostic_msgs.msg import DiagnosticStatus
from diagnostic_updater import Updater
from jarvis_reporter import Reporter, Severity
from rcl_interfaces.msg import ParameterDescriptor, SetParametersResult
from rclpy.executors import ExternalShutdownException
from rclpy.lifecycle import LifecycleNode, TransitionCallbackReturn
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan

INJECTIONS = ("none", "stall", "slow")
RATE_WINDOW_S = 2.0
RATE_WINDOW_NS = int(RATE_WINDOW_S * 1e9)
STALE_CODE = "sensor.scan.stale"


class ScanRelay(LifecycleNode):
    def __init__(self, **kwargs):
        super().__init__("scan_relay", **kwargs)
        self.declare_parameter("input_topic", "/scan_raw")
        self.declare_parameter("output_topic", "/scan")
        self.declare_parameter("expected_rate_hz", 10.0)
        self.declare_parameter("stale_after_s", 0.5)
        self.declare_parameter("startup_grace_s", 3.0)  # how long the first scan may take to arrive
        self.declare_parameter("component", "lidar")
        self.declare_parameter("autostart", False)
        self.declare_parameter("inject", "none", ParameterDescriptor(description=" | ".join(INJECTIONS)))
        self.add_on_set_parameters_callback(self._validate)

        self._active = False
        self._pub = self._sub = self._watchdog = None
        self._sent = collections.deque()  # node-clock stamps (ns) of relayed scans
        self._since = 0  # last relayed scan, or activation
        self._activated = 0
        self._first_sent = None  # first relayed scan since activation
        self._received = 0
        self._jarvis = Reporter(self, self.get_parameter("component").value)
        # The updater declares parameters, so it lives as long as the node, not one configuration.
        self._diagnostics = Updater(self)
        self._diagnostics.setHardwareID(self.get_parameter("component").value)
        self._diagnostics.add("output rate", self._rate_status)

    def _validate(self, params):
        for p in params:
            if p.name == "inject" and p.value not in INJECTIONS:
                return SetParametersResult(successful=False, reason=f"inject must be one of {INJECTIONS}")
        return SetParametersResult(successful=True)

    # -- lifecycle -------------------------------------------------------------------------------

    def on_configure(self, state):
        self._topic = self.get_parameter("output_topic").value
        self._expected = self.get_parameter("expected_rate_hz").value
        self._stale_after = int(self.get_parameter("stale_after_s").value * 1e9)
        self._grace = int(self.get_parameter("startup_grace_s").value * 1e9)
        self._pub = self.create_lifecycle_publisher(LaserScan, self._topic, qos_profile_sensor_data)
        return TransitionCallbackReturn.SUCCESS

    def on_activate(self, state):
        self._since = self._activated = self._now()
        self._first_sent = None
        self._sub = self.create_subscription(
            LaserScan, self.get_parameter("input_topic").value, self._on_scan, qos_profile_sensor_data
        )
        self._watchdog = self.create_timer(0.1, self._check_stale)
        self._active = True
        return super().on_activate(state)

    def on_deactivate(self, state):
        self._active = False
        self.destroy_timer(self._watchdog)
        self.destroy_subscription(self._sub)
        self._jarvis.clear(STALE_CODE, "relay deactivated")
        return super().on_deactivate(state)

    def on_cleanup(self, state):
        self.destroy_lifecycle_publisher(self._pub)
        self._sent.clear()
        return TransitionCallbackReturn.SUCCESS

    # -- relay -----------------------------------------------------------------------------------

    def _now(self):
        return self.get_clock().now().nanoseconds

    def _on_scan(self, msg):
        self._received += 1
        inject = self.get_parameter("inject").value
        if inject == "stall" or (inject == "slow" and self._received % 2):
            return
        self._pub.publish(msg)
        self._since = self._now()
        self._sent.append(self._since)
        if self._first_sent is None:
            self._first_sent = self._since

    def _starting(self, now):
        """True while the lidar may still be coming up: no stale fault is reported then."""
        return self._first_sent is None and now - self._activated < self._grace

    def _warming(self, now):
        """True until the rate window holds a full window of output: a partial one reads low."""
        if self._first_sent is None:
            return self._starting(now)
        return now - self._first_sent < RATE_WINDOW_NS

    def _check_stale(self):
        now = self._now()
        if self._starting(now):
            return
        silence = now - self._since
        if silence > self._stale_after:
            self._jarvis.fault(
                STALE_CODE,
                Severity.ERROR,
                f"no scan on {self._topic} for {silence / 1e9:.1f} s",
                {"topic": self._topic, "silence_ms": silence // 1_000_000},
            )
        else:
            self._jarvis.clear(STALE_CODE, "scans are flowing again")

    def _rate_status(self, stat):
        now = self._now()
        while self._sent and self._sent[0] < now - RATE_WINDOW_NS:
            self._sent.popleft()
        rate = len(self._sent) / RATE_WINDOW_S
        ratio = rate / self._expected if self._active and self._expected > 0 else 1.0
        if not self._active:
            stat.summary(DiagnosticStatus.OK, "inactive")
        elif self._warming(now):
            stat.summary(DiagnosticStatus.OK, "starting")
        elif ratio >= 0.8:
            stat.summary(DiagnosticStatus.OK, "nominal")
        elif ratio >= 0.3:
            stat.summary(DiagnosticStatus.WARN, f"output rate low: {rate:.1f} Hz")
        else:
            stat.summary(DiagnosticStatus.ERROR, f"output rate collapsed: {rate:.1f} Hz")
        stat.add("rate_hz", f"{rate:.1f}")
        stat.add("expected_hz", f"{self._expected:.1f}")
        stat.add("inject", self.get_parameter("inject").value)
        return stat


def main(args=None):
    rclpy.init(args=args)
    node = ScanRelay()
    if node.get_parameter("autostart").value:
        node.trigger_configure()
        node.trigger_activate()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
