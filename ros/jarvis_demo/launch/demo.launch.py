# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Jarvis demo without a simulator: a fake lidar, the scan relay and the full Jarvis stack.

    ros2 launch jarvis_demo demo.launch.py
    ros2 run jarvis_demo inject scan-stall     # then watch: ros2 topic echo /jarvis/faults

Use nav2_sim.launch.py for the same demo on a simulated TurtleBot 4 with Nav2.
"""

import os
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

DEMO = Path(get_package_share_directory("jarvis_demo"))
BRINGUP = Path(get_package_share_directory("jarvis_bringup"))


def generate_launch_description():
    arg = LaunchConfiguration
    jarvis = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(str(BRINGUP / "launch" / "jarvis.launch.py")),
        launch_arguments={
            "robot_id": arg("robot_id"),
            "data_dir": arg("data_dir"),
            "catalog": str(DEMO / "config" / "demo_faults.yaml"),
            "blackbox_topics": "/scan /jarvis/events",
        }.items(),
    )
    return LaunchDescription(
        [
            DeclareLaunchArgument("robot_id", default_value="demo-bot"),
            DeclareLaunchArgument(
                "data_dir", default_value=os.environ.get("JARVIS_DATA", "~/.local/share/jarvis/demo")
            ),
            DeclareLaunchArgument("fake_lidar", default_value="true", description="Publish /scan_raw"),
            jarvis,
            Node(
                package="jarvis_demo",
                executable="fake_lidar",
                namespace="demo",
                condition=IfCondition(arg("fake_lidar")),
            ),
            Node(
                package="jarvis_demo",
                executable="scan_relay",
                namespace="demo",
                parameters=[{"autostart": True, "input_topic": "/scan_raw", "output_topic": "/scan"}],
                output="screen",
            ),
        ]
    )
