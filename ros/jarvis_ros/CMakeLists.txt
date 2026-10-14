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
find_package(rclcpp REQUIRED)
find_package(rclcpp_components REQUIRED)
find_package(rclcpp_lifecycle REQUIRED)
find_package(rosbag2_interfaces REQUIRED)

# Conversions, type adapters and state files, shared by every Jarvis node.
add_library(jarvis_ros SHARED
  src/conversions.cpp
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
  ${builtin_interfaces_TARGETS}
  ${jarvis_interfaces_TARGETS}
)

# Composable lifecycle components (each also gets a standalone executable).
add_library(jarvis_components SHARED
  src/event_hub_node.cpp
  src/fault_manager_node.cpp
)
target_link_libraries(jarvis_components PUBLIC
  jarvis_ros
  diagnostic_updater::diagnostic_updater
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

install(DIRECTORY include/ DESTINATION include/${PROJECT_NAME})
install(TARGETS jarvis_ros jarvis_components
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

  ament_add_gtest(test_state_file test/test_state_file.cpp)
  target_link_libraries(test_state_file jarvis_ros)

  add_launch_test(test/test_fault_flow.launch.py TIMEOUT 120)
endif()

ament_export_include_directories(include/${PROJECT_NAME})
ament_export_targets(export_${PROJECT_NAME} HAS_LIBRARY_TARGET)
ament_export_dependencies(
  builtin_interfaces jarvis_core jarvis_interfaces nlohmann_json rclcpp
)
ament_package()
