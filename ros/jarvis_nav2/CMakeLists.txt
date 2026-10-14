cmake_minimum_required(VERSION 3.22)
project(jarvis_nav2)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
add_compile_options(-Wall -Wextra -Wpedantic)

find_package(ament_cmake REQUIRED)
find_package(action_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(jarvis_ros REQUIRED)
find_package(nav2_msgs REQUIRED)
find_package(pluginlib REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_action REQUIRED)
find_package(rclcpp_lifecycle REQUIRED)

# Nav2 actuators (pluginlib plugins of jarvis_ros::Actuator, see actuators.xml).
add_library(jarvis_nav2_actuators SHARED src/nav2_actuators.cpp)
target_link_libraries(jarvis_nav2_actuators PUBLIC
  jarvis_ros::jarvis_ros
  pluginlib::pluginlib
  rclcpp_action::rclcpp_action
  ${action_msgs_TARGETS}
  ${geometry_msgs_TARGETS}
  ${nav2_msgs_TARGETS}
)
pluginlib_export_plugin_description_file(jarvis_ros actuators.xml)

install(TARGETS jarvis_nav2_actuators
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION bin
)

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  ament_add_gtest(test_nav2_actuators test/test_nav2_actuators.cpp TIMEOUT 120)
  target_link_libraries(test_nav2_actuators
    jarvis_ros::jarvis_ros pluginlib::pluginlib rclcpp_action::rclcpp_action ${nav2_msgs_TARGETS})
endif()

ament_package()
