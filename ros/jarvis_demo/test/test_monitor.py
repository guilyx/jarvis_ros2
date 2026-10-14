# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The monitor's screen is a pure function of what it saw: test it without any ROS traffic."""

from builtin_interfaces.msg import Time
from jarvis_demo.monitor import Snapshot, render
from jarvis_interfaces.msg import Attribute, Event, Execution, ExecutionArray, Fault, FaultArray, StepProgress


def fault(code, state=Fault.STATE_ACTIVE, severity=Event.SEVERITY_ERROR, component="lidar", muted_by=""):
    f = Fault(code=code, state=state, severity=severity, occurrences=3, muted_by=muted_by, message="no scan")
    f.source.component = component
    return f


def event(code, kind=Event.KIND_FAULT_STATE, state="", message="", seq=0):
    e = Event(code=code, kind=kind, severity=Event.SEVERITY_ERROR, message=message, seq=seq)
    e.stamp = Time(sec=1)
    e.source.robot = "demo-bot"
    if state:
        e.attrs = [Attribute(key="state", type=Attribute.TYPE_STRING, string_value=state)]
    return e


def test_before_any_message_it_says_what_it_waits_for():
    screen = render(Snapshot(), color=False)
    assert "is Jarvis running?" in screen
    assert "(none yet)" in screen


def test_open_faults_come_first_and_muted_ones_last():
    snap = Snapshot()
    snap.on_faults(
        FaultArray(
            faults=[
                fault("diag.scan_relay.output_rate", severity=Event.SEVERITY_WARN, muted_by="root-id"),
                fault("safety.estop.engaged", Fault.STATE_LATCHED, Event.SEVERITY_CRITICAL, "base"),
                fault("sensor.scan.stale"),
            ]
        )
    )
    screen = render(snap, width=140, color=False)
    rows = [line for line in screen.splitlines() if line.startswith("  ") and "." in line]
    assert [r.split()[2] for r in rows] == [
        "safety.estop.engaged",
        "sensor.scan.stale",
        "diag.scan_relay.output_rate",
    ]
    assert rows[0].split()[0] == "LATCHED"
    assert rows[2].split()[0] == "MUTED"
    assert "2 open fault(s), 1 muted" in screen


def test_events_show_newest_first_with_fault_state_and_tour_narration():
    snap = Snapshot()
    snap.on_event(event("sensor.scan.stale", state="PENDING"))
    snap.on_event(event("sensor.scan.stale", state="ACTIVE"))
    snap.on_event(event("demo.tour.step", kind=Event.KIND_LOG, message="1/4 Lidar stall"))
    screen = render(snap, color=False)
    assert "demo-bot" in screen
    assert "TOUR  1/4 Lidar stall" in screen
    lines = screen.splitlines()
    active = next(i for i, line in enumerate(lines) if "→ ACTIVE" in line)
    pending = next(i for i, line in enumerate(lines) if "→ PENDING" in line)
    assert active < pending


def test_lines_fit_the_terminal_and_color_is_optional():
    snap = Snapshot()
    snap.on_faults(FaultArray(faults=[fault("sensor.scan.stale" + ".x" * 40)]))
    assert all(len(line) <= 60 for line in render(snap, width=60, color=False).splitlines())
    assert "\033[" in render(snap, color=True)
    assert "\033[" not in render(snap, color=False)


def test_history_and_live_events_are_not_counted_twice():
    snap = Snapshot()
    for seq in (1, 2, 3):
        snap.on_event(event("sensor.scan.stale", seq=seq))
    snap.on_event(event("sensor.scan.stale", seq=3))  # live copy of the last history event
    snap.on_event(event("sensor.scan.stale", seq=4))
    assert snap.total_events == 4
    assert [e.seq for e in snap.events] == [1, 2, 3, 4]


def test_history_read_after_live_events_still_shows_in_order():
    # A wedged relay reports every second: live events arrive while the history is being read.
    snap = Snapshot(history=5)
    snap.on_event(event("sensor.scan.stale", seq=10))
    snap.on_event(event("demo.tour.step", kind=Event.KIND_LOG, message="2/5", seq=11))
    for seq in range(1, 12):
        code = "demo.tour.step" if seq == 3 else f"code.n{seq}"
        snap.on_event(event(code, kind=Event.KIND_LOG, message="1/5", seq=seq))
    assert [e.seq for e in snap.events] == [7, 8, 9, 10, 11]
    assert snap.total_events == 11
    assert snap.tour == "2/5", "older narration doesn't replace newer"


def test_the_robot_id_comes_from_faults_when_no_event_was_seen():
    snap = Snapshot()
    f = fault("sensor.scan.stale")
    f.source.robot = "demo-bot"
    snap.on_faults(FaultArray(faults=[f]))
    assert "JARVIS · demo-bot" in render(snap, color=False)


def execution(plan, state, reason="", requester_id="lidar-stale-restart", step=""):
    x = Execution(plan_id=plan, state=state, reason=reason, requester_type=Execution.REQUESTER_RULE)
    x.requester_id = requester_id
    if step:
        x.steps = [StepProgress(id=step, action="lifecycle.restart")]
    return x


def test_recoveries_show_what_runs_first_then_the_latest_outcomes():
    snap = Snapshot()
    assert "is recovery running?" in render(snap, color=False)
    snap.on_executions(ExecutionArray(mode="ENABLED"))
    assert "RECOVERY  mode ENABLED" in render(snap, color=False)
    assert "none yet" in render(snap, color=False)

    snap.on_executions(
        ExecutionArray(
            mode="ENABLED",
            executions=[execution("restart-lifecycle-node", Execution.STATE_RUNNING, step="restart")],
            recent=[
                execution("restart-lifecycle-node", Execution.STATE_FAILED, "step verify failed: timeout"),
                execution("restart-lifecycle-node", Execution.STATE_SUCCEEDED, "all steps succeeded"),
            ],
        )
    )
    screen = render(snap, width=200, color=False)
    rows = [line.split()[0] for line in screen.splitlines() if "restart-lifecycle-node" in line]
    assert rows == ["RUNNING", "SUCCEEDED", "FAILED"]
    assert "rule lidar-stale-restart" in screen
    assert "restart" in next(line for line in screen.splitlines() if line.lstrip().startswith("RUNNING"))


def test_the_screen_fits_the_terminal_by_leaving_out_the_oldest_events():
    snap = Snapshot(history=50)
    for seq in range(1, 31):
        snap.on_event(event(f"code.n{seq}", seq=seq))
    screen = render(snap, color=False, height=20).splitlines()
    assert len(screen) <= 20
    assert any("code.n30" in line for line in screen), "the newest event stays"
    assert not any("code.n1 " in line or line.endswith("code.n1") for line in screen)
    assert screen[-1].startswith("inject:"), "the hints stay"
    assert len(render(snap, color=False).splitlines()) > 30, "without a height, everything"
