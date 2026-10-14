#!/usr/bin/env bash
# The core must build and run without ROS (ADR-0001). Fail if any ROS header or package sneaks in.
set -euo pipefail
cd "$(dirname "$0")/.."
pattern='#include *[<"](rclcpp|rclpy|rcl|rmw|rosidl|builtin_interfaces|std_msgs|jarvis_interfaces)[/_.]|import +rclpy|from +rclpy|find_package\((ament_cmake|rclcpp)'
if grep -rEn "$pattern" core/; then
  echo "error: core/ must stay ROS-free (see docs/adr/0001-hexagonal-ros-agnostic-core.md)" >&2
  exit 1
fi
echo "core/ is ROS-free"
