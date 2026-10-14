cmake_minimum_required(VERSION 3.22)
project(jarvis_reporter)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
add_compile_options(-Wall -Wextra -Wpedantic)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
find_package(jarvis_interfaces REQUIRED)
find_package(rclcpp REQUIRED)

# Header-only C++ client; apps need C++17.
add_library(jarvis_reporter INTERFACE)
target_include_directories(jarvis_reporter INTERFACE
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include/${PROJECT_NAME}>
)
target_link_libraries(jarvis_reporter INTERFACE rclcpp::rclcpp ${jarvis_interfaces_TARGETS})
target_compile_features(jarvis_reporter INTERFACE cxx_std_17)

install(DIRECTORY include/ DESTINATION include/${PROJECT_NAME})
install(TARGETS jarvis_reporter EXPORT export_${PROJECT_NAME})

# Python client.
ament_python_install_package(${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  find_package(ament_cmake_pytest REQUIRED)

  ament_add_gtest(test_reporter test/test_reporter.cpp)
  target_link_libraries(test_reporter jarvis_reporter)

  ament_add_pytest_test(test_reporter_py test/test_reporter.py
    APPEND_ENV PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}
    TIMEOUT 60
  )
endif()

ament_export_include_directories(include/${PROJECT_NAME})
ament_export_targets(export_${PROJECT_NAME})
ament_export_dependencies(jarvis_interfaces rclcpp)
ament_package()
