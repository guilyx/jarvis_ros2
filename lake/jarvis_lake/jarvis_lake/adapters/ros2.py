# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The lake on the ROS graph: woken by the recorder's closed splits, reporting on /diagnostics, and
raising `jarvis.storage.low` while the disk is short of free space after retention, and
`jarvis.upload.behind` while what waits to be uploaded is older than the upload's `max_lag_s`.

The fault is raised when retention found nothing more it may delete, and cleared once a fresh look
at the disk finds it above the floor: in between (a file system that frees space lazily, a deleted
file still open), nothing changes, so the fault does not flap. The fault manager restores open
faults after a restart, the lake's included: while /jarvis/faults lists it and the disk has room,
the lake clears it, whoever raised it. The same goes for `jarvis.upload.behind`.

This is the only module of jarvis_lake that imports rclpy, and only when the service runs with ROS.
"""

import signal
import threading

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from jarvis_interfaces.msg import FaultArray
from jarvis_reporter import Reporter, Severity
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.signals import SignalHandlerOptions
from rosbag2_interfaces.msg import WriteSplitEvent

from jarvis_lake import upload

SPLIT_TOPIC = "/jarvis/recorder/events/write_split"
DISK_LOW = "jarvis.storage.low"
UPLOAD_BEHIND = "jarvis.upload.behind"
VALUES = (
    "splits",
    "splits_converted",
    "splits_pending",
    "bags_bytes",
    "lake_bytes",
    "rows",
    "disk_free_bytes",
)


class LakeNode(Node):
    def __init__(self, service):
        super().__init__("lake", namespace="jarvis")
        self._service = service
        self.create_subscription(WriteSplitEvent, SPLIT_TOPIC, self._on_split, 10)
        self._diagnostics = self.create_publisher(DiagnosticArray, "/diagnostics", 10)
        self._jarvis = Reporter(self, "lake")
        self._hub = False  # the event hub is matched: what we report is not lost to discovery
        self._disk_low_open = False  # the fault manager holds jarvis.storage.low for the lake
        self._behind_open = False  # ... and jarvis.upload.behind
        latched = QoSProfile(
            depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL
        )
        self.create_subscription(FaultArray, "/jarvis/faults", self._on_faults, latched)

    def _on_faults(self, msg):
        mine = {f.code for f in msg.faults if f.source.component == "lake"}
        self._disk_low_open = DISK_LOW in mine
        self._behind_open = UPLOAD_BEHIND in mine

    def _on_split(self, msg):
        self.get_logger().debug(f"split closed: {msg.closed_file}")
        self._service.poke(msg.closed_file)

    def publish_status(self, status):
        """Jarvis' own health goes out with hardware_id `jarvis` (the diagnostics bridge skips it)."""
        level = DiagnosticStatus.OK
        message = "converting splits as they close"
        self._hub = self._hub or self._jarvis.wait_for_hub(timeout=2.0)
        free, floor = status.get("disk_free_bytes", 0) >> 20, status.get("disk_min_free_bytes", 0) >> 20
        if status.get("disk_short_bytes"):
            # Retention deleted all it may: recordings are at risk, someone must act.
            level, message = DiagnosticStatus.ERROR, f"disk low: {free} MiB free of {floor} MiB wanted"
            self._jarvis.fault(DISK_LOW, Severity.ERROR, message, {"free_mib": free, "min_free_mib": floor})
        elif status.get("disk_low"):
            level, message = DiagnosticStatus.WARN, f"disk low: {free} MiB free, retention is freeing space"
        elif status.get("last_error"):
            level, message = DiagnosticStatus.ERROR, status["last_error"]
        elif status.get("splits_failed"):
            level, message = DiagnosticStatus.WARN, f"{len(status['splits_failed'])} splits unreadable"
        elif status.get("splits_pending", 0) > 1:
            level, message = DiagnosticStatus.WARN, f"{status['splits_pending']} splits waiting"
        if not status.get("disk_low") and not status.get("disk_short_bytes"):
            # force: the fault may be from before a restart, when this reporter raised nothing.
            self._jarvis.clear(DISK_LOW, "free disk is back above the floor", force=self._disk_low_open)
        behind = upload.behind_message(status.get("upload"))
        if behind:
            message = behind if level == DiagnosticStatus.OK else message
            level = max(level, DiagnosticStatus.WARN)
            up = status["upload"]
            self._jarvis.fault(
                UPLOAD_BEHIND,
                Severity.WARN,
                behind,
                {"pending_files": up["pending_files"], "oldest_pending_s": up["oldest_pending_s"]},
            )
        elif status.get("upload") is not None:
            self._jarvis.clear(UPLOAD_BEHIND, "the history is uploaded again", force=self._behind_open)
        values = {k: status.get(k) for k in VALUES}
        if status.get("upload"):
            values |= {
                f"upload_{k}": status["upload"][k] for k in ("pending_files", "oldest_pending_s", "lost")
            }
        diag = DiagnosticStatus(
            level=level,
            name="jarvis: lake",
            hardware_id="jarvis",
            message=message,
            values=[KeyValue(key=k, value=str(v)) for k, v in values.items()],
        )
        array = DiagnosticArray(status=[diag])
        array.header.stamp = self.get_clock().now().to_msg()
        self._diagnostics.publish(array)


def run(service, args=None):
    """Runs the service with ROS: passes on the main thread, the node on a background executor.

    SIGINT and SIGTERM are the lake's own: they stop the loop, then the node shuts down in order.
    rclpy's handler would shut the context down from C under the executor and the main thread
    (an RCLError, or a shutdown that hangs until SIGKILL)."""
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    node = LakeNode(service)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    stop = threading.Event()

    def on_signal(signum, frame):
        stop.set()
        service.poke()

    previous = {s: signal.signal(s, on_signal) for s in (signal.SIGINT, signal.SIGTERM)}

    def spin():
        try:
            executor.spin()
        except (KeyboardInterrupt, ExternalShutdownException):
            pass
        finally:
            stop.set()
            service.poke()

    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    write_status = service.write_status

    def write_and_publish():
        status = write_status()
        if rclpy.ok():
            node.publish_status(status)
        return status

    service.write_status = write_and_publish
    try:
        service.run(stop)
    finally:
        stop.set()
        executor.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()
        thread.join(timeout=5.0)
        for s, handler in previous.items():
            signal.signal(s, handler)
