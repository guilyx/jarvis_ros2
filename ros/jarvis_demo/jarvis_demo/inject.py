# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Fault injector for the Jarvis demo: each scenario produces one kind of fault the way it happens.

    inject scan-wedge               relay hangs       -> sensor.scan.stale, Jarvis restarts the relay
    inject scan-stall --seconds 5   lidar stops       -> sensor.scan.stale (a restart can't fix it)
    inject scan-slow --seconds 10   lidar at 50 %     -> diag.scan_relay.output_rate
    inject rosout-error --count 10  a node errors     -> ros.log.error
    inject diag-error --seconds 5   driver reports    -> diag.fault_injector.motor_driver
    inject estop --seconds 2        e-stop pressed    -> safety.estop.engaged (latched)
    inject nav-abort                unreachable goal  -> ros.action.navigate_to_pose.aborted
    inject tour                     wedge, stall, diag-error, rosout-error, estop, narrated in the monitor
    inject clear                    an operator clears the latched faults

Run as `ros2 run jarvis_demo inject <scenario>`.
"""

import argparse
import sys
import time
from types import SimpleNamespace

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from jarvis_interfaces.msg import Event, Fault
from jarvis_interfaces.srv import ClearFault, GetFaults
from jarvis_reporter import Reporter, Severity
from rclpy.action import ActionClient
from rclpy.parameter import Parameter
from rclpy.parameter_client import AsyncParameterClient
from rclpy.qos import QoSProfile, ReliabilityPolicy

TOUR_CODE = "demo.tour.step"
# What Jarvis decides and does, printed while a scenario runs.
REACTIONS = ("jarvis.rule.", "jarvis.recovery.", "jarvis.notify", "jarvis.incident.")
# Last phases of an execution (a failed one's ladder reports ESCALATED and LADDER_DONE after FAILED).
SETTLED = ("SUCCEEDED", "FAILED", "ABORTED", "REJECTED", "EXPIRED", "ESCALATED", "LADDER_DONE")
OPERATOR_RESTART = (
    "ros2 jarvis recover restart-lifecycle-node -a node={relay} -a component=lidar -a fault=sensor.scan.stale"
)


def spin_for(node, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.05)


def attr(event, key):
    return next((a.string_value for a in event.attrs if a.key == key), "")


class Reactions:
    """Prints what Jarvis decides and does (rules, recoveries, notifications) as it happens."""

    def __init__(self, node, quiet=False):
        self.events = []
        self.quiet = quiet
        stream = QoSProfile(depth=1000, reliability=ReliabilityPolicy.RELIABLE)
        node.create_subscription(Event, "/jarvis/events", self._on_event, stream)
        # Events published before the subscription matches are lost: wait for the hub's publisher.
        deadline = time.monotonic() + 5.0
        while node.count_publishers("/jarvis/events") == 0 and time.monotonic() < deadline:
            spin_for(node, 0.05)
        spin_for(node, 0.3)

    def _on_event(self, e):
        if not e.code.startswith(REACTIONS):
            return
        self.events.append(e)
        if not self.quiet:
            print(f"  jarvis: {e.code:<28} {e.message}")

    def executions(self):
        """Phase of every execution seen so far, by execution id."""
        phases = {}
        for e in self.events:
            if e.kind == Event.KIND_RECOVERY and attr(e, "execution_id"):
                phases[attr(e, "execution_id")] = attr(e, "phase")
        return phases

    def settled(self):
        phases = self.executions()
        return bool(phases) and all(p in SETTLED for p in phases.values())


_parameter_clients = {}


def parameter_client(node, relay):
    """One client per relay: scan-wedge polls the relay's parameters."""
    key = (node.get_name(), relay)
    if key not in _parameter_clients:
        client = AsyncParameterClient(node, relay)
        if not client.wait_for_services(timeout_sec=10.0):
            raise RuntimeError(f"{relay} is not running (ros2 launch jarvis_demo demo.launch.py)")
        _parameter_clients[key] = client
    return _parameter_clients[key]


def set_inject(node, relay, value):
    future = parameter_client(node, relay).set_parameters([Parameter("inject", value=value)])
    rclpy.spin_until_future_complete(node, future, timeout_sec=10.0)
    result = future.result()
    if result is None or not result.results[0].successful:
        raise RuntimeError(f"could not set inject={value} on {relay}")


def get_inject(node, relay):
    future = parameter_client(node, relay).get_parameters(["inject"])
    rclpy.spin_until_future_complete(node, future, timeout_sec=10.0)
    result = future.result()
    return result.values[0].string_value if result and result.values else ""


def scan(node, args, mode):
    reactions = Reactions(node, quiet=args.quiet)
    set_inject(node, args.relay, mode)
    print(f"{args.relay}: inject={mode} for {args.seconds:g} s")
    try:
        spin_for(node, args.seconds)
    finally:
        set_inject(node, args.relay, "none")
        print(f"{args.relay}: inject=none")
    # A recovery still running when the injection ends settles once scans flow again.
    deadline = time.monotonic() + 10.0
    while reactions.executions() and not reactions.settled() and time.monotonic() < deadline:
        spin_for(node, 0.1)
    spin_for(node, 0.5)  # what follows the last phase: notifications, the incident


def scan_wedge(node, args):
    """Wedges the relay, then waits for Jarvis to restart it and for the recovery to settle."""
    reactions = Reactions(node, quiet=args.quiet)
    set_inject(node, args.relay, "wedge")
    print(f"{args.relay}: inject=wedge, it relays nothing until it is restarted")
    deadline = time.monotonic() + args.timeout
    unwedged = False
    while time.monotonic() < deadline:
        spin_for(node, 0.25)
        unwedged = unwedged or get_inject(node, args.relay) != "wedge"
        if unwedged and reactions.settled():
            spin_for(node, 0.5)
            phases = sorted(set(reactions.executions().values()))
            print(f"{args.relay}: restarted, scans flow again (recovery {', '.join(phases)})")
            return
    if unwedged:
        raise RuntimeError(f"{args.relay} was restarted, but recovery did not settle in {args.timeout:g} s")
    raise RuntimeError(
        f"{args.relay} is still wedged after {args.timeout:g} s: Jarvis did not restart it. Is the e-stop "
        "latched (inject clear), the rule suppressed (see above) or recovery off (ros2 jarvis mode)? "
        "Restart it as an operator: " + OPERATOR_RESTART.format(relay=args.relay)
    )


def wait_for_readers(node, topic, count_readers, timeout=10.0):
    """DDS drops what is published before discovery completes: wait for a reader first."""
    deadline = time.monotonic() + timeout
    while count_readers() == 0:
        if time.monotonic() >= deadline:
            raise RuntimeError(f"nobody reads {topic}: is Jarvis running?")
        spin_for(node, 0.05)
    spin_for(node, 0.5)  # every endpoint of the reader's process matches shortly after the first


def rosout_error(node, args):
    # /rosout's publisher is internal to rcl: wait until a reader (the bridge) is on the graph.
    wait_for_readers(node, "/rosout", lambda: node.count_subscribers("/rosout"))
    for i in range(args.count):
        node.get_logger().error(f"injected error {i + 1}/{args.count}: costmap update failed")
        spin_for(node, args.period)


def diag_error(node, args):
    pub = node.create_publisher(DiagnosticArray, "/diagnostics", 10)
    wait_for_readers(node, "/diagnostics", pub.get_subscription_count)

    def publish(level, message):
        status = DiagnosticStatus(
            level=level, name=f"{node.get_name()}: motor driver", hardware_id="motor_left", message=message
        )
        status.values = [KeyValue(key="current_a", value="14.2" if level else "2.1")]
        pub.publish(DiagnosticArray(status=[status]))

    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        publish(DiagnosticStatus.ERROR, "overcurrent")
        spin_for(node, 1.0)
    for _ in range(3):  # a few OKs, as a real 1 Hz updater would send
        publish(DiagnosticStatus.OK, "nominal")
        spin_for(node, 1.0)


def estop(node, args):
    jarvis = Reporter(node, "base")
    if not jarvis.wait_for_hub():
        raise RuntimeError("the event hub is not reachable: is Jarvis running?")
    jarvis.fault("safety.estop.engaged", Severity.CRITICAL, "emergency stop pressed")
    print(f"e-stop pressed for {args.seconds:g} s")
    spin_for(node, args.seconds)
    jarvis.clear("safety.estop.engaged", "emergency stop released")
    jarvis.flush()
    print("e-stop released; the fault stays LATCHED until an operator clears it: inject clear")


def nav_abort(node, args):
    try:
        from nav2_msgs.action import NavigateToPose
    except ImportError as e:
        raise RuntimeError("nav-abort needs Nav2 (nav2_msgs); run it with nav2_sim.launch.py") from e
    client = ActionClient(node, NavigateToPose, "/navigate_to_pose")
    if not client.wait_for_server(timeout_sec=10.0):
        raise RuntimeError("no /navigate_to_pose server: start nav2_sim.launch.py first")
    goal = NavigateToPose.Goal()
    goal.pose.header.frame_id = "map"
    goal.pose.pose.position.x, goal.pose.pose.position.y = args.x, args.y
    goal.pose.pose.orientation.w = 1.0
    print(f"sending an unreachable goal ({args.x}, {args.y}); Nav2 will retry, then abort")
    sent = client.send_goal_async(goal)
    rclpy.spin_until_future_complete(node, sent)
    result = sent.result().get_result_async()
    rclpy.spin_until_future_complete(node, result, timeout_sec=args.timeout)
    print("goal status:", result.result().status if result.done() else "still running")


def call(node, client, request, timeout=10.0):
    if not client.wait_for_service(timeout_sec=timeout):
        raise RuntimeError(f"{client.srv_name} is not available: is Jarvis running?")
    future = client.call_async(request)
    rclpy.spin_until_future_complete(node, future, timeout_sec=timeout)
    if future.result() is None:
        raise RuntimeError(f"no reply from {client.srv_name}")
    return future.result()


def clear(node, args):
    get_faults = node.create_client(GetFaults, "/jarvis/fault_manager/get_faults")
    faults = call(node, get_faults, GetFaults.Request()).faults
    targets = [f for f in faults if args.all or f.state == Fault.STATE_LATCHED]
    if not targets:
        print("nothing to clear" + ("" if args.all else " (no latched fault; --all clears every open one)"))
        return
    clear_fault = node.create_client(ClearFault, "/jarvis/fault_manager/clear_fault")
    for f in targets:
        request = ClearFault.Request(fault_id=f.fault_id, operator_name=args.operator, reason=args.reason)
        reply = call(node, clear_fault, request)
        outcome = "cleared" if reply.success else "NOT cleared"
        print(f"{outcome} {f.code} ({f.source.component}): {reply.message}")


RELAY = "/demo/scan_relay"
# The restart rule's cooldown (config/rules: lidar-stale-restart), plus a margin: after a restart, the
# next stall must come later than this, whatever the pace, or the rule stays quiet and nothing fails.
RESTART_COOLDOWN_S = 5.0 + 2.0
# (narration, step, pause after the step in seconds, the least pause whatever the pace)
TOUR = (
    (
        "1/5 The lidar relay hangs: sensor.scan.stale goes ACTIVE (black-box snapshot), rule "
        "lidar-stale-restart runs plan restart-lifecycle-node, the relay restarts, scans flow: SUCCEEDED.",
        lambda node: scan_wedge(node, SimpleNamespace(relay=RELAY, timeout=30.0, quiet=False)),
        6.0,
        RESTART_COOLDOWN_S,
    ),
    (
        "2/5 The lidar itself stops for 20 s: its rate symptom is muted, the restart can't help, the "
        "plan's check times out (FAILED) and the incident goes to the operators (ESCALATED, notify).",
        lambda node: scan(node, SimpleNamespace(seconds=20.0, relay=RELAY, quiet=False), "stall"),
        4.0,
        0.0,
    ),
    (
        "3/5 Motor driver overcurrent on /diagnostics for 5 s: the bridge raises it, the next OK clears it.",
        lambda node: diag_error(node, SimpleNamespace(seconds=5.0)),
        3.0,
        0.0,
    ),
    (
        "4/5 A node logs errors in a loop: sustained /rosout errors become ros.log.error, which heals "
        "after 60 s of quiet.",
        lambda node: rosout_error(node, SimpleNamespace(count=8, period=0.5)),
        3.0,
        0.0,
    ),
    (
        "5/5 E-stop pressed for 2 s: CRITICAL, and still LATCHED after release until an operator "
        "clears it (inject clear). While it is latched, Jarvis restarts nothing.",
        lambda node: estop(node, SimpleNamespace(seconds=2.0)),
        0.0,
        0.0,
    ),
)


def tour(node, args):
    """Runs the scenarios one after the other, narrating each step on the event stream too."""
    narrator = Reporter(node, "tour")
    if not narrator.wait_for_hub():
        raise RuntimeError("the event hub is not reachable: is Jarvis running?")
    for text, step, pause, at_least in TOUR:
        print(f"\n== {text}")
        narrator.log(TOUR_CODE, Severity.INFO, text)
        step(node)
        spin_for(node, max(pause * args.pace, at_least))
    done = "Tour done. The e-stop stays latched until an operator clears it: inject clear."
    narrator.log(TOUR_CODE, Severity.INFO, done)
    narrator.flush()
    print(f"\n== {done}")


def parser():
    p = argparse.ArgumentParser(
        prog="inject", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="scenario", required=True)
    for name, default in (("scan-stall", 5.0), ("scan-slow", 10.0)):
        s = sub.add_parser(name)
        s.add_argument("--seconds", type=float, default=default)
        s.add_argument("--relay", default=RELAY)
        s.add_argument("--quiet", action="store_true", help="don't print what Jarvis does")
    s = sub.add_parser("scan-wedge")
    s.add_argument("--timeout", type=float, default=30.0, help="how long to wait for Jarvis to restart it")
    s.add_argument("--relay", default=RELAY)
    s.add_argument("--quiet", action="store_true", help="don't print what Jarvis does")
    s = sub.add_parser("rosout-error")
    s.add_argument("--count", type=int, default=10)
    s.add_argument("--period", type=float, default=0.5)
    sub.add_parser("diag-error").add_argument("--seconds", type=float, default=5.0)
    sub.add_parser("estop").add_argument("--seconds", type=float, default=2.0)
    s = sub.add_parser("nav-abort")
    s.add_argument("--x", type=float, default=100.0)
    s.add_argument("--y", type=float, default=100.0)
    s.add_argument("--timeout", type=float, default=120.0)
    sub.add_parser("tour").add_argument("--pace", type=float, default=1.0, help="scale the pauses")
    s = sub.add_parser("clear")
    s.add_argument("--all", action="store_true", help="clear every open fault, not only latched ones")
    s.add_argument("--operator", default="demo-operator")
    s.add_argument("--reason", default="checked during the demo")
    return p


SCENARIOS = {
    "scan-wedge": scan_wedge,
    "scan-stall": lambda node, args: scan(node, args, "stall"),
    "scan-slow": lambda node, args: scan(node, args, "slow"),
    "rosout-error": rosout_error,
    "diag-error": diag_error,
    "estop": estop,
    "nav-abort": nav_abort,
    "tour": tour,
    "clear": clear,
}


def main(argv=None):
    args = parser().parse_args(rclpy.utilities.remove_ros_args(argv if argv is not None else sys.argv)[1:])
    rclpy.init()
    node = rclpy.create_node("fault_injector", namespace="demo")
    try:
        SCENARIOS[args.scenario](node, args)
    except RuntimeError as e:
        print(f"inject: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
    return 0
