# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Stands in for the simulator's lidar when the demo runs without Gazebo: /scan_raw at 10 Hz.

A driver can hang, and only a restart of its process brings it back (the container demo,
docs/guides/containers.md):

    ros2 param set /fake_lidar inject hang    # publishes nothing until the process restarts
"""

import math
import random

import rclpy
from rcl_interfaces.msg import ParameterDescriptor, SetParametersResult
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan

INJECTIONS = ("none", "hang")


class FakeLidar(Node):
    def __init__(self):
        super().__init__("fake_lidar")
        self.declare_parameter("topic", "/scan_raw")
        self.declare_parameter("rate_hz", 10.0)
        self.declare_parameter("frame_id", "base_scan")
        self.declare_parameter("inject", "none", ParameterDescriptor(description=" | ".join(INJECTIONS)))
        self.add_on_set_parameters_callback(self._validate)
        self._hung = False
        topic = self.get_parameter("topic").value
        self._pub = self.create_publisher(LaserScan, topic, qos_profile_sensor_data)
        self.create_timer(1.0 / self.get_parameter("rate_hz").value, self._publish)

    def _validate(self, params):
        for p in params:
            if p.name == "inject" and p.value not in INJECTIONS:
                return SetParametersResult(successful=False, reason=f"inject: one of {', '.join(INJECTIONS)}")
            if p.name == "inject" and p.value == "hang" and not self._hung:
                self._hung = True  # for good: only a new process publishes again
                self.get_logger().warning("injected: the driver hangs, publishing nothing until it restarts")
        return SetParametersResult(successful=True)

    def _publish(self):
        if self._hung:
            return
        beams = 360
        msg = LaserScan()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.get_parameter("frame_id").value
        msg.angle_min, msg.angle_max = -math.pi, math.pi
        msg.angle_increment = 2 * math.pi / beams
        msg.range_min, msg.range_max = 0.1, 12.0
        msg.ranges = [2.0 + random.uniform(-0.01, 0.01) for _ in range(beams)]
        self._pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = FakeLidar()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except RuntimeError:
        # SIGINT can shut ROS down while a message is being taken (rclpy on Lyrical raises then).
        if rclpy.ok():
            raise
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
