cmake_minimum_required(VERSION 3.22)
project(jarvis_demo NONE)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

ament_python_install_package(${PROJECT_NAME})
install(PROGRAMS scripts/fake_lidar scripts/inject scripts/scan_relay DESTINATION lib/${PROJECT_NAME})
install(DIRECTORY config launch DESTINATION share/${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(launch_testing_ament_cmake REQUIRED)
  # Own ROS domain: packages are tested in parallel and launch tests share node names.
  add_launch_test(test/test_demo.launch.py TIMEOUT 180 ENV ROS_DOMAIN_ID=64)
endif()

ament_package()
