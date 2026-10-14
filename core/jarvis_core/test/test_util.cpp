// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include <gtest/gtest.h>

#include <set>

#include "jarvis_core/types.hpp"
#include "jarvis_core/util.hpp"

using namespace jarvis_core;

TEST(Glob, MatchesShellStylePatterns)
{
  EXPECT_TRUE(glob_match("ros.topic.*", "ros.topic.stale"));
  EXPECT_TRUE(glob_match("*", ""));
  EXPECT_TRUE(glob_match("drive.*", "drive.motor.overcurrent"));
  EXPECT_TRUE(glob_match("*_comm_*", "motor_comm_fl"));
  EXPECT_TRUE(glob_match("lidar_?", "lidar_1"));
  EXPECT_FALSE(glob_match("lidar_?", "lidar_10"));
  EXPECT_FALSE(glob_match("nav.*", "navigation.lost"));
  EXPECT_TRUE(glob_match("/sensors/*/scan", "/sensors/front/scan"));
  EXPECT_FALSE(glob_match("abc", "abcd"));
}

TEST(Code, ValidatesDottedLowercase)
{
  EXPECT_TRUE(is_valid_code("ros.topic.stale"));
  EXPECT_TRUE(is_valid_code("power_battery_low"));
  EXPECT_TRUE(is_valid_code("a1.b2"));
  EXPECT_FALSE(is_valid_code(""));
  EXPECT_FALSE(is_valid_code("Ros.topic"));
  EXPECT_FALSE(is_valid_code("ros..topic"));
  EXPECT_FALSE(is_valid_code(".ros"));
  EXPECT_FALSE(is_valid_code("ros."));
  EXPECT_FALSE(is_valid_code("ros-topic"));
}

TEST(Duration, ParsesUnits)
{
  EXPECT_EQ(parse_duration("1s"), kSecond);
  EXPECT_EQ(parse_duration("250ms"), 250 * kMillisecond);
  EXPECT_EQ(parse_duration("1.5s"), 1500 * kMillisecond);
  EXPECT_EQ(parse_duration("2m"), 2 * kMinute);
  EXPECT_EQ(parse_duration("1h"), kHour);
  EXPECT_EQ(parse_duration("10"), 10 * kSecond);
  EXPECT_EQ(parse_duration(" 5us "), 5000);
  EXPECT_FALSE(parse_duration("abc"));
  EXPECT_FALSE(parse_duration("5 parsecs"));
  EXPECT_FALSE(parse_duration(""));
}

TEST(Iso8601, RoundTripsWithNanoseconds)
{
  const Timestamp t = 1'760'346'843'120'000'001;  // 2025-10-13T09:14:03.120000001Z
  const std::string s = format_iso8601(t);
  EXPECT_EQ(s, "2025-10-13T09:14:03.120000001Z");
  EXPECT_EQ(parse_iso8601(s), t);
  EXPECT_EQ(parse_iso8601("1970-01-01T00:00:12.5Z"), 12'500'000'000LL);
  EXPECT_EQ(parse_iso8601("2000-02-29T00:00:00Z"), 951'782'400LL * kSecond);
  EXPECT_EQ(format_iso8601(0), "1970-01-01T00:00:00.000000000Z");
  EXPECT_FALSE(parse_iso8601("2026-10-13 09:14:03Z"));
  EXPECT_FALSE(parse_iso8601("2026-13-13T09:14:03Z"));
  EXPECT_FALSE(parse_iso8601("2026-10-13T09:14:03.1234567890Z"));
}

TEST(Uuid7, IsWellFormedUniqueAndTimeOrdered)
{
  IdGenerator ids(42);
  std::set<std::string> seen;
  std::string previous;
  for (int i = 0; i < 1000; ++i) {
    const std::string id = ids.uuid7(1'760'000'000'000'000'000LL + i * kMillisecond);
    ASSERT_EQ(id.size(), 36u);
    EXPECT_EQ(id[14], '7') << id;                                                         // version
    EXPECT_TRUE(id[19] == '8' || id[19] == '9' || id[19] == 'a' || id[19] == 'b') << id;  // variant
    EXPECT_TRUE(seen.insert(id).second);
    EXPECT_GT(id, previous);  // later milliseconds sort later
    previous = id;
  }
}

TEST(Enums, ParseAndPrint)
{
  EXPECT_EQ(parse_severity("warning"), Severity::Warn);
  EXPECT_EQ(parse_severity("FATAL"), Severity::Critical);
  EXPECT_EQ(parse_severity("Error"), Severity::Error);
  EXPECT_FALSE(parse_severity("loud"));
  EXPECT_EQ(parse_kind("fault_raise"), Kind::FaultRaise);
  EXPECT_EQ(to_string(Kind::RuleMatch), "RULE_MATCH");
  EXPECT_FALSE(is_valid(Kind::Unspecified));
  EXPECT_TRUE(Severity::Error > Severity::Warn);
}
