// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// ROS messages of a type known only at run time, by name ("lifecycle_msgs/msg/Transition"),
// through their C++ introspection type support: a plan's YAML in, JSON out. The generic actuators
// (service.call, action.goal) build requests and goals with it and read responses and results.
//
//   auto [request, response] = MessageType::service("lifecycle_msgs/srv/ChangeState");
//   auto msg = request->create();
//   request->fill(msg.get(), {{"transition", {{"label", "activate"}}}}, "request");  // error, if any
//   response->to_json(reply)  ->  {"success": true}
//
// Unknown fields, values of the wrong type, out-of-range integers and arrays of the wrong length
// are errors that name the field ("request.transition: lifecycle_msgs/msg/Transition has no field
// 'idd' (it has: id, label)"). Fields left out keep the defaults of the .msg.

#pragma once

#include <memory>
#include <nlohmann/json.hpp>
#include <optional>
#include <rcpputils/shared_library.hpp>
#include <rosidl_typesupport_introspection_cpp/message_introspection.hpp>
#include <string>
#include <utility>
#include <vector>

namespace jarvis_ros
{

class MessageType : public std::enable_shared_from_this<MessageType>
{
public:
  using Ptr = std::shared_ptr<const MessageType>;
  using Members = rosidl_typesupport_introspection_cpp::MessageMembers;

  /// A message type: "pkg/msg/Name", or an action's part ("pkg/action/Name_Goal"). Throws
  /// std::runtime_error (an unknown package or type) with a message fit for a plan's author.
  static Ptr message(const std::string & type);

  /// A service type's request and response: "pkg/srv/Name", or an action's part
  /// ("pkg/action/Name_SendGoal").
  static std::pair<Ptr, Ptr> service(const std::string & type);

  MessageType(std::shared_ptr<rcpputils::SharedLibrary> library, const Members * members);

  /// "lifecycle_msgs/msg/Transition".
  std::string name() const;
  std::vector<std::string> fields() const;
  const Members * members() const { return members_; }

  /// A message with its defaults, released through its type support (it keeps the type loaded).
  std::shared_ptr<void> create() const;

  /// Sets the fields `value` names; `path` prefixes errors ("request"). With `templates`, strings
  /// holding `{{ }}` stand for any value (a plan's arguments before they are rendered).
  std::optional<std::string> fill(
    void * message, const nlohmann::json & value, const std::string & path,
    bool templates = false) const;

  nlohmann::json to_json(const void * message) const;

  /// The expectation when a plan gives none: `success: true` if there is a boolean `success`.
  nlohmann::json default_expect() const;

private:
  std::shared_ptr<rcpputils::SharedLibrary> library_;
  const Members * members_;
};

/// Whether `actual` (a message as JSON) holds the fields `expected` gives, or what differs
/// ("result.error_code is 904, expected 0"). Numbers compare as numbers; a string expected
/// matches a number or boolean that prints the same (a rendered template).
std::optional<std::string> mismatch(
  const nlohmann::json & expected, const nlohmann::json & actual, const std::string & path);

/// What a response says about itself: its `message`, `error_msg`, `error_message` or `reason`.
std::string message_detail(const nlohmann::json & message);

/// A message as compact JSON, cut to `max` characters.
std::string message_summary(const nlohmann::json & message, std::size_t max = 200);

}  // namespace jarvis_ros
