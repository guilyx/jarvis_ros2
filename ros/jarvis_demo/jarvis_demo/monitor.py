# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Live terminal view of Jarvis: open faults and the latest events, refreshed twice a second.

ros2 run jarvis_demo monitor          # full-screen view (Ctrl-C to quit)
ros2 run jarvis_demo monitor --once   # print one snapshot and exit, for scripts and CI
"""

import argparse
import collections
import shutil
import sys
import time

import rclpy
from jarvis_interfaces.msg import Event, Fault, FaultArray
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
}
TOUR_CODE = "demo.tour.step"
HINT = "inject: scan-stall · scan-slow · estop · rosout-error · diag-error · tour   then: inject clear"


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
        self.events = collections.deque(maxlen=history)
        self.total_events = 0
        self.last_seq = 0
        self.robot = ""
        self.tour = ""

    def on_faults(self, msg):
        self.faults = msg

    def on_event(self, msg):
        # History from the hub and live events overlap while the monitor starts: seq tells them apart.
        if msg.seq and msg.seq <= self.last_seq:
            return
        self.last_seq = max(self.last_seq, msg.seq)
        self.total_events += 1
        self.robot = msg.source.robot or self.robot
        if msg.code == TOUR_CODE:
            self.tour = msg.message
        self.events.append(msg)


def render(snap, width=100, color=True):
    """The whole screen as one string: header, open faults, recent events, hints.

    Every line is cut to `width` before it is colored, so escape codes never count as text.
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
    lines += [("", None), ("RECENT EVENTS", "INFO")]
    if not snap.events:
        lines.append(("  (none yet)", None))
    for e in reversed(snap.events):
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


def prefill(node, snap, timeout=3.0):
    """Recent history from the event hub's ring buffer, so the view isn't empty at start."""
    client = node.create_client(QueryEvents, "/jarvis/event_hub/query_events")
    try:
        if not client.wait_for_service(timeout_sec=timeout):
            return
        after = 0
        for _ in range(20):  # 20 pages of 500 is far more than the screen shows
            future = client.call_async(QueryEvents.Request(after_seq=after, limit=500))
            rclpy.spin_until_future_complete(node, future, timeout_sec=timeout)
            reply = future.result()
            if reply is None:
                return
            for event in reply.events:
                snap.on_event(event)
            if reply.next_seq == 0:
                return
            after = reply.next_seq
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
    node.create_subscription(Event, "/jarvis/events", snap.on_event, stream)
    tty = sys.stdout.isatty()
    try:
        prefill(node, snap)
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
            screen = render(snap, width(tty), color=tty)
            sys.stdout.write(("\033[H\033[2J" if tty else "\n") + screen + "\n")
            sys.stdout.flush()
    except (KeyboardInterrupt, ExternalShutdownException):
        return 0
    finally:
        if tty and not args.once:
            sys.stdout.write("\033[?25h\n")
        node.destroy_node()
        rclpy.try_shutdown()
