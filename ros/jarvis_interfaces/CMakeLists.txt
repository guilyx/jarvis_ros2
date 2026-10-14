cmake_minimum_required(VERSION 3.22)
project(jarvis_interfaces)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(builtin_interfaces REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  msg/Attribute.msg
  msg/Source.msg
  msg/Event.msg
  msg/Fault.msg
  msg/FaultArray.msg
  srv/QueryEvents.srv
  srv/GetFaults.srv
  srv/AcknowledgeFault.srv
  srv/ClearFault.srv
  DEPENDENCIES builtin_interfaces
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
