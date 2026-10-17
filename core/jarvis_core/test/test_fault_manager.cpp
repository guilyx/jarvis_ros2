// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include <gtest/gtest.h>

#include <algorithm>
#include <string>
#include <vector>

#include "jarvis_core/fault_manager.hpp"
#include "jarvis_core/json.hpp"

using namespace jarvis_core;

namespace
{

constexpr Timestamp kT0 = 1'000'000 * kSecond;

constexpr const char * kCatalog = R"yaml(
schema: jarvis.faults/v1
defaults:
  severity: ERROR
  debounce: immediate
  heal: { on_clear: true }
faults:
  - code: ros.topic.stale
    debounce: { persist: 1s }
    capture: { blackbox: true }
  - code: nav.goal.aborted
    debounce: { count: 3, within: 5m }
  - code: drive.motor.overcurrent
    severity: WARN
    debounce: { counter: { confirm_at: -3, heal_at: 3 } }
  - code: power.battery.low
    heal: { on_clear: false, after: 30s }
  - code: safety.estop.engaged
    severity: CRITICAL
    latch: true
    incident: always
  - code: drive.motor.fault
  - code: nav.controller.failed
correlation:
  - id: estop-cascade
    root: [safety.estop.engaged]
    symptoms: ["drive.*", "nav.controller.*"]
    window: 1s
)yaml";

class FaultManagerTest : public ::testing::Test
{
protected:
  FaultManagerTest() : fm_(FaultCatalog::from_yaml(kCatalog), config(), 7) {}

  static FaultManagerConfig config()
  {
    FaultManagerConfig c;
    c.robot_id = "tb4-sim";
    return c;
  }

  static Event report(
    Kind kind, const std::string & code, const std::string & component = "lidar",
    Severity severity = Severity::Error, Attributes attrs = {})
  {
    static int counter = 0;
    Event e;
    e.id = "evt-" + std::to_string(++counter);
    e.kind = kind;
    e.code = code;
    e.severity = severity;
    e.source.robot = "tb4-sim";
    e.source.node = "/jarvis/bridges/watchdog";
    e.source.component = component;
    e.attrs = std::move(attrs);
    return e;
  }

  Effects raise(
    const std::string & code, Timestamp at, const std::string & component = "lidar",
    Severity severity = Severity::Error, Attributes attrs = {})
  {
    last_event_ = report(Kind::FaultRaise, code, component, severity, std::move(attrs));
    return fm_.on_event(last_event_, at);
  }

  Effects clear(const std::string & code, Timestamp at, const std::string & component = "lidar")
  {
    last_event_ = report(Kind::FaultClear, code, component);
    return fm_.on_event(last_event_, at);
  }

  static std::vector<Event> emitted(const Effects & fx)
  {
    std::vector<Event> out;
    for (const auto & f : fx) {
      if (const auto * e = std::get_if<EmitEvent>(&f)) {
        out.push_back(e->event);
      }
    }
    return out;
  }

  static std::vector<std::string> fault_states(const Effects & fx, const std::string & code)
  {
    std::vector<std::string> out;
    for (const auto & e : emitted(fx)) {
      if (e.kind == Kind::FaultState && e.code == code) {
        out.push_back(*attr_string(e.attrs, "state"));
      }
    }
    return out;
  }

  static std::vector<std::string> codes(const Effects & fx)
  {
    std::vector<std::string> out;
    for (const auto & e : emitted(fx)) {
      out.push_back(e.code);
    }
    return out;
  }

  template <typename T>
  static bool has(const Effects & fx)
  {
    return std::any_of(
      fx.begin(), fx.end(), [](const Effect & f) { return std::holds_alternative<T>(f); });
  }

  const FaultEpisode & only_open()
  {
    open_ = fm_.faults();
    EXPECT_EQ(open_.size(), 1u);
    return open_.front();
  }

  FaultManager fm_;
  Event last_event_;
  std::vector<FaultEpisode> open_;
};

using V = std::vector<std::string>;

}  // namespace

TEST_F(FaultManagerTest, ImmediateDebounceConfirmsOnFirstRaise)
{
  const auto fx = raise("something.broke", kT0);
  EXPECT_EQ(fault_states(fx, "something.broke"), V{"ACTIVE"});
  EXPECT_TRUE(has<FaultsChanged>(fx));
  EXPECT_TRUE(has<PersistState>(fx));
  const auto c = codes(fx);
  EXPECT_EQ(std::count(c.begin(), c.end(), "jarvis.incident.opened"), 1);

  const auto & ep = only_open();
  EXPECT_EQ(ep.state, FaultState::Active);
  EXPECT_EQ(ep.occurrences, 1u);
  EXPECT_EQ(ep.confirmed_at, kT0);
  EXPECT_FALSE(ep.cataloged);
  EXPECT_FALSE(ep.incident_id.empty());
}

TEST_F(FaultManagerTest, TransitionEventsCarryContext)
{
  const auto fx = raise(
    "something.broke", kT0, "lidar", Severity::Error,
    {{"node", std::string("/demo/scan_relay")}, {"silence_ms", std::int64_t{1600}}});
  const auto events = emitted(fx);
  const auto it = std::find_if(
    events.begin(), events.end(), [](const Event & e) { return e.kind == Kind::FaultState; });
  ASSERT_NE(it, events.end());
  const Event & e = *it;
  EXPECT_EQ(e.source.node, "/jarvis/fault_manager");
  EXPECT_EQ(e.source.component, "lidar");
  EXPECT_EQ(e.source.robot, "tb4-sim");
  EXPECT_EQ(e.causation_id, last_event_.id);
  EXPECT_EQ(e.correlation_id, only_open().incident_id);
  EXPECT_EQ(e.stamp, kT0);
  EXPECT_EQ(*attr_string(e.attrs, "node"), "/demo/scan_relay");
  EXPECT_EQ(attr_int(e.attrs, "silence_ms"), 1600);
  EXPECT_EQ(*attr_string(e.attrs, "prev_state"), "NONE");
  EXPECT_EQ(*attr_string(e.attrs, "fault_id"), only_open().fault_id);
  EXPECT_EQ(std::get<bool>(e.attrs.at("uncataloged")), true);
}

TEST_F(FaultManagerTest, PersistDebounceConfirmsOnDeadline)
{
  auto fx = raise("ros.topic.stale", kT0);
  EXPECT_EQ(fault_states(fx, "ros.topic.stale"), V{"PENDING"});
  EXPECT_FALSE(has<CaptureBlackbox>(fx));
  EXPECT_EQ(fm_.next_deadline(), kT0 + kSecond);

  EXPECT_TRUE(emitted(fm_.advance_to(kT0 + 500 * kMillisecond)).empty());
  fx = fm_.advance_to(kT0 + 2 * kSecond);
  EXPECT_EQ(fault_states(fx, "ros.topic.stale"), V{"ACTIVE"});
  EXPECT_TRUE(has<CaptureBlackbox>(fx)) << "catalog asks for a black-box snapshot";
  EXPECT_EQ(only_open().confirmed_at, kT0 + kSecond) << "confirms at the deadline, not at now";
}

TEST_F(FaultManagerTest, PersistDebounceDiscardsIfClearedEarly)
{
  raise("ros.topic.stale", kT0);
  const auto fx = clear("ros.topic.stale", kT0 + 300 * kMillisecond);
  ASSERT_EQ(fault_states(fx, "ros.topic.stale"), V{"CLEARED"});
  const auto e = emitted(fx).front();
  EXPECT_EQ(std::get<bool>(e.attrs.at("confirmed")), false);
  EXPECT_TRUE(fm_.faults().empty());
  EXPECT_TRUE(fm_.incidents().empty()) << "unconfirmed faults never open incidents";
  EXPECT_FALSE(fm_.next_deadline());
}

TEST_F(FaultManagerTest, CountDebounceNeedsEnoughRaisesInTheWindow)
{
  EXPECT_EQ(fault_states(raise("nav.goal.aborted", kT0, "nav"), "nav.goal.aborted"), V{"PENDING"});
  EXPECT_TRUE(
    fault_states(raise("nav.goal.aborted", kT0 + kMinute, "nav"), "nav.goal.aborted").empty());
  EXPECT_EQ(
    fault_states(raise("nav.goal.aborted", kT0 + 2 * kMinute, "nav"), "nav.goal.aborted"),
    V{"ACTIVE"});
}

TEST_F(FaultManagerTest, CountDebounceExpires)
{
  raise("nav.goal.aborted", kT0, "nav");
  raise("nav.goal.aborted", kT0 + 4 * kMinute, "nav");
  // The first raise falls out of the 5-minute window; one raise remains, still pending.
  EXPECT_TRUE(fault_states(fm_.advance_to(kT0 + 5 * kMinute), "nav.goal.aborted").empty());
  EXPECT_EQ(only_open().state, FaultState::Pending);
  EXPECT_EQ(only_open().raise_times.size(), 1u);
  // Then the second one ages out too.
  const auto fx = fm_.advance_to(kT0 + 10 * kMinute);
  EXPECT_EQ(fault_states(fx, "nav.goal.aborted"), V{"CLEARED"});
  EXPECT_TRUE(fm_.faults().empty());
}

TEST_F(FaultManagerTest, CounterDebounceToleratesFlapping)
{
  Timestamp t = kT0;
  const auto step = [&](bool failed) {
    t += kSecond;
    return failed ? raise("drive.motor.overcurrent", t, "left_wheel", Severity::Warn)
                  : clear("drive.motor.overcurrent", t, "left_wheel");
  };
  step(true);   // -1
  step(true);   // -2
  step(false);  // -1
  step(true);   // -2
  EXPECT_EQ(only_open().state, FaultState::Pending);
  EXPECT_EQ(fault_states(step(true), "drive.motor.overcurrent"), V{"ACTIVE"});  // -3

  for (int i = 0; i < 5; ++i) {
    EXPECT_TRUE(fault_states(step(false), "drive.motor.overcurrent").empty()) << i;
  }
  EXPECT_EQ(fault_states(step(false), "drive.motor.overcurrent"), V{"CLEARED"});
}

TEST_F(FaultManagerTest, HealsOnClearAndResolvesTheIncident)
{
  raise("something.broke", kT0);
  const auto fx = clear("something.broke", kT0 + kSecond);
  EXPECT_EQ(fault_states(fx, "something.broke"), V{"CLEARED"});
  const auto c = codes(fx);
  EXPECT_NE(std::find(c.begin(), c.end(), "jarvis.incident.resolved"), c.end());
  EXPECT_TRUE(fm_.faults().empty());
  ASSERT_EQ(fm_.faults(true).size(), 1u);
  EXPECT_EQ(fm_.faults(true).front().state, FaultState::Cleared);
}

TEST_F(FaultManagerTest, HealsAfterSilenceAndRepeatsExtendIt)
{
  raise("power.battery.low", kT0, "battery");
  EXPECT_TRUE(
    fault_states(clear("power.battery.low", kT0 + kSecond, "battery"), "power.battery.low").empty())
    << "on_clear is off for this code";
  raise("power.battery.low", kT0 + 20 * kSecond, "battery");
  EXPECT_TRUE(emitted(fm_.advance_to(kT0 + 40 * kSecond)).empty()) << "repeat pushed the deadline";
  EXPECT_EQ(fault_states(fm_.advance_to(kT0 + 50 * kSecond), "power.battery.low"), V{"CLEARED"});
}

TEST_F(FaultManagerTest, LatchedFaultsWaitForAnOperator)
{
  auto fx = raise("safety.estop.engaged", kT0, "base", Severity::Critical);
  EXPECT_EQ(fault_states(fx, "safety.estop.engaged"), V{"ACTIVE"});

  fx = clear("safety.estop.engaged", kT0 + kSecond, "base");
  EXPECT_EQ(fault_states(fx, "safety.estop.engaged"), V{"LATCHED"});
  EXPECT_EQ(only_open().state, FaultState::Latched);

  fx = raise("safety.estop.engaged", kT0 + 2 * kSecond, "base", Severity::Critical);
  EXPECT_EQ(fault_states(fx, "safety.estop.engaged"), V{"ACTIVE"});
  EXPECT_EQ(only_open().occurrences, 2u);
  clear("safety.estop.engaged", kT0 + 3 * kSecond, "base");

  const std::string id = only_open().fault_id;
  const auto r = fm_.clear(id, "alice", "area checked", false, kT0 + 4 * kSecond);
  ASSERT_TRUE(r.success) << r.message;
  const auto events = emitted(r.effects);
  ASSERT_GE(events.size(), 2u);
  EXPECT_EQ(events[0].kind, Kind::Operator);
  EXPECT_EQ(events[0].code, "fault.clear");
  EXPECT_EQ(*attr_string(events[0].attrs, "user"), "alice");
  EXPECT_EQ(fault_states(r.effects, "safety.estop.engaged"), V{"CLEARED"});
  EXPECT_EQ(events[1].causation_id, events[0].id);
  EXPECT_EQ(std::get<bool>(events[1].attrs.at("forced")), true);
  EXPECT_TRUE(fm_.faults().empty());

  EXPECT_FALSE(fm_.clear(id, "alice", "", false, kT0 + 5 * kSecond).success) << "already cleared";
}

TEST_F(FaultManagerTest, CriticalReportsSkipDebounce)
{
  const auto fx = raise("ros.topic.stale", kT0, "lidar", Severity::Critical);
  EXPECT_EQ(fault_states(fx, "ros.topic.stale"), V{"ACTIVE"});
}

TEST_F(FaultManagerTest, ReportersCanEscalateButNotDowngradeCatalogSeverity)
{
  raise("drive.motor.overcurrent", kT0, "w", Severity::Debug);
  EXPECT_EQ(only_open().severity, Severity::Warn);
  raise("drive.motor.overcurrent", kT0 + 1, "w", Severity::Error);
  EXPECT_EQ(only_open().severity, Severity::Error);
}

TEST_F(FaultManagerTest, AcknowledgeIsAuditedAndIdempotent)
{
  raise("something.broke", kT0);
  const std::string id = only_open().fault_id;
  auto r = fm_.acknowledge(id, "bob", "looking", kT0 + 1);
  ASSERT_TRUE(r.success);
  EXPECT_EQ(codes(r.effects), V{"fault.acknowledge"});
  EXPECT_TRUE(only_open().acknowledged);
  EXPECT_EQ(only_open().acknowledged_by, "bob");
  r = fm_.acknowledge(id, "carol", "", kT0 + 2);
  EXPECT_TRUE(r.success);
  EXPECT_TRUE(emitted(r.effects).empty());
  EXPECT_FALSE(fm_.acknowledge("nope", "bob", "", kT0).success);
}

TEST_F(FaultManagerTest, SymptomsOfARootCauseAreMutedAndClearedWithIt)
{
  raise("safety.estop.engaged", kT0, "base", Severity::Critical);
  const std::string root = fm_.faults().front().fault_id;
  const std::string incident = fm_.faults().front().incident_id;

  const auto fx = raise("drive.motor.fault", kT0 + 300 * kMillisecond, "left_wheel");
  const auto ev = emitted(fx);
  EXPECT_EQ(codes(fx), (V{"drive.motor.fault", "jarvis.fault.muted"}))
    << "no separate incident for a symptom";
  ASSERT_EQ(ev.size(), 2u);
  EXPECT_EQ(*attr_string(ev[0].attrs, "muted_by"), root);
  EXPECT_EQ(ev[0].correlation_id, incident);
  EXPECT_EQ(*attr_string(ev[1].attrs, "muted_by"), root);
  EXPECT_EQ(ev[1].message, "drive.motor.fault muted as a symptom of safety.estop.engaged");

  const auto faults = fm_.faults();
  const auto sym = std::find_if(faults.begin(), faults.end(), [](const FaultEpisode & f) {
    return f.code == "drive.motor.fault";
  });
  ASSERT_NE(sym, faults.end());
  EXPECT_EQ(sym->muted_by, root);
  EXPECT_EQ(sym->incident_id, incident);
  EXPECT_EQ(fm_.find_incident(incident)->fault_ids.size(), 2u);

  const auto r = fm_.clear(root, "alice", "reset", false, kT0 + kSecond);
  ASSERT_TRUE(r.success);
  EXPECT_EQ(r.cleared_symptoms, V{"drive.motor.fault"});
  EXPECT_TRUE(fm_.faults().empty());
}

TEST_F(FaultManagerTest, SymptomsOutsideTheWindowStandAlone)
{
  raise("safety.estop.engaged", kT0, "base", Severity::Critical);
  raise("drive.motor.fault", kT0 + 5 * kSecond, "left_wheel");
  for (const auto & f : fm_.faults()) {
    EXPECT_TRUE(f.muted_by.empty()) << f.code;
  }
  EXPECT_EQ(fm_.incidents().size(), 2u);
}

TEST_F(FaultManagerTest, ARootArrivingJustAfterItsSymptomMutesItRetroactively)
{
  raise("nav.controller.failed", kT0, "controller_server");
  ASSERT_EQ(fm_.incidents().size(), 1u);
  const auto fx =
    raise("safety.estop.engaged", kT0 + 200 * kMillisecond, "base", Severity::Critical);
  const auto c = codes(fx);
  EXPECT_NE(std::find(c.begin(), c.end(), "jarvis.fault.muted"), c.end());
  EXPECT_NE(std::find(c.begin(), c.end(), "jarvis.incident.closed"), c.end())
    << "the symptom's own incident merges into the root's";
  const auto incidents = fm_.incidents();
  ASSERT_EQ(incidents.size(), 1u);
  EXPECT_EQ(incidents.front().fault_ids.size(), 2u);
}

TEST_F(FaultManagerTest, FaultsOnTheSameComponentShareAnIncident)
{
  raise("something.broke", kT0, "lidar");
  const auto fx = raise("something.else", kT0 + 10 * kSecond, "lidar");
  const auto c = codes(fx);
  EXPECT_NE(std::find(c.begin(), c.end(), "jarvis.incident.joined"), c.end());
  raise("unrelated.fault", kT0 + 20 * kSecond, "battery");
  EXPECT_EQ(fm_.incidents().size(), 2u);
}

TEST_F(FaultManagerTest, IncidentFollowsRecoveryAndClosesAfterResolution)
{
  raise("something.broke", kT0);
  const std::string incident = fm_.faults().front().incident_id;

  Event recovery;
  recovery.kind = Kind::Recovery;
  recovery.code = "restart-lifecycle-node";
  recovery.correlation_id = incident;
  recovery.attrs["phase"] = std::string("STARTED");
  fm_.on_event(recovery, kT0 + 1);
  EXPECT_EQ(fm_.find_incident(incident)->state, IncidentState::Recovering);

  recovery.attrs["phase"] = std::string("FAILED");
  fm_.on_event(recovery, kT0 + 2);
  EXPECT_EQ(fm_.find_incident(incident)->state, IncidentState::Open);

  Event escalate;
  escalate.kind = Kind::System;
  escalate.code = "jarvis.incident.escalate";
  escalate.correlation_id = incident;
  const auto fx = fm_.on_event(escalate, kT0 + 3);
  EXPECT_EQ(fm_.find_incident(incident)->state, IncidentState::Escalated);
  EXPECT_EQ(codes(fx), V{"jarvis.incident.escalated"});
  EXPECT_GE(emitted(fx).front().severity, Severity::Error);

  EXPECT_FALSE(fm_.close_incident(incident, "alice", kT0 + 4).success) << "still has an open fault";
  clear("something.broke", kT0 + 5);
  EXPECT_EQ(fm_.find_incident(incident)->state, IncidentState::Resolved);

  EXPECT_EQ(fm_.next_deadline(), kT0 + 5 + 24 * kHour);
  const auto closed = fm_.advance_to(kT0 + 25 * kHour);
  EXPECT_EQ(codes(closed), V{"jarvis.incident.closed"});
  EXPECT_EQ(fm_.find_incident(incident), nullptr);
}

TEST_F(FaultManagerTest, DryRunsAndSubPlansDoNotMoveTheIncident)
{
  raise("something.broke", kT0);
  const std::string incident = fm_.faults().front().incident_id;
  Event recovery;
  recovery.kind = Kind::Recovery;
  recovery.code = "jarvis.recovery.started";
  recovery.correlation_id = incident;
  recovery.attrs["phase"] = std::string("STARTED");

  recovery.attrs["dry_run"] = true;
  fm_.on_event(recovery, kT0 + 1);
  EXPECT_EQ(fm_.find_incident(incident)->state, IncidentState::Open) << "a dry run changes nothing";

  recovery.attrs["dry_run"] = false;
  recovery.attrs["parent_id"] = std::string("exec-parent");
  fm_.on_event(recovery, kT0 + 2);
  EXPECT_EQ(fm_.find_incident(incident)->state, IncidentState::Open)
    << "sub-plans are the parent's";

  recovery.attrs.erase("parent_id");
  fm_.on_event(recovery, kT0 + 3);
  EXPECT_EQ(fm_.find_incident(incident)->state, IncidentState::Recovering);
}

TEST_F(FaultManagerTest, OperatorCanCloseAResolvedIncident)
{
  raise("something.broke", kT0);
  const std::string incident = fm_.faults().front().incident_id;
  clear("something.broke", kT0 + 1);
  const auto r = fm_.close_incident(incident, "alice", kT0 + 2);
  ASSERT_TRUE(r.success) << r.message;
  EXPECT_EQ(codes(r.effects), (V{"incident.close", "jarvis.incident.closed"}));
}

TEST_F(FaultManagerTest, FreezeFrameIncludesLatestStateValues)
{
  Event mode;
  mode.kind = Kind::State;
  mode.code = "robot.mode";
  mode.attrs["value"] = std::string("AUTONOMOUS");
  fm_.on_event(mode, kT0);
  raise("something.broke", kT0 + 1, "lidar", Severity::Error, {{"node", std::string("/x")}});
  const auto & ff = only_open().freeze_frame;
  EXPECT_EQ(std::get<std::string>(ff.at("state.robot.mode")), "AUTONOMOUS");
  EXPECT_EQ(std::get<std::string>(ff.at("node")), "/x");
}

TEST_F(FaultManagerTest, IgnoresClearsWithoutAnOpenFault)
{
  EXPECT_TRUE(clear("never.raised", kT0).empty());
}

TEST_F(FaultManagerTest, SnapshotAndRestoreResumeExactly)
{
  Event mode;
  mode.kind = Kind::State;
  mode.code = "robot.mode";
  mode.attrs["value"] = std::string("AUTONOMOUS");
  fm_.on_event(mode, kT0);
  raise("ros.topic.stale", kT0);                                    // pending, deadline +1s
  raise("safety.estop.engaged", kT0, "base", Severity::Critical);   // active, latched policy
  clear("safety.estop.engaged", kT0 + 100 * kMillisecond, "base");  // -> LATCHED
  const nlohmann::json state = fm_.snapshot();

  FaultManager restored(FaultCatalog::from_yaml(kCatalog), config(), 99);
  const auto fx = restored.restore(nlohmann::json::parse(state.dump()), kT0 + 200 * kMillisecond);
  EXPECT_EQ(codes(fx), V{"jarvis.fault_manager.restored"});
  EXPECT_TRUE(has<FaultsChanged>(fx));
  EXPECT_EQ(restored.faults().size(), 2u);
  EXPECT_EQ(restored.incidents().size(), fm_.incidents().size());
  EXPECT_EQ(restored.next_deadline(), fm_.next_deadline());
  EXPECT_EQ(nlohmann::json(restored.snapshot()), nlohmann::json(fm_.snapshot()));

  // The restored manager keeps working: the pending fault confirms, a clear finds its episode.
  EXPECT_EQ(fault_states(restored.advance_to(kT0 + 2 * kSecond), "ros.topic.stale"), V{"ACTIVE"});
  Event clear_evt = report(Kind::FaultClear, "ros.topic.stale");
  EXPECT_EQ(
    fault_states(restored.on_event(clear_evt, kT0 + 3 * kSecond), "ros.topic.stale"), V{"CLEARED"});
}

TEST_F(FaultManagerTest, SameInputsGiveTheSameDecisions)
{
  // Replay determinism: two managers with the same seed fed the same inputs emit identical events.
  auto run = []() {
    FaultManager fm(FaultCatalog::from_yaml(kCatalog), config(), 1234);
    std::vector<nlohmann::json> out;
    auto record = [&out](const Effects & fx) {
      for (const auto & e : emitted(fx)) {
        out.push_back(e);
      }
    };
    Event e = report(Kind::FaultRaise, "ros.topic.stale");
    e.id = "fixed-1";
    record(fm.on_event(e, kT0));
    record(fm.advance_to(kT0 + 2 * kSecond));
    e = report(Kind::FaultClear, "ros.topic.stale");
    e.id = "fixed-2";
    record(fm.on_event(e, kT0 + 3 * kSecond));
    return out;
  };
  EXPECT_EQ(run(), run());
}

TEST_F(FaultManagerTest, AFlappingFaultKeepsMemoryBounded)
{
  // Every raise after a clear opens a new incident; the resolved ones would wait 24 h to close.
  FaultManagerConfig c = config();
  c.max_resolved_incidents = 5;
  FaultManager fm(FaultCatalog::from_yaml(kCatalog), c, 7);
  std::vector<std::string> closed;
  for (int i = 0; i < 20; ++i) {
    fm.on_event(report(Kind::FaultRaise, "drive.motor.fault"), kT0 + i * kSecond);
    for (const auto & e : emitted(fm.on_event(
           report(Kind::FaultClear, "drive.motor.fault"),
           kT0 + i * kSecond + 500 * kMillisecond))) {
      if (e.code == "jarvis.incident.closed") {
        closed.push_back(*attr_string(e.attrs, "reason"));
      }
    }
  }
  EXPECT_EQ(fm.incidents(true).size(), 5u);
  ASSERT_EQ(closed.size(), 15u);
  EXPECT_EQ(closed.front(), "auto-closed: more than 5 resolved incidents");
  for (const auto & inc : fm.incidents(true)) {
    EXPECT_EQ(inc.state, IncidentState::Resolved);
    EXPECT_GE(inc.resolved_at, kT0 + 15 * kSecond) << "the newest are kept";
  }
}
