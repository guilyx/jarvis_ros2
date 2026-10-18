cmake_minimum_required(VERSION 3.22)
project(jarvis_medkit NONE)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

ament_python_install_package(${PROJECT_NAME})
install(PROGRAMS scripts/medkit_bridge DESTINATION lib/${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  # Own ROS domain: packages are tested in parallel.
  ament_add_pytest_test(test_bridge test/test_bridge.py
    APPEND_ENV PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR} ROS_DOMAIN_ID=71
    TIMEOUT 60
  )
endif()

ament_package()
