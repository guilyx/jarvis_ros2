# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0

import itertools
import time

import pytest
import rclpy
from jarvis_interfaces.msg import Attribute, Event
from jarvis_reporter import Reporter, Severity
from rclpy.qos import QoSProfile, ReliabilityPolicy

_topics = itertools.count()


@pytest.fixture(scope="module")
def node():
    rclpy.init()
    n = rclpy.create_node("app", namespace="test")
    yield n
    n.destroy_node()
    rclpy.shutdown()


class Harness:
    def __init__(self, node):
        self.node = node
        self.topic = f"/test_reporter_py_{next(_topics)}"
        self.received = []
        qos = QoSProfile(depth=1000, reliability=ReliabilityPolicy.RELIABLE)
        self.sub = node.create_subscription(Event, self.topic, self.received.append, qos)
        self.reporter = Reporter(node, "lidar", topic=self.topic, repeat_period=0.2)
        deadline = time.monotonic() + 5.0
        while self.sub.get_publisher_count() == 0 and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.01)
        assert self.sub.get_publisher_count() > 0

    def receive(self, n):
        deadline = time.monotonic() + 5.0
        while len(self.received) < n and time.monotonic() < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.01)
        settle = time.monotonic() + 0.1
        while time.monotonic() < settle:
            rclpy.spin_once(self.node, timeout_sec=0.01)
        return self.received

    def close(self):
        self.node.destroy_subscription(self.sub)


@pytest.fixture
def h(node):
    harness = Harness(node)
    yield harness
    harness.close()


def test_fault_is_sent_on_edge_then_repeated_at_most_once_per_period(h):
    assert h.reporter.fault("sensor.lidar.stale", Severity.ERROR, "no scan")
    assert not h.reporter.fault("sensor.lidar.stale", Severity.ERROR, "no scan")
    assert h.reporter.is_raised("sensor.lidar.stale")
    time.sleep(0.25)
    assert h.reporter.fault("sensor.lidar.stale", Severity.ERROR, "still no scan")

    msgs = h.receive(2)
    assert [m.kind for m in msgs] == [Event.KIND_FAULT_RAISE] * 2
    first = msgs[0]
    assert (first.code, first.severity) == ("sensor.lidar.stale", Event.SEVERITY_ERROR)
    assert first.message == "no scan"
    assert (first.source.node, first.source.component) == ("/test/app", "lidar")
    assert first.id == "", "the event hub assigns ids"
    assert first.stamp.sec > 0
    assert msgs[1].message == "still no scan"


def test_clear_is_sent_only_for_a_raised_fault(h):
    assert not h.reporter.clear("sensor.lidar.stale")
    assert h.reporter.fault("sensor.lidar.stale")
    assert h.reporter.clear("sensor.lidar.stale", "scans are back")
    assert not h.reporter.clear("sensor.lidar.stale")
    assert not h.reporter.is_raised("sensor.lidar.stale")
    assert h.reporter.fault("sensor.lidar.stale"), "a new episode is reported at once"

    msgs = h.receive(3)
    assert [m.kind for m in msgs] == [Event.KIND_FAULT_RAISE, Event.KIND_FAULT_CLEAR, Event.KIND_FAULT_RAISE]
    assert msgs[1].severity == Event.SEVERITY_INFO
    assert msgs[1].message == "scans are back"


def test_a_forced_clear_heals_what_a_previous_life_raised(h):
    # After a restart the reporter has raised nothing, but the fault manager restored the fault.
    assert h.reporter.clear("sensor.lidar.stale", "scans are back", force=True)
    assert not h.reporter.is_raised("sensor.lidar.stale")
    (msg,) = h.receive(1)
    assert msg.kind == Event.KIND_FAULT_CLEAR and msg.code == "sensor.lidar.stale"


def test_state_is_sent_when_the_value_or_its_type_changes(h):
    assert h.reporter.state("robot.mode", "IDLE")
    assert not h.reporter.state("robot.mode", "IDLE")
    assert h.reporter.state("robot.mode", "AUTONOMOUS")
    assert h.reporter.state("robot.docked", 1)
    assert h.reporter.state("robot.docked", True)

    msgs = h.receive(4)
    assert len(msgs) == 4
    assert msgs[0].kind == Event.KIND_STATE
    assert [(a.key, a.string_value) for a in msgs[1].attrs] == [("value", "AUTONOMOUS")]
    assert msgs[2].attrs[0].type == Attribute.TYPE_INT
    assert msgs[3].attrs[0].type == Attribute.TYPE_BOOL


def test_log_is_never_debounced_and_attributes_keep_their_type(h):
    attrs = {"topic": "/scan", "silence_ms": 1200, "rate_hz": 9.5, "degraded": True}
    h.reporter.log("sensor.lidar.slow", Severity.WARN, "scan rate low", attrs)
    h.reporter.log("sensor.lidar.slow", Severity.WARN, "scan rate low", attrs)

    msgs = h.receive(2)
    assert len(msgs) == 2
    assert (msgs[0].kind, msgs[0].severity) == (Event.KIND_LOG, Event.SEVERITY_WARN)
    by_key = {a.key: a for a in msgs[0].attrs}
    assert (by_key["topic"].type, by_key["topic"].string_value) == (Attribute.TYPE_STRING, "/scan")
    assert (by_key["silence_ms"].type, by_key["silence_ms"].int_value) == (Attribute.TYPE_INT, 1200)
    assert (by_key["rate_hz"].type, by_key["rate_hz"].double_value) == (Attribute.TYPE_DOUBLE, 9.5)
    assert (by_key["degraded"].type, by_key["degraded"].bool_value) == (Attribute.TYPE_BOOL, True)


def test_attrs_must_be_a_mapping(h):
    with pytest.raises(TypeError):
        h.reporter.log("app.bad.attrs", Severity.INFO, "oops", [("k", "v")])


def test_waits_for_the_hub_and_flushes(h, node):
    assert h.reporter.wait_for_hub(1.0)
    assert h.reporter.fault("app.cli.report")
    assert h.reporter.flush(2.0)
    assert len(h.receive(1)) == 1
    alone = Reporter(node, "cli", topic="/test_reporter_py_nobody")
    assert not alone.wait_for_hub(0.05), "no reader on that topic"


def test_reporters_can_share_a_publisher(h, node):
    bms = Reporter(node, "bms", publisher=h.reporter._publisher)
    charger = Reporter(node, "charger", publisher=h.reporter._publisher)
    assert bms._publisher is charger._publisher is h.reporter._publisher
    bms.fault("power.bms.overtemp")
    charger.state("charger.state", "idle")
    got = h.receive(2)
    assert [(e.code, e.source.component) for e in got] == [
        ("power.bms.overtemp", "bms"),
        ("charger.state", "charger"),
    ]
