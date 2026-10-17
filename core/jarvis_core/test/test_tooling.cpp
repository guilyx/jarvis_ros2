// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include <gtest/gtest.h>

#include <deque>
#include <filesystem>
#include <fstream>
#include <functional>
#include <sstream>

#include "jarvis_core/event_hub.hpp"
#include "jarvis_core/fault_manager.hpp"
#include "jarvis_core/json.hpp"
#include "jarvis_core/tooling.hpp"

using namespace jarvis_core;
namespace fs = std::filesystem;

namespace
{

constexpr Timestamp kT0 = 1'000 * kSecond;

class TempDir
{
public:
  TempDir()
  {
    path_ = fs::temp_directory_path() /
            ("jarvis_tooling_" + std::to_string(::testing::UnitTest::GetInstance()->random_seed()) +
             "_" + std::to_string(counter_++));
    fs::create_directories(path_);
  }
  ~TempDir() { fs::remove_all(path_); }
  std::string write(const std::string & name, const std::string & text) const
  {
    const auto p = path_ / name;
    fs::create_directories(p.parent_path());
    std::ofstream(p) << text;
    return p.string();
  }
  std::string dir(const std::string & name) const { return (path_ / name).string(); }

private:
  static inline int counter_ = 0;
  fs::path path_;
};

std::vector<std::string> messages(const LintReport & r, bool errors)
{
  std::vector<std::string> out;
  for (const auto & f : r.findings) {
    if (f.error == errors) {
      out.push_back(f.message);
    }
  }
  return out;
}

bool contains(const std::vector<std::string> & list, const std::string & part)
{
  return std::any_of(list.begin(), list.end(), [&part](const std::string & s) {
    return s.find(part) != std::string::npos;
  });
}

constexpr const char * kPlans = R"(
schema: jarvis.plans/v1
plans:
  - id: restart
    params: { node: { required: true, pattern: "^/" } }
    locks: ["node:{{ params.node }}"]
    steps:
      - { id: restart, action: lifecycle.restart, with: { node: "{{ params.node }}" } }
      - id: verify
        action: wait_for
        with: { fault: { code: ros.topic.stale, component: "*" }, state: CLEARED }
        timeout: 30s
)";

constexpr const char * kRules = R"(
schema: jarvis.rules/v1
rules:
  - id: stale-restart
    partition_by: [source.component]
    when: { event: { kind: FAULT_STATE, code: ros.topic.stale, attrs.state: ACTIVE } }
    then: [{ recover: { plan: restart, args: { node: "/{{ event.source.component }}" } } }]
    policy: { cooldown: 30s }
  - id: stale-storm
    when:
      count: { event: { kind: FAULT_STATE, code: ros.topic.stale, attrs.state: ACTIVE }, threshold: 3, within: 5m }
    then: [{ notify: { message: stale storm } }]
  - id: recovered
    when: { event: { kind: RECOVERY, attrs.phase: SUCCEEDED } }
    then: [{ notify: { message: "{{ event.attrs.plan }} worked" } }]
)";

/// Runs the real loop (hub, fault manager, rule engine, orchestrator, an actuator that always
/// succeeds) over a scripted scenario, and returns the canonical stream it produced.
std::vector<Event> live_run(const std::string & rules_yaml)
{
  EventHub hub(EventHubConfig{}, 1, 1);
  FaultManagerConfig fmc;
  fmc.robot_id = "bot";
  FaultManager fm(
    FaultCatalog::from_yaml("schema: jarvis.faults/v1\ndefaults: { severity: ERROR, debounce: "
                            "immediate, heal: { on_clear: true } }\n"),
    fmc, 2);
  RuleEngine rules(RuleEngineConfig{"bot"}, RuleSet::from_yaml(rules_yaml));
  OrchestratorConfig oc;
  oc.robot_id = "bot";
  oc.id_seed = 3;
  Orchestrator orch(oc, PlanSet::from_yaml(kPlans));

  Timestamp now = kT0;
  std::vector<Event> stream;
  std::deque<Event> inbox;
  std::vector<StartAction> pending;
  std::function<void(const Effects &)> perform = [&](const Effects & fx) {
    for (const auto & f : fx) {
      if (const auto * e = std::get_if<EmitEvent>(&f)) {
        inbox.push_back(e->event);
      } else if (const auto * r = std::get_if<RequestRecovery>(&f)) {
        perform(orch.submit(to_request(*r), now).effects);
      } else if (const auto * s = std::get_if<StartAction>(&f)) {
        pending.push_back(*s);
      }
    }
  };
  auto pump = [&] {
    for (;;) {
      while (!inbox.empty()) {
        Event e = inbox.front();
        inbox.pop_front();
        for (auto & published : hub.ingest(e, now).publish) {
          stream.push_back(published);
          perform(fm.on_event(published, now));
          perform(rules.on_event(published, now));
          perform(orch.on_event(published, now));
        }
      }
      if (pending.empty()) {
        return;
      }
      for (const auto & s : std::exchange(pending, {})) {
        perform(orch.on_action_result(s.execution_id, s.step_id, s.attempt, true, "", now));
      }
    }
  };
  int n = 0;
  auto report = [&](Kind kind, const std::string & component, Timestamp at) {
    now = at;
    perform(fm.advance_to(now));
    perform(rules.advance_to(now));
    perform(orch.advance_to(now));
    Event e;
    e.id = "report-" + std::to_string(++n);
    e.kind = kind;
    e.code = "ros.topic.stale";
    e.severity = Severity::Error;
    e.source.node = "/jarvis/bridges/topic_watchdog";
    e.source.component = component;
    inbox.push_back(e);
    pump();
  };
  report(Kind::FaultRaise, "lidar", kT0);                 // restart lidar
  report(Kind::FaultClear, "lidar", kT0 + 5 * kSecond);   // verified, recovered
  report(Kind::FaultRaise, "lidar", kT0 + 10 * kSecond);  // cooldown: no restart
  report(Kind::FaultClear, "lidar", kT0 + 12 * kSecond);
  report(Kind::FaultRaise, "camera", kT0 + 20 * kSecond);  // restart camera; third stale: storm
  report(Kind::FaultClear, "camera", kT0 + 25 * kSecond);
  report(Kind::FaultRaise, "lidar", kT0 + 45 * kSecond);  // cooldown over: restart lidar
  report(Kind::FaultClear, "lidar", kT0 + 50 * kSecond);
  return stream;
}

}  // namespace

// -- lint -----------------------------------------------------------------------------------------

TEST(Lint, CrossChecksRulesPlansAndTheCatalog)
{
  TempDir t;
  t.write("plans/p.yaml", R"(
schema: jarvis.plans/v1
plans:
  - id: restart
    params: { node: { required: true } }
    steps: [{ id: s, action: lifecycle.restart, with: { node: "{{ params.node }}" } }]
  - id: operator-only
    allowed_requesters: [OPERATOR]
    steps: [{ id: s, action: check, with: { faults_inactive: [x] } }]
)");
  t.write("rules/r.yaml", R"(
schema: jarvis.rules/v1
rules:
  - id: unknown-plan
    when: { event: { code: a.b } }
    then: [{ recover: { plan: nope } }]
  - id: no-rule-requests
    when: { event: { code: a.b } }
    then: [{ recover: { plan: operator-only } }]
  - id: bad-args
    when: { event: { code: a.b } }
    then: [{ recover: { plan: restart, args: { nod: x } } }]
  - id: templated
    when: { event: { code: a.b } }
    then: [{ recover: { plan: "{{ event.attrs.plan }}" } }]
  - id: uncataloged
    when: { event: { kind: FAULT_STATE, code: sensor.mystery } }
    then: [{ notify: { message: hi } }]
)");
  t.write("faults.yaml", "schema: jarvis.faults/v1\nfaults: [{ code: sensor.known }]\n");
  const auto r =
    lint({t.dir("rules"), t.dir("plans"), t.dir("faults.yaml"), default_actuator_actions()});
  EXPECT_EQ(r.rules, 5u);
  EXPECT_EQ(r.plans, 2u);
  EXPECT_EQ(r.faults, 1u);
  EXPECT_FALSE(r.ok());
  const auto errors = messages(r, true);
  EXPECT_TRUE(contains(errors, "rule 'unknown-plan': recovers with unknown plan 'nope'"));
  EXPECT_TRUE(contains(
    errors, "rule 'no-rule-requests': plan 'operator-only' does not accept RULE requests"));
  EXPECT_TRUE(contains(errors, "rule 'bad-args': plan 'restart' has no parameter 'nod'"));
  EXPECT_TRUE(contains(errors, "rule 'bad-args': plan 'restart' needs the argument 'node'"));
  EXPECT_EQ(errors.size(), 4u);
  const auto warnings = messages(r, false);
  EXPECT_TRUE(contains(warnings, "rule 'templated': the plan is a template"));
  EXPECT_TRUE(contains(warnings, "watches 'sensor.mystery', which is not in the catalog"));
  EXPECT_TRUE(contains(warnings, "plan 'restart' never verifies its effect"));
}

TEST(Lint, ReportsParseErrorsAndUnknownActuators)
{
  TempDir t;
  t.write("rules/r.yaml", "schema: jarvis.rules/v1\nrules: [{ id: Bad }]\n");
  t.write(
    "plans/p.yaml",
    "schema: jarvis.plans/v1\nplans: [{ id: p, steps: [{ id: s, action: vendor.zap }] }]\n");
  const auto r = lint({t.dir("rules"), t.dir("plans"), "", default_actuator_actions()});
  const auto errors = messages(r, true);
  ASSERT_EQ(errors.size(), 2u);
  EXPECT_TRUE(contains(errors, "rules[0].id"));
  EXPECT_TRUE(contains(errors, "unknown action 'vendor.zap'"));
  auto with_vendor = default_actuator_actions();
  with_vendor.insert("vendor.zap");
  EXPECT_EQ(messages(lint({"", t.dir("plans"), "", with_vendor}), true).size(), 0u);
}

TEST(Lint, TheShippedConfigurationIsClean)
{
  const std::string config = std::string(JARVIS_REPO_DIR) + "/ros/jarvis_bringup/config";
  const auto r = lint(
    {config + "/rules", config + "/plans", config + "/faults.yaml", default_actuator_actions()});
  for (const auto & f : r.findings) {
    ADD_FAILURE() << (f.error ? "error: " : "warning: ") << f.message;
  }
  EXPECT_GE(r.rules, 1u);
  EXPECT_GE(r.plans, 1u);
}

// -- backtest -------------------------------------------------------------------------------------

TEST(Backtest, AReplayOfALiveRunMakesTheSameDecisions)
{
  const auto stream = live_run(kRules);
  const auto live_matches = std::count_if(
    stream.begin(), stream.end(), [](const Event & e) { return e.kind == Kind::RuleMatch; });
  ASSERT_EQ(live_matches, 7)
    << "lidar, camera, lidar restarts; the storm; three recoveries succeeded";
  auto from = [&stream](const std::string & node) {
    return static_cast<std::size_t>(std::count_if(
      stream.begin(), stream.end(), [&node](const Event & e) { return e.source.node == node; }));
  };

  // Without plans: the live recovery outcomes stay in the stream.
  BacktestOptions options;
  options.robot_id = "bot";
  options.simulate_recovery = false;
  auto report = backtest(RuleSet::from_yaml(kRules), nullptr, stream, options);
  EXPECT_EQ(report.recorded, 7u);
  EXPECT_TRUE(report.reproduces()) << report.to_json().dump(2);
  EXPECT_EQ(report.events, stream.size() - from("/jarvis/rule_engine"));
  EXPECT_EQ(report.recoveries.size(), 3u);
  EXPECT_EQ(report.recoveries[0].args.at("node"), "/lidar");
  EXPECT_EQ(report.recoveries[0].reason, "not simulated (no plans)");

  // With plans: recovery runs again, in dry run, and its outcomes feed the `recovered` rule.
  const auto plans = PlanSet::from_yaml(kPlans);
  options.simulate_recovery = true;
  report = backtest(RuleSet::from_yaml(kRules), &plans, stream, options);
  EXPECT_TRUE(report.reproduces()) << report.to_json().dump(2);
  ASSERT_EQ(report.recoveries.size(), 3u);
  for (const auto & r : report.recoveries) {
    EXPECT_EQ(r.state, ExecState::Succeeded) << r.reason;
  }
  EXPECT_EQ(report.events, stream.size() - from("/jarvis/rule_engine") - from("/jarvis/recovery"))
    << "the live engines' own events are recomputed, not replayed";
}

TEST(Backtest, ShowsWhatAChangedRuleWouldDecide)
{
  const auto stream = live_run(kRules);
  std::string stricter = kRules;
  stricter.replace(stricter.find("cooldown: 30s"), 13, "cooldown: 1m");
  BacktestOptions options;
  options.robot_id = "bot";
  options.simulate_recovery = false;
  const auto report = backtest(RuleSet::from_yaml(stricter), nullptr, stream, options);
  EXPECT_TRUE(report.compared());
  EXPECT_FALSE(report.reproduces());
  ASSERT_EQ(report.missing.size(), 1u) << report.to_json().dump(2);
  EXPECT_EQ(report.missing[0].substr(0, 17), "stale-restart <- ");
  EXPECT_TRUE(report.extra.empty());
  for (const auto & s : report.stats) {
    if (s.id == "stale-restart") {
      EXPECT_EQ(s.fires, 2u);
      EXPECT_EQ(s.cooled_down, 2u);
    }
  }
  const auto j = report.to_json();
  EXPECT_EQ(j["comparison"]["reproduces"], false);
  EXPECT_EQ(j["matches"].size(), report.matches.size());
}

TEST(Backtest, ReadsJsonLines)
{
  const auto stream = live_run(kRules);
  std::stringstream lines;
  lines << "# exported by ros2 jarvis export\n\n";
  for (const auto & e : stream) {
    lines << nlohmann::json(e).dump() << "\n";
  }
  const auto back = read_event_lines(lines);
  ASSERT_EQ(back.size(), stream.size());
  EXPECT_EQ(back.front(), stream.front());
  EXPECT_EQ(back.back(), stream.back());

  std::stringstream bad(nlohmann::json(stream.front()).dump() + "\nnot json\n");
  try {
    read_event_lines(bad);
    ADD_FAILURE() << "accepted a bad line";
  } catch (const std::runtime_error & e) {
    EXPECT_EQ(std::string(e.what()).substr(0, 7), "line 2:");
  }
  EXPECT_EQ(backtest(RuleSet{}, nullptr, {}).compared(), false);
}

TEST(Lint, EveryCookbookRecipeIsClean)
{
  // docs/guides/rules-cookbook.md: each rules or plans block is a file, linted with the shipped
  // plans next to them. No error and no warning: a recipe is what people copy.
  std::ifstream in(std::string(JARVIS_DOCS_DIR) + "/guides/rules-cookbook.md");
  ASSERT_TRUE(in) << "the cookbook is not where it should be";
  std::stringstream doc;
  doc << in.rdbuf();
  const std::string text = doc.str();
  TempDir t;
  int rules = 0;
  int plans = 0;
  for (std::size_t pos = text.find("```yaml"); pos != std::string::npos;
       pos = text.find("```yaml", pos + 1)) {
    const auto start = text.find('\n', pos) + 1;
    const auto end = text.find("```", start);
    const std::string block = text.substr(start, end - start);
    if (block.find("schema: jarvis.rules/v1") != std::string::npos) {
      t.write("rules/recipe_" + std::to_string(rules++) + ".yaml", block);
    } else if (block.find("schema: jarvis.plans/v1") != std::string::npos) {
      t.write("plans/recipe_" + std::to_string(plans++) + ".yaml", block);
    }
  }
  std::ifstream shipped(
    std::string(JARVIS_REPO_DIR) + "/ros/jarvis_bringup/config/plans/core.yaml");
  std::stringstream core;
  core << shipped.rdbuf();
  t.write("plans/core.yaml", core.str());
  const auto r = lint({t.dir("rules"), t.dir("plans"), "", default_actuator_actions()});
  for (const auto & f : r.findings) {
    ADD_FAILURE() << (f.error ? "error: " : "warning: ") << f.message;
  }
  EXPECT_GE(rules, 5);
  EXPECT_GE(plans, 3);
}
