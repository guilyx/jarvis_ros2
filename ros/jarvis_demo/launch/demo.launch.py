# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Jarvis demo without a simulator: a fake lidar, the scan relay and the full Jarvis stack.

    ros2 launch jarvis_demo demo.launch.py
    ros2 run jarvis_demo monitor               # live view, in another shell
    ros2 run jarvis_demo inject tour           # every scenario, narrated in the monitor

Jarvis runs the demo's rules (config/rules: restart the lidar relay when it hangs, escalate when
that keeps happening) with the generic recovery plans of jarvis_bringup.

The gateway serves the API and dashboard on http://localhost:8080 (`gateway_host:=0.0.0.0` to reach
it from other machines). Its demo settings let anyone in as admin: never use them on a real robot.

`foxglove:=true` also starts foxglove_bridge on ws://localhost:8765 (the Docker demo does).

Use nav2_sim.launch.py for the same demo on a simulated TurtleBot 4 with Nav2, and docking.launch.py
for a low battery that sends the robot to its dock.
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
            "rules": arg("rules"),
            "plans": arg("plans"),
            "recovery_mode": arg("recovery_mode"),
            "blackbox_topics": "/scan /jarvis/event_batches",
            "split_duration_s": "60",  # history (jarvis_lake) shows up within a minute
            "gateway": arg("gateway"),
            "gateway_host": arg("gateway_host"),
            "gateway_port": arg("gateway_port"),
            "gateway_config": arg("gateway_config"),
            "agent": arg("agent"),
        }.items(),
    )
    return LaunchDescription(
        [
            DeclareLaunchArgument("robot_id", default_value="demo-bot"),
            DeclareLaunchArgument(
                "data_dir", default_value=os.environ.get("JARVIS_DATA", "~/.local/share/jarvis/demo")
            ),
            DeclareLaunchArgument("fake_lidar", default_value="true", description="Publish /scan_raw"),
            DeclareLaunchArgument(
                "base",
                default_value="false",
                description="A robot base: battery, docking server, initial pose (docking.launch.py)",
            ),
            DeclareLaunchArgument(
                "rules",
                default_value=str(DEMO / "config" / "rules"),
                description="Directory of rules (jarvis.rules/v1)",
            ),
            DeclareLaunchArgument(
                "plans",
                default_value=str(BRINGUP / "config" / "plans"),
                description="Directory of recovery plans (jarvis.plans/v1)",
            ),
            DeclareLaunchArgument(
                "recovery_mode", default_value="ENABLED", description="ENABLED, DRY_RUN or DISABLED"
            ),
            DeclareLaunchArgument(
                "foxglove", default_value="false", description="Serve the graph to Foxglove on port 8765"
            ),
            DeclareLaunchArgument("gateway", default_value="true", description="Serve API and dashboard"),
            DeclareLaunchArgument("gateway_host", default_value="127.0.0.1"),
            DeclareLaunchArgument("gateway_port", default_value="8080"),
            DeclareLaunchArgument(
                "gateway_config",
                default_value=str(DEMO / "config" / "gateway.yaml"),
                description="Gateway settings: a copy of the demo's, with a fleet to watch, say",
            ),
            DeclareLaunchArgument("agent", default_value="true", description="Analyze what goes wrong"),
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
            Node(
                package="jarvis_demo",
                executable="fake_base",
                namespace="demo",
                condition=IfCondition(arg("base")),
                output="screen",
            ),
            Node(
                package="foxglove_bridge",
                executable="foxglove_bridge",
                parameters=[{"port": 8765, "address": "0.0.0.0"}],
                condition=IfCondition(arg("foxglove")),
            ),
        ]
    )
