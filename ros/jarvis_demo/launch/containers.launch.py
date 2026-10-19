# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The container demo: the lidar driver runs in a container, and Jarvis restarts the container.

    docker run -d --name lidar-driver --network host --ipc host <an image with your driver>
    ros2 launch jarvis_demo containers.launch.py
    ros2 param set /fake_lidar inject hang       # the driver hangs: only a restart brings it back

demo.launch.py without its own lidar (the container is the driver), with the container rule
(config/containers/rules) and the recovery node allowed to restart `lidar-driver` and nothing
else (config/containers/jarvis.yaml). docs/guides/containers.md runs the demo's driver in a
container. Every argument of demo.launch.py applies.
"""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

DEMO = Path(get_package_share_directory("jarvis_demo"))
CONTAINERS = DEMO / "config" / "containers"


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "rules", default_value=str(CONTAINERS / "rules"), description="Container rule"
            ),
            DeclareLaunchArgument(
                "params_overlay",
                default_value=str(CONTAINERS / "jarvis.yaml"),
                description="The containers Jarvis may restart",
            ),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(str(DEMO / "launch" / "demo.launch.py")),
                launch_arguments={
                    "fake_lidar": "false",
                    "rules": LaunchConfiguration("rules"),
                    "params_overlay": LaunchConfiguration("params_overlay"),
                }.items(),
            ),
        ]
    )
