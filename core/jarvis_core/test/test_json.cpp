// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Contract test: the C++ codec reads and writes the same examples the JSON Schemas are validated
// against (schemas/tests), so C++ and the canonical contract can't drift apart.

#include <gtest/gtest.h>

#include <fstream>
#include <string>

#include "jarvis_core/fault_manager.hpp"
#include "jarvis_core/json.hpp"

using namespace jarvis_core;

namespace
{

nlohmann::json load_example(const std::string & name)
{
  std::ifstream in(std::string(JARVIS_SCHEMA_EXAMPLES_DIR) + "/" + name);
  EXPECT_TRUE(in.good()) << "missing example " << name;
  return nlohmann::json::parse(in);
}

}  // namespace

class EventExamples : public ::testing::TestWithParam<const char *>
{
};

TEST_P(EventExamples, RoundTripsThroughTheCodec)
{
  const nlohmann::json original = load_example(GetParam());
  const Event e = original.get<Event>();
  const nlohmann::json again = e;
  EXPECT_EQ(again.get<Event>(), e);
  // Every field present in the example survives unchanged (timestamps are re-rendered with 9 digits).
  for (const auto & [key, value] : original.items()) {
    if (key == "stamp" || key == "recv_stamp") {
      EXPECT_EQ(
        parse_iso8601(again.at(key).get<std::string>()), parse_iso8601(value.get<std::string>()));
    } else {
      EXPECT_EQ(again.at(key), value) << key;
    }
  }
}

INSTANTIATE_TEST_SUITE_P(
  Schemas, EventExamples,
  ::testing::Values("event.fault_raise.json", "event.minimal.json", "event.insight.json"));

TEST(EventJson, TypedAttributesKeepTheirTypes)
{
  const Event e = load_example("event.fault_raise.json").get<Event>();
  EXPECT_EQ(e.seq, 281474976711698ULL);
  EXPECT_EQ(e.kind, Kind::FaultRaise);
  EXPECT_EQ(std::get<std::string>(e.attrs.at("topic")), "/scan");
  EXPECT_DOUBLE_EQ(std::get<double>(e.attrs.at("expected_hz")), 10.5);
  EXPECT_EQ(std::get<std::int64_t>(e.attrs.at("silence_ms")), 1600);
  EXPECT_EQ(std::get<bool>(e.attrs.at("stale")), true);
}

TEST(EventJson, RejectsUnknownKind)
{
  nlohmann::json j = load_example("event.minimal.json");
  j["kind"] = "GOSSIP";
  EXPECT_THROW(j.get<Event>(), std::invalid_argument);
}

TEST(FaultJson, RoundTripsTheExample)
{
  const nlohmann::json original = load_example("fault.active.json");
  const FaultEpisode f = original.get<FaultEpisode>();
  EXPECT_EQ(f.state, FaultState::Active);
  EXPECT_EQ(f.source.component, "lidar");
  const nlohmann::json again = f;
  for (const auto & [key, value] : original.items()) {
    EXPECT_EQ(
      parse_iso8601(again.at(key).is_string() ? again.at(key).get<std::string>() : ""),
      parse_iso8601(value.is_string() ? value.get<std::string>() : ""))
      << key;
    if (!value.is_string() || !parse_iso8601(value.get<std::string>())) {
      EXPECT_EQ(again.at(key), value) << key;
    }
  }
}
