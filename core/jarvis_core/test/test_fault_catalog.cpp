// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include <gtest/gtest.h>

#include "jarvis_core/fault_catalog.hpp"

using namespace jarvis_core;

namespace
{

constexpr const char * kCatalog = R"yaml(
schema: jarvis.faults/v1
defaults:
  severity: ERROR
  debounce: immediate
  heal: { on_clear: true, after: 30s }
  latch: false
faults:
  - code: ros.topic.stale
    description: A watched topic stopped publishing.
    debounce: { persist: 1s }
    capture: { freeze_frame: [/odom, /cmd_vel], blackbox: true }
    runbook: runbooks/ros.topic.stale.md
  - code: drive.motor.overcurrent
    severity: WARN
    debounce: { counter: { confirm_at: -3, heal_at: 3 } }
    heal: { after: 60s }
  - code: nav.goal.aborted
    debounce: { count: 3, within: 5m }
    incident: never
  - code: safety.estop.engaged
    severity: CRITICAL
    latch: true
    incident: always
correlation:
  - id: estop-cascade
    root: [safety.estop.engaged]
    symptoms: ["drive.*", "nav.*"]
    window: 1s
)yaml";

}  // namespace

TEST(FaultCatalog, ParsesEveryPolicyField)
{
  const auto catalog = FaultCatalog::from_yaml(kCatalog);
  EXPECT_EQ(catalog.size(), 4u);

  const auto stale = catalog.policy_for("ros.topic.stale");
  EXPECT_TRUE(stale.cataloged);
  EXPECT_EQ(stale.severity, Severity::Error);
  EXPECT_EQ(stale.debounce.mode, DebouncePolicy::Mode::Persist);
  EXPECT_EQ(stale.debounce.persist, kSecond);
  EXPECT_EQ(stale.heal.after, 30 * kSecond) << "inherits defaults";
  EXPECT_TRUE(stale.capture.blackbox);
  EXPECT_EQ(stale.capture.freeze_frame_topics, (std::vector<std::string>{"/odom", "/cmd_vel"}));
  EXPECT_EQ(stale.runbook, "runbooks/ros.topic.stale.md");

  const auto motor = catalog.policy_for("drive.motor.overcurrent");
  EXPECT_EQ(motor.severity, Severity::Warn);
  EXPECT_EQ(motor.debounce.mode, DebouncePolicy::Mode::Counter);
  EXPECT_EQ(motor.debounce.confirm_at, -3);
  EXPECT_EQ(motor.debounce.heal_at, 3);
  EXPECT_EQ(motor.heal.after, kMinute);
  EXPECT_TRUE(motor.heal.on_clear);

  const auto aborted = catalog.policy_for("nav.goal.aborted");
  EXPECT_EQ(aborted.debounce.mode, DebouncePolicy::Mode::Count);
  EXPECT_EQ(aborted.debounce.count, 3);
  EXPECT_EQ(aborted.debounce.within, 5 * kMinute);
  EXPECT_EQ(aborted.incident, IncidentPolicy::Never);

  const auto estop = catalog.policy_for("safety.estop.engaged");
  EXPECT_TRUE(estop.latch);
  EXPECT_EQ(estop.incident, IncidentPolicy::Always);

  ASSERT_EQ(catalog.correlation().size(), 1u);
  const auto & rule = catalog.correlation().front();
  EXPECT_TRUE(rule.is_root("safety.estop.engaged"));
  EXPECT_TRUE(rule.is_symptom("drive.motor.fault"));
  EXPECT_TRUE(rule.is_symptom("nav.controller.failed"));
  EXPECT_FALSE(rule.is_symptom("ros.topic.stale"));
  EXPECT_EQ(rule.window, kSecond);
  EXPECT_TRUE(rule.mute_symptoms);
  EXPECT_TRUE(rule.clear_with_root);
}

TEST(FaultCatalog, UnknownCodesGetDefaults)
{
  const auto catalog = FaultCatalog::from_yaml(kCatalog);
  const auto p = catalog.policy_for("something.new");
  EXPECT_FALSE(p.cataloged);
  EXPECT_EQ(p.code, "something.new");
  EXPECT_EQ(p.debounce.mode, DebouncePolicy::Mode::Immediate);
  EXPECT_EQ(p.heal.after, 30 * kSecond);
}

TEST(FaultCatalog, EmptyDocumentIsValid) { EXPECT_EQ(FaultCatalog::from_yaml("").size(), 0u); }

TEST(FaultCatalog, ErrorsNameThePath)
{
  struct Case
  {
    const char * yaml;
    const char * expected;
  };
  const Case cases[] = {
    {"faults:\n  - code: Bad.Code\n", "faults[0].code"},
    {"faults:\n  - code: a.b\n    debounce: { persist: forever }\n", "faults[0].debounce.persist"},
    {"faults:\n  - code: a.b\n    severity: LOUD\n", "faults[0].severity"},
    {"faults:\n  - code: a.b\n    lacth: true\n", "faults[0].lacth"},
    {"faults:\n  - code: a.b\n  - code: a.b\n", "duplicate"},
    {"faults:\n  - code: a.b\n    debounce: { count: 2 }\n", "within"},
    {"faults:\n  - code: a.b\n    debounce: { counter: { confirm_at: 2 } }\n", "negative"},
    {"schema: jarvis.faults/v9\n", "schema"},
    {"correlation:\n  - id: x\n    root: [a]\n", "symptoms"},
    {"faults: [\n", "invalid YAML"},
  };
  for (const auto & c : cases) {
    try {
      FaultCatalog::from_yaml(c.yaml);
      ADD_FAILURE() << "expected an error for:\n" << c.yaml;
    } catch (const CatalogError & e) {
      EXPECT_NE(std::string(e.what()).find(c.expected), std::string::npos) << "got: " << e.what();
    }
  }
}
