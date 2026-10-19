cmake_minimum_required(VERSION 3.22)
project(jarvis_bridges)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
add_compile_options(-Wall -Wextra -Wpedantic)

find_package(ament_cmake REQUIRED)
find_package(action_msgs REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(diagnostic_msgs REQUIRED)
find_package(jarvis_core REQUIRED)
find_package(jarvis_ros REQUIRED)
find_package(lifecycle_msgs REQUIRED)
find_package(rcl_interfaces REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_components REQUIRED)
find_package(rclcpp_lifecycle REQUIRED)
find_package(rmw REQUIRED)
find_package(rmw_implementation REQUIRED)

# Pure message -> event translators (no node), unit-tested on their own.
add_library(jarvis_translators SHARED src/translators.cpp src/watchdogs.cpp)
target_include_directories(jarvis_translators PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include/${PROJECT_NAME}>
)
target_link_libraries(jarvis_translators PUBLIC
  jarvis_core::jarvis_core
  ${action_msgs_TARGETS}
  ${builtin_interfaces_TARGETS}
  ${diagnostic_msgs_TARGETS}
  ${lifecycle_msgs_TARGETS}
  ${rcl_interfaces_TARGETS}
  rmw::rmw
  # rmw_qos_profile_check_compatible is the middleware's: the one in use answers.
  rmw_implementation::rmw_implementation
)

add_library(jarvis_bridge_components SHARED
  src/action_status_bridge.cpp
  src/diagnostics_bridge.cpp
  src/lifecycle_bridge.cpp
  src/node_watchdog_bridge.cpp
  src/qos_watchdog_bridge.cpp
  src/rosout_bridge.cpp
  src/topic_watchdog_bridge.cpp
)
target_link_libraries(jarvis_bridge_components PUBLIC
  jarvis_translators
  jarvis_ros::jarvis_ros
  rclcpp::rclcpp
  rclcpp_components::component
  rclcpp_lifecycle::rclcpp_lifecycle
)
rclcpp_components_register_node(jarvis_bridge_components
  PLUGIN "jarvis_bridges::DiagnosticsBridge"
  EXECUTABLE diagnostics_bridge
)
rclcpp_components_register_node(jarvis_bridge_components
  PLUGIN "jarvis_bridges::RosoutBridge"
  EXECUTABLE rosout_bridge
)
rclcpp_components_register_node(jarvis_bridge_components
  PLUGIN "jarvis_bridges::ActionStatusBridge"
  EXECUTABLE action_status_bridge
)
rclcpp_components_register_node(jarvis_bridge_components
  PLUGIN "jarvis_bridges::TopicWatchdogBridge"
  EXECUTABLE topic_watchdog
)
rclcpp_components_register_node(jarvis_bridge_components
  PLUGIN "jarvis_bridges::NodeWatchdogBridge"
  EXECUTABLE node_watchdog
)
rclcpp_components_register_node(jarvis_bridge_components
  PLUGIN "jarvis_bridges::QosWatchdogBridge"
  EXECUTABLE qos_watchdog
)
rclcpp_components_register_node(jarvis_bridge_components
  PLUGIN "jarvis_bridges::LifecycleBridge"
  EXECUTABLE lifecycle_bridge
)

install(DIRECTORY include/ DESTINATION include/${PROJECT_NAME})
install(TARGETS jarvis_translators jarvis_bridge_components
  EXPORT export_${PROJECT_NAME}
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION bin
)

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  find_package(launch_testing_ament_cmake REQUIRED)

  ament_add_gtest(test_translators test/test_translators.cpp)
  target_link_libraries(test_translators jarvis_translators)

  ament_add_gtest(test_watchdogs test/test_watchdogs.cpp)
  target_link_libraries(test_watchdogs jarvis_translators)

  # Own ROS domain: packages are tested in parallel and launch tests share node names.
  add_launch_test(test/test_bridges.launch.py TIMEOUT 120 ENV ROS_DOMAIN_ID=62)
endif()

ament_export_include_directories(include/${PROJECT_NAME})
ament_export_targets(export_${PROJECT_NAME} HAS_LIBRARY_TARGET)
ament_export_dependencies(
  action_msgs builtin_interfaces diagnostic_msgs jarvis_core jarvis_ros lifecycle_msgs rcl_interfaces rclcpp
  rclcpp_lifecycle rmw rmw_implementation
)
ament_package()
