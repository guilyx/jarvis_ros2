// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>
#include <sstream>

#include "jarvis_core/rules.hpp"

using namespace jarvis_core;

namespace
{

Event make_event(Kind kind, std::string code, Severity sev = Severity::Error)
{
  Event e;
  e.kind = kind;
  e.code = std::move(code);
  e.severity = sev;
  e.source.component = "lidar_front";
  e.source.node = "/sensors/lidar_driver";
  return e;
}

/// One-field matcher from YAML, through the real parser.
Matcher matcher(const std::string & field_yaml)
{
  const auto set = RuleSet::from_yaml(
    "schema: jarvis.rules/v1\nrules:\n  - id: probe\n    when:\n      event: { " + field_yaml +
    " }\n    then:\n      - notify: { message: probe }\n");
  return std::get<EventPattern>(set.rules().front().when.v).matcher;
}

std::string error_of(const std::string & rules_yaml)
{
  try {
    RuleSet::from_yaml("schema: jarvis.rules/v1\nrules:\n" + rules_yaml);
  } catch (const RuleError & e) {
    return e.what();
  }
  return "<no error>";
}

constexpr const char * kOkRule = R"(  - id: ok
    when: { event: { code: a.b } }
    then: [ { notify: { message: hi } } ]
)";

}  // namespace

// -- tests and matchers ---------------------------------------------------------------------------

TEST(Matcher, ScalarsAreEqualityOrGlob)
{
  auto e = make_event(Kind::FaultState, "ros.topic.stale");
  EXPECT_TRUE(matcher("code: ros.topic.stale").matches(e));
  EXPECT_TRUE(matcher("code: ros.topic.*").matches(e));
  EXPECT_TRUE(matcher("code: \"ros.?opic.stale\"").matches(e));
  EXPECT_FALSE(matcher("code: ros.topic").matches(e));
  EXPECT_TRUE(matcher("kind: FAULT_STATE").matches(e));
  EXPECT_TRUE(matcher("source.component: { glob: \"lidar_*\" }").matches(e));
}

TEST(Matcher, AttributesCompareByType)
{
  auto e = make_event(Kind::FaultState, "ros.topic.stale");
  e.attrs["state"] = std::string("ACTIVE");
  e.attrs["expected_hz"] = 10.0;
  e.attrs["drops"] = std::int64_t{3};
  EXPECT_TRUE(matcher("attrs.state: ACTIVE").matches(e));
  EXPECT_TRUE(matcher("attrs.expected_hz: { gte: 5 }").matches(e));
  EXPECT_TRUE(matcher("attrs.expected_hz: 10").matches(e)) << "10 == 10.0";
  EXPECT_FALSE(matcher("attrs.expected_hz: { lt: 10 }").matches(e));
  EXPECT_TRUE(matcher("attrs.drops: { gt: 2, lte: 3 }").matches(e)) << "two operators: both hold";
  EXPECT_FALSE(matcher("attrs.drops: { gt: 2, lt: 3 }").matches(e));
  EXPECT_TRUE(matcher("attrs.state: { in: [ACTIVE, LATCHED] }").matches(e));
  EXPECT_FALSE(matcher("attrs.state: { not: { in: [ACTIVE, LATCHED] } }").matches(e));
  EXPECT_TRUE(matcher("attrs.missing: { exists: false }").matches(e));
  EXPECT_FALSE(matcher("attrs.missing: { gte: 0 }").matches(e)) << "absent never compares";
  EXPECT_TRUE(matcher("attrs.missing: { not: MANUAL }").matches(e));
  EXPECT_TRUE(matcher("attrs.state: { regex: \"^ACT\" }").matches(e));
}

TEST(Matcher, SeverityOrderAndTags)
{
  auto e = make_event(Kind::Log, "app.thing", Severity::Warn);
  e.tags = {"perception", "lidar"};
  EXPECT_TRUE(matcher("severity: \">=WARN\"").matches(e));
  EXPECT_FALSE(matcher("severity: \">WARN\"").matches(e));
  EXPECT_TRUE(matcher("severity: \"<ERROR\"").matches(e));
  EXPECT_TRUE(matcher("severity: warning").matches(e)) << "aliases normalize";
  EXPECT_TRUE(matcher("severity: { in: [WARN, ERROR] }").matches(e));
  EXPECT_TRUE(matcher("tags: [perception]").matches(e));
  EXPECT_TRUE(matcher("tags: [lidar, perception]").matches(e));
  EXPECT_FALSE(matcher("tags: [perception, nav]").matches(e));
}

// -- templates ------------------------------------------------------------------------------------

TEST(TemplateTest, RendersVariablesAndRejectsGaps)
{
  const auto t = Template::parse("restart {{ event.attrs.node }} on {{robot}}");
  EXPECT_FALSE(t.is_literal());
  EXPECT_EQ(t.variables(), (std::vector<std::string>{"event.attrs.node", "robot"}));
  auto lookup = [](const std::string & v) -> std::optional<std::string> {
    if (v == "robot") return std::string("tb4");
    if (v == "event.attrs.node") return std::string("/scan_relay");
    return std::nullopt;
  };
  EXPECT_EQ(t.render(lookup), "restart /scan_relay on tb4");
  EXPECT_EQ(Template::parse("{{ nope }}").render(lookup), std::nullopt);
  EXPECT_EQ(
    Template::parse("{{ event.attrs.empty }}").render([](const std::string &) {
      return std::optional<std::string>("");
    }),
    std::nullopt)
    << "a template that renders empty fails";
  EXPECT_TRUE(Template::parse("plain").is_literal());
  EXPECT_THROW(Template::parse("{{ open"), RuleError);
  EXPECT_THROW(Template::parse("{{ a b }}"), RuleError);
}

// -- parsing --------------------------------------------------------------------------------------

constexpr const char * kDesignExamples = R"yaml(
schema: jarvis.rules/v1
rules:
  - id: scan-stale-restart
    description: /scan went stale → restart the scan relay (managed node).
    partition_by: [source.component]
    when:
      event: { kind: FAULT_STATE, code: ros.topic.stale, attrs.state: ACTIVE, attrs.topic: /scan }
    guard: { faults_inactive: [safety.estop.engaged] }
    then:
      - recover: { plan: restart-lifecycle-node, args: { node: "{{ event.attrs.node }}", component: "{{ event.source.component }}" } }
    policy: { cooldown: 30s, max_fires: { count: 3, per: 1h } }

  - id: scan-restart-exhausted
    when:
      event: { kind: SYSTEM, code: jarvis.rule.suppressed, attrs.rule: scan-stale-restart }
    then:
      - recover: { plan: safe-stop }
      - incident: { escalate: true }
      - notify: { channel: ops, message: "Lidar keeps stalling on {{ robot }}. Navigation stopped." }

  - id: nav-goal-aborts
    when:
      count: { event: { kind: FAULT_RAISE, code: ros.action.navigate_to_pose.aborted }, threshold: 3, within: 5m }
    then:
      - recover: { plan: clear-costmaps }
      - ask_agent: { focus: "Why does NavigateToPose keep aborting? Planner, controller or map?" }
    policy: { cooldown: 2m }

  - id: localization-unrecoverable
    when:
      sequence:
        within: 90s
        steps:
          - id: lost
            event: { kind: FAULT_STATE, code: nav.localization.lost, attrs.state: ACTIVE }
          - id: tried
            event: { kind: RECOVERY, attrs.plan: relocalize, attrs.phase: { in: [SUCCEEDED, FAILED] } }
          - id: still_lost
            absent: { kind: FAULT_STATE, code: nav.localization.lost, attrs.state: CLEARED }
            for: 20s
    then:
      - recover: { plan: safe-stop }
      - incident: { escalate: true }

  - id: nav2-server-error
    partition_by: [source.component]
    when:
      event: { kind: FAULT_STATE, code: ros.lifecycle.error, attrs.state: ACTIVE,
               source.component: { in: [controller_server, planner_server, bt_navigator, behavior_server] } }
    then:
      - recover: { plan: nav2-reset }
    policy: { cooldown: 60s, max_fires: { count: 2, per: 1h }, dry_run: true }
)yaml";

TEST(RuleSetParse, DesignExamplesParseIntoTheRightModel)
{
  const auto set = RuleSet::from_yaml(kDesignExamples);
  ASSERT_EQ(set.rules().size(), 5u);

  const Rule & r1 = *set.find("scan-stale-restart");
  EXPECT_EQ(r1.partition_by, std::vector<std::string>{"source.component"});
  EXPECT_EQ(r1.guard.faults_inactive, std::vector<std::string>{"safety.estop.engaged"});
  ASSERT_EQ(r1.then.size(), 1u);
  EXPECT_EQ(r1.then[0].type, RuleAction::Type::Recover);
  EXPECT_EQ(r1.then[0].fields.at("plan").text(), "restart-lifecycle-node");
  EXPECT_EQ(r1.then[0].args.at("node").text(), "{{ event.attrs.node }}");
  EXPECT_EQ(r1.policy.cooldown, 30 * kSecond);
  ASSERT_TRUE(r1.policy.max_fires);
  EXPECT_EQ(r1.policy.max_fires->count, 3);
  EXPECT_EQ(r1.policy.max_fires->per, kHour);

  const auto & count = std::get<CountPattern>(set.find("nav-goal-aborts")->when.v);
  EXPECT_EQ(count.threshold, 3);
  EXPECT_EQ(count.within, 5 * kMinute);

  const auto & seq = std::get<SequencePattern>(set.find("localization-unrecoverable")->when.v);
  ASSERT_EQ(seq.steps.size(), 3u);
  EXPECT_FALSE(seq.steps[1].absent);
  EXPECT_TRUE(seq.steps[2].absent);
  EXPECT_EQ(seq.steps[2].for_duration, 20 * kSecond);

  EXPECT_TRUE(set.find("nav2-server-error")->policy.dry_run) << "dry_run inside policy";
  const auto & incident = set.find("scan-restart-exhausted")->then[1];
  EXPECT_EQ(incident.fields.at("op").text(), "escalate");
}

TEST(RuleSetParse, EveryRuleExampleInTheDesignDocParses)
{
  std::ifstream in(std::string(JARVIS_DOCS_DIR) + "/design/05-rules-and-recovery.md");
  ASSERT_TRUE(in) << "design doc not found";
  std::stringstream doc;
  doc << in.rdbuf();
  const std::string text = doc.str();
  int parsed = 0;
  for (std::size_t pos = text.find("```yaml"); pos != std::string::npos;
       pos = text.find("```yaml", pos + 1)) {
    const auto start = text.find('\n', pos) + 1;
    const auto end = text.find("```", start);
    const std::string block = text.substr(start, end - start);
    if (
      block.find("schema: jarvis.rules/v1") == std::string::npos ||
      block.find("…") != std::string::npos) {
      continue;  // plans, matcher snippets, and the elided format overview
    }
    EXPECT_NO_THROW(RuleSet::from_yaml(block)) << block;
    ++parsed;
  }
  EXPECT_GE(parsed, 1);
}

TEST(RuleSetParse, AllAndAnyNestPatterns)
{
  const auto set = RuleSet::from_yaml(R"(
schema: jarvis.rules/v1
rules:
  - id: brownout
    when:
      all:
        within: 10s
        of:
          - event: { code: power.voltage.low }
          - any:
              - event: { code: drive.motor.reset }
              - count: { event: { code: comms.can.error }, threshold: 5, within: 2s }
    then: [ { emit: { kind: FAULT_RAISE, code: power.brownout, severity: ERROR, message: "brownout" } } ]
)");
  const auto & all = std::get<AllPattern>(set.rules().front().when.v);
  EXPECT_EQ(all.within, 10 * kSecond);
  ASSERT_EQ(all.of.size(), 2u);
  EXPECT_EQ(std::get<AnyPattern>(all.of[1].v).of.size(), 2u);
  const auto & emit = set.rules().front().then.front();
  EXPECT_EQ(emit.fields.at("kind").text(), "FAULT_RAISE");
  EXPECT_EQ(emit.fields.at("severity").text(), "ERROR");
}

TEST(RuleSetParse, ErrorsNameTheExactPath)
{
  auto has = [](const std::string & error, const std::string & needle) {
    return error.find(needle) != std::string::npos;
  };
  EXPECT_TRUE(has(
    error_of("  - id: Bad_Id\n    when: { event: { code: a } }\n    then: [ { notify: { message: x "
             "} } ]\n"),
    "rules[0].id: 'Bad_Id' is not kebab-case"));
  EXPECT_TRUE(has(
    error_of("  - id: x\n    when: { count: { event: { code: a }, threshold: 0, within: 1s } }\n"
             "    then: [ { notify: { message: x } } ]\n"),
    "rules[0].when.count.threshold: must be >= 1"));
  EXPECT_TRUE(has(
    error_of(
      "  - id: x\n    when: { event: { cod: a } }\n    then: [ { notify: { message: x } } ]\n"),
    "rules[0].when.event.cod: unknown field"));
  EXPECT_TRUE(has(
    error_of("  - id: x\n    when: { event: {} }\n    then: [ { notify: { message: x } } ]\n"),
    "would match every event"));
  EXPECT_TRUE(has(
    error_of("  - id: x\n    when: { event: { kind: FAULTY } }\n    then: [ { notify: { message: x "
             "} } ]\n"),
    "rules[0].when.event.kind: unknown kind 'FAULTY'"));
  EXPECT_TRUE(has(
    error_of("  - id: x\n    when: { event: { severity: \">=LOUD\" } }\n    then: [ { notify: { "
             "message: x } } ]\n"),
    "invalid severity comparison"));
  EXPECT_TRUE(has(
    error_of("  - id: x\n    when: { event: { attrs.v: { regex: \"(\" } } }\n    then: [ { notify: "
             "{ message: x } } ]\n"),
    "rules[0].when.event.attrs.v.regex: invalid regex"));
  EXPECT_TRUE(has(
    error_of(
      "  - id: x\n    when:\n      sequence:\n        within: 10s\n        steps:\n"
      "          - absent: { code: a }\n            for: 1s\n          - event: { code: b }\n"
      "    then: [ { notify: { message: x } } ]\n"),
    "cannot start with an absent step"));
  EXPECT_TRUE(has(
    error_of(
      "  - id: x\n    when:\n      sequence:\n        within: 10s\n        steps:\n"
      "          - event: { code: a }\n          - absent: { code: b }\n            for: 10s\n"
      "    then: [ { notify: { message: x } } ]\n"),
    "rules[0].when.sequence.steps[1].for: must be shorter"));
  EXPECT_TRUE(has(
    error_of("  - id: x\n    when: { event: { code: a } }\n    then: [ { notify: { message: \"{{ "
             "evnt.code }}\" } } ]\n"),
    "rules[0].then[0].notify.message: unknown template variable '{{ evnt.code }}'"));
  EXPECT_TRUE(has(
    error_of("  - id: x\n    when: { event: { code: a } }\n    then: [ { notify: { message: \"{{ "
             "steps.lost.code }}\" } } ]\n"),
    "unknown template variable '{{ steps.lost.code }}'"))
    << "no such sequence step";
  EXPECT_TRUE(has(
    error_of("  - id: x\n    when: { event: { code: a } }\n"
             "    then: [ { notify: { message: \"{{ partition.source.node }}\" } } ]\n"),
    "unknown template variable '{{ partition.source.node }}'"))
    << "not a partition key";
  EXPECT_TRUE(has(
    error_of("  - id: x\n    when: { event: { code: a } }\n    then: [ { emit: { kind: "
             "FAULT_STATE, code: a.b } } ]\n"),
    "rules emit LOG, STATE, FAULT_RAISE, FAULT_CLEAR or SYSTEM events"));
  EXPECT_TRUE(has(
    error_of("  - id: x\n    when: { event: { code: a } }\n    then: [ { emit: { kind: LOG, code: "
             "Not.Valid } } ]\n"),
    "rules[0].then[0].emit.code: 'Not.Valid' is not a dotted lowercase code"));
  EXPECT_TRUE(has(
    error_of("  - id: x\n    when: { event: { code: a } }\n    then: [ { incident: { open: true, "
             "escalate: true } } ]\n"),
    "set exactly one of"));
  EXPECT_TRUE(has(
    error_of("  - id: x\n    when: { event: { code: a } }\n    then: []\n"),
    "rules[0].then: expected a non-empty list of actions"));
  EXPECT_TRUE(has(error_of(std::string(kOkRule) + kOkRule), "rules[1].id: duplicate rule id 'ok'"));
  EXPECT_TRUE(has(
    error_of(
      "  - id: x\n    when: { event: { code: a } }\n    then: [ { notify: { message: x } } ]\n"
      "    policy: { coldown: 1s }\n"),
    "rules[0].policy.coldown: unknown key"));
  EXPECT_THROW(RuleSet::from_yaml("rules: []\n"), RuleError) << "the schema line is required";
  EXPECT_TRUE(RuleSet::from_yaml("").rules().empty());
}

TEST(RuleSetParse, DirectoriesMergeAndRejectDuplicateIdsAcrossFiles)
{
  const auto dir = std::filesystem::temp_directory_path() / "jarvis_rules_test";
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);
  auto write = [&dir](const std::string & name, const std::string & id) {
    std::ofstream(dir / name)
      << "schema: jarvis.rules/v1\nrules:\n  - id: " << id
      << "\n    when: { event: { code: a.b } }\n    then: [ { notify: { message: hi } } ]\n";
  };
  write("10-a.yaml", "first");
  write("20-b.yml", "second");
  std::ofstream(dir / "README.md") << "ignored";
  auto set = RuleSet::from_directory(dir.string());
  ASSERT_EQ(set.rules().size(), 2u);
  EXPECT_EQ(set.rules()[0].id, "first") << "files load in name order";
  EXPECT_EQ(set.rules()[1].file, (dir / "20-b.yml").string());

  write("30-c.yaml", "first");
  try {
    RuleSet::from_directory(dir.string());
    FAIL() << "duplicate id across files";
  } catch (const RuleError & e) {
    EXPECT_NE(
      std::string(e.what()).find("30-c.yaml: duplicate rule id 'first' (also in"),
      std::string::npos)
      << e.what();
  }
  std::filesystem::remove_all(dir);
}
