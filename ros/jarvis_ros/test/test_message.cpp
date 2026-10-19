// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Messages of types known at run time: YAML/JSON in, JSON out, and the errors a plan's author sees.

#include <gtest/gtest.h>

#include <diagnostic_msgs/msg/diagnostic_array.hpp>
#include <lifecycle_msgs/srv/change_state.hpp>
#include <optional>
#include <rcl_interfaces/msg/parameter_value.hpp>
#include <string>

#include "jarvis_ros/message.hpp"

using jarvis_ros::MessageType;
using nlohmann::json;

TEST(Message, AServiceRequestFromYaml)
{
  auto [request, response] = MessageType::service("lifecycle_msgs/srv/ChangeState");
  EXPECT_EQ(request->name(), "lifecycle_msgs/srv/ChangeState_Request");
  auto msg = request->create();
  EXPECT_EQ(
    request->fill(msg.get(), {{"transition", {{"id", 3}, {"label", "activate"}}}}, "request"),
    std::nullopt);
  // The memory is the C++ message: the generic clients send it as it is.
  const auto & typed = *static_cast<lifecycle_msgs::srv::ChangeState::Request *>(msg.get());
  EXPECT_EQ(typed.transition.id, 3);
  EXPECT_EQ(typed.transition.label, "activate");
  EXPECT_EQ(
    request->to_json(msg.get()), json::parse(R"({"transition": {"id": 3, "label": "activate"}})"));
  EXPECT_EQ(response->default_expect(), json::parse(R"({"success": true})"));
  EXPECT_EQ(
    MessageType::message("rcl_interfaces/msg/ParameterValue")->default_expect(), json::object());

  // An empty response has no fields, not rosidl's placeholder.
  auto [empty_request, empty] = MessageType::service("std_srvs/srv/Empty");
  EXPECT_EQ(empty->to_json(empty->create().get()), json::object());
  EXPECT_EQ(empty->fields(), std::vector<std::string>{});
  EXPECT_EQ(
    empty_request->fill(empty_request->create().get(), {{"x", 1}}, "request").value_or("ok"),
    "request: std_srvs/srv/Empty_Request has no field 'x' (it has none)");
}

TEST(Message, ErrorsNameTheField)
{
  auto [request, response] = MessageType::service("lifecycle_msgs/srv/ChangeState");
  auto msg = request->create();
  auto fill = [&](const json & v) { return request->fill(msg.get(), v, "request").value_or("ok"); };
  EXPECT_EQ(
    fill({{"transition", {{"idd", 3}}}}),
    "request.transition: lifecycle_msgs/msg/Transition has no field 'idd' (it has: id, label)");
  EXPECT_EQ(
    fill({{"transition", {{"id", 300}}}}), "request.transition.id: 300 is out of range (0 to 255)");
  EXPECT_EQ(
    fill({{"transition", {{"id", -1}}}}), "request.transition.id: -1 is out of range (0 to 255)");
  EXPECT_EQ(
    fill({{"transition", {{"id", 2.5}}}}), "request.transition.id: expects an integer, not 2.5");
  EXPECT_EQ(
    fill({{"transition", {{"id", "x"}}}}), "request.transition.id: expects an integer, not \"x\"");
  EXPECT_EQ(
    fill({{"transition", {{"label", 4}}}}), "request.transition.label: expects a string, not 4");
  EXPECT_EQ(
    fill({{"transition", 5}}),
    "request.transition: expects the fields of lifecycle_msgs/msg/Transition, not 5");
  // A rendered template gives strings: numbers and booleans spelled out are taken.
  EXPECT_EQ(fill({{"transition", {{"id", "7"}}}}), "ok");
  EXPECT_EQ(static_cast<lifecycle_msgs::srv::ChangeState::Request *>(msg.get())->transition.id, 7);
}

TEST(Message, ArraysOfEveryKind)
{
  auto type = MessageType::message("rcl_interfaces/msg/ParameterValue");
  auto msg = type->create();
  const auto value = json::parse(R"({
    "type": 9, "bool_array_value": [true, false, true], "byte_array_value": [1, 255],
    "integer_array_value": [-5, "6"], "double_array_value": [1, 2.5], "string_array_value": ["a", "b"]
  })");
  ASSERT_EQ(type->fill(msg.get(), value, ""), std::nullopt);
  const auto & typed = *static_cast<rcl_interfaces::msg::ParameterValue *>(msg.get());
  EXPECT_EQ(typed.bool_array_value, (std::vector<bool>{true, false, true}));
  EXPECT_EQ(typed.integer_array_value, (std::vector<std::int64_t>{-5, 6}));
  const auto out = type->to_json(msg.get());
  EXPECT_EQ(out["bool_array_value"], json::parse("[true, false, true]"));
  EXPECT_EQ(out["byte_array_value"], json::parse("[1, 255]"));
  EXPECT_EQ(out["double_array_value"], json::parse("[1.0, 2.5]"));
  EXPECT_EQ(out["string_array_value"], json::parse(R"(["a", "b"])"));
  EXPECT_EQ(out["string_value"], "");
  EXPECT_EQ(
    type->fill(msg.get(), {{"byte_array_value", {1, 256}}}, "v").value_or("ok"),
    "v.byte_array_value[1]: 256 is out of range (0 to 255)");
  EXPECT_EQ(
    type->fill(msg.get(), {{"string_array_value", "a"}}, "v").value_or("ok"),
    "v.string_array_value: expects a list, not \"a\"");
}

TEST(Message, NestedMessagesAndFixedArrays)
{
  auto type = MessageType::message("diagnostic_msgs/msg/DiagnosticArray");
  auto msg = type->create();
  ASSERT_EQ(
    type->fill(
      msg.get(), json::parse(R"({"header": {"stamp": {"sec": 5}, "frame_id": "base"},
                      "status": [{"level": 2, "name": "lidar", "values": [{"key": "rate", "value": "0"}]}]})"),
      ""),
    std::nullopt);
  const auto & typed = *static_cast<diagnostic_msgs::msg::DiagnosticArray *>(msg.get());
  ASSERT_EQ(typed.status.size(), 1u);
  EXPECT_EQ(typed.status[0].level, 2);
  EXPECT_EQ(typed.status[0].values[0].key, "rate");
  EXPECT_EQ(typed.header.stamp.sec, 5);
  const auto out = type->to_json(msg.get());
  EXPECT_EQ(out["status"][0]["values"][0]["value"], "0");
  EXPECT_EQ(out["header"]["stamp"]["nanosec"], 0);

  // An action's goal request: a fixed-size UUID, then the goal.
  auto request = MessageType::message("jarvis_interfaces/action/Analyze_SendGoal_Request");
  auto goal = request->create();
  EXPECT_EQ(
    request->fill(goal.get(), {{"goal_id", {{"uuid", {1, 2, 3}}}}}, "").value_or("ok"),
    "goal_id.uuid: expects exactly 16 items, not 3");
  EXPECT_EQ(request->fill(goal.get(), {{"goal", {{"question", "why?"}}}}, "").value_or("ok"), "ok");
  EXPECT_EQ(request->to_json(goal.get())["goal"]["question"], "why?");
  EXPECT_EQ(request->to_json(goal.get())["goal_id"]["uuid"].size(), 16u);
}

TEST(Message, TemplatesWaitUntilRendered)
{
  auto type = MessageType::message("diagnostic_msgs/msg/DiagnosticArray");
  auto msg = type->create();
  const auto value =
    json::parse(R"({"status": [{"level": "{{ params.level }}", "name": "{{ params.name }}"}]})");
  EXPECT_EQ(type->fill(msg.get(), value, "", true), std::nullopt);
  EXPECT_NE(type->fill(msg.get(), value, ""), std::nullopt) << "not a number once it reaches ROS";
  EXPECT_NE(type->fill(msg.get(), {{"statuss", "{{ x }}"}}, "", true), std::nullopt)
    << "a field that doesn't exist is wrong whatever the value";
}

TEST(Message, UnknownTypes)
{
  try {
    MessageType::message("nope_msgs/msg/Thing");
    FAIL() << "expected an error";
  } catch (const std::runtime_error & e) {
    EXPECT_NE(std::string(e.what()).find("unknown type 'nope_msgs/msg/Thing'"), std::string::npos)
      << e.what();
  }
  EXPECT_THROW(MessageType::message("lifecycle_msgs/msg/Nope"), std::runtime_error);
  EXPECT_THROW(MessageType::service("lifecycle_msgs/Transition"), std::runtime_error);
}

TEST(Message, Expectations)
{
  const auto response =
    json::parse(R"({"success": false, "message": "busy", "status": {"code": 904},
                                         "ratio": 0.10000000149011612})");
  EXPECT_EQ(
    jarvis_ros::mismatch({{"success", true}}, response, "response"),
    "response.success is false, expected true");
  EXPECT_EQ(
    jarvis_ros::mismatch({{"status", {{"code", 0}}}}, response, "result"),
    "result.status.code is 904, expected 0");
  EXPECT_EQ(jarvis_ros::mismatch({{"nope", 1}}, response, "result"), "result.nope: no such field");
  EXPECT_EQ(jarvis_ros::mismatch({{"status", {{"code", "904"}}}}, response, "r"), std::nullopt)
    << "a rendered template";
  EXPECT_EQ(jarvis_ros::mismatch({{"ratio", 0.1}}, response, "r"), std::nullopt) << "a float32";
  EXPECT_EQ(jarvis_ros::mismatch(json::object(), response, "r"), std::nullopt);
  EXPECT_EQ(jarvis_ros::message_detail(response), "busy");
  EXPECT_EQ(jarvis_ros::message_detail({{"error_msg", "dock not found"}}), "dock not found");
  EXPECT_EQ(jarvis_ros::message_summary({{"a", std::string(300, 'x')}}).size(), 200u);
}
