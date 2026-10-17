cmake_minimum_required(VERSION 3.22)
project(jarvis_bringup NONE)

find_package(ament_cmake REQUIRED)

install(DIRECTORY config launch DESTINATION share/${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(launch_testing_ament_cmake REQUIRED)
  # Own ROS domain: packages are tested in parallel and launch tests share node names.
  add_launch_test(test/test_bringup.launch.py TIMEOUT 180 ENV ROS_DOMAIN_ID=63)
  add_launch_test(test/test_lake.launch.py TIMEOUT 180 ENV ROS_DOMAIN_ID=67)
  add_launch_test(test/test_gateway.launch.py TIMEOUT 180 ENV ROS_DOMAIN_ID=68)
  add_launch_test(test/test_cli.launch.py TIMEOUT 180 ENV ROS_DOMAIN_ID=66)
  add_launch_test(test/test_load.launch.py TIMEOUT 240 ENV ROS_DOMAIN_ID=69)
  # Starts and kills the stack itself: a plain pytest, not a launch test.
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(test_durability test/test_durability.py TIMEOUT 420 ENV ROS_DOMAIN_ID=70)
endif()

ament_package()
