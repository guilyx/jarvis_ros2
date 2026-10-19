cmake_minimum_required(VERSION 3.22)
project(jarvis_fleet NONE)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

ament_python_install_package(${PROJECT_NAME})
install(PROGRAMS scripts/config_sync DESTINATION lib/${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(test_config test/test_config.py
    APPEND_ENV PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR} ROS_DOMAIN_ID=77
    TIMEOUT 120
  )
endif()

ament_package()
