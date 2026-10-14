// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include <gtest/gtest.h>

#include "jarvis_ros/conversions.hpp"

using namespace jarvis_core;

TEST(Conversions, TimeRoundTrips)
{
  const Timestamp t = 1'760'346'843'120'000'001LL;
  const auto msg = jarvis_ros::to_msg_time(t);
  EXPECT_EQ(msg.sec, 1'760'346'843);
  EXPECT_EQ(msg.nanosec, 120'000'001u);
  EXPECT_EQ(jarvis_ros::from_msg_time(msg), t);
  EXPECT_EQ(jarvis_ros::from_msg_time(jarvis_ros::to_msg_time(0)), 0);
}

TEST(Conversions, EventRoundTripsWithTypedAttributes)
{
  Event e;
  e.id = "01928f6e-7c1a-7b3e-9a51-3f0c2d4e5a61";
  e.seq = (7ull << 48) | 42;
  e.stamp = 1'000 * kSecond + 5;
  e.recv_stamp = 1'000 * kSecond + 9;
  e.source = {"tb4-sim", "/jarvis/bridges/topic_watchdog", "lidar", "devbox"};
  e.kind = Kind::FaultRaise;
  e.code = "ros.topic.stale";
  e.severity = Severity::Error;
  e.message = "/scan silent";
  e.attrs = {
    {"topic", std::string("/scan")},
    {"silence_ms", std::int64_t{1600}},
    {"expected_hz", 10.5},
    {"stale", true}};
  e.tags = {"perception"};
  e.payload = "{}";
  e.payload_schema = "x/v1";
  e.correlation_id = "inc";
  e.causation_id = "cause";

  jarvis_interfaces::msg::Event msg;
  jarvis_ros::to_msg(e, msg);
  EXPECT_EQ(msg.kind, jarvis_interfaces::msg::Event::KIND_FAULT_RAISE);
  EXPECT_EQ(msg.severity, jarvis_interfaces::msg::Event::SEVERITY_ERROR);
  ASSERT_EQ(msg.attrs.size(), 4u);

  Event back;
  jarvis_ros::from_msg(msg, back);
  EXPECT_EQ(back, e);
}

TEST(Conversions, KindAndSeverityConstantsMatchTheCore)
{
  using M = jarvis_interfaces::msg::Event;
  EXPECT_EQ(M::KIND_LOG, static_cast<uint8_t>(Kind::Log));
  EXPECT_EQ(M::KIND_SYSTEM, static_cast<uint8_t>(Kind::System));
  EXPECT_EQ(M::SEVERITY_DEBUG, static_cast<uint8_t>(Severity::Debug));
  EXPECT_EQ(M::SEVERITY_CRITICAL, static_cast<uint8_t>(Severity::Critical));
  using F = jarvis_interfaces::msg::Fault;
  EXPECT_EQ(F::STATE_PENDING, static_cast<uint8_t>(FaultState::Pending));
  EXPECT_EQ(F::STATE_CLEARED, static_cast<uint8_t>(FaultState::Cleared));
}

TEST(Conversions, FaultCarriesItsLifecycle)
{
  FaultEpisode f;
  f.fault_id = "f1";
  f.code = "safety.estop.engaged";
  f.state = FaultState::Latched;
  f.severity = Severity::Critical;
  f.occurrences = 2;
  f.latch = true;
  f.muted_by = "root";
  f.freeze_frame = {{"state.robot.mode", std::string("AUTONOMOUS")}};
  const auto msg = jarvis_ros::to_msg(f);
  EXPECT_EQ(msg.state, jarvis_interfaces::msg::Fault::STATE_LATCHED);
  EXPECT_EQ(msg.severity, jarvis_interfaces::msg::Event::SEVERITY_CRITICAL);
  EXPECT_EQ(msg.occurrences, 2u);
  EXPECT_TRUE(msg.latch);
  EXPECT_EQ(msg.muted_by, "root");
  ASSERT_EQ(msg.freeze_frame.size(), 1u);
  EXPECT_EQ(msg.freeze_frame[0].string_value, "AUTONOMOUS");
}
