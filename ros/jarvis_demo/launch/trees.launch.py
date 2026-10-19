# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The behavior-tree demo: Jarvis restarts the wedged lidar relay with a behavior tree.

    ros2 launch jarvis_demo trees.launch.py
    ros2 run jarvis_demo inject scan-wedge      # the relay hangs; a lifecycle cleanup cures it

demo.launch.py with the behavior-tree rule and plan (config/trees), and the recovery node loading
jarvis_bt's `bt.run` with jarvis_bt's shipped trees: restart_lifecycle_node.xml reactivates the
relay first, which does not cure a wedge, then cleans it up and configures it again, which does.
Every argument of demo.launch.py applies. docs/guides/behavior-trees.md.
"""

import tempfile
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

DEMO = Path(get_package_share_directory("jarvis_demo"))
TREES = DEMO / "config" / "trees"

# The built-in actuators (jarvis_bringup's jarvis.yaml), and bt.run.
OVERLAY = """/jarvis/recovery:
  ros__parameters:
    actuators:
      - jarvis_ros::LifecycleActuator
      - jarvis_ros::ServiceActuator
      - jarvis_ros::GenericActuator
      - jarvis_ros::ParamActuator
      - jarvis_ros::BagActuator
      - jarvis_ros::ProcessActuator
      - jarvis_bt::TreeActuator
    bt:
      trees_dir: {trees}
"""


def include_demo(context):
    trees = LaunchConfiguration("trees_dir").perform(context)
    with tempfile.NamedTemporaryFile("w", prefix="jarvis_trees_", suffix=".yaml", delete=False) as overlay:
        overlay.write(OVERLAY.format(trees=trees))
    return [
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(DEMO / "launch" / "demo.launch.py")),
            launch_arguments={
                "rules": LaunchConfiguration("rules"),
                "plans": LaunchConfiguration("plans"),
                "params_overlay": overlay.name,
            }.items(),
        )
    ]


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument("rules", default_value=str(TREES / "rules"), description="Tree rule"),
            DeclareLaunchArgument("plans", default_value=str(TREES / "plans"), description="Tree plan"),
            DeclareLaunchArgument(
                "trees_dir",
                default_value=str(Path(get_package_share_directory("jarvis_bt")) / "trees"),
                description="Where bt.run finds trees",
            ),
            OpaqueFunction(function=include_demo),
        ]
    )
