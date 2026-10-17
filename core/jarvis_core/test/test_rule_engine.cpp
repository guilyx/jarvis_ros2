// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include <gtest/gtest.h>

#include <algorithm>
#include <random>
#include <tuple>

#include "jarvis_core/rule_engine.hpp"

using namespace jarvis_core;

namespace
{

constexpr Timestamp kT0 = 1'000 * kSecond;

std::string header(const std::string & rules)
{
  return "schema: jarvis.rules/v1\nrules:\n" + rules;
}

class EngineTest : public ::testing::Test
{
protected:
  void load(const std::string & rules)
  {
    engine_ = std::make_unique<RuleEngine>(
      RuleEngineConfig{"bot", "/jarvis/rule_engine", 1000}, RuleSet::from_yaml(header(rules)));
  }

  Event event(
    Kind kind, const std::string & code, Timestamp t, const std::string & component = "lidar")
  {
    Event e;
    e.id = "e" + std::to_string(++seq_);
    e.seq = seq_;
    e.stamp = t;
    e.recv_stamp = t;
    e.kind = kind;
    e.code = code;
    e.severity = Severity::Error;
    e.source.robot = "bot";
    e.source.component = component;
    return e;
  }

  Event fault_state(
    const std::string & code, const std::string & state, Timestamp t,
    const std::string & component = "lidar", const std::string & fault_id = "")
  {
    Event e = event(Kind::FaultState, code, t, component);
    e.attrs["state"] = state;
    e.attrs["fault_id"] = fault_id.empty() ? code + "@" + component : fault_id;
    e.correlation_id = "inc-1";
    return e;
  }

  // As live: the engine sees an event a little after the hub received it.
  Effects feed(const Event & e) { return engine_->on_event(e, e.recv_stamp + kLatency); }
  static constexpr Duration kLatency = kMillisecond / 20;

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
  static std::vector<std::string> codes(const Effects & fx)
  {
    std::vector<std::string> out;
    for (const auto & e : emitted(fx)) {
      out.push_back(e.code);
    }
    return out;
  }
  static std::vector<RequestRecovery> recoveries(const Effects & fx)
  {
    std::vector<RequestRecovery> out;
    for (const auto & f : fx) {
      if (const auto * r = std::get_if<RequestRecovery>(&f)) {
        out.push_back(*r);
      }
    }
    return out;
  }
  static int matches(const Effects & fx)
  {
    const auto c = codes(fx);
    return static_cast<int>(std::count(c.begin(), c.end(), "jarvis.rule.matched"));
  }
  RuleStats stats(const std::string & id) const
  {
    for (const auto & s : engine_->stats()) {
      if (s.id == id) {
        return s;
      }
    }
    return {};
  }

  std::unique_ptr<RuleEngine> engine_;
  std::uint64_t seq_{0};
};

using V = std::vector<std::string>;

}  // namespace

// -- event patterns, actions, templates -----------------------------------------------------------

TEST_F(EngineTest, FiresAskToPersistTheirHistory)
{
  load(R"(  - id: r
    when: { event: { code: a.b } }
    then: [{ notify: { message: hi } }]
    policy: { cooldown: 1m }
)");
  auto has_persist = [](const Effects & fx) {
    return std::any_of(fx.begin(), fx.end(), [](const Effect & f) {
      return std::holds_alternative<PersistState>(f);
    });
  };
  EXPECT_FALSE(has_persist(feed(event(Kind::Log, "x.y", kT0)))) << "no match, nothing to save";
  EXPECT_TRUE(has_persist(feed(event(Kind::Log, "a.b", kT0 + 1))));
  EXPECT_FALSE(has_persist(feed(event(Kind::Log, "a.b", kT0 + 2)))) << "cooled down: no fire";
}

TEST_F(EngineTest, AMatchEmitsRuleMatchThenRunsActionsWithRenderedTemplates)
{
  load(R"(  - id: scan-stale-restart
    description: Restart the scan relay.
    partition_by: [source.component]
    when:
      event: { kind: FAULT_STATE, code: ros.topic.stale, attrs.state: ACTIVE }
    then:
      - recover: { plan: restart-lifecycle-node, args: { node: "{{ event.attrs.node }}", component: "{{ partition.source.component }}" } }
      - notify: { channel: ops, message: "{{ event.code }} on {{ robot }}" }
)");
  Event e = fault_state("ros.topic.stale", "ACTIVE", kT0);
  e.attrs["node"] = "/scan_relay";
  const auto fx = feed(e);
  EXPECT_EQ(codes(fx), (V{"jarvis.rule.matched", "jarvis.notify"}));

  const Event match = emitted(fx)[0];
  EXPECT_EQ(match.kind, Kind::RuleMatch);
  EXPECT_EQ(match.causation_id, e.id) << "caused by the matched event";
  EXPECT_EQ(match.correlation_id, "inc-1") << "stays in the incident";
  EXPECT_EQ(*attr_string(match.attrs, "rule"), "scan-stale-restart");
  EXPECT_EQ(*attr_string(match.attrs, "partition.source.component"), "lidar");
  EXPECT_NE(match.payload.find("\"events\":[\"" + e.id + "\"]"), std::string::npos)
    << match.payload;

  const auto rec = recoveries(fx);
  ASSERT_EQ(rec.size(), 1u);
  EXPECT_EQ(rec[0].plan, "restart-lifecycle-node");
  EXPECT_EQ(rec[0].args.at("node"), "/scan_relay");
  EXPECT_EQ(rec[0].args.at("component"), "lidar");
  EXPECT_EQ(rec[0].rationale, "Restart the scan relay.");
  EXPECT_EQ(rec[0].match_id, match.id);

  const Event notify = emitted(fx)[1];
  EXPECT_EQ(notify.message, "ros.topic.stale on bot");
  EXPECT_EQ(*attr_string(notify.attrs, "channel"), "ops");
  EXPECT_EQ(notify.causation_id, match.id);

  EXPECT_EQ(matches(feed(fault_state("ros.topic.stale", "CLEARED", kT0 + 1))), 0);
  EXPECT_EQ(stats("scan-stale-restart").fires, 1u);
}

TEST_F(EngineTest, AnActionWhoseTemplateRendersEmptyIsSkippedAndReported)
{
  load(R"(  - id: needs-node
    when: { event: { code: ros.topic.stale } }
    then:
      - recover: { plan: restart, args: { node: "{{ event.attrs.node }}" } }
      - notify: { message: "still notified" }
)");
  const auto fx = feed(event(Kind::FaultRaise, "ros.topic.stale", kT0));
  EXPECT_EQ(codes(fx), (V{"jarvis.rule.matched", "jarvis.rule.action_failed", "jarvis.notify"}));
  EXPECT_TRUE(recoveries(fx).empty());
  EXPECT_EQ(stats("needs-node").action_failures, 1u);
}

TEST_F(EngineTest, EmitIncidentAndAgentActionsProduceTheirEvents)
{
  load(R"(  - id: composite
    when: { event: { code: drive.motor.reset } }
    then:
      - emit: { kind: FAULT_RAISE, code: drive.motor.flapping, severity: ERROR, message: "motor {{ event.source.component }} flaps", attrs: { count: "{{ count }}" } }
      - incident: { escalate: true }
      - ask_agent: { focus: "Why does {{ event.source.component }} reset?" }
)");
  Event e = event(Kind::FaultRaise, "drive.motor.reset", kT0, "left");
  e.correlation_id = "inc-9";
  const auto ev = emitted(feed(e));
  ASSERT_EQ(ev.size(), 4u);
  EXPECT_EQ(ev[1].kind, Kind::FaultRaise);
  EXPECT_EQ(ev[1].code, "drive.motor.flapping");
  EXPECT_EQ(ev[1].severity, Severity::Error);
  EXPECT_EQ(ev[1].message, "motor left flaps");
  EXPECT_EQ(*attr_string(ev[1].attrs, "count"), "1");
  EXPECT_EQ(ev[2].code, "jarvis.incident.escalate");
  EXPECT_EQ(ev[2].correlation_id, "inc-9");
  EXPECT_EQ(ev[3].code, "jarvis.agent.request");
  EXPECT_EQ(*attr_string(ev[3].attrs, "focus"), "Why does left reset?");

  e.correlation_id.clear();
  e.id = "no-incident";
  const auto c = codes(feed(e));
  EXPECT_NE(std::find(c.begin(), c.end(), "jarvis.rule.action_failed"), c.end())
    << "nothing to escalate without an incident";
}

TEST_F(EngineTest, OpeningAnIncidentSaysWhatWasMatched)
{
  load(R"(  - id: opener
    when: { event: { kind: FAULT_STATE, code: drive.motor.hot } }
    then: [ { incident: { open: true } } ]
)");
  Event e = event(Kind::FaultState, "drive.motor.hot", kT0, "left");
  e.severity = Severity::Warn;
  e.attrs["fault_id"] = std::string("fault-7");
  const auto ev = emitted(feed(e));
  ASSERT_EQ(ev.size(), 2u);
  EXPECT_EQ(ev[1].code, "jarvis.incident.open");
  EXPECT_EQ(ev[1].severity, Severity::Warn);
  EXPECT_EQ(ev[1].source.component, "left");
  EXPECT_EQ(*attr_string(ev[1].attrs, "matched_code"), "drive.motor.hot");
  EXPECT_EQ(*attr_string(ev[1].attrs, "fault_id"), "fault-7");
  EXPECT_EQ(*attr_string(ev[1].attrs, "rule"), "opener");
}

// -- guards ---------------------------------------------------------------------------------------

TEST_F(EngineTest, GuardsReadActiveFaultsAndLatestStates)
{
  load(R"(  - id: guarded
    when: { event: { code: nav.goal.aborted } }
    guard:
      faults_inactive: [safety.*]
      state: { robot.mode: { in: [AUTONOMOUS] } }
    then: [ { notify: { message: x } } ]
)");
  auto mode = [this](const std::string & m, Timestamp t) {
    Event s = event(Kind::State, "robot.mode", t);
    s.attrs["value"] = m;
    feed(s);
  };
  EXPECT_EQ(matches(feed(event(Kind::FaultRaise, "nav.goal.aborted", kT0))), 0) << "no mode yet";
  mode("AUTONOMOUS", kT0 + 1);
  EXPECT_EQ(matches(feed(event(Kind::FaultRaise, "nav.goal.aborted", kT0 + 2))), 1);

  feed(fault_state("safety.estop.engaged", "LATCHED", kT0 + 3, "base"));
  EXPECT_EQ(matches(feed(event(Kind::FaultRaise, "nav.goal.aborted", kT0 + 4))), 0);
  EXPECT_EQ(stats("guarded").guard_blocked, 2u);
  feed(fault_state("safety.estop.engaged", "CLEARED", kT0 + 5, "base"));
  EXPECT_EQ(matches(feed(event(Kind::FaultRaise, "nav.goal.aborted", kT0 + 6))), 1);

  engine_->sync_active_faults({{"f1", "safety.estop.engaged"}});
  EXPECT_EQ(matches(feed(event(Kind::FaultRaise, "nav.goal.aborted", kT0 + 7))), 0)
    << "seeded from the snapshot";
  mode("MANUAL", kT0 + 8);
  engine_->sync_active_faults({});
  EXPECT_EQ(matches(feed(event(Kind::FaultRaise, "nav.goal.aborted", kT0 + 9))), 0);
}

// -- count ----------------------------------------------------------------------------------------

TEST_F(EngineTest, CountFiresOnTheNthEventInASlidingWindowThenStartsOver)
{
  load(R"(  - id: aborts
    when: { count: { event: { code: nav.goal.aborted }, threshold: 3, within: 5m } }
    then: [ { notify: { message: "{{ count }} aborts" } } ]
)");
  auto abort_at = [this](Timestamp t) {
    return feed(event(Kind::FaultRaise, "nav.goal.aborted", t));
  };
  EXPECT_EQ(matches(abort_at(kT0)), 0);
  EXPECT_EQ(matches(abort_at(kT0 + 2 * kMinute)), 0);
  EXPECT_EQ(matches(abort_at(kT0 + 6 * kMinute)), 0) << "the first one left the window";
  const auto fx = abort_at(kT0 + 6 * kMinute + kSecond);
  ASSERT_EQ(matches(fx), 1) << "2 min, 6 min, 6 min 1 s: three within 5 min";
  EXPECT_EQ(emitted(fx)[1].message, "3 aborts");
  EXPECT_EQ(matches(abort_at(kT0 + 7 * kMinute)), 0) << "the count restarts after a fire";
  EXPECT_EQ(matches(feed(event(Kind::FaultRaise, "other.code", kT0 + 7 * kMinute))), 0);
}

// -- sequence -------------------------------------------------------------------------------------

constexpr const char * kSequenceRule = R"(  - id: seq
    when:
      sequence:
        within: 10s
        steps:
          - { id: first, event: { code: a.one } }
          - { id: second, event: { code: a.two } }
    then: [ { notify: { message: "{{ steps.first.id }} then {{ steps.second.id }}" } } ]
)";

TEST_F(EngineTest, SequenceMatchesInOrderAndIgnoresUnrelatedEvents)
{
  load(kSequenceRule);
  feed(event(Kind::Log, "a.one", kT0));
  feed(event(Kind::Log, "z.unrelated", kT0 + kSecond));
  const auto fx = feed(event(Kind::Log, "a.two", kT0 + 2 * kSecond));
  ASSERT_EQ(matches(fx), 1);
  EXPECT_EQ(emitted(fx)[1].message, "e1 then e3");

  EXPECT_EQ(matches(feed(event(Kind::Log, "a.two", kT0 + 3 * kSecond))), 0) << "out of order";
  feed(event(Kind::Log, "a.one", kT0 + 4 * kSecond));
  EXPECT_EQ(matches(feed(event(Kind::Log, "a.two", kT0 + 15 * kSecond))), 0)
    << "outside the window";
}

TEST_F(EngineTest, SequenceKeepsTheEarliestStart)
{
  load(kSequenceRule);
  feed(event(Kind::Log, "a.one", kT0));            // e1
  feed(event(Kind::Log, "a.one", kT0 + kSecond));  // e2: ignored, e1 is in progress
  const auto fx = feed(event(Kind::Log, "a.two", kT0 + 2 * kSecond));
  EXPECT_EQ(emitted(fx)[1].message, "e1 then e3");
}

constexpr const char * kAbsentRule = R"(  - id: unrecoverable
    when:
      sequence:
        within: 90s
        steps:
          - { id: lost, event: { code: nav.localization.lost, attrs.state: ACTIVE } }
          - { id: tried, event: { kind: RECOVERY, attrs.plan: relocalize } }
          - { id: still_lost, absent: { code: nav.localization.lost, attrs.state: CLEARED }, for: 20s }
    then: [ { incident: { escalate: true } } ]
)";

TEST_F(EngineTest, AnAbsentStepCompletesOnATimer)
{
  load(kAbsentRule);
  feed(fault_state("nav.localization.lost", "ACTIVE", kT0, "amcl"));
  Event tried = event(Kind::Recovery, "jarvis.recovery.finished", kT0 + 10 * kSecond, "amcl");
  tried.attrs["plan"] = "relocalize";
  feed(tried);
  EXPECT_EQ(engine_->next_deadline(), kT0 + 30 * kSecond);
  EXPECT_EQ(matches(engine_->advance_to(kT0 + 30 * kSecond - 1)), 0);
  const auto fx = engine_->advance_to(kT0 + 30 * kSecond);
  ASSERT_EQ(matches(fx), 1);
  EXPECT_EQ(emitted(fx)[0].stamp, kT0 + 30 * kSecond) << "fires when the silence completes";
  EXPECT_EQ(codes(fx)[1], "jarvis.incident.escalate");
}

TEST_F(EngineTest, AnAbsentStepFailsWhenTheEventShowsUpOrTheWindowEnds)
{
  load(kAbsentRule);
  Event tried = event(Kind::Recovery, "jarvis.recovery.finished", 0, "amcl");
  tried.attrs["plan"] = "relocalize";

  feed(fault_state("nav.localization.lost", "ACTIVE", kT0, "amcl"));
  tried.stamp = tried.recv_stamp = kT0 + 10 * kSecond;
  feed(tried);
  feed(fault_state("nav.localization.lost", "CLEARED", kT0 + 25 * kSecond, "amcl"));
  EXPECT_EQ(matches(engine_->advance_to(kT0 + 40 * kSecond)), 0) << "it recovered in time";

  feed(fault_state("nav.localization.lost", "ACTIVE", kT0 + 100 * kSecond, "amcl"));
  tried.id = "late";
  tried.stamp = tried.recv_stamp = kT0 + 180 * kSecond;
  feed(tried);
  EXPECT_EQ(matches(engine_->advance_to(kT0 + 300 * kSecond)), 0)
    << "the silence would end after the 90 s window";
}

// -- all / any ------------------------------------------------------------------------------------

TEST_F(EngineTest, AllNeedsEverySubPatternWithinTheWindowInAnyOrder)
{
  load(R"(  - id: brownout
    when:
      all:
        within: 10s
        of:
          - event: { code: power.voltage.low }
          - event: { code: drive.motor.reset }
    then: [ { notify: { message: brownout } } ]
)");
  feed(event(Kind::Log, "drive.motor.reset", kT0));
  EXPECT_EQ(matches(feed(event(Kind::Log, "power.voltage.low", kT0 + 5 * kSecond))), 1);
  feed(event(Kind::Log, "power.voltage.low", kT0 + 20 * kSecond));
  EXPECT_EQ(matches(feed(event(Kind::Log, "drive.motor.reset", kT0 + 35 * kSecond))), 0);
}

TEST_F(EngineTest, AnyFiresOnTheFirstSubPatternThatCompletes)
{
  load(R"(  - id: comms
    when:
      any:
        - event: { code: comms.link.down }
        - count: { event: { code: comms.packet.lost }, threshold: 2, within: 1s }
    then: [ { notify: { message: comms } } ]
)");
  EXPECT_EQ(matches(feed(event(Kind::Log, "comms.link.down", kT0))), 1);
  EXPECT_EQ(matches(feed(event(Kind::Log, "comms.packet.lost", kT0 + kSecond))), 0);
  EXPECT_EQ(matches(feed(event(Kind::Log, "comms.packet.lost", kT0 + kSecond + 1))), 1);
}

// -- partitions and policy ------------------------------------------------------------------------

TEST_F(EngineTest, PartitionsMatchIndependently)
{
  load(R"(  - id: per-wheel
    partition_by: [source.component]
    when: { count: { event: { code: drive.motor.overcurrent }, threshold: 2, within: 1m } }
    then: [ { notify: { message: "{{ partition.source.component }}" } } ]
)");
  feed(event(Kind::FaultRaise, "drive.motor.overcurrent", kT0, "left"));
  EXPECT_EQ(matches(feed(event(Kind::FaultRaise, "drive.motor.overcurrent", kT0 + 1, "right"))), 0);
  const auto fx = feed(event(Kind::FaultRaise, "drive.motor.overcurrent", kT0 + 2, "left"));
  ASSERT_EQ(matches(fx), 1);
  EXPECT_EQ(emitted(fx)[1].message, "left");
  EXPECT_EQ(stats("per-wheel").partitions, 2u);
}

TEST_F(EngineTest, CooldownIsPerPartition)
{
  load(R"(  - id: cooled
    partition_by: [source.component]
    when: { event: { code: x.fault } }
    then: [ { notify: { message: x } } ]
    policy: { cooldown: 30s }
)");
  EXPECT_EQ(matches(feed(event(Kind::FaultRaise, "x.fault", kT0, "a"))), 1);
  EXPECT_EQ(matches(feed(event(Kind::FaultRaise, "x.fault", kT0 + 10 * kSecond, "a"))), 0);
  EXPECT_EQ(matches(feed(event(Kind::FaultRaise, "x.fault", kT0 + 10 * kSecond, "b"))), 1);
  EXPECT_EQ(matches(feed(event(Kind::FaultRaise, "x.fault", kT0 + 31 * kSecond, "a"))), 1);
  EXPECT_EQ(stats("cooled").cooled_down, 1u);
}

TEST_F(EngineTest, MaxFiresSuppressesOnceForTheRestOfTheWindow)
{
  load(R"(  - id: limited
    when: { event: { code: x.fault } }
    then: [ { recover: { plan: restart } } ]
    policy: { max_fires: { count: 2, per: 1h } }
)");
  EXPECT_EQ(matches(feed(event(Kind::FaultRaise, "x.fault", kT0))), 1);
  EXPECT_EQ(matches(feed(event(Kind::FaultRaise, "x.fault", kT0 + kMinute))), 1);
  auto fx = feed(event(Kind::FaultRaise, "x.fault", kT0 + 2 * kMinute));
  EXPECT_EQ(codes(fx), V{"jarvis.rule.suppressed"});
  EXPECT_EQ(emitted(fx)[0].severity, Severity::Warn);
  EXPECT_EQ(*attr_string(emitted(fx)[0].attrs, "rule"), "limited")
    << "other rules can escalate on it";
  EXPECT_TRUE(feed(event(Kind::FaultRaise, "x.fault", kT0 + 3 * kMinute)).empty()) << "said once";
  EXPECT_EQ(stats("limited").suppressed, 2u);
  EXPECT_EQ(matches(feed(event(Kind::FaultRaise, "x.fault", kT0 + kHour + kMinute))), 1)
    << "the window moved on";
}

TEST_F(EngineTest, ShadowModeRecordsMatchesButRunsNothing)
{
  load(R"(  - id: shadow
    dry_run: true
    when: { event: { code: x.fault } }
    then: [ { recover: { plan: restart } } ]
)");
  auto fx = feed(event(Kind::FaultRaise, "x.fault", kT0));
  ASSERT_EQ(codes(fx), V{"jarvis.rule.matched"});
  EXPECT_EQ(std::get<bool>(emitted(fx)[0].attrs.at("dry_run")), true);
  EXPECT_TRUE(recoveries(fx).empty());
  EXPECT_EQ(stats("shadow").dry_fires, 1u);

  const auto live = engine_->set_rule_state("shadow", std::nullopt, false, "alice", "tested", kT0);
  ASSERT_TRUE(live);
  EXPECT_EQ(recoveries(feed(event(Kind::FaultRaise, "x.fault", kT0 + 1))).size(), 1u);
  ASSERT_TRUE(engine_->set_rule_state("shadow", false, std::nullopt));
  EXPECT_TRUE(feed(event(Kind::FaultRaise, "x.fault", kT0 + 2)).empty()) << "disabled";
  EXPECT_FALSE(engine_->set_rule_state("nope", true, std::nullopt));

  // Who changed what is on the stream (the audit trail), and the change is saved.
  const auto audit = emitted(live.effects);
  ASSERT_EQ(audit.size(), 1u);
  EXPECT_EQ(audit[0].kind, Kind::Operator);
  EXPECT_EQ(audit[0].code, "rule.state");
  EXPECT_EQ(*attr_string(audit[0].attrs, "operator"), "alice");
  EXPECT_EQ(std::get<bool>(audit[0].attrs.at("dry_run")), false);
  EXPECT_EQ(audit[0].message, "alice set rule shadow: enabled (tested)");
  EXPECT_TRUE(std::any_of(live.effects.begin(), live.effects.end(), [](const auto & f) {
    return std::holds_alternative<PersistState>(f);
  }));
}

TEST_F(EngineTest, HigherPriorityRulesActFirst)
{
  load(R"(  - id: low
    when: { event: { code: x.fault } }
    then: [ { notify: { message: low } } ]
  - id: high
    when: { event: { code: x.fault } }
    then: [ { notify: { message: high } } ]
    policy: { priority: 10 }
)");
  const auto ev = emitted(feed(event(Kind::FaultRaise, "x.fault", kT0)));
  ASSERT_EQ(ev.size(), 4u);
  EXPECT_EQ(ev[1].message, "high");
  EXPECT_EQ(ev[3].message, "low");
}

TEST_F(EngineTest, ARuleNeverFeedsOnItsOwnConsequences)
{
  load(R"(  - id: echo
    when: { event: { code: x.fault } }
    then: [ { emit: { kind: FAULT_RAISE, code: x.fault, message: again } } ]
  - id: watcher
    when: { event: { code: x.fault, message: again } }
    then: [ { notify: { message: seen } } ]
)");
  const auto first = emitted(feed(event(Kind::FaultRaise, "x.fault", kT0)));
  ASSERT_EQ(first.size(), 2u);
  // The emitted event comes back through the event hub, sequenced.
  Event back = first[1];
  back.seq = ++seq_;
  back.recv_stamp = kT0 + 1;
  feed(first[0]);  // its RULE_MATCH comes back too
  const auto fx = feed(back);
  EXPECT_EQ(codes(fx), (V{"jarvis.rule.matched", "jarvis.notify"})) << "only the other rule reacts";
  EXPECT_EQ(*attr_string(emitted(fx)[0].attrs, "rule"), "watcher");
}

TEST_F(EngineTest, TheTimeBasisDecidesWhichClockWindowsUse)
{
  const char * rule = R"(  - id: burst
    time_basis: %s
    when: { count: { event: { code: x.fault }, threshold: 2, within: 1s } }
    then: [ { notify: { message: x } } ]
)";
  for (const auto & [basis, expected] : {std::pair{"received", 1}, std::pair{"source", 0}}) {
    char buf[512];
    std::snprintf(buf, sizeof(buf), rule, basis);
    load(buf);
    Event a = event(Kind::FaultRaise, "x.fault", kT0);
    Event b = event(Kind::FaultRaise, "x.fault", kT0 + 100 * kMillisecond);
    b.stamp = kT0 + 10 * kSecond;  // the producer's clock says far apart
    feed(a);
    EXPECT_EQ(matches(feed(b)), expected) << basis;
  }
}

// -- reload and persistence -----------------------------------------------------------------------

TEST_F(EngineTest, ReloadKeepsFireHistoryForSurvivingRules)
{
  const char * rules = R"(  - id: cooled
    when: { event: { code: x.fault } }
    then: [ { notify: { message: x } } ]
    policy: { cooldown: 1m }
)";
  load(rules);
  EXPECT_EQ(matches(feed(event(Kind::FaultRaise, "x.fault", kT0))), 1);
  const auto fx = engine_->reload(RuleSet::from_yaml(header(rules)), kT0 + 1);
  EXPECT_EQ(codes(fx), V{"jarvis.rules.reloaded"});
  EXPECT_EQ(matches(feed(event(Kind::FaultRaise, "x.fault", kT0 + 10 * kSecond))), 0)
    << "still cooling down";
  EXPECT_EQ(stats("cooled").fires, 1u) << "stats survive too";
}

// Live, an event reaches the engine a little after the hub stamped it: a rule without any policy
// (nothing keeps its partition alive) must still see it. Source time can be older still.
TEST_F(EngineTest, RulesWithoutPolicyFireOnEventsThatArriveLate)
{
  load(R"(  - id: plain
    when: { event: { code: x.fault } }
    then: [ { notify: { message: x } } ]
  - id: by-source-time
    time_basis: source
    partition_by: [source.component]
    when: { event: { code: x.fault } }
    then: [ { notify: { message: x } } ]
    policy: { cooldown: 1s }
)");
  Event e = event(Kind::FaultRaise, "x.fault", kT0);
  e.recv_stamp = kT0 + 2 * kMillisecond;
  engine_->on_event(e, kT0 + 5 * kMillisecond);
  EXPECT_EQ(stats("plain").fires, 1u) << "received 3 ms before the engine saw it";
  EXPECT_EQ(stats("by-source-time").fires, 1u);
  Event old = event(Kind::FaultRaise, "x.fault", kT0 + kSecond, "b");
  old.recv_stamp = kT0 + 3 * kSecond;
  engine_->on_event(old, kT0 + 3 * kSecond);
  EXPECT_EQ(stats("plain").fires, 2u);
  EXPECT_EQ(stats("by-source-time").fires, 2u) << "stamped 2 s before, past the 1 s horizon";
}

TEST_F(EngineTest, MaxFiresSurvivesARestart)
{
  const char * rules = R"(  - id: limited
    partition_by: [source.component]
    when: { event: { code: x.fault } }
    then: [ { notify: { message: x } } ]
    policy: { max_fires: { count: 1, per: 1h } }
)";
  load(rules);
  feed(event(Kind::FaultRaise, "x.fault", kT0, "a"));
  const auto saved = engine_->snapshot();
  load(rules);
  engine_->restore(saved);
  EXPECT_EQ(
    codes(feed(event(Kind::FaultRaise, "x.fault", kT0 + kMinute, "a"))),
    V{"jarvis.rule.suppressed"});
  EXPECT_EQ(matches(feed(event(Kind::FaultRaise, "x.fault", kT0 + kMinute, "b"))), 1);
}

// -- properties -----------------------------------------------------------------------------------

namespace
{

constexpr const char * kMixedRules = R"(  - id: p-event
    partition_by: [source.component]
    when: { event: { code: c.a, severity: ">=ERROR" } }
    then: [ { notify: { message: "{{ partition.source.component }}" } } ]
    policy: { cooldown: 2s, max_fires: { count: 3, per: 30s } }
  - id: p-count
    partition_by: [source.component]
    when: { count: { event: { code: c.b }, threshold: 3, within: 5s } }
    then: [ { notify: { message: "{{ count }}" } } ]
  - id: p-seq
    partition_by: [source.component]
    when:
      sequence:
        within: 8s
        steps:
          - { id: s1, event: { code: c.a } }
          - { id: s2, event: { code: c.b } }
          - { id: s3, absent: { code: c.c }, for: 2s }
    then: [ { notify: { message: "{{ steps.s1.id }}" } } ]
  - id: p-all
    when:
      all:
        within: 4s
        of:
          - event: { code: c.c }
          - count: { event: { code: c.a }, threshold: 2, within: 3s }
    then: [ { notify: { message: all } } ]
  - id: p-any
    partition_by: [source.component]
    when:
      any:
        - sequence: { within: 3s, steps: [ { event: { code: c.c } }, { event: { code: c.c } } ] }
        - count: { event: { code: c.b, severity: CRITICAL }, threshold: 2, within: 2s }
    then: [ { notify: { message: any } } ]
)";

struct Stream
{
  std::vector<Event> events;
  Timestamp end{0};
};

Stream random_stream(unsigned seed, int n)
{
  std::mt19937 rng(seed);
  const std::vector<std::string> codes{"c.a", "c.b", "c.c"};
  const std::vector<std::string> comps{"x", "y", "z"};
  const std::vector<Severity> sevs{Severity::Warn, Severity::Error, Severity::Critical};
  Stream s;
  Timestamp t = kT0;
  for (int i = 0; i < n; ++i) {
    t += std::uniform_int_distribution<Timestamp>(0, 1500)(rng) * kMillisecond;
    Event e;
    e.id = "r" + std::to_string(i);
    e.seq = static_cast<std::uint64_t>(i + 1);
    e.stamp = e.recv_stamp = t;
    e.kind = Kind::FaultRaise;
    e.code = codes[rng() % codes.size()];
    e.severity = sevs[rng() % sevs.size()];
    e.source.component = comps[rng() % comps.size()];
    s.events.push_back(e);
  }
  s.end = t + kMinute;
  return s;
}

/// (rule, partition, fire time) of every RULE_MATCH, in emission order.
using Decision = std::tuple<std::string, std::string, Timestamp>;

std::vector<Decision> decisions(const Effects & fx)
{
  std::vector<Decision> out;
  for (const auto & f : fx) {
    if (const auto * e = std::get_if<EmitEvent>(&f); e && e->event.kind == Kind::RuleMatch) {
      const auto * part = attr_string(e->event.attrs, "partition.source.component");
      out.emplace_back(*attr_string(e->event.attrs, "rule"), part ? *part : "", e->event.stamp);
    }
  }
  return out;
}

std::vector<Decision> run(const Stream & s, Duration tick)
{
  RuleEngine engine({"bot", "/jarvis/rule_engine", 1000}, RuleSet::from_yaml(header(kMixedRules)));
  std::vector<Decision> out;
  auto take = [&out](const Effects & fx) {
    auto d = decisions(fx);
    out.insert(out.end(), d.begin(), d.end());
  };
  Timestamp next_tick = kT0;
  for (const auto & e : s.events) {
    while (tick > 0 && next_tick < e.recv_stamp) {
      take(engine.advance_to(next_tick));
      next_tick += tick;
    }
    take(engine.on_event(e, e.recv_stamp));
  }
  take(engine.advance_to(s.end));
  return out;
}

}  // namespace

TEST(EngineProperties, ReplayMakesTheSameDecisionsAsLive)
{
  for (unsigned seed = 1; seed <= 20; ++seed) {
    const Stream s = random_stream(seed, 400);
    const auto live = run(s, 100 * kMillisecond);
    const auto replay = run(s, 100 * kMillisecond);
    ASSERT_EQ(live, replay) << "seed " << seed;
    EXPECT_FALSE(live.empty()) << "seed " << seed << " exercised nothing";
  }
}

TEST(EngineProperties, TimerGranularityDoesNotChangeDecisions)
{
  for (unsigned seed = 1; seed <= 20; ++seed) {
    const Stream s = random_stream(seed, 400);
    auto fine = run(s, 50 * kMillisecond);
    auto events_only = run(s, 0);  // timers only run when events arrive, plus once at the end
    std::sort(fine.begin(), fine.end());
    std::sort(events_only.begin(), events_only.end());
    ASSERT_EQ(fine, events_only) << "seed " << seed;
  }
}
