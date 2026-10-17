# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Live terminal view of Jarvis: open faults, recoveries and the latest events, refreshed twice a second.

ros2 run jarvis_demo monitor          # full-screen view (Ctrl-C to quit)
ros2 run jarvis_demo monitor --once   # print one snapshot and exit, for scripts and CI
"""

import argparse
import bisect
import collections
import shutil
import sys
import time

import rclpy
from diagnostic_msgs.msg import DiagnosticArray
from jarvis_interfaces.msg import Event, Execution, ExecutionArray, Fault, FaultArray
from jarvis_interfaces.srv import QueryEvents
from rclpy.executors import ExternalShutdownException
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

KINDS = {
    Event.KIND_LOG: "LOG",
    Event.KIND_STATE: "STATE",
    Event.KIND_FAULT_RAISE: "RAISE",
    Event.KIND_FAULT_CLEAR: "CLEAR",
    Event.KIND_FAULT_STATE: "FAULT",
    Event.KIND_RULE_MATCH: "RULE",
    Event.KIND_RECOVERY: "RECOVERY",
    Event.KIND_INSIGHT: "INSIGHT",
    Event.KIND_OPERATOR: "OPERATOR",
    Event.KIND_SYSTEM: "SYSTEM",
}
SEVERITIES = {10: "DEBUG", 20: "INFO", 30: "WARN", 40: "ERROR", 50: "CRIT"}
STATES = {
    Fault.STATE_PENDING: "PENDING",
    Fault.STATE_ACTIVE: "ACTIVE",
    Fault.STATE_LATCHED: "LATCHED",
    Fault.STATE_CLEARED: "CLEARED",
}
EXEC_STATES = {
    Execution.STATE_PENDING_APPROVAL: "APPROVAL",
    Execution.STATE_QUEUED: "QUEUED",
    Execution.STATE_RUNNING: "RUNNING",
    Execution.STATE_SUCCEEDED: "SUCCEEDED",
    Execution.STATE_FAILED: "FAILED",
    Execution.STATE_ABORTED: "ABORTED",
    Execution.STATE_REJECTED: "REJECTED",
    Execution.STATE_EXPIRED: "EXPIRED",
}
REQUESTERS = {
    Execution.REQUESTER_OPERATOR: "operator",
    Execution.REQUESTER_RULE: "rule",
    Execution.REQUESTER_AGENT: "agent",
    Execution.REQUESTER_PLAN: "plan",
}
COLORS = {
    "DEBUG": "2",
    "INFO": "32",
    "WARN": "33",
    "ERROR": "31",
    "CRIT": "1;31",
    "PENDING": "33",
    "ACTIVE": "1;31",
    "LATCHED": "1;35",
    "CLEARED": "32",
    "MUTED": "2",
    "APPROVAL": "1;33",
    "QUEUED": "33",
    "RUNNING": "1;36",
    "SUCCEEDED": "32",
    "FAILED": "1;31",
    "ABORTED": "33",
    "REJECTED": "2",
    "EXPIRED": "2",
}
TOUR_CODE = "demo.tour.step"
HINT = (
    "inject: scan-wedge · scan-stall · scan-slow · estop · rosout-error · diag-error · tour   "
    "then: inject clear"
)
RECOVERY_ROWS = 4
HUB_SILENCE_S = 3.0  # the event hub reports on /diagnostics every second


def paint(text, key, color):
    code = COLORS.get(key)
    return f"\033[{code}m{text}\033[0m" if color and code else text


def attr(event, key):
    return next((a.string_value for a in event.attrs if a.key == key), "")


def clock(stamp):
    return time.strftime("%H:%M:%S", time.localtime(stamp.sec)) if stamp.sec else "--:--:--"


def fit(text, width):
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


class Snapshot:
    """What the monitor has seen so far. Filled by subscriptions, read by render()."""

    def __init__(self, history=12):
        self.faults = None  # FaultArray, None until the latched snapshot arrives
        self.executions = None  # ExecutionArray, None until the recovery node publishes one
        self.events = collections.deque(maxlen=history)  # the latest ones, in seq order
        self.total_events = 0
        self.robot = ""
        self.tour = ""
        self._tour_seq = 0
        self.hub_seen = None  # when the event hub last reported on /diagnostics (monotonic)

    def on_diagnostics(self, msg, now=None):
        if any(s.hardware_id == "jarvis" and s.name.endswith("event hub") for s in msg.status):
            self.hub_seen = time.monotonic() if now is None else now

    def hub_silent_for(self, now=None):
        """Seconds since the event hub went quiet (crashed, killed), or 0 while it reports."""
        if self.hub_seen is None:
            return 0.0
        silent = (time.monotonic() if now is None else now) - self.hub_seen
        return silent if silent > HUB_SILENCE_S else 0.0

    def on_faults(self, msg):
        self.faults = msg

    def on_executions(self, msg):
        self.executions = msg

    def on_event(self, msg):
        # Live events start flowing while the hub's history is still being read: the two overlap
        # and interleave. seq orders them and tells copies apart.
        if msg.seq and any(e.seq == msg.seq for e in self.events):
            return
        self.total_events += 1
        self.robot = msg.source.robot or self.robot
        if msg.code == TOUR_CODE and msg.seq >= self._tour_seq:
            self.tour, self._tour_seq = msg.message, msg.seq
        if not msg.seq or not self.events or msg.seq > self.events[-1].seq:
            self.events.append(msg)
            return
        keep = self.events.maxlen
        if len(self.events) == keep and msg.seq < self.events[0].seq:
            return  # older than everything on screen
        ordered = list(self.events)
        bisect.insort(ordered, msg, key=lambda e: e.seq)
        self.events = collections.deque(ordered[-keep:], maxlen=keep)


def execution_rows(snap):
    """Running and waiting executions first, then the latest settled ones, newest first."""
    if snap.executions is None:
        return [("  (no snapshot from /jarvis/executions: is recovery running?)", None)]
    rows = list(snap.executions.executions) + list(reversed(snap.executions.recent))
    if not rows:
        return [("  none yet", None)]
    lines = [(f"  {'STATE':<10} {'PLAN':<26} {'BY':<28} STEP · REASON", None)]
    for x in rows[:RECOVERY_ROWS]:
        state = EXEC_STATES.get(x.state, "?")
        by = fit(f"{REQUESTERS.get(x.requester_type, '?')} {x.requester_id}".strip(), 28)
        step = x.steps[x.current_step].id if x.state == Execution.STATE_RUNNING and x.steps else ""
        detail = " · ".join(part for part in (step, x.reason) if part)
        dry = " (dry run)" if x.dry_run else ""
        lines.append((f"  {state:<10} {fit(x.plan_id + dry, 26):<26} {by:<28} {detail}", state))
    return lines


def render(snap, width=100, color=True, height=None, now=None):
    """The whole screen as one string: header, open faults, recoveries, recent events, hints.

    Every line is cut to `width` before it is colored, so escape codes never count as text. With a
    `height`, the oldest events are left out so the screen and the cursor fit (or it would scroll).
    """
    lines = []  # (text, color key)
    faults = list(snap.faults.faults) if snap.faults else []
    live = [f for f in faults if not f.muted_by]
    robot = snap.robot or next((f.source.robot for f in faults if f.source.robot), "")
    lines.append(
        (
            f"JARVIS · {robot or 'waiting for Jarvis'} · {time.strftime('%H:%M:%S')}   "
            f"{len(live)} open fault(s), {len(faults) - len(live)} muted · {snap.total_events} events",
            "INFO",
        )
    )
    silent = snap.hub_silent_for(now)
    if silent:
        down = f"DOWN  no word from the event hub for {silent:.0f} s: this is the last it said"
        lines.append((down, "CRIT"))
    if snap.tour:
        lines.append((f"TOUR  {snap.tour}", "WARN"))
    lines += [("", None), ("OPEN FAULTS", "INFO")]
    if snap.faults is None:
        lines.append(("  (no snapshot from /jarvis/faults yet: is Jarvis running?)", None))
    elif not faults:
        lines.append(("  none, all good", "CLEARED"))
    else:
        lines.append((f"  {'STATE':<8} {'SEV':<5} {'CODE':<34} {'COMPONENT':<18} {'N':>4}  MESSAGE", None))
        for f in sorted(faults, key=lambda f: (bool(f.muted_by), -f.severity, f.code)):
            state = "MUTED" if f.muted_by else STATES.get(f.state, "?")
            sev = SEVERITIES.get(f.severity, str(f.severity))
            code, component = fit(f.code, 34), fit(f.source.component, 18)
            row = f"  {state:<8} {sev:<5} {code:<34} {component:<18} {f.occurrences:>4}  {f.message}"
            lines.append((row, state))
    mode = snap.executions.mode if snap.executions is not None else ""
    lines += [("", None), ("RECOVERY" + (f"  mode {mode}" if mode else ""), "INFO"), *execution_rows(snap)]
    lines += [("", None), ("RECENT EVENTS", "INFO")]
    if not snap.events:
        lines.append(("  (none yet)", None))
    # The hints take 2 rows, and the cursor one more: a full screen would scroll the header away.
    room = len(snap.events) if height is None else max(1, height - len(lines) - 3)
    for e in list(reversed(snap.events))[:room]:
        sev = SEVERITIES.get(e.severity, str(e.severity))
        kind = KINDS.get(e.kind, str(e.kind))
        state = attr(e, "state") if e.kind == Event.KIND_FAULT_STATE else ""
        text = f"  {clock(e.stamp)}  {kind:<8} {sev:<5} {e.code}"
        text += f" → {state}" if state else ""
        text += f"  {e.message}" if e.message else ""
        lines.append((text, state or sev))
    lines += [("", None), (HINT, "DEBUG")]
    return "\n".join(paint(fit(text, width), key, color) for text, key in lines)


def width(tty):
    """The terminal's width; without a terminal (scripts, CI, logs) lines are never cut."""
    return shutil.get_terminal_size().columns if tty else 10_000


def height(tty):
    """The terminal's height; without a terminal every event is printed."""
    return shutil.get_terminal_size().lines if tty else None


def prefill(node, snap, timeout=3.0):
    """Recent history from the event hub's ring buffer, so the view isn't empty at start.

    Returns False when the hub did not answer within `timeout` seconds.
    """
    client = node.create_client(QueryEvents, "/jarvis/event_hub/query_events")
    deadline = time.monotonic() + timeout
    try:
        if not client.wait_for_service(timeout_sec=timeout):
            return False
        after = 0
        for _ in range(20):  # 20 pages of 500 is far more than the screen shows
            future = client.call_async(QueryEvents.Request(after_seq=after, limit=500))
            rclpy.spin_until_future_complete(node, future, timeout_sec=max(0.5, deadline - time.monotonic()))
            reply = future.result()
            if reply is None:
                return False
            for event in reply.events:
                snap.on_event(event)
            if reply.next_seq == 0:
                return True
            after = reply.next_seq
        return True
    finally:
        node.destroy_client(client)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="monitor", description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="print one snapshot and exit")
    parser.add_argument("--timeout", type=float, default=15.0, help="--once: how long to wait for Jarvis")
    parser.add_argument("--history", type=int, default=12, help="how many recent events to show")
    args = parser.parse_args(rclpy.utilities.remove_ros_args(argv if argv is not None else sys.argv)[1:])

    rclpy.init()
    node = rclpy.create_node("monitor", namespace="demo")
    snap = Snapshot(history=args.history)
    latched = QoSProfile(
        depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL
    )
    stream = QoSProfile(depth=1000, reliability=ReliabilityPolicy.RELIABLE)
    node.create_subscription(FaultArray, "/jarvis/faults", snap.on_faults, latched)
    node.create_subscription(ExecutionArray, "/jarvis/executions", snap.on_executions, latched)
    node.create_subscription(Event, "/jarvis/events", snap.on_event, stream)
    node.create_subscription(DiagnosticArray, "/diagnostics", snap.on_diagnostics, 10)
    tty = sys.stdout.isatty()
    try:
        # A snapshot for a script has time to wait for the hub; the live view starts at once.
        if not prefill(node, snap, timeout=args.timeout if args.once else 3.0):
            print("monitor: no history from the event hub, showing live events only", file=sys.stderr)
        if args.once:
            deadline = time.monotonic() + args.timeout
            while snap.faults is None and time.monotonic() < deadline:
                rclpy.spin_once(node, timeout_sec=0.1)
            settle = time.monotonic() + 1.0  # pick up the events in flight
            while time.monotonic() < settle:
                rclpy.spin_once(node, timeout_sec=0.05)
            print(render(snap, width(tty), color=tty))
            return 0 if snap.faults is not None else 1
        if tty:
            sys.stdout.write("\033[?25l")  # hide the cursor while redrawing
        period = 0.5 if tty else 5.0  # piped to a file or logs: a snapshot every few seconds
        while True:
            until = time.monotonic() + period
            while time.monotonic() < until:
                rclpy.spin_once(node, timeout_sec=0.05)
            screen = render(snap, width(tty), color=tty, height=height(tty))
            sys.stdout.write(("\033[H\033[2J" if tty else "\n") + screen + "\n")
            sys.stdout.flush()
    except (KeyboardInterrupt, ExternalShutdownException):
        return 0
    finally:
        if tty and not args.once:
            sys.stdout.write("\033[?25h\n")
        node.destroy_node()
        rclpy.try_shutdown()
