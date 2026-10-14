// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Core <-> ROS message conversions, plus REP-2007 type adapters so nodes publish and subscribe
// jarvis_core types directly (zero conversions between components with intra-process comms).

#pragma once

#include <builtin_interfaces/msg/time.hpp>
#include <jarvis_core/fault_manager.hpp>
#include <jarvis_core/orchestrator.hpp>
#include <jarvis_core/rule_engine.hpp>
#include <jarvis_core/types.hpp>
#include <jarvis_interfaces/msg/attribute.hpp>
#include <jarvis_interfaces/msg/event.hpp>
#include <jarvis_interfaces/msg/execution.hpp>
#include <jarvis_interfaces/msg/fault.hpp>
#include <jarvis_interfaces/msg/rule_status.hpp>
#include <jarvis_interfaces/msg/source.hpp>
#include <map>
#include <optional>
#include <rclcpp/type_adapter.hpp>
#include <string>
#include <utility>
#include <vector>

namespace jarvis_ros
{

builtin_interfaces::msg::Time to_msg_time(jarvis_core::Timestamp t);
jarvis_core::Timestamp from_msg_time(const builtin_interfaces::msg::Time & t);

std::vector<jarvis_interfaces::msg::Attribute> to_msg(const jarvis_core::Attributes & attrs);
jarvis_core::Attributes from_msg(const std::vector<jarvis_interfaces::msg::Attribute> & attrs);

jarvis_interfaces::msg::Source to_msg(const jarvis_core::Source & source);
jarvis_core::Source from_msg(const jarvis_interfaces::msg::Source & source);

void to_msg(const jarvis_core::Event & event, jarvis_interfaces::msg::Event & msg);
/// Kind and severity values are copied as-is; the event hub rejects unknown ones.
void from_msg(const jarvis_interfaces::msg::Event & msg, jarvis_core::Event & event);

jarvis_interfaces::msg::Fault to_msg(const jarvis_core::FaultEpisode & fault);
/// What the rule engine and the orchestrator need from a /jarvis/faults entry.
std::pair<std::string, jarvis_core::FaultView> to_view(const jarvis_interfaces::msg::Fault & fault);

/// ExecuteRecovery / Execution REQUESTER_* values. 0 (unset) reads as OPERATOR.
std::uint8_t to_msg(jarvis_core::Requester requester);
std::optional<jarvis_core::Requester> requester_from_msg(std::uint8_t value);
jarvis_interfaces::msg::Execution to_msg(const jarvis_core::Execution & execution);
/// Attribute values as plan arguments (their text).
std::map<std::string, std::string> args_from_msg(
  const std::vector<jarvis_interfaces::msg::Attribute> & attrs);

jarvis_interfaces::msg::RuleStatus to_msg(
  const jarvis_core::RuleStats & stats, const jarvis_core::Rule * rule);

}  // namespace jarvis_ros

template <>
struct rclcpp::TypeAdapter<jarvis_core::Event, jarvis_interfaces::msg::Event>
{
  using is_specialized = std::true_type;
  using custom_type = jarvis_core::Event;
  using ros_message_type = jarvis_interfaces::msg::Event;

  static void convert_to_ros_message(const custom_type & source, ros_message_type & destination)
  {
    jarvis_ros::to_msg(source, destination);
  }

  static void convert_to_custom(const ros_message_type & source, custom_type & destination)
  {
    jarvis_ros::from_msg(source, destination);
  }
};

namespace jarvis_ros
{
using AdaptedEvent = rclcpp::TypeAdapter<jarvis_core::Event, jarvis_interfaces::msg::Event>;
}  // namespace jarvis_ros
