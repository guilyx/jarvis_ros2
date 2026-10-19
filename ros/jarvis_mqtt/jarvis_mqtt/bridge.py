# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""mqtt_bridge: devices that don't run ROS report to Jarvis over MQTT (roadmap 8.2).

A battery's BMS, a charger, a PLC gateway, a microcontroller: anything with an MQTT client reports
faults, states and logs as small JSON messages, and Jarvis's rules, recovery and agent work on them
like on any other. The device is the component: it reports under its own topic, so broker ACLs can
keep each device to its own name.

    <prefix>/devices/<device>/report   in   {"fault": "power.bms.cell_overtemp", "severity": "ERROR",
                                             "message": "cell 3 at 61 °C", "attrs": {"cell": 3}}
                                            {"clear": "power.bms.cell_overtemp", "message": "cooled"}
                                            {"state": "power.bms.soc", "value": 54}
                                            {"log": "power.bms.balancing", "severity": "INFO", ...}
                                            (or a list of them)
    <prefix>/devices/<device>/status   in   online | offline: set "offline" as the device's last
                                            will, and Jarvis raises mqtt.device.offline when it
                                            drops off the network
    <prefix>/robots/<robot>/faults     out  Jarvis's open faults, retained, for HMIs and PLCs

Reports that don't fit (an unknown key, a code that isn't dotted lowercase, a code outside `allow`, a
`jarvis.*` or `mqtt.*` code) are refused with a `mqtt.report.rejected` log that says why. Devices in
`devices` are expected: one that isn't online within `startup_grace_s` is offline. Losing the broker
raises `mqtt.broker.unreachable`.
"""

import fnmatch
import json
import os
import re
import sys
import threading

import paho.mqtt.client as mqtt
import rclpy
from jarvis_interfaces.msg import Event, Fault, FaultArray
from jarvis_reporter import Reporter, Severity
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

CODE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
DEVICE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
RESERVED = ("jarvis.*", "mqtt.*")
SEVERITIES = {s.name: s for s in Severity}
KINDS = ("fault", "clear", "state", "log")
OPEN = {Fault.STATE_PENDING: "PENDING", Fault.STATE_ACTIVE: "ACTIVE", Fault.STATE_LATCHED: "LATCHED"}
SEVERITY_NAMES = {
    Event.SEVERITY_DEBUG: "DEBUG",
    Event.SEVERITY_INFO: "INFO",
    Event.SEVERITY_WARN: "WARN",
    Event.SEVERITY_ERROR: "ERROR",
    Event.SEVERITY_CRITICAL: "CRITICAL",
}
MAX_PAYLOAD = 64 * 1024
MAX_MESSAGE = 500
LATCHED = QoSProfile(
    depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL
)
INGEST = QoSProfile(depth=1000, reliability=ReliabilityPolicy.RELIABLE)
HUB_WAIT_S = 10.0  # how long the bridge waits for the event hub before it listens to the broker


def _scalar(v):
    if isinstance(v, int) and not isinstance(v, bool):
        return -(2**63) <= v < 2**63
    return isinstance(v, (str, float, bool))


def parse(payload, allow=("*",)):
    """A device's report: a list of (kind, code, fields), or the reason it is refused."""
    if len(payload) > MAX_PAYLOAD:
        return None, f"larger than {MAX_PAYLOAD // 1024} KiB"
    try:
        doc = json.loads(payload)
    except (UnicodeDecodeError, ValueError):
        return None, "not JSON"
    items = doc if isinstance(doc, list) else [doc]
    out = []
    for item in items:
        if not isinstance(item, dict):
            return None, "a report is a JSON object (or a list of them)"
        kinds = [k for k in KINDS if k in item]
        if len(kinds) != 1:
            return None, "one of fault, clear, state or log, with the code"
        kind = kinds[0]
        unknown = set(item) - {kind, "severity", "message", "attrs", "value"}
        if unknown:
            return None, f"unknown key {sorted(unknown)[0]!r}"
        code = item[kind]
        if not isinstance(code, str) or not CODE.match(code):
            return None, f"code {code!r} is not dotted lowercase (power.bms.cell_overtemp)"
        if any(fnmatch.fnmatchcase(code, r) for r in RESERVED):
            return None, f"code {code!r} is reserved for Jarvis"
        if not any(fnmatch.fnmatchcase(code, a) for a in allow):
            return None, f"code {code!r} is not allowed (allow: {', '.join(allow)})"
        severity = item.get("severity", "ERROR" if kind == "fault" else "INFO")
        if severity not in SEVERITIES:
            return None, f"severity {severity!r} is not one of {', '.join(SEVERITIES)}"
        message = item.get("message", "")
        if not isinstance(message, str):
            return None, "message is text"
        attrs = item.get("attrs", {})
        if not isinstance(attrs, dict) or not all(
            isinstance(k, str) and _scalar(v) for k, v in attrs.items()
        ):
            return None, "attrs maps names to text, numbers or booleans"
        if kind == "state" and not _scalar(item.get("value")):
            return None, "a state has a value (text, number or boolean)"
        fields = {"severity": SEVERITIES[severity], "message": message[:MAX_MESSAGE], "attrs": attrs}
        if kind == "state":
            fields["value"] = item["value"]
        out.append((kind, code, fields))
    return out, None


def open_faults(msg, robot):
    """Jarvis's open faults as the JSON the bridge publishes."""
    faults = [
        {
            "code": f.code,
            "severity": SEVERITY_NAMES.get(f.severity, "ERROR"),
            "state": OPEN[f.state],
            "component": f.source.component,
            "message": f.message,
            "muted": bool(f.muted_by),
            "incident_id": f.incident_id,
        }
        for f in msg.faults
        if f.state in OPEN
    ]
    faults.sort(key=lambda f: (f["code"], f["component"]))
    return {"robot": robot, "faults": faults}


def make_client(client_id):
    try:  # paho-mqtt 2.x
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except AttributeError:  # 1.x
        return mqtt.Client(client_id=client_id)


class MqttBridge(Node):
    def __init__(self, **kwargs):
        super().__init__("mqtt", **kwargs)
        self.declare_parameter("robot_id", "robot")
        self.declare_parameter("host", "localhost")
        self.declare_parameter("port", 1883)
        self.declare_parameter("tls", False)
        self.declare_parameter("ca_file", "")
        self.declare_parameter("username", "")
        self.declare_parameter("password_env", "")  # the variable that holds the password
        self.declare_parameter("client_id", "")  # default: jarvis-<robot_id>
        self.declare_parameter("prefix", "jarvis")
        self.declare_parameter("allow", ["*"])
        self.declare_parameter("devices", [""])  # expected devices ("" = none)
        self.declare_parameter("startup_grace_s", 10.0)
        self.declare_parameter("publish_faults", True)
        p = lambda name: self.get_parameter(name).value  # noqa: E731
        self._robot = p("robot_id")
        self._prefix = p("prefix").rstrip("/")
        self._allow = tuple(a for a in p("allow") if a) or ("*",)
        self._expected = {d for d in p("devices") if d}
        self._lock = threading.Lock()
        self._reporters = {}
        self._online = {}  # device -> True/False, from its status
        self._rejected = {}  # device -> last refusal (ns), to keep a chatty device from flooding
        self._published = None
        # One publisher for every device: it is matched to the event hub once, so a device's first
        # report isn't lost to discovery.
        self._ingest = self.create_publisher(Event, "/jarvis/ingest", INGEST)
        self._jarvis = Reporter(self, "mqtt", publisher=self._ingest)

        self._client = make_client(p("client_id") or f"jarvis-{self._robot}")
        if p("username"):
            password = os.environ.get(p("password_env"), "") if p("password_env") else ""
            self._client.username_pw_set(p("username"), password or None)
        if p("tls"):
            self._client.tls_set(ca_certs=p("ca_file") or None)
        self._subscribed = threading.Event()  # connected and subscribed: devices are heard
        self._client.on_connect = self._on_connect
        self._client.on_subscribe = lambda *_args: self._subscribed.set()
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=10)
        self._listening = False
        self._waiting_since = self.get_clock().now()
        self._hub = self.create_timer(0.1, self._listen)

        if p("publish_faults"):
            self.create_subscription(FaultArray, "/jarvis/faults", self._on_faults, LATCHED)
        self._started = self.get_clock().now()
        self._grace_over = False
        self.create_timer(1.0, self._check_expected)

    def connected(self):
        """Connected to the broker and subscribed to the devices' topics."""
        return self._client.is_connected() and self._subscribed.is_set()

    def _listen(self):
        """Connects to the broker once the event hub hears the bridge: the retained statuses that
        come first would be lost otherwise."""
        waited = (self.get_clock().now() - self._waiting_since).nanoseconds / 1e9
        if self._ingest.get_subscription_count() == 0 and waited < HUB_WAIT_S:
            return
        self._hub.cancel()
        self._listening = True
        host, port = self.get_parameter("host").value, int(self.get_parameter("port").value)
        self._client.connect_async(host, port, keepalive=10)
        self._client.loop_start()
        self.get_logger().info(f"MQTT broker {host}:{port}, devices under {self._prefix}/devices/")

    def destroy_node(self):
        if self._listening:
            self._client.loop_stop()
            self._client.disconnect()
        super().destroy_node()

    # -- MQTT (paho's network thread) ---------------------------------------------------------------

    def _on_connect(self, client, _userdata, _flags, rc, *_props):
        if getattr(rc, "is_failure", rc != 0):
            self.get_logger().error(f"the MQTT broker refused the connection: {rc}")
            return
        client.subscribe([(f"{self._prefix}/devices/+/report", 1), (f"{self._prefix}/devices/+/status", 1)])
        self._jarvis.clear("mqtt.broker.unreachable", "connected to the MQTT broker", force=True)
        self.get_logger().info("connected to the MQTT broker")
        with self._lock:
            last = self._published
        if last is not None:
            self._publish(last)

    def _on_disconnect(self, _client, _userdata, *args):
        self._subscribed.clear()
        rc = args[-2] if len(args) >= 2 else (args[0] if args else 0)
        self._jarvis.fault(
            "mqtt.broker.unreachable",
            Severity.ERROR,
            f"lost the MQTT broker ({rc}): devices' reports and liveness are not seen",
            {"broker": f"{self.get_parameter('host').value}:{self.get_parameter('port').value}"},
        )

    def _on_message(self, _client, _userdata, msg):
        parts = msg.topic[len(self._prefix) + 1 :].split("/")
        if len(parts) != 3 or parts[0] != "devices":
            return
        device, kind = parts[1], parts[2]
        if not DEVICE.match(device):
            return self.get_logger().warning(f"ignored a device named {device!r}", throttle_duration_sec=10.0)
        if kind == "status":
            return self._status(device, msg.payload.decode(errors="replace").strip().lower())
        reports, why = parse(msg.payload, self._allow)
        if why:
            return self._reject(device, why)
        jarvis = self._reporter(device)
        for kind_, code, f in reports:
            attrs = {"device": device, **f["attrs"]}
            if kind_ == "fault":
                jarvis.fault(code, f["severity"], f["message"], attrs)
            elif kind_ == "clear":
                jarvis.clear(code, f["message"], attrs, force=True)  # raised before a restart, maybe
            elif kind_ == "state":
                jarvis.state(code, f["value"], f["message"])
            else:
                jarvis.log(code, f["severity"], f["message"], attrs)

    def _status(self, device, status):
        if status not in ("online", "offline"):
            return self._reject(device, f"status {status!r} is not online or offline")
        with self._lock:
            was = self._online.get(device)
            self._online[device] = status == "online"
        jarvis = self._reporter(device)
        if status == "offline":
            jarvis.fault("mqtt.device.offline", Severity.ERROR, f"{device} went offline", {"device": device})
        elif was is not True:
            jarvis.clear("mqtt.device.offline", f"{device} is online", {"device": device}, force=True)

    def _reject(self, device, why):
        now = self.get_clock().now().nanoseconds
        with self._lock:
            if now - self._rejected.get(device, -(10**12)) < 10 * 10**9:
                return
            self._rejected[device] = now
        self.get_logger().warning(f"refused a report from {device}: {why}")
        self._jarvis.log(
            "mqtt.report.rejected",
            Severity.WARN,
            f"refused a report from {device}: {why}",
            {"device": device},
        )

    def _reporter(self, device):
        with self._lock:
            if device not in self._reporters:
                self._reporters[device] = Reporter(self, device, publisher=self._ingest)
            return self._reporters[device]

    # -- ROS (the executor's thread) ----------------------------------------------------------------

    def _check_expected(self):
        if self._grace_over:
            return
        if (self.get_clock().now() - self._started).nanoseconds < self.get_parameter(
            "startup_grace_s"
        ).value * 1e9:
            return
        self._grace_over = True
        for device in sorted(self._expected):
            with self._lock:
                seen = device in self._online
            if not seen:
                self._reporter(device).fault(
                    "mqtt.device.offline",
                    Severity.ERROR,
                    f"{device} never came online",
                    {"device": device},
                )

    def _on_faults(self, msg):
        doc = open_faults(msg, self._robot)
        with self._lock:
            if doc == self._published:
                return
            self._published = doc
        self._publish(doc)

    def _publish(self, doc):
        topic = f"{self._prefix}/robots/{self._robot}/faults"
        self._client.publish(topic, json.dumps(doc, separators=(",", ":")), qos=1, retain=True)


def main(args=None):
    rclpy.init(args=args)
    node = MqttBridge()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except RuntimeError:
        # SIGINT can shut ROS down while a message is being taken (rclpy on Lyrical raises then).
        if rclpy.ok():
            raise
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
