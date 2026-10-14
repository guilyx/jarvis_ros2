// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Recovery plans (`jarvis.plans/v1`): ordered steps with timeouts and retries, locks, gates and an
// `on_failure` escalation ladder (docs/design/05-rules-and-recovery.md §8-10).

#pragma once

#include <functional>
#include <map>
#include <nlohmann/json.hpp>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "jarvis_core/rules.hpp"
#include "jarvis_core/types.hpp"

namespace jarvis_core
{

class PlanError : public std::runtime_error
{
public:
  using std::runtime_error::runtime_error;
};

enum class Requester { Rule, Operator, Agent, Plan };
std::string_view to_string(Requester r);
/// Case-insensitive: RULE, OPERATOR, AGENT, PLAN.
std::optional<Requester> parse_requester(std::string_view text);

/// Ordered: an agent's effective autonomy is the lower of the deployment's and the plan's.
enum class AgentPolicy { Never = 0, Advise = 1, Supervised = 2, Autonomous = 3 };
std::string_view to_string(AgentPolicy p);
/// never, advise, supervised, autonomous (`observe` is accepted as never).
std::optional<AgentPolicy> parse_agent_policy(std::string_view text);

struct ParamSpec
{
  enum class Type { String, Int, Number, Bool };
  Type type{Type::String};
  bool required{false};
  std::optional<std::string> default_value;  ///< canonical text, already validated
  std::string pattern;  ///< strings: a regex the value must contain (anchor with ^ and $)
  std::vector<std::string> one_of;
  std::string description;
};

struct PlanStep
{
  std::string id;
  std::string action;
  nlohmann::json with = nlohmann::json::object();  ///< strings may hold `{{ }}` templates
  Duration timeout{10 * kSecond};                  ///< per attempt
  int retry_attempts{0};                           ///< extra attempts after the first
  Duration retry_backoff{0};
  double retry_multiplier{1.0};
  std::optional<Guard> when;  ///< skipped (and recorded) when false
  bool continue_on_failure{false};
};

struct FailureStep
{
  enum class Type { Run, Escalate };
  Type type{Type::Escalate};
  std::string plan;                      ///< run
  std::map<std::string, Template> args;  ///< run
  std::string to{"operator"};            ///< escalate
};

struct Plan
{
  std::string id;
  std::string description;
  std::string rev;  ///< content hash of the definition, recorded on every execution
  std::map<std::string, ParamSpec> params;
  std::vector<Template> locks;  ///< e.g. "node:{{ params.node }}"
  bool queue_on_conflict{false};
  Duration queue_ttl{30 * kSecond};
  std::set<Requester> allowed_requesters{
    Requester::Rule, Requester::Operator, Requester::Agent, Requester::Plan};
  AgentPolicy agent_policy{AgentPolicy::Advise};
  std::set<Requester> requires_approval;
  Guard preconditions;
  std::optional<MaxFires> max_attempts;
  Duration timeout{5 * kMinute};
  std::vector<PlanStep> steps;
  std::vector<FailureStep> on_failure;
  std::string file;

  const PlanStep * find_step(const std::string & step_id) const;
};

/// Actions the core runs itself: wait_for, delay, check, emit, run. Anything else is an actuator
/// provided by the shell.
const std::set<std::string> & core_actions();

class PlanSet
{
public:
  static PlanSet from_yaml(const std::string & text, const std::string & file = "");
  static PlanSet from_file(const std::string & path);
  static PlanSet from_directory(const std::string & dir);

  /// Cross-plan checks: every action is a core action or one of `actuators`, every `run` names a
  /// known plan that accepts PLAN requests and gets its required args, no cycles through `run`
  /// steps or `on_failure` ladders, `run` nesting depth <= `max_depth`. Throws PlanError.
  /// Returns warnings (a plan that never verifies its effect, steps that outlast the plan timeout).
  std::vector<std::string> validate(
    const std::set<std::string> & actuators, int max_depth = 3) const;

  void merge(PlanSet other);
  const std::vector<Plan> & plans() const { return plans_; }
  const Plan * find(const std::string & id) const;

private:
  std::vector<Plan> plans_;
};

/// Checks and completes plan arguments: required ones present, types, patterns and `one_of`
/// respected, defaults filled in, no unknown names. Returns the error, or nothing.
std::optional<std::string> bind_params(
  const Plan & plan, const std::map<std::string, std::string> & args,
  std::map<std::string, std::string> & bound);

/// Variables plan templates resolve: `params.<name>`, `plan.id`, `execution.id`,
/// `correlation_id`, `robot`. A string that is exactly one `{{ var }}` takes the variable's JSON
/// value (so `{{ params.enable }}` of a bool parameter renders as `true`, not `"true"`).
using JsonLookup = std::function<std::optional<nlohmann::json>(const std::string &)>;

/// Renders every `{{ }}` string in a JSON tree. Returns the first template that failed, if any.
std::optional<std::string> render_tree(nlohmann::json & tree, const JsonLookup & lookup);
/// The typed JSON value of a bound parameter.
nlohmann::json param_json(const ParamSpec & spec, const std::string & value);

/// `wait_for` / `check` arguments in their runtime form (after rendering). Throw PlanError.
Matcher matcher_from_json(const nlohmann::json & tree, const std::string & path);
Guard guard_from_json(const nlohmann::json & tree, const std::string & path);

}  // namespace jarvis_core
