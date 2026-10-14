// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include <gtest/gtest.h>

#include <jarvis_bridges/translators.hpp>
#include <jarvis_core/util.hpp>
#include <string>
#include <vector>

using namespace jarvis_bridges;
using action_msgs::msg::GoalStatus;
using action_msgs::msg::GoalStatusArray;
using diagnostic_msgs::msg::DiagnosticArray;
using diagnostic_msgs::msg::DiagnosticStatus;
using jarvis_core::Kind;
using jarvis_core::kSecond;
using rcl_interfaces::msg::Log;

namespace
{

DiagnosticArray diag(const std::string & name, uint8_t level, const std::string & hw = "")
{
  DiagnosticStatus s;
  s.name = name;
  s.level = level;
  s.hardware_id = hw;
  s.message = "msg";
  DiagnosticArray a;
  a.status.push_back(s);
  return a;
}

Log log_line(uint8_t level, const std::string & logger = "controller_server")
{
  Log l;
  l.level = level;
  l.name = logger;
  l.msg = "something went wrong";
  l.file = "controller_server.cpp";
  l.function = "computeControl";
  l.line = 42;
  l.stamp.sec = 100;
  return l;
}

GoalStatus goal(uint8_t id, int8_t status)
{
  GoalStatus g;
  g.goal_info.goal_id.uuid.fill(0);
  g.goal_info.goal_id.uuid[15] = id;
  g.status = status;
  return g;
}

GoalStatusArray goals(std::vector<GoalStatus> list)
{
  GoalStatusArray a;
  a.status_list = std::move(list);
  return a;
}

std::vector<std::string> codes(const std::vector<jarvis_core::Event> & events)
{
  std::vector<std::string> out;
  for (const auto & e : events) {
    out.push_back(e.code);
  }
  return out;
}

}  // namespace

// -- naming ---------------------------------------------------------------------------------------

TEST(Naming, ToCodeSplitsOnSeparatorsAndSnakeCasesTheRest)
{
  EXPECT_EQ(to_code("controller_server: Controller"), "controller_server.controller");
  EXPECT_EQ(to_code("Controller Server: /cmd_vel"), "controller_server.cmd_vel");
  EXPECT_EQ(to_code("/robot1/navigate_to_pose"), "robot1.navigate_to_pose");
  EXPECT_EQ(to_code("  Motor-Driver (left)  "), "motor_driver_left");
  EXPECT_EQ(to_code("a::b//c.."), "a.b.c");
  EXPECT_EQ(to_code("___"), "");
  EXPECT_EQ(to_code("Ünïcode"), "n_code");
  EXPECT_EQ(to_segment("CPU temp: core/0"), "cpu_temp_core_0");
}

TEST(Naming, MakeCodeIsAlwaysValidAndBounded)
{
  EXPECT_EQ(make_code("diag", ""), "diag.unnamed");
  EXPECT_EQ(make_code("diag", "lidar.status"), "diag.lidar.status");
  const std::string code = make_code("diag", std::string(200, 'a') + ".b", 20);
  EXPECT_LE(code.size(), 20u);
  EXPECT_TRUE(jarvis_core::is_valid_code(code)) << code;
  EXPECT_TRUE(jarvis_core::is_valid_code(make_code("diag", "abcdefghijklmn.", 20)));
  EXPECT_TRUE(jarvis_core::is_valid_code(make_code("diag", "abcdefghijklm_x", 19)));
}

TEST(Naming, ParseValueRestoresTypes)
{
  EXPECT_EQ(parse_value("42"), jarvis_core::AttrValue(int64_t{42}));
  EXPECT_EQ(parse_value("-7"), jarvis_core::AttrValue(int64_t{-7}));
  EXPECT_EQ(parse_value("12.5"), jarvis_core::AttrValue(12.5));
  EXPECT_EQ(parse_value("True"), jarvis_core::AttrValue(true));
  EXPECT_EQ(parse_value("false"), jarvis_core::AttrValue(false));
  EXPECT_EQ(parse_value("nan"), jarvis_core::AttrValue(std::string("nan")));
  EXPECT_EQ(parse_value("12 V"), jarvis_core::AttrValue(std::string("12 V")));
  EXPECT_EQ(parse_value(""), jarvis_core::AttrValue(std::string()));
}

TEST(Naming, UuidIsFormatted)
{
  std::array<uint8_t, 16> u{};
  u[0] = 0xab;
  u[15] = 0x01;
  EXPECT_EQ(uuid_to_string(u), "ab000000-0000-0000-0000-000000000001");
}

// -- diagnostics ----------------------------------------------------------------------------------

TEST(Diagnostics, LevelChangesBecomeRaisesAndClears)
{
  DiagnosticsTranslator t({});
  // First sighting of an OK status is a clear: it heals a fault restored after a restart.
  auto out = t.on_array(diag("lidar: driver", DiagnosticStatus::OK, "lidar0"), 1);
  ASSERT_EQ(out.size(), 1u);
  EXPECT_EQ(out[0].kind, Kind::FaultClear);
  EXPECT_EQ(out[0].code, "diag.lidar.driver");
  EXPECT_EQ(out[0].source.component, "lidar0");

  EXPECT_TRUE(t.on_array(diag("lidar: driver", DiagnosticStatus::OK, "lidar0"), 2).empty());

  out = t.on_array(diag("lidar: driver", DiagnosticStatus::WARN, "lidar0"), 3);
  ASSERT_EQ(out.size(), 1u);
  EXPECT_EQ(out[0].kind, Kind::FaultRaise);
  EXPECT_EQ(out[0].severity, Severity::Warn);
  EXPECT_EQ(out[0].message, "msg");

  EXPECT_TRUE(t.on_array(diag("lidar: driver", DiagnosticStatus::WARN, "lidar0"), 4).empty())
    << "steady states are not re-sent";

  out = t.on_array(diag("lidar: driver", DiagnosticStatus::ERROR, "lidar0"), 5);
  ASSERT_EQ(out.size(), 1u);
  EXPECT_EQ(out[0].severity, Severity::Error);

  out = t.on_array(diag("lidar: driver", DiagnosticStatus::STALE, "lidar0"), 6);
  ASSERT_EQ(out.size(), 1u);
  EXPECT_EQ(out[0].severity, Severity::Critical);

  out = t.on_array(diag("lidar: driver", DiagnosticStatus::OK, "lidar0"), 7);
  ASSERT_EQ(out.size(), 1u);
  EXPECT_EQ(out[0].kind, Kind::FaultClear);
  EXPECT_EQ(out[0].severity, Severity::Info);
}

TEST(Diagnostics, SameNameOnTwoHardwareIdsAreTwoStatuses)
{
  DiagnosticsTranslator t({});
  EXPECT_EQ(t.on_array(diag("motor", DiagnosticStatus::ERROR, "left"), 1).size(), 1u);
  EXPECT_EQ(t.on_array(diag("motor", DiagnosticStatus::ERROR, "right"), 2).size(), 1u);
}

TEST(Diagnostics, ComponentFallsBackToTheFirstNameSegment)
{
  DiagnosticsTranslator t({});
  const auto out = t.on_array(diag("controller_server: Controller", DiagnosticStatus::ERROR), 1);
  ASSERT_EQ(out.size(), 1u);
  EXPECT_EQ(out[0].source.component, "controller_server");
}

TEST(Diagnostics, IgnoresJarvisOwnExportAndConfiguredNames)
{
  DiagnosticsConfig config;
  config.ignore_names = {"*heartbeat*"};
  DiagnosticsTranslator t(config);
  EXPECT_TRUE(
    t.on_array(diag("fault_manager: sensor.stale", DiagnosticStatus::ERROR, "jarvis"), 1).empty());
  EXPECT_TRUE(t.on_array(diag("base: heartbeat", DiagnosticStatus::ERROR), 1).empty());
  EXPECT_EQ(t.on_array(diag("base: battery", DiagnosticStatus::ERROR), 1).size(), 1u);
}

TEST(Diagnostics, RepeatsSteadyFaultsWhenAsked)
{
  DiagnosticsConfig config;
  config.repeat_period = kSecond;
  DiagnosticsTranslator t(config);
  EXPECT_EQ(t.on_array(diag("x", DiagnosticStatus::ERROR), 0).size(), 1u);
  EXPECT_TRUE(t.on_array(diag("x", DiagnosticStatus::ERROR), kSecond / 2).empty());
  EXPECT_EQ(t.on_array(diag("x", DiagnosticStatus::ERROR), kSecond).size(), 1u);
  EXPECT_EQ(t.on_array(diag("x", DiagnosticStatus::ERROR), 0).size(), 1u) << "time went backwards";
  EXPECT_EQ(t.on_array(diag("x", DiagnosticStatus::OK), kSecond).size(), 1u);
  EXPECT_TRUE(t.on_array(diag("x", DiagnosticStatus::OK), 5 * kSecond).empty())
    << "OK is never repeated";
}

TEST(Diagnostics, ValuesBecomeTypedAttributesWithinTheHubLimit)
{
  DiagnosticsTranslator t({});
  auto array = diag("battery", DiagnosticStatus::WARN, "bms");
  array.header.stamp.sec = 7;
  for (const auto & [k, v] : std::vector<std::pair<std::string, std::string>>{
         {"Voltage (V)", "11.2"}, {"Cycles", "301"}, {"Charging", "False"}, {"Vendor", "ACME"}}) {
    diagnostic_msgs::msg::KeyValue kv;
    kv.key = k;
    kv.value = v;
    array.status[0].values.push_back(kv);
  }
  auto out = t.on_array(array, 99 * kSecond);
  ASSERT_EQ(out.size(), 1u);
  const auto & a = out[0].attrs;
  EXPECT_EQ(out[0].stamp, 7 * kSecond) << "the header stamp wins over now";
  EXPECT_EQ(a.at("voltage_v"), jarvis_core::AttrValue(11.2));
  EXPECT_EQ(a.at("cycles"), jarvis_core::AttrValue(int64_t{301}));
  EXPECT_EQ(a.at("charging"), jarvis_core::AttrValue(false));
  EXPECT_EQ(a.at("vendor"), jarvis_core::AttrValue(std::string("ACME")));
  EXPECT_EQ(a.at("diag.name"), jarvis_core::AttrValue(std::string("battery")));
  EXPECT_EQ(a.at("diag.level"), jarvis_core::AttrValue(int64_t{1}));

  auto big = diag("big", DiagnosticStatus::ERROR);
  for (int i = 0; i < 100; ++i) {
    diagnostic_msgs::msg::KeyValue kv;
    kv.key = "k" + std::to_string(i);
    kv.value = "v";
    big.status[0].values.push_back(kv);
  }
  out = t.on_array(big, 1);
  ASSERT_EQ(out.size(), 1u);
  EXPECT_LE(out[0].attrs.size(), 32u);
  EXPECT_EQ(out[0].attrs.at("jarvis.truncated"), jarvis_core::AttrValue(true));
  EXPECT_EQ(out[0].attrs.count("diag.name"), 1u) << "our own keys survive truncation";
}

// -- rosout ---------------------------------------------------------------------------------------

TEST(Rosout, LevelsMapToLogsAndFaults)
{
  RosoutTranslator t({});
  EXPECT_FALSE(t.on_log(log_line(Log::INFO), 1)) << "below min_level";

  auto warn = t.on_log(log_line(Log::WARN), 1);
  ASSERT_TRUE(warn);
  EXPECT_EQ(warn->kind, Kind::Log);
  EXPECT_EQ(warn->code, "ros.log.warn");
  EXPECT_EQ(warn->severity, Severity::Warn);
  EXPECT_EQ(warn->source.component, "controller_server");
  EXPECT_EQ(warn->stamp, 100 * kSecond);
  EXPECT_EQ(warn->attrs.at("log.line"), jarvis_core::AttrValue(int64_t{42}));

  auto error = t.on_log(log_line(Log::ERROR), 1);
  ASSERT_TRUE(error);
  EXPECT_EQ(error->kind, Kind::FaultRaise);
  EXPECT_EQ(error->code, "ros.log.error");
  EXPECT_EQ(error->message, "something went wrong");

  auto fatal = t.on_log(log_line(Log::FATAL), 1);
  ASSERT_TRUE(fatal);
  EXPECT_EQ(fatal->code, "ros.log.fatal");
  EXPECT_EQ(fatal->severity, Severity::Critical);
}

TEST(Rosout, FaultsAreThrottledPerLoggerButLogsAreNot)
{
  RosoutTranslator t({});
  EXPECT_TRUE(t.on_log(log_line(Log::ERROR), 0));
  EXPECT_FALSE(t.on_log(log_line(Log::ERROR), kSecond / 2));
  EXPECT_TRUE(t.on_log(log_line(Log::ERROR, "planner_server"), kSecond / 2)) << "other logger";
  EXPECT_TRUE(t.on_log(log_line(Log::ERROR), kSecond));
  EXPECT_TRUE(t.on_log(log_line(Log::WARN), kSecond));
  EXPECT_TRUE(t.on_log(log_line(Log::WARN), kSecond));
}

TEST(Rosout, SkipsJarvisOwnLoggersAndHonoursLevels)
{
  RosoutConfig config;
  config.min_level = Severity::Error;
  config.fault_level = Severity::Critical;
  RosoutTranslator t(config);
  EXPECT_FALSE(t.on_log(log_line(Log::ERROR, "jarvis.event_hub"), 1));
  EXPECT_FALSE(t.on_log(log_line(Log::WARN), 1));
  auto error = t.on_log(log_line(Log::ERROR), 1);
  ASSERT_TRUE(error);
  EXPECT_EQ(error->kind, Kind::Log) << "below fault_level";
}

// -- action status --------------------------------------------------------------------------------

TEST(ActionStatus, FirstArrayIsABaseline)
{
  ActionStatusTranslator t;
  EXPECT_TRUE(
    t.on_status("/navigate_to_pose", goals({goal(1, GoalStatus::STATUS_ABORTED)}), 1).empty());
}

TEST(ActionStatus, AbortRaisesAndTheNextSuccessClears)
{
  ActionStatusTranslator t;
  const std::string action = "/navigate_to_pose";
  t.on_status(action, goals({}), 0);

  auto out = t.on_status(action, goals({goal(1, GoalStatus::STATUS_EXECUTING)}), 1);
  EXPECT_EQ(codes(out), std::vector<std::string>{"ros.action.navigate_to_pose.status"});
  EXPECT_EQ(out[0].kind, Kind::State);
  EXPECT_EQ(out[0].attrs.at("value"), jarvis_core::AttrValue(std::string("EXECUTING")));
  EXPECT_EQ(out[0].source.component, action);

  EXPECT_TRUE(t.on_status(action, goals({goal(1, GoalStatus::STATUS_EXECUTING)}), 2).empty());

  out = t.on_status(action, goals({goal(1, GoalStatus::STATUS_ABORTED)}), 3);
  EXPECT_EQ(
    codes(out), (std::vector<std::string>{
                  "ros.action.navigate_to_pose.status", "ros.action.navigate_to_pose.aborted"}));
  EXPECT_EQ(out[1].kind, Kind::FaultRaise);
  EXPECT_EQ(out[1].severity, Severity::Error);
  EXPECT_EQ(
    out[1].attrs.at("goal_id"),
    jarvis_core::AttrValue(uuid_to_string(goal(1, 0).goal_info.goal_id.uuid)));

  out = t.on_status(
    action, goals({goal(1, GoalStatus::STATUS_ABORTED), goal(2, GoalStatus::STATUS_SUCCEEDED)}), 4);
  ASSERT_EQ(out.size(), 2u);
  EXPECT_EQ(out[1].kind, Kind::FaultClear);
  EXPECT_EQ(out[1].code, "ros.action.navigate_to_pose.aborted");

  // Once cleared, more successes don't repeat the clear.
  out = t.on_status(action, goals({goal(3, GoalStatus::STATUS_SUCCEEDED)}), 5);
  EXPECT_EQ(codes(out), std::vector<std::string>{"ros.action.navigate_to_pose.status"});
}

TEST(ActionStatus, FirstSuccessClearsOnceToHealRestoredFaults)
{
  ActionStatusTranslator t;
  t.on_status("/follow_path", goals({}), 0);
  auto out = t.on_status("/follow_path", goals({goal(1, GoalStatus::STATUS_SUCCEEDED)}), 1);
  ASSERT_EQ(out.size(), 2u);
  EXPECT_EQ(out[1].kind, Kind::FaultClear);
}

TEST(ActionStatus, ActionsAreTrackedSeparately)
{
  ActionStatusTranslator t;
  t.on_status("/a", goals({}), 0);
  t.on_status("/robot2/b", goals({}), 0);
  EXPECT_EQ(t.on_status("/a", goals({goal(1, GoalStatus::STATUS_ABORTED)}), 1).size(), 2u);
  const auto out = t.on_status("/robot2/b", goals({goal(1, GoalStatus::STATUS_ABORTED)}), 1);
  ASSERT_EQ(out.size(), 2u);
  EXPECT_EQ(out[1].code, "ros.action.robot2.b.aborted");
}
