# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The docking demo: a low battery sends the robot to its dock, through Nav2's docking server.

    ros2 launch jarvis_demo docking.launch.py
    ros2 param set /demo/base inject drain       # battery at 12 %  -> power.battery.low
    ros2 param set /demo/base inject no_dock     # then the dock isn't found: docking fails, escalated

demo.launch.py with the robot's base (fake_base: battery, /dock_robot, /set_initial_pose) and the
docking rules and plan (config/docking). The plan drives Nav2's DockRobot action and AMCL's
SetInitialPose service with the generic actuators (action.goal, service.call): no actuator was
written for them. Every argument of demo.launch.py applies.
"""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

DEMO = Path(get_package_share_directory("jarvis_demo"))
DOCKING = DEMO / "config" / "docking"


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument("rules", default_value=str(DOCKING / "rules"), description="Docking rules"),
            DeclareLaunchArgument("plans", default_value=str(DOCKING / "plans"), description="Docking plan"),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(str(DEMO / "launch" / "demo.launch.py")),
                launch_arguments={
                    "base": "true",
                    "rules": LaunchConfiguration("rules"),
                    "plans": LaunchConfiguration("plans"),
                }.items(),
            ),
        ]
    )
