cmake_minimum_required(VERSION 3.22)
project(jarvis_bringup NONE)

find_package(ament_cmake REQUIRED)

install(DIRECTORY config launch DESTINATION share/${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(launch_testing_ament_cmake REQUIRED)
  # Own ROS domain: packages are tested in parallel and launch tests share node names.
  add_launch_test(test/test_bringup.launch.py TIMEOUT 180 ENV ROS_DOMAIN_ID=63)
endif()

ament_package()
