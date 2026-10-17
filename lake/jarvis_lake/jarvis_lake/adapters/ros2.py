# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The lake on the ROS graph: woken by the recorder's closed splits, reporting on /diagnostics.

This is the only module of jarvis_lake that imports rclpy, and only when the service runs with ROS.
"""

import threading

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.node import Node
from rosbag2_interfaces.msg import WriteSplitEvent

SPLIT_TOPIC = "/jarvis/recorder/events/write_split"


class LakeNode(Node):
    def __init__(self, service):
        super().__init__("lake", namespace="jarvis")
        self._service = service
        self.create_subscription(WriteSplitEvent, SPLIT_TOPIC, self._on_split, 10)
        self._diagnostics = self.create_publisher(DiagnosticArray, "/diagnostics", 10)

    def _on_split(self, msg):
        self.get_logger().debug(f"split closed: {msg.closed_file}")
        self._service.poke(msg.closed_file)

    def publish_status(self, status):
        """Jarvis' own health goes out with hardware_id `jarvis` (the diagnostics bridge skips it)."""
        level = DiagnosticStatus.OK
        message = "converting splits as they close"
        if status.get("last_error"):
            level, message = DiagnosticStatus.ERROR, status["last_error"]
        elif status.get("splits_failed"):
            level, message = DiagnosticStatus.WARN, f"{len(status['splits_failed'])} splits unreadable"
        elif status.get("splits_pending", 0) > 1:
            level, message = DiagnosticStatus.WARN, f"{status['splits_pending']} splits waiting"
        values = {
            k: status.get(k)
            for k in ("splits", "splits_converted", "splits_pending", "bags_bytes", "lake_bytes", "rows")
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
    """Runs the service with ROS: passes on the main thread, the node on a background executor."""
    rclpy.init(args=args)
    node = LakeNode(service)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    stop = threading.Event()

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
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        executor.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()
        thread.join(timeout=5.0)
