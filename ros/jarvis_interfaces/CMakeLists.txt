cmake_minimum_required(VERSION 3.22)
project(jarvis_interfaces)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(action_msgs REQUIRED)
find_package(builtin_interfaces REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  msg/Attribute.msg
  msg/Source.msg
  msg/Event.msg
  msg/Fault.msg
  msg/FaultArray.msg
  msg/GateResult.msg
  msg/StepProgress.msg
  msg/Execution.msg
  msg/ExecutionArray.msg
  msg/RuleStatus.msg
  msg/PlanInfo.msg
  srv/QueryEvents.srv
  srv/GetFaults.srv
  srv/AcknowledgeFault.srv
  srv/ClearFault.srv
  srv/ReloadConfig.srv
  srv/SetRuleState.srv
  srv/ListRules.srv
  srv/SetRecoveryMode.srv
  srv/DecideApproval.srv
  srv/CancelExecution.srv
  srv/CloseIncident.srv
  srv/ListPlans.srv
  action/ExecuteRecovery.action
  DEPENDENCIES action_msgs builtin_interfaces
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
