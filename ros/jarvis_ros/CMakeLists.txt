cmake_minimum_required(VERSION 3.22)
project(jarvis_ros)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
add_compile_options(-Wall -Wextra -Wpedantic)

find_package(ament_cmake REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(diagnostic_msgs REQUIRED)
find_package(diagnostic_updater REQUIRED)
find_package(jarvis_core REQUIRED)
find_package(jarvis_interfaces REQUIRED)
find_package(lifecycle_msgs REQUIRED)
find_package(nlohmann_json REQUIRED)
find_package(pluginlib REQUIRED)
find_package(rcl_interfaces REQUIRED)
find_package(rcpputils REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_action REQUIRED)
find_package(rclcpp_components REQUIRED)
find_package(rclcpp_lifecycle REQUIRED)
find_package(rosbag2_interfaces REQUIRED)
find_package(rosidl_typesupport_introspection_cpp REQUIRED)
find_package(std_srvs REQUIRED)

# Conversions, type adapters, state files and run-time typed messages, shared by every Jarvis node.
add_library(jarvis_ros SHARED
  src/config_dir.cpp
  src/conversions.cpp
  src/message.cpp
  src/state_file.cpp
)
target_include_directories(jarvis_ros PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include/${PROJECT_NAME}>
)
target_link_libraries(jarvis_ros PUBLIC
  jarvis_core::jarvis_core
  nlohmann_json::nlohmann_json
  rclcpp::rclcpp
  rclcpp_lifecycle::rclcpp_lifecycle
  rcpputils::rcpputils
  rosidl_typesupport_introspection_cpp::rosidl_typesupport_introspection_cpp
  ${builtin_interfaces_TARGETS}
  ${jarvis_interfaces_TARGETS}
)

# Built-in actuators (pluginlib plugins of jarvis_ros::Actuator, see actuators.xml).
add_library(jarvis_actuators SHARED src/actuators.cpp src/generic_actuators.cpp src/process_actuators.cpp)
target_link_libraries(jarvis_actuators PUBLIC
  jarvis_ros
  pluginlib::pluginlib
  rclcpp_action::rclcpp_action
  ${lifecycle_msgs_TARGETS}
  ${rcl_interfaces_TARGETS}
  ${rosbag2_interfaces_TARGETS}
  ${std_srvs_TARGETS}
)
pluginlib_export_plugin_description_file(jarvis_ros actuators.xml)

# Composable lifecycle components (each also gets a standalone executable).
add_library(jarvis_components SHARED
  src/event_hub_node.cpp
  src/fault_manager_node.cpp
  src/recovery_node.cpp
  src/rule_engine_node.cpp
)
target_link_libraries(jarvis_components PUBLIC
  jarvis_ros
  diagnostic_updater::diagnostic_updater
  pluginlib::pluginlib
  rclcpp_action::rclcpp_action
  rclcpp_components::component
  rclcpp_lifecycle::rclcpp_lifecycle
  ${diagnostic_msgs_TARGETS}
  ${lifecycle_msgs_TARGETS}
  ${rosbag2_interfaces_TARGETS}
)
rclcpp_components_register_node(jarvis_components
  PLUGIN "jarvis_ros::EventHubNode"
  EXECUTABLE event_hub
)
rclcpp_components_register_node(jarvis_components
  PLUGIN "jarvis_ros::FaultManagerNode"
  EXECUTABLE fault_manager
)
rclcpp_components_register_node(jarvis_components
  PLUGIN "jarvis_ros::RuleEngineNode"
  EXECUTABLE rule_engine
)
rclcpp_components_register_node(jarvis_components
  PLUGIN "jarvis_ros::RecoveryNode"
  EXECUTABLE recovery
)

# `jarvis_load`: Jarvis under load, end to end (tools/load.cpp).
add_executable(jarvis_load tools/load.cpp)
target_link_libraries(jarvis_load rclcpp::rclcpp nlohmann_json::nlohmann_json ${jarvis_interfaces_TARGETS})
install(TARGETS jarvis_load RUNTIME DESTINATION lib/${PROJECT_NAME})

install(DIRECTORY include/ DESTINATION include/${PROJECT_NAME})
install(TARGETS jarvis_ros jarvis_actuators jarvis_components
  EXPORT export_${PROJECT_NAME}
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION bin
)

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  find_package(launch_testing_ament_cmake REQUIRED)

  ament_add_gtest(test_conversions test/test_conversions.cpp)
  target_link_libraries(test_conversions jarvis_ros)

  ament_add_gtest(test_message test/test_message.cpp)
  target_link_libraries(test_message jarvis_ros ${diagnostic_msgs_TARGETS} ${lifecycle_msgs_TARGETS} ${rcl_interfaces_TARGETS})

  ament_add_gtest(test_state_file test/test_state_file.cpp)
  target_link_libraries(test_state_file jarvis_ros)

  ament_add_gtest(test_actuators test/test_actuators.cpp TIMEOUT 120)
  target_link_libraries(test_actuators
    jarvis_ros pluginlib::pluginlib rclcpp_action::rclcpp_action ${lifecycle_msgs_TARGETS} ${std_srvs_TARGETS}
  )

  ament_add_gtest(test_process_actuators test/test_process_actuators.cpp TIMEOUT 60)
  target_link_libraries(test_process_actuators jarvis_ros pluginlib::pluginlib)

  # Own ROS domain: packages are tested in parallel and launch tests share node names.
  add_launch_test(test/test_fault_flow.launch.py TIMEOUT 120 ENV ROS_DOMAIN_ID=61)
  add_launch_test(test/test_recovery_flow.launch.py TIMEOUT 180 ENV ROS_DOMAIN_ID=65)
endif()

ament_export_include_directories(include/${PROJECT_NAME})
ament_export_targets(export_${PROJECT_NAME} HAS_LIBRARY_TARGET)
ament_export_dependencies(
  builtin_interfaces jarvis_core jarvis_interfaces nlohmann_json pluginlib rclcpp rclcpp_lifecycle rcpputils
  rosidl_typesupport_introspection_cpp
)
ament_package()
