cmake_minimum_required(VERSION 3.22)
project(jarvis_bt)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
add_compile_options(-Wall -Wextra -Wpedantic)

find_package(ament_cmake REQUIRED)
find_package(behaviortree_cpp REQUIRED)
find_package(jarvis_interfaces REQUIRED)
find_package(jarvis_ros REQUIRED)
find_package(pluginlib REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_action REQUIRED)
find_package(rclcpp_lifecycle REQUIRED)
find_package(yaml-cpp REQUIRED)
if(TARGET yaml-cpp::yaml-cpp)
  set(JARVIS_BT_YAML yaml-cpp::yaml-cpp)
else()
  set(JARVIS_BT_YAML yaml-cpp)
endif()

# BehaviorTree.CPP bundles an older nlohmann/json under the same include guard as the system one,
# which Jarvis's libraries (and the jarvis_ros::Actuator interface) are built with: whichever a
# file includes first wins. Every file here gets the system one first.
add_compile_options(-include nlohmann/json.hpp)

# The Jarvis BehaviorTree.CPP nodes (ROS calls of run-time types, Jarvis's faults).
add_library(jarvis_bt SHARED src/nodes.cpp)
target_include_directories(jarvis_bt PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include/${PROJECT_NAME}>
)
target_link_libraries(jarvis_bt PUBLIC
  behaviortree_cpp::behaviortree_cpp
  jarvis_ros::jarvis_ros
  rclcpp::rclcpp
  rclcpp_action::rclcpp_action
  ${jarvis_interfaces_TARGETS}
  PRIVATE ${JARVIS_BT_YAML}
)

# The same nodes as a BehaviorTree.CPP plugin, for a robot's own trees.
add_library(jarvis_bt_nodes SHARED src/plugin.cpp)
target_compile_definitions(jarvis_bt_nodes PRIVATE BT_PLUGIN_EXPORT)
target_link_libraries(jarvis_bt_nodes PUBLIC jarvis_bt)

# bt.run (a pluginlib plugin of jarvis_ros::Actuator, see actuators.xml).
add_library(jarvis_bt_actuator SHARED src/tree_actuator.cpp)
target_link_libraries(jarvis_bt_actuator PUBLIC
  jarvis_bt
  pluginlib::pluginlib
  rclcpp_lifecycle::rclcpp_lifecycle
)
pluginlib_export_plugin_description_file(jarvis_ros actuators.xml)

install(DIRECTORY include/ DESTINATION include/${PROJECT_NAME})
install(TARGETS jarvis_bt jarvis_bt_nodes jarvis_bt_actuator
  EXPORT export_${PROJECT_NAME}
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION bin
)
install(DIRECTORY trees DESTINATION share/${PROJECT_NAME})
ament_export_targets(export_${PROJECT_NAME} HAS_LIBRARY_TARGET)
ament_export_dependencies(behaviortree_cpp jarvis_interfaces jarvis_ros rclcpp rclcpp_action)

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  find_package(std_srvs REQUIRED)
  ament_add_gtest(test_tree_actuator test/test_tree_actuator.cpp TIMEOUT 120 ENV ROS_DOMAIN_ID=74)
  target_link_libraries(test_tree_actuator
    jarvis_bt pluginlib::pluginlib rclcpp_lifecycle::rclcpp_lifecycle ${std_srvs_TARGETS})
endif()

ament_package()
