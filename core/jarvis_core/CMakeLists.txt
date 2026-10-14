cmake_minimum_required(VERSION 3.22)
project(jarvis_core VERSION 0.1.0 LANGUAGES CXX)

# Plain CMake on purpose: the core must build and test without ROS (ADR-0001).
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

option(BUILD_TESTING "Build the unit tests" ON)
option(JARVIS_SANITIZE "Build with AddressSanitizer and UndefinedBehaviorSanitizer" OFF)
option(JARVIS_WERROR "Treat warnings as errors" OFF)

find_package(nlohmann_json 3.10 REQUIRED)
find_package(yaml-cpp REQUIRED)
if(TARGET yaml-cpp::yaml-cpp)
  set(JARVIS_YAML_TARGET yaml-cpp::yaml-cpp)
else()
  set(JARVIS_YAML_TARGET yaml-cpp)
endif()

add_library(jarvis_core
  src/event_hub.cpp
  src/fault_catalog.cpp
  src/fault_manager.cpp
  src/json.cpp
  src/rules.cpp
  src/types.cpp
  src/util.cpp
)
add_library(jarvis_core::jarvis_core ALIAS jarvis_core)
target_include_directories(jarvis_core PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>
)
target_link_libraries(jarvis_core
  PUBLIC nlohmann_json::nlohmann_json
  PRIVATE ${JARVIS_YAML_TARGET}
)
target_compile_options(jarvis_core PRIVATE -Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wno-sign-conversion)
if(JARVIS_WERROR)
  target_compile_options(jarvis_core PRIVATE -Werror)
endif()

if(JARVIS_SANITIZE)
  foreach(t jarvis_core)
    target_compile_options(${t} PUBLIC -fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=all)
    target_link_options(${t} PUBLIC -fsanitize=address,undefined)
  endforeach()
endif()

include(GNUInstallDirs)
install(DIRECTORY include/ DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})
install(TARGETS jarvis_core EXPORT jarvis_coreTargets
  ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
  LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
  RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
)
install(EXPORT jarvis_coreTargets
  NAMESPACE jarvis_core::
  DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/jarvis_core
)
include(CMakePackageConfigHelpers)
configure_package_config_file(cmake/jarvis_coreConfig.cmake.in
  ${CMAKE_CURRENT_BINARY_DIR}/jarvis_coreConfig.cmake
  INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/jarvis_core
)
write_basic_package_version_file(${CMAKE_CURRENT_BINARY_DIR}/jarvis_coreConfigVersion.cmake
  COMPATIBILITY SameMinorVersion
)
install(FILES
  ${CMAKE_CURRENT_BINARY_DIR}/jarvis_coreConfig.cmake
  ${CMAKE_CURRENT_BINARY_DIR}/jarvis_coreConfigVersion.cmake
  DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/jarvis_core
)

if(BUILD_TESTING)
  enable_testing()
  find_package(GTest REQUIRED)
  add_executable(jarvis_core_tests
    test/test_util.cpp
    test/test_json.cpp
    test/test_event_hub.cpp
    test/test_fault_catalog.cpp
    test/test_fault_manager.cpp
    test/test_rules.cpp
  )
  target_link_libraries(jarvis_core_tests PRIVATE jarvis_core GTest::gtest_main)
  target_compile_definitions(jarvis_core_tests PRIVATE
    JARVIS_SCHEMA_EXAMPLES_DIR="${CMAKE_CURRENT_SOURCE_DIR}/../../schemas/examples"
    JARVIS_DOCS_DIR="${CMAKE_CURRENT_SOURCE_DIR}/../../docs"
  )
  include(GoogleTest)
  gtest_discover_tests(jarvis_core_tests DISCOVERY_MODE PRE_TEST)
endif()
