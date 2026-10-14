// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include <gtest/gtest.h>

#include <fstream>
#include <sstream>
#include <tuple>

#include "jarvis_core/plans.hpp"

using namespace jarvis_core;

namespace
{

std::string header(const std::string & plans)
{
  return "schema: jarvis.plans/v1\nplans:\n" + plans;
}

const std::set<std::string> kNav2Actuators{
  "lifecycle.restart", "nav2.manage_lifecycle", "nav2.clear_costmaps", "service.empty",
  "nav2.cancel_navigation"};

std::string design_doc_plans()
{
  std::ifstream in(std::string(JARVIS_DOCS_DIR) + "/design/05-rules-and-recovery.md");
  std::stringstream doc;
  doc << in.rdbuf();
  const std::string text = doc.str();
  for (std::size_t pos = text.find("```yaml"); pos != std::string::npos;
       pos = text.find("```yaml", pos + 1)) {
    const auto start = text.find('\n', pos) + 1;
    const std::string block = text.substr(start, text.find("```", start) - start);
    if (block.find("schema: jarvis.plans/v1") != std::string::npos) {
      return block;
    }
  }
  return "";
}

}  // namespace

TEST(PlanSetParse, TheDesignDocPlansParseAndValidate)
{
  const auto text = design_doc_plans();
  ASSERT_FALSE(text.empty()) << "no jarvis.plans/v1 block in the design doc";
  const auto set = PlanSet::from_yaml(text);
  ASSERT_EQ(set.plans().size(), 5u);
  const auto warnings = set.validate(kNav2Actuators);
  ASSERT_EQ(warnings.size(), 1u);
  EXPECT_NE(warnings[0].find("clear-costmaps"), std::string::npos) << warnings[0];

  const Plan & p = *set.find("restart-lifecycle-node");
  EXPECT_EQ(p.params.at("node").type, ParamSpec::Type::String);
  EXPECT_TRUE(p.params.at("node").required);
  EXPECT_EQ(p.params.at("node").pattern, "^/");
  ASSERT_EQ(p.locks.size(), 1u);
  EXPECT_EQ(p.locks[0].text(), "node:{{ params.node }}");
  EXPECT_FALSE(p.queue_on_conflict);
  EXPECT_EQ(
    p.allowed_requesters,
    (std::set<Requester>{Requester::Rule, Requester::Operator, Requester::Agent}));
  EXPECT_EQ(p.agent_policy, AgentPolicy::Autonomous);
  EXPECT_TRUE(p.requires_approval.empty());
  EXPECT_EQ(p.preconditions.faults_inactive, std::vector<std::string>{"safety.estop.engaged"});
  ASSERT_TRUE(p.max_attempts);
  EXPECT_EQ(p.max_attempts->count, 5);
  EXPECT_EQ(p.max_attempts->per, kHour);
  EXPECT_EQ(p.timeout, 90 * kSecond);
  ASSERT_EQ(p.steps.size(), 2u);
  EXPECT_EQ(p.steps[0].action, "lifecycle.restart");
  EXPECT_EQ(p.steps[0].with, (nlohmann::json{{"node", "{{ params.node }}"}}));
  EXPECT_EQ(p.steps[0].timeout, 20 * kSecond);
  EXPECT_EQ(p.steps[0].retry_attempts, 2);
  EXPECT_EQ(p.steps[0].retry_backoff, 2 * kSecond);
  EXPECT_DOUBLE_EQ(p.steps[0].retry_multiplier, 2.0);
  EXPECT_EQ(p.steps[1].with["state"], "CLEARED");
  ASSERT_EQ(p.on_failure.size(), 2u);
  EXPECT_EQ(p.on_failure[0].type, FailureStep::Type::Run);
  EXPECT_EQ(p.on_failure[0].plan, "nav2-reset");
  EXPECT_EQ(p.on_failure[1].type, FailureStep::Type::Escalate);
  EXPECT_EQ(p.on_failure[1].to, "operator");

  EXPECT_EQ(set.find("nav2-reset")->agent_policy, AgentPolicy::Supervised);
  EXPECT_EQ(set.find("clear-costmaps")->steps[0].timeout, 5 * kSecond);
  EXPECT_EQ(set.find("relocalize")->agent_policy, AgentPolicy::Supervised);
}

TEST(PlanSetParse, DefaultsAndOptionalFields)
{
  const auto set = PlanSet::from_yaml(header(R"(
  - id: poke
    params:
      count: { type: int, default: "3" }
      ratio: { type: number, default: "0.5" }
      enable: { type: bool }
      side: { type: string, one_of: [left, right], default: right, description: which side }
    locks: ["side:{{ params.side }}"]
    on_conflict: queue
    queue_ttl: 10s
    requires_approval: [agent, RULE]
    steps:
      - id: say
        action: emit
        with: { code: demo.poke, message: "poke {{ params.count }} on {{ robot }}", attrs: { n: 1 } }
      - id: nap
        action: delay
        with: { duration: 2s }
        when: { faults_inactive: [safety.*] }
        continue_on_failure: true
      - id: look
        action: check
        with: { state: { robot.motion: STOPPED } }
        retry: { attempts: 3, backoff: 1s }
)"));
  const Plan & p = set.plans().at(0);
  EXPECT_EQ(p.params.at("count").default_value, "3");
  EXPECT_EQ(p.params.at("ratio").default_value, "0.5");
  EXPECT_FALSE(p.params.at("enable").default_value);
  EXPECT_EQ(p.params.at("side").one_of, (std::vector<std::string>{"left", "right"}));
  EXPECT_TRUE(p.queue_on_conflict);
  EXPECT_EQ(p.queue_ttl, 10 * kSecond);
  EXPECT_EQ(p.allowed_requesters.size(), 4u) << "all four by default";
  EXPECT_EQ(p.agent_policy, AgentPolicy::Advise) << "agents only advise by default";
  EXPECT_EQ(p.requires_approval, (std::set<Requester>{Requester::Agent, Requester::Rule}));
  EXPECT_EQ(p.timeout, 5 * kMinute);
  EXPECT_EQ(p.steps[0].timeout, 10 * kSecond);
  ASSERT_TRUE(p.steps[1].when);
  EXPECT_EQ(p.steps[1].when->faults_inactive, std::vector<std::string>{"safety.*"});
  EXPECT_TRUE(p.steps[1].continue_on_failure);
  EXPECT_EQ(p.steps[2].retry_attempts, 3);
  EXPECT_DOUBLE_EQ(p.steps[2].retry_multiplier, 1.0);
  EXPECT_EQ(p.steps[0].with["attrs"]["n"], 1) << "plain scalars keep their type";
  EXPECT_FALSE(p.rev.empty());
  EXPECT_TRUE(set.validate({}).size() == 0) << "a check step verifies";
}

TEST(PlanSetParse, RevisionFollowsTheDefinition)
{
  const std::string a =
    "  - { id: a, steps: [{ id: s, action: delay, with: { duration: 1s } }] }\n";
  const std::string b =
    "  - { id: a, steps: [{ id: s, action: delay, with: { duration: 2s } }] }\n";
  EXPECT_EQ(
    PlanSet::from_yaml(header(a)).plans()[0].rev, PlanSet::from_yaml(header(a)).plans()[0].rev);
  EXPECT_NE(
    PlanSet::from_yaml(header(a)).plans()[0].rev, PlanSet::from_yaml(header(b)).plans()[0].rev);
}

TEST(PlanSetParse, RejectsMistakesWithThePath)
{
  const std::vector<std::tuple<std::string, std::string>> cases{
    {"  - { id: a, steps: [{ id: s, action: delay, with: { duration: 1s } }], colour: red }",
     "plans[0].colour: unknown key"},
    {"  - { id: Bad_Id, steps: [{ id: s, action: delay, with: { duration: 1s } }] }",
     "plans[0].id: 'Bad_Id' must be kebab-case"},
    {"  - { id: a }", "plans[0].steps: needs at least one step"},
    {"  - { id: a, steps: [{ id: s, action: delay, with: { duration: 1s } }, { id: s, action: "
     "delay, with: { duration: 1s } }] }",
     "plans[0].steps[1].id: duplicate step id 's'"},
    {"  - { id: a, steps: [{ id: s, action: x.y, with: { node: '{{ params.node }}' } }] }",
     "plans[0].steps[0].with.node: unknown template variable '{{ params.node }}'"},
    {"  - { id: a, steps: [{ id: s, action: wait_for, with: {} }] }",
     "plans[0].steps[0].with: needs either 'event'"},
    {"  - { id: a, steps: [{ id: s, action: wait_for, with: { fault: { code: x }, state: GONE } }] "
     "}",
     "plans[0].steps[0].with.state: expected ACTIVE, LATCHED or CLEARED"},
    {"  - { id: a, steps: [{ id: s, action: wait_for, with: { event: { colour: red } } }] }",
     "plans[0].steps[0].with.event.colour: unknown field"},
    {"  - { id: a, steps: [{ id: s, action: delay, with: { duration: soon } }] }",
     "plans[0].steps[0].with.duration: invalid duration 'soon'"},
    {"  - { id: a, steps: [{ id: s, action: emit, with: { code: Not.Valid } }] }",
     "plans[0].steps[0].with.code: 'Not.Valid' is not a dotted lowercase code"},
    {"  - { id: a, steps: [{ id: s, action: emit, with: { code: x.y, kind: RECOVERY } }] }",
     "plans[0].steps[0].with.kind: plans emit LOG"},
    {"  - { id: a, params: { p: { type: string } }, steps: [{ id: s, action: run, with: { plan: "
     "'{{ params.p }}' } }] }",
     "plans[0].steps[0].with.plan: needs a plan id"},
    {"  - { id: a, steps: [{ id: s, action: restart, with: {} }] }",
     "plans[0].steps[0].action: unknown core action 'restart'"},
    {"  - { id: a, steps: [{ id: s, action: x.y, retry: { attempts: 0 } }] }",
     "plans[0].steps[0].retry.attempts: must be between 1 and 10"},
    {"  - { id: a, steps: [{ id: s, action: x.y, retry: { attempts: 1, multiplier: 0.5 } }] }",
     "plans[0].steps[0].retry.multiplier: must be between 1.0 and 10.0"},
    {"  - { id: a, steps: [{ id: s, action: x.y, timeout: 0s }] }",
     "plans[0].steps[0].timeout: must be greater than 0"},
    {"  - { id: a, params: { n: { type: string, pattern: '^/', default: relative } }, steps: [{ "
     "id: s, action: x.y }] }",
     "plans[0].params.n.default: parameter 'n': 'relative' does not match pattern '^/'"},
    {"  - { id: a, params: { n: { type: int, default: many } }, steps: [{ id: s, action: x.y }] }",
     "plans[0].params.n.default: parameter 'n': 'many' is not a valid int"},
    {"  - { id: a, params: { n: { required: true, default: x } }, steps: [{ id: s, action: x.y }] "
     "}",
     "plans[0].params.n.default: a required parameter has no default"},
    {"  - { id: a, params: { n: { type: int, pattern: x } }, steps: [{ id: s, action: x.y }] }",
     "plans[0].params.n.pattern: only string parameters take a pattern"},
    {"  - { id: a, params: { Bad: {} }, steps: [{ id: s, action: x.y }] }",
     "plans[0].params.Bad: names are lowercase letters, digits and '_'"},
    {"  - { id: a, allowed_requesters: [ROBOT], steps: [{ id: s, action: x.y }] }",
     "plans[0].allowed_requesters[0]: expected RULE, OPERATOR, AGENT or PLAN"},
    {"  - { id: a, agent_policy: yolo, steps: [{ id: s, action: x.y }] }",
     "plans[0].agent_policy: expected never, advise, supervised or autonomous"},
    {"  - { id: a, on_conflict: wait, steps: [{ id: s, action: x.y }] }",
     "plans[0].on_conflict: expected reject or queue"},
    {"  - { id: a, steps: [{ id: s, action: x.y }], on_failure: [{ run: { plan: b }, escalate: "
     "operator }] }",
     "plans[0].on_failure[0]: expected exactly one of 'run' or 'escalate'"},
    {"  - { id: a, steps: [{ id: s, action: x.y }], on_failure: [{ run: { plan: b, args: { n: '{{ "
     "event.code }}' } } }] }",
     "plans[0].on_failure[0].run.args.n: unknown template variable"},
    {"  - { id: a, locks: ['{{ params.x }}'], steps: [{ id: s, action: x.y }] }",
     "plans[0].locks[0]: unknown template variable"},
    {"  - { id: a, max_attempts: { count: 0, per: 1h }, steps: [{ id: s, action: x.y }] }",
     "plans[0].max_attempts.count: must be >= 1"},
  };
  for (const auto & [yaml, expected] : cases) {
    try {
      PlanSet::from_yaml(header(yaml + "\n"));
      ADD_FAILURE() << "accepted: " << yaml;
    } catch (const PlanError & e) {
      EXPECT_NE(std::string(e.what()).find(expected), std::string::npos)
        << "got: " << e.what() << "\nwanted: " << expected;
    }
  }
  EXPECT_THROW(PlanSet::from_yaml("schema: jarvis.rules/v1\nplans: []\n"), PlanError);
  EXPECT_THROW(
    PlanSet::from_yaml(header("  - { id: a, steps: [{ id: s, action: x.y }] }\n  - { id: a, steps: "
                              "[{ id: s, action: x.y }] }\n")),
    PlanError);
  EXPECT_TRUE(PlanSet::from_yaml("").plans().empty());
}

TEST(PlanSetValidate, CatchesCrossPlanMistakes)
{
  auto error = [](const std::string & yaml, const std::set<std::string> & actuators = {"x.y"}) {
    try {
      PlanSet::from_yaml(header(yaml)).validate(actuators);
    } catch (const PlanError & e) {
      return std::string(e.what());
    }
    return std::string("(no error)");
  };
  EXPECT_NE(
    error("  - { id: a, steps: [{ id: s, action: x.z }] }\n")
      .find("unknown action 'x.z' (known: x.y)"),
    std::string::npos);
  EXPECT_NE(
    error("  - { id: a, steps: [{ id: s, action: run, with: { plan: b } }] }\n")
      .find("step 's': unknown plan 'b'"),
    std::string::npos);
  EXPECT_NE(
    error("  - { id: a, steps: [{ id: s, action: x.y }], on_failure: [{ run: { plan: b } }] }\n"
          "  - { id: b, allowed_requesters: [RULE], steps: [{ id: s, action: x.y }] }\n")
      .find("plan 'b' does not allow PLAN requests"),
    std::string::npos);
  EXPECT_NE(
    error("  - { id: a, steps: [{ id: s, action: run, with: { plan: b } }] }\n"
          "  - { id: b, params: { node: { required: true } }, steps: [{ id: s, action: x.y }] }\n")
      .find("missing required argument 'node' for plan 'b'"),
    std::string::npos);
  EXPECT_NE(
    error("  - { id: a, steps: [{ id: s, action: run, with: { plan: b, args: { nod: x } } }] }\n"
          "  - { id: b, params: { node: {} }, steps: [{ id: s, action: x.y }] }\n")
      .find("plan 'b' has no parameter 'nod'"),
    std::string::npos);
  EXPECT_NE(
    error("  - { id: a, steps: [{ id: s, action: x.y }], on_failure: [{ run: { plan: b } }] }\n"
          "  - { id: b, steps: [{ id: s, action: x.y }], on_failure: [{ run: { plan: a } }] }\n")
      .find("plans form a cycle: a -> b -> a"),
    std::string::npos)
    << "ladders that call each other would never end";
  EXPECT_NE(
    error("  - { id: a, steps: [{ id: s, action: run, with: { plan: a } }] }\n")
      .find("cycle: a -> a"),
    std::string::npos);
  EXPECT_NE(
    error("  - { id: a, steps: [{ id: s, action: run, with: { plan: b } }] }\n"
          "  - { id: b, steps: [{ id: s, action: run, with: { plan: c } }] }\n"
          "  - { id: c, steps: [{ id: s, action: run, with: { plan: d } }] }\n"
          "  - { id: d, steps: [{ id: s, action: x.y }] }\n")
      .find("plan 'a' nests 4 plans deep through 'run' (max 3)"),
    std::string::npos);
  // A ladder is not nesting: each entry is a new execution.
  EXPECT_EQ(
    error("  - { id: a, steps: [{ id: s, action: check, with: { faults_inactive: [x] } }], "
          "on_failure: [{ run: { plan: b } }] }\n"
          "  - { id: b, steps: [{ id: s, action: check, with: { faults_inactive: [x] } }], "
          "on_failure: [{ run: { plan: c } }] }\n"
          "  - { id: c, steps: [{ id: s, action: check, with: { faults_inactive: [x] } }], "
          "on_failure: [{ run: { plan: d } }] }\n"
          "  - { id: d, steps: [{ id: s, action: check, with: { faults_inactive: [x] } }] }\n"),
    "(no error)");
  const auto warnings =
    PlanSet::from_yaml(header("  - { id: a, timeout: 5s, steps: [{ id: s, action: x.y, timeout: "
                              "3s, retry: { attempts: 1, backoff: 1s } }] }\n"))
      .validate({"x.y"});
  ASSERT_EQ(warnings.size(), 2u);
  EXPECT_NE(warnings[0].find("never verifies"), std::string::npos);
  EXPECT_NE(warnings[1].find("can take up to 7s, longer than its timeout (5s)"), std::string::npos)
    << warnings[1];
}

TEST(PlanParams, BindChecksTypesPatternsAndDefaults)
{
  const auto set = PlanSet::from_yaml(header(R"(
  - id: p
    params:
      node: { type: string, required: true, pattern: "^/" }
      count: { type: int, default: "2" }
      ratio: { type: number }
      on: { type: bool }
      side: { one_of: [left, right] }
    steps: [{ id: s, action: x.y }]
)"));
  const Plan & p = set.plans()[0];
  std::map<std::string, std::string> bound;
  EXPECT_EQ(bind_params(p, {{"node", "/a"}}, bound), std::nullopt);
  EXPECT_EQ(bound, (std::map<std::string, std::string>{{"node", "/a"}, {"count", "2"}}));
  EXPECT_EQ(
    bind_params(p, {{"node", "/a"}, {"count", "07"}, {"ratio", "1"}, {"on", "true"}}, bound),
    std::nullopt);
  EXPECT_EQ(bound.at("count"), "7");
  EXPECT_EQ(bound.at("ratio"), "1.0");
  EXPECT_EQ(bind_params(p, {}, bound), "missing required parameter 'node'");
  EXPECT_EQ(
    bind_params(p, {{"node", "a"}}, bound), "parameter 'node': 'a' does not match pattern '^/'");
  EXPECT_EQ(
    bind_params(p, {{"node", "/a"}, {"count", "2.5"}}, bound),
    "parameter 'count': '2.5' is not a valid int");
  EXPECT_EQ(
    bind_params(p, {{"node", "/a"}, {"on", "yes"}}, bound),
    "parameter 'on': 'yes' is not a valid bool");
  EXPECT_EQ(
    bind_params(p, {{"node", "/a"}, {"side", "up"}}, bound),
    "parameter 'side': 'up' is not one of left, right");
  EXPECT_EQ(
    bind_params(p, {{"node", "/a"}, {"colour", "red"}}, bound),
    "unknown parameter 'colour' for plan 'p'");

  EXPECT_EQ(param_json(p.params.at("count"), "7"), 7);
  EXPECT_EQ(param_json(p.params.at("on"), "true"), true);
  EXPECT_EQ(param_json(p.params.at("ratio"), "1.5"), 1.5);
  EXPECT_EQ(param_json(p.params.at("node"), "/a"), "/a");
}

TEST(PlanTemplates, RenderTreeKeepsTypesOfWholeVariables)
{
  nlohmann::json tree = {
    {"node", "{{ params.node }}"},
    {"on", "{{ params.on }}"},
    {"label", "restart {{ params.node }} ({{ params.on }})"},
    {"list", {"{{ params.count }}", "literal", 3}},
  };
  const JsonLookup lookup = [](const std::string & var) -> std::optional<nlohmann::json> {
    if (var == "params.node") {
      return nlohmann::json("/relay");
    }
    if (var == "params.on") {
      return nlohmann::json(true);
    }
    if (var == "params.count") {
      return nlohmann::json(2);
    }
    return std::nullopt;
  };
  EXPECT_EQ(render_tree(tree, lookup), std::nullopt);
  EXPECT_EQ(tree["node"], "/relay");
  EXPECT_EQ(tree["on"], true);
  EXPECT_EQ(tree["label"], "restart /relay (true)");
  EXPECT_EQ(tree["list"], (nlohmann::json{2, "literal", 3}));

  nlohmann::json bad = {{"x", {{"y", "a {{ params.missing }}"}}}};
  EXPECT_EQ(render_tree(bad, lookup), "a {{ params.missing }}");
}

TEST(PlanTemplates, MatchersFromJsonKeepTypes)
{
  Event e;
  e.kind = Kind::State;
  e.code = "nav.stack.state";
  e.attrs["value"] = std::string("5");
  EXPECT_TRUE(matcher_from_json({{"kind", "STATE"}, {"code", "nav.*"}, {"attrs.value", "5"}}, "m")
                .matches(e));
  EXPECT_FALSE(matcher_from_json({{"attrs.value", "6"}}, "m").matches(e));
  e.attrs["value"] = std::int64_t{5};
  EXPECT_TRUE(matcher_from_json({{"attrs.value", {{"gte", 4}}}}, "m").matches(e));
  EXPECT_THROW(matcher_from_json({{"colour", "red"}}, "m"), PlanError);
  EXPECT_THROW(matcher_from_json(nlohmann::json::object(), "m"), PlanError);

  const Guard g = guard_from_json(
    {{"faults_inactive", {"safety.*"}}, {"state", {{"robot.motion", "STOPPED"}}}}, "g");
  EXPECT_TRUE(g.passes({}, {{"robot.motion", std::string("STOPPED")}}));
  EXPECT_FALSE(
    g.passes({{"f1", "safety.estop.engaged"}}, {{"robot.motion", std::string("STOPPED")}}));
  EXPECT_FALSE(g.passes({}, {{"robot.motion", std::string("MOVING")}}));
  EXPECT_THROW(guard_from_json({{"colour", "red"}}, "g"), PlanError);
}

TEST(PlanEnums, RoundTrip)
{
  for (auto r : {Requester::Rule, Requester::Operator, Requester::Agent, Requester::Plan}) {
    EXPECT_EQ(parse_requester(to_string(r)), r);
  }
  EXPECT_EQ(parse_requester("agent"), Requester::Agent);
  EXPECT_EQ(parse_requester("robot"), std::nullopt);
  for (auto p :
       {AgentPolicy::Never, AgentPolicy::Advise, AgentPolicy::Supervised,
        AgentPolicy::Autonomous}) {
    EXPECT_EQ(parse_agent_policy(to_string(p)), p);
  }
  EXPECT_EQ(parse_agent_policy("observe"), AgentPolicy::Never);
  EXPECT_LT(AgentPolicy::Advise, AgentPolicy::Supervised)
    << "ordered: min() is the effective level";
}
