# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Jarvis demo on a simulated TurtleBot 4 with Nav2 and Gazebo Harmonic.

NOT RUN IN CI: it needs Nav2 and the TB4 simulation, which this repository does not depend on:

    sudo apt install ros-jazzy-nav2-bringup ros-jazzy-nav2-minimal-tb4-sim
    ros2 launch jarvis_demo nav2_sim.launch.py headless:=false
    ros2 run jarvis_demo inject scan-wedge                # relay hangs, Jarvis restarts it
    ros2 run jarvis_demo inject scan-stall --seconds 10   # costmaps go blind, Nav2 aborts
    ros2 run jarvis_demo inject nav-abort                 # unreachable goal

The scan relay sits between the simulated lidar and Nav2: Nav2's parameters are rewritten to read
/scan_relayed, so `inject scan-stall` blinds the costmaps, AMCL and the collision monitor.
"""

from pathlib import Path

from ament_index_python.packages import PackageNotFoundError, get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

DEMO = Path(get_package_share_directory("jarvis_demo"))
BRINGUP = Path(get_package_share_directory("jarvis_bringup"))
RELAYED = "/scan_relayed"


def generate_launch_description():
    try:
        nav2 = Path(get_package_share_directory("nav2_bringup"))
        from nav2_common.launch import RewrittenYaml
    except (PackageNotFoundError, ImportError) as e:
        raise RuntimeError(
            "nav2_sim.launch.py needs Nav2: sudo apt install ros-jazzy-nav2-bringup "
            "ros-jazzy-nav2-minimal-tb4-sim (or use demo.launch.py, which needs no simulator)"
        ) from e

    arg = LaunchConfiguration
    # Every scan consumer in Nav2's default parameters reads the relayed topic.
    nav2_params = RewrittenYaml(
        source_file=str(nav2 / "params" / "nav2_params.yaml"),
        param_rewrites={"topic": RELAYED, "scan_topic": RELAYED},
        convert_types=True,
    )
    simulation = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(str(nav2 / "launch" / "tb4_simulation_launch.py")),
        launch_arguments={
            "use_sim_time": "true",
            "headless": arg("headless"),
            "params_file": nav2_params,
        }.items(),
    )
    jarvis = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(str(BRINGUP / "launch" / "jarvis.launch.py")),
        launch_arguments={
            "robot_id": arg("robot_id"),
            "use_sim_time": "true",
            "catalog": str(DEMO / "config" / "demo_faults.yaml"),
            "rules": str(DEMO / "config" / "rules"),
            "blackbox_topics": f"/scan {RELAYED} /odom /tf /tf_static /cmd_vel /jarvis/event_batches",
        }.items(),
    )
    relay = Node(
        package="jarvis_demo",
        executable="scan_relay",
        namespace="demo",
        parameters=[
            {"autostart": True, "use_sim_time": True, "input_topic": "/scan", "output_topic": RELAYED}
        ],
        output="screen",
    )
    return LaunchDescription(
        [
            DeclareLaunchArgument("robot_id", default_value="tb4-sim"),
            DeclareLaunchArgument("headless", default_value="true", description="Run Gazebo without GUI"),
            simulation,
            jarvis,
            relay,
        ]
    )
