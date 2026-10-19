# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The bridge against a real MQTT broker (mosquitto) and real MQTT clients: what devices report
reaches /jarvis/ingest, bad reports are refused with a reason, a device that drops off (its last
will) or never shows up is offline, Jarvis's open faults go out retained, and losing the broker is a
fault of its own."""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time

import paho.mqtt.client as mqtt
import pytest
import rclpy
from jarvis_interfaces.msg import Event, Fault, FaultArray
from jarvis_mqtt.bridge import LATCHED, MqttBridge, make_client, parse
from rclpy.executors import MultiThreadedExecutor
from rclpy.parameter import Parameter
from rclpy.qos import QoSProfile, ReliabilityPolicy

PREFIX = "jarvis"


def mosquitto():
    path = os.environ.get("MOSQUITTO") or shutil.which("mosquitto") or "/usr/sbin/mosquitto"
    if not os.path.exists(path):
        pytest.fail("mosquitto is needed (rosdep installs it: apt install mosquitto)")
    return path


class Broker:
    def __init__(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as conf:
            conf.write(f"listener {self.port} 127.0.0.1\nallow_anonymous true\n")
        self.conf = conf
        self.proc = None
        self.start()

    def start(self):
        self.proc = subprocess.Popen([mosquitto(), "-c", self.conf.name], stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("mosquitto did not start")

    def stop(self):
        self.proc.terminate()
        self.proc.wait(timeout=10)


def device(broker, name, keepalive=60):
    """A device's MQTT client: its last will says offline; it says online once connected."""
    c = make_client(f"test-{name}-{time.monotonic_ns()}")
    status = f"{PREFIX}/devices/{name}/status"
    c.will_set(status, "offline", qos=1, retain=True)
    c.connect("127.0.0.1", broker.port, keepalive=keepalive)
    c.loop_start()
    c.publish(status, "online", qos=1, retain=True).wait_for_publish(5)
    return c


def report(client, name, *items):
    body = items[0] if len(items) == 1 else list(items)
    client.publish(f"{PREFIX}/devices/{name}/report", json.dumps(body), qos=1).wait_for_publish(5)


class Robot:
    """The bridge, and what it put on /jarvis/ingest."""

    def __init__(self, broker, **params):
        self.node = rclpy.create_node("mqtt_test")
        self.seen = []
        stream = QoSProfile(depth=1000, reliability=ReliabilityPolicy.RELIABLE)
        self.node.create_subscription(Event, "/jarvis/ingest", self.seen.append, stream)
        self.faults = self.node.create_publisher(FaultArray, "/jarvis/faults", LATCHED)
        overrides = {
            "host": "127.0.0.1",
            "port": broker.port,
            "robot_id": "amr-7",
            "devices": ["bms", "charger"],
            "startup_grace_s": 1.5,
            **params,
        }
        self.bridge = MqttBridge(parameter_overrides=[Parameter(k, value=v) for k, v in overrides.items()])
        self.executor = MultiThreadedExecutor()
        for n in (self.node, self.bridge):
            self.executor.add_node(n)
        threading.Thread(target=self.executor.spin, daemon=True).start()

    def events(self, kind=None, code=None, component=None):
        return [
            e
            for e in list(self.seen)
            if (kind is None or e.kind == kind)
            and (code is None or e.code == code)
            and (component is None or e.source.component == component)
        ]

    def close(self):
        self.executor.shutdown()
        self.bridge.destroy_node()
        self.node.destroy_node()


def wait(predicate, what, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = predicate()
        if found:
            return found
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for {what}")


def attrs(event):
    return {a.key: a.string_value or a.int_value or a.double_value or a.bool_value for a in event.attrs}


@pytest.fixture(scope="module")
def broker():
    b = Broker()
    yield b
    b.stop()


@pytest.fixture
def robot(broker):
    rclpy.init()
    r = Robot(broker)
    wait(r.bridge.connected, "the bridge connected")
    yield r
    r.close()
    rclpy.shutdown()


def test_what_a_report_may_say():
    ok, why = parse(
        json.dumps(
            [
                {"fault": "power.bms.cell_overtemp", "message": "cell 3 at 61 °C", "attrs": {"cell": 3}},
                {"clear": "power.bms.cell_overtemp"},
                {"state": "power.bms.soc", "value": 54.5},
                {"log": "power.bms.balancing", "severity": "INFO", "message": "balancing"},
            ]
        ).encode()
    )
    assert why is None and [(k, c) for k, c, _ in ok] == [
        ("fault", "power.bms.cell_overtemp"),
        ("clear", "power.bms.cell_overtemp"),
        ("state", "power.bms.soc"),
        ("log", "power.bms.balancing"),
    ]
    assert ok[0][2]["severity"].name == "ERROR" and ok[2][2]["value"] == 54.5

    def refused(doc, allow=("*",)):
        return parse(doc if isinstance(doc, bytes) else json.dumps(doc).encode(), allow)[1]

    assert refused(b"{not json") == "not JSON"
    assert refused({"fault": "a.b", "clear": "a.b"}) == "one of fault, clear, state or log, with the code"
    assert refused({"fault": "a.b", "colour": "red"}) == "unknown key 'colour'"
    assert "not dotted lowercase" in refused({"fault": "Power.BMS"})
    assert (
        refused({"fault": "jarvis.recovery.disabled"})
        == "code 'jarvis.recovery.disabled' is reserved for Jarvis"
    )
    assert "reserved" in refused({"fault": "mqtt.device.offline"})
    assert refused({"fault": "safety.estop.engaged"}, ("power.*",)) == (
        "code 'safety.estop.engaged' is not allowed (allow: power.*)"
    )
    assert "severity 'LOUD'" in refused({"fault": "a.b", "severity": "LOUD"})
    assert "attrs" in refused({"fault": "a.b", "attrs": {"nested": {"x": 1}}})
    assert "attrs" in refused({"fault": "a.b", "attrs": {"big": 2**70}})
    assert "value" in refused({"state": "a.b"})
    assert "KiB" in refused(b" " * (65 * 1024))


def test_what_devices_report_reaches_jarvis(robot, broker):
    bms = device(broker, "bms")
    report(
        bms, "bms", {"fault": "power.bms.cell_overtemp", "message": "cell 3 at 61 °C", "attrs": {"cell": 3}}
    )
    raised = wait(lambda: robot.events(Event.KIND_FAULT_RAISE, "power.bms.cell_overtemp"), "the fault")[0]
    assert raised.source.component == "bms" and raised.severity == Event.SEVERITY_ERROR
    assert raised.message == "cell 3 at 61 °C" and attrs(raised) == {"device": "bms", "cell": 3}

    report(
        bms,
        "bms",
        {"clear": "power.bms.cell_overtemp", "message": "cooled down"},
        {"state": "power.bms.soc", "value": 54},
    )
    wait(lambda: robot.events(Event.KIND_FAULT_CLEAR, "power.bms.cell_overtemp"), "the clear")
    state = wait(lambda: robot.events(Event.KIND_STATE, "power.bms.soc"), "the state")[0]
    assert attrs(state)["value"] == 54 and state.source.component == "bms"

    # A report that doesn't fit is refused, and Jarvis says why.
    report(bms, "bms", {"fault": "Power.BMS"})
    refused = wait(lambda: robot.events(Event.KIND_LOG, "mqtt.report.rejected"), "the refusal")[0]
    assert refused.source.component == "mqtt" and "refused a report from bms" in refused.message
    assert not robot.events(code="Power.BMS")
    bms.disconnect()


def test_a_device_that_drops_off_or_never_shows_up_is_offline(robot, broker):
    # charger is expected and never comes: offline once the grace is over.
    gone = wait(lambda: robot.events(Event.KIND_FAULT_RAISE, "mqtt.device.offline", "charger"), "charger")[0]
    assert gone.message == "charger never came online"

    # bms loses its network: the broker sends its last will.
    bms = device(broker, "bms", keepalive=1)
    time.sleep(0.5)
    bms.loop_stop()
    bms.socket().close()  # no DISCONNECT: the broker publishes the will after 1.5 keepalives
    dropped = wait(lambda: robot.events(Event.KIND_FAULT_RAISE, "mqtt.device.offline", "bms"), "bms offline")[
        0
    ]
    assert dropped.message == "bms went offline" and attrs(dropped) == {"device": "bms"}

    # It comes back.
    bms = device(broker, "bms")
    wait(lambda: robot.events(Event.KIND_FAULT_CLEAR, "mqtt.device.offline", "bms"), "bms online again")
    bms.disconnect()


def test_jarvis_open_faults_go_out_retained(robot, broker):
    def fault(code, component, state, severity=Event.SEVERITY_ERROR):
        f = Fault(code=code, state=state, severity=severity, message=f"{code} on {component}")
        f.source.component = component
        return f

    robot.faults.publish(
        FaultArray(
            faults=[
                fault("sensor.scan.stale", "lidar", Fault.STATE_ACTIVE),
                fault("safety.estop.engaged", "base", Fault.STATE_LATCHED, Event.SEVERITY_CRITICAL),
                fault("ros.log.error", "planner", Fault.STATE_CLEARED),
            ]
        )
    )
    # A subscriber that comes later still gets them: retained.
    time.sleep(1.0)
    got = []
    hmi = make_client(f"test-hmi-{time.monotonic_ns()}")
    hmi.on_message = lambda _c, _u, msg: got.append(json.loads(msg.payload))
    hmi.connect("127.0.0.1", broker.port)
    hmi.subscribe(f"{PREFIX}/robots/amr-7/faults", qos=1)
    hmi.loop_start()
    doc = wait(lambda: got and got[-1], "the retained faults")
    assert doc["robot"] == "amr-7"
    assert [(f["code"], f["state"], f["severity"]) for f in doc["faults"]] == [
        ("safety.estop.engaged", "LATCHED", "CRITICAL"),
        ("sensor.scan.stale", "ACTIVE", "ERROR"),
    ]
    hmi.loop_stop()
    hmi.disconnect()


def test_allow_keeps_devices_to_their_codes(broker):
    rclpy.init()
    r = Robot(broker, allow=["power.*"], devices=[""])
    try:
        wait(r.bridge.connected, "connected")
        bms = device(broker, "bms")
        report(bms, "bms", {"fault": "safety.estop.engaged"})
        refused = wait(lambda: r.events(Event.KIND_LOG, "mqtt.report.rejected"), "the refusal")[0]
        assert "not allowed (allow: power.*)" in refused.message
        assert not r.events(code="safety.estop.engaged")
        bms.disconnect()
    finally:
        r.close()
        rclpy.shutdown()


def test_losing_the_broker_is_a_fault():
    b = Broker()
    rclpy.init()
    r = Robot(b, devices=[""])
    try:
        wait(r.bridge.connected, "connected")
        time.sleep(0.5)
        b.stop()
        lost = wait(lambda: r.events(Event.KIND_FAULT_RAISE, "mqtt.broker.unreachable"), "broker lost")[0]
        assert lost.source.component == "mqtt"
        before = len(r.events(Event.KIND_FAULT_CLEAR, "mqtt.broker.unreachable"))
        b.start()
        wait(
            lambda: len(r.events(Event.KIND_FAULT_CLEAR, "mqtt.broker.unreachable")) > before,
            "reconnected",
            30,
        )
    finally:
        r.close()
        rclpy.shutdown()
        b.stop()


def test_paho_both_majors():
    # paho-mqtt 1.x on Ubuntu 24.04 (Jazzy), 2.x on 26.04 (Lyrical): either works.
    assert isinstance(make_client("x"), mqtt.Client)
