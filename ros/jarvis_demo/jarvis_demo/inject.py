# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Fault injector for the Jarvis demo: each scenario produces one kind of fault the way it happens.

    inject scan-stall --seconds 5   lidar stops       -> sensor.scan.stale (rate status muted)
    inject scan-slow --seconds 10   lidar at 50 %     -> diag.scan_relay.output_rate
    inject rosout-error --count 10  a node errors     -> ros.log.error
    inject diag-error --seconds 5   driver reports    -> diag.fault_injector.motor_driver
    inject estop --seconds 2        e-stop pressed    -> safety.estop.engaged (latched)
    inject nav-abort                unreachable goal  -> ros.action.navigate_to_pose.aborted

Run as `ros2 run jarvis_demo inject <scenario>`.
"""

import argparse
import sys
import time

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from jarvis_reporter import Reporter, Severity
from rclpy.action import ActionClient
from rclpy.parameter import Parameter
from rclpy.parameter_client import AsyncParameterClient


def spin_for(node, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.05)


def set_inject(node, relay, value):
    client = AsyncParameterClient(node, relay)
    if not client.wait_for_services(timeout_sec=10.0):
        raise RuntimeError(f"{relay} is not running (ros2 launch jarvis_demo demo.launch.py)")
    future = client.set_parameters([Parameter("inject", value=value)])
    rclpy.spin_until_future_complete(node, future, timeout_sec=10.0)
    result = future.result()
    if result is None or not result.results[0].successful:
        raise RuntimeError(f"could not set inject={value} on {relay}")


def scan(node, args, mode):
    set_inject(node, args.relay, mode)
    print(f"{args.relay}: inject={mode} for {args.seconds:g} s")
    try:
        spin_for(node, args.seconds)
    finally:
        set_inject(node, args.relay, "none")
        print(f"{args.relay}: inject=none")


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
    print("e-stop released; the fault stays LATCHED until an operator clears it:")
    print("  ros2 service call /jarvis/fault_manager/clear_fault jarvis_interfaces/srv/ClearFault \\")
    print('    "{fault_id: <id from /jarvis/faults>, operator_name: you, reason: area checked}"')


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


def parser():
    p = argparse.ArgumentParser(
        prog="inject", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="scenario", required=True)
    for name, default in (("scan-stall", 5.0), ("scan-slow", 10.0)):
        s = sub.add_parser(name)
        s.add_argument("--seconds", type=float, default=default)
        s.add_argument("--relay", default="/demo/scan_relay")
    s = sub.add_parser("rosout-error")
    s.add_argument("--count", type=int, default=10)
    s.add_argument("--period", type=float, default=0.5)
    sub.add_parser("diag-error").add_argument("--seconds", type=float, default=5.0)
    sub.add_parser("estop").add_argument("--seconds", type=float, default=2.0)
    s = sub.add_parser("nav-abort")
    s.add_argument("--x", type=float, default=100.0)
    s.add_argument("--y", type=float, default=100.0)
    s.add_argument("--timeout", type=float, default=120.0)
    return p


SCENARIOS = {
    "scan-stall": lambda node, args: scan(node, args, "stall"),
    "scan-slow": lambda node, args: scan(node, args, "slow"),
    "rosout-error": rosout_error,
    "diag-error": diag_error,
    "estop": estop,
    "nav-abort": nav_abort,
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
