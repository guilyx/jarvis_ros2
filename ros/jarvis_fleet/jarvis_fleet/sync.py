# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""config_sync: the robot's rules and plans from the fleet's storage (jarvis_fleet.config).

    ros2 run jarvis_fleet config_sync --source s3://fleet/config --robot amr-7 \\
        --data-dir ~/.local/share/jarvis --catalog faults.yaml

The launch starts it with `config_from:=<URI>` (and points the rule engine and the recovery
orchestrator at <data_dir>/config/active). `--once` looks once, applies what passes, and exits:
0 in sync or applied, 1 otherwise.

On the graph: /jarvis/config, reloading through /jarvis/recovery/reload and
/jarvis/rule_engine/reload; `jarvis.config.rejected` while the source's revision is refused;
"jarvis: config" on /diagnostics.
"""

import argparse
import os
import pathlib
import shutil
import signal
import sys
import threading

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from jarvis_interfaces.msg import FaultArray
from jarvis_interfaces.srv import ReloadConfig
from jarvis_reporter import Reporter, Severity
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.signals import SignalHandlerOptions

from jarvis_fleet import config

REJECTED = "jarvis.config.rejected"
CALL_TIMEOUT_S = 30.0  # a reload parses every file: seconds for hundreds
RETRY_S = 10.0  # the rule engine or the recovery orchestrator not answering yet
RELOADS = (("plans", "/jarvis/recovery/reload"), ("rules", "/jarvis/rule_engine/reload"))  # in order
LEVELS = {
    "in-sync": DiagnosticStatus.OK,
    "applied": DiagnosticStatus.OK,
    "settling": DiagnosticStatus.OK,
    "starting": DiagnosticStatus.OK,
    "empty": DiagnosticStatus.WARN,
    "offline": DiagnosticStatus.WARN,
    "waiting": DiagnosticStatus.WARN,
    "rejected": DiagnosticStatus.WARN,
}


class ConfigNode(Node):
    def __init__(self):
        super().__init__("config", namespace="jarvis")
        self._jarvis = Reporter(self, "config")
        self._diagnostics = self.create_publisher(DiagnosticArray, "/diagnostics", 10)
        self._reloads = [(kind, self.create_client(ReloadConfig, name)) for kind, name in RELOADS]
        self._open = False  # the fault manager holds jarvis.config.rejected for us
        latched = QoSProfile(
            depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL
        )
        self.create_subscription(FaultArray, "/jarvis/faults", self._on_faults, latched)
        self._hub = False
        self._said = ""

    def _on_faults(self, msg):
        self._open = any(f.code == REJECTED and f.source.component == "config" for f in msg.faults)

    def reload(self, validate_only):
        """Plans first, then rules; every error of the two. Raises config.Unavailable."""
        errors = []
        for kind, client in self._reloads:
            if not client.wait_for_service(timeout_sec=5.0):
                raise config.Unavailable(f"{client.srv_name} is not answering")
            request = ReloadConfig.Request(validate_only=validate_only, operator_name=config.OPERATOR)
            done = threading.Event()
            future = client.call_async(request)
            future.add_done_callback(lambda _f, done=done: done.set())
            if not done.wait(CALL_TIMEOUT_S) or future.result() is None:
                client.remove_pending_request(future)
                raise config.Unavailable(f"{client.srv_name} did not answer in {CALL_TIMEOUT_S:.0f} s")
            response = future.result()
            if not response.success:
                errors += [f"{kind}: {e}" for e in response.errors] or [f"{kind}: {response.message}"]
                if not validate_only:
                    break  # the rules wait for plans that loaded
        return errors

    def report(self, syncer, result, previous_revision):
        status = syncer.status()
        self._hub = self._hub or self._jarvis.wait_for_hub(timeout=2.0)
        if result.state == "rejected":
            self._jarvis.fault(
                REJECTED,
                Severity.WARN,
                result.message,
                {
                    "revision": (status["rejected"] or {}).get("revision") or "",
                    "active": status["revision"] or "",
                    "errors": len((status["rejected"] or {}).get("errors", [])),
                },
            )
        elif result.state in ("in-sync", "applied"):
            # force: raised before a restart, the fault manager restored it.
            self._jarvis.clear(REJECTED, f"rules and plans {status['revision']} accepted", force=self._open)
        if result.state == "applied":
            self._jarvis.log(
                "jarvis.config.applied",
                Severity.INFO,
                result.message,
                {
                    "revision": status["revision"],
                    "previous": previous_revision or "",
                    "source": status["source"],
                },
            )
            self.get_logger().info(result.message)
        elif result.state in ("rejected", "offline", "waiting", "empty") and result.message != self._said:
            self.get_logger().warning(result.message)  # once, not every period
        self._said = result.message
        values = {
            "state": status["state"],
            "revision": status["revision"],
            "source": status["source"],
            "rules": len(status["files"].get("rules", [])),
            "plans": len(status["files"].get("plans", [])),
            "rejected": (status["rejected"] or {}).get("revision") or "",
        }
        diag = DiagnosticStatus(
            level=LEVELS.get(result.state, DiagnosticStatus.WARN),
            name="jarvis: config",
            hardware_id="jarvis",
            message=result.message,
            values=[KeyValue(key=k, value=str(v)) for k, v in values.items()],
        )
        array = DiagnosticArray(status=[diag])
        array.header.stamp = self.get_clock().now().to_msg()
        self._diagnostics.publish(array)


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="config_sync", description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", required=True, help="s3://bucket/prefix, file:///mnt/fleet, ...")
    parser.add_argument("--robot", default="robot", help="this robot's id: its own files at robots/<robot>/")
    parser.add_argument(
        "--data-dir", default=os.environ.get("JARVIS_DATA", "~/.local/share/jarvis"), help="Jarvis' data"
    )
    parser.add_argument("--catalog", default="", help="the fault catalog the rules are checked against")
    parser.add_argument("--period", type=float, default=60.0, help="seconds between looks at the source")
    parser.add_argument("--once", action="store_true", help="look once, apply, exit")
    return parser.parse_args(argv)


def main(argv=None):
    argv = rclpy.utilities.remove_ros_args(sys.argv if argv is None else argv)[1:]
    args = parse_args(argv)
    jarvis = os.environ.get("JARVIS_BIN") or shutil.which("jarvis")
    if not jarvis:
        print("error: the `jarvis` tool is not on PATH (source the workspace, or set JARVIS_BIN)")
        return 2
    store = config.Store(pathlib.Path(args.data_dir).expanduser() / "config")
    if store.current() is None:
        print(f"error: no rules and plans under {store.root}: the launch seeds them (config_from:=...)")
        return 2
    try:
        source = config.Source(args.source, args.robot)
    except (OSError, ValueError) as e:
        print(f"error: {args.source}: {e}")
        return 2

    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    node = ConfigNode()
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    stop = threading.Event()
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, lambda *_: stop.set())

    def spin():
        try:
            executor.spin()
        except (KeyboardInterrupt, ExternalShutdownException):
            pass
        finally:
            stop.set()

    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    syncer = config.Syncer(store, source, config.lint(jarvis, args.catalog), node.reload)
    where = config.upload.redact(args.source)
    node.get_logger().info(f"rules and plans from {where}, every {args.period:.0f} s")
    result = None
    try:
        while not stop.is_set():
            previous = store.current()
            result = syncer.run()
            if result.state == "settling":
                stop.wait(syncer.settle_s)
                continue
            node.report(syncer, result, previous)
            if args.once:
                break
            stop.wait(RETRY_S if result.state == "waiting" else args.period)
    finally:
        node._jarvis.flush(timeout=2.0)
        executor.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()
        thread.join(timeout=5.0)
    return 0 if result and result.state in ("in-sync", "applied") else 1


if __name__ == "__main__":
    sys.exit(main())
