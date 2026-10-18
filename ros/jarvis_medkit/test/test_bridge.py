# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The bridge against a stand-in for ros2_medkit's fault manager (its real messages and services):
what medkit confirms, updates and clears reaches /jarvis/ingest, a resync catches up on what the
bridge missed, and a restarted bridge clears what medkit cleared while it was down."""

import threading
import time

import pytest
import rclpy
from builtin_interfaces.msg import Time
from jarvis_interfaces.msg import Event
from jarvis_interfaces.msg import Fault as JarvisFault
from jarvis_interfaces.srv import GetFaults
from jarvis_medkit.bridge import EVENTS, INGEST, Bridge, component_of, jarvis_code
from rclpy.executors import MultiThreadedExecutor
from rclpy.parameter import Parameter
from ros2_medkit_msgs.msg import Fault, FaultEvent
from ros2_medkit_msgs.srv import ListFaults

FM = "/test_fm"
JARVIS = "/test_jarvis/fault_manager"


def fault(code, severity=2, sources=("/powertrain/motor_controller",), status="CONFIRMED", text=""):
    return Fault(
        fault_code=code,
        severity=severity,
        description=text or code.lower(),
        first_occurred=Time(sec=1),
        last_occurred=Time(sec=1),
        occurrence_count=1,
        status=status,
        reporting_sources=list(sources),
    )


class FakeFaultManager:
    """ros2_medkit's fault manager as the bridge sees it: ~/events and ~/list_faults."""

    def __init__(self, node):
        self.confirmed = []
        self.events = node.create_publisher(FaultEvent, f"{FM}/events", EVENTS)
        node.create_service(ListFaults, f"{FM}/list_faults", self._list)

    def _list(self, request, response):
        assert list(request.statuses) == ["CONFIRMED"]
        response.faults = list(self.confirmed)
        return response

    def emit(self, kind, f, auto_cleared=()):
        self.events.publish(FaultEvent(event_type=kind, fault=f, auto_cleared_codes=list(auto_cleared)))


class FakeJarvis:
    """Jarvis's fault manager as the bridge sees it at start: ~/get_faults, its open faults."""

    def __init__(self, node):
        self.open = []
        node.create_service(GetFaults, f"{JARVIS}/get_faults", self._get)

    def _get(self, request, response):
        assert list(request.codes) == ["medkit.*"] and request.include_muted
        response.faults = list(self.open)
        return response


def jarvis_fault(code, component, node):
    f = JarvisFault(code=code, state=JarvisFault.STATE_ACTIVE, severity=Event.SEVERITY_ERROR)
    f.source.node = node
    f.source.component = component
    return f


class Robot:
    """The stand-ins, what reached /jarvis/ingest, and the bridge once started."""

    def __init__(self):
        self.node = rclpy.create_node("medkit_test")
        self.fm = FakeFaultManager(self.node)
        self.fm.confirmed = [
            fault("MOTOR_OVERHEAT", 3, ["/powertrain/motor_left", "/powertrain/motor_right"])
        ]
        self.jarvis = FakeJarvis(self.node)
        self.seen = []
        self.node.create_subscription(Event, "/jarvis/ingest", self.seen.append, INGEST)
        self.executor = MultiThreadedExecutor()
        self.executor.add_node(self.node)
        self.bridge = None

    def start(self):
        self.bridge = Bridge(
            parameter_overrides=[
                Parameter("fault_manager", value=FM),
                Parameter("jarvis_fault_manager", value=JARVIS),
                Parameter("resync_period_s", value=0.5),
            ]
        )
        self.executor.add_node(self.bridge)
        threading.Thread(target=self.executor.spin, daemon=True).start()
        return self.fm, self.seen


@pytest.fixture
def robot():
    rclpy.init()
    r = Robot()
    yield r
    r.executor.shutdown()
    if r.bridge:
        r.bridge.destroy_node()
    r.node.destroy_node()
    rclpy.shutdown()


@pytest.fixture
def ros(robot):
    return robot.start()


def wait(predicate, what, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for {what}")


def faults_of(seen, kind):
    return sorted((e.code, e.source.component) for e in list(seen) if e.kind == kind)


def test_codes_and_components():
    assert jarvis_code("MOTOR_OVERHEAT") == "medkit.motor_overheat"
    assert jarvis_code("lidar-front.Stale", "medkit") == "medkit.lidar_front.stale"
    assert jarvis_code("..x..", "robot.medkit") == "robot.medkit.x"
    assert component_of("/powertrain/motor_controller") == "motor_controller"
    assert component_of("") == "medkit"


def test_what_medkit_confirms_and_clears_reaches_jarvis(ros):
    fm, seen = ros
    # At start the bridge catches up on what medkit already confirmed: one fault per source.
    wait(lambda: len(faults_of(seen, Event.KIND_FAULT_RAISE)) == 2, "the confirmed fault")
    assert faults_of(seen, Event.KIND_FAULT_RAISE) == [
        ("medkit.motor_overheat", "motor_left"),
        ("medkit.motor_overheat", "motor_right"),
    ]
    first = next(e for e in seen if e.kind == Event.KIND_FAULT_RAISE)
    assert first.severity == Event.SEVERITY_CRITICAL and "medkit" in first.tags
    assert first.source.node == "/powertrain/motor_left"
    assert {a.key: a.string_value or a.int_value for a in first.attrs}[
        "medkit.fault_code"
    ] == "MOTOR_OVERHEAT"

    # Then it follows medkit's events: a fault confirmed...
    lidar = fault("LIDAR_STALE", 2, ["/sensors/lidar"], text="no scan for 2 s")
    fm.confirmed.append(lidar)
    fm.emit("fault_confirmed", lidar)
    wait(
        lambda: ("medkit.lidar_stale", "lidar") in faults_of(seen, Event.KIND_FAULT_RAISE), "the lidar fault"
    )
    raised = next(e for e in seen if e.code == "medkit.lidar_stale")
    assert raised.message == "no scan for 2 s" and raised.severity == Event.SEVERITY_ERROR

    # ... and cleared, with the symptoms medkit cleared with it.
    fm.confirmed = []
    fm.emit(
        "fault_cleared", fault("LIDAR_STALE", 2, ["/sensors/lidar"], status="CLEARED"), ["MOTOR_OVERHEAT"]
    )
    wait(lambda: len(faults_of(seen, Event.KIND_FAULT_CLEAR)) == 3, "the clears")
    assert faults_of(seen, Event.KIND_FAULT_CLEAR) == [
        ("medkit.lidar_stale", "lidar"),
        ("medkit.motor_overheat", "motor_left"),
        ("medkit.motor_overheat", "motor_right"),
    ]


def test_a_resync_catches_up_on_missed_events(ros):
    fm, seen = ros
    wait(lambda: len(faults_of(seen, Event.KIND_FAULT_RAISE)) == 2, "the first sync")
    # medkit confirms one fault and heals another while the bridge was not listening (no event).
    fm.confirmed = [fault("BATTERY_LOW", 1, ["/power/bms"])]
    wait(lambda: ("medkit.battery_low", "bms") in faults_of(seen, Event.KIND_FAULT_RAISE), "the missed fault")
    wait(lambda: len(faults_of(seen, Event.KIND_FAULT_CLEAR)) == 2, "the healed fault cleared")
    clear = next(e for e in seen if e.kind == Event.KIND_FAULT_CLEAR)
    assert clear.message == "no longer confirmed in ros2_medkit"


def test_a_restarted_bridge_clears_what_medkit_cleared_meanwhile(robot):
    # Jarvis kept two medkit faults open across the bridge's restart; medkit still confirms one of
    # them (motor_left) and cleared the other (the lidar).
    robot.fm.confirmed = [fault("MOTOR_OVERHEAT", 3, ["/powertrain/motor_left"])]
    robot.jarvis.open = [
        jarvis_fault("medkit.motor_overheat", "motor_left", "/powertrain/motor_left"),
        jarvis_fault("medkit.lidar_stale", "lidar", "/sensors/lidar"),
    ]
    _, seen = robot.start()
    wait(
        lambda: faults_of(seen, Event.KIND_FAULT_CLEAR) == [("medkit.lidar_stale", "lidar")],
        "the stale clear",
    )
    clear = next(e for e in seen if e.kind == Event.KIND_FAULT_CLEAR)
    assert clear.source.node == "/sensors/lidar" and clear.message == "no longer confirmed in ros2_medkit"
    # The one still confirmed is not raised again, and later resyncs leave it be.
    time.sleep(1.5)
    assert faults_of(seen, Event.KIND_FAULT_RAISE) == []
    assert robot.bridge.raised.keys() == {("MOTOR_OVERHEAT", "/powertrain/motor_left")}
    # medkit clearing it still reaches Jarvis.
    robot.fm.confirmed = []
    robot.fm.emit("fault_cleared", fault("MOTOR_OVERHEAT", 3, ["/powertrain/motor_left"], status="CLEARED"))
    wait(
        lambda: ("medkit.motor_overheat", "motor_left") in faults_of(seen, Event.KIND_FAULT_CLEAR),
        "the clear",
    )
