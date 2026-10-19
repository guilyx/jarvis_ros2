cmake_minimum_required(VERSION 3.22)
project(jarvis_demo NONE)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

ament_python_install_package(${PROJECT_NAME})
install(
  PROGRAMS scripts/fake_base scripts/fake_lidar scripts/inject scripts/monitor scripts/scan_relay
  DESTINATION lib/${PROJECT_NAME}
)
install(DIRECTORY config launch DESTINATION share/${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  find_package(launch_testing_ament_cmake REQUIRED)
  ament_add_pytest_test(test_monitor test/test_monitor.py
    APPEND_ENV PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}
    TIMEOUT 60
  )
  # Own ROS domain: packages are tested in parallel and launch tests share node names.
  add_launch_test(test/test_demo.launch.py TIMEOUT 180 ENV ROS_DOMAIN_ID=64)
  add_launch_test(test/test_docking.launch.py TIMEOUT 150 ENV ROS_DOMAIN_ID=72)
endif()

ament_package()
