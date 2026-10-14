// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_core/plans.hpp"

#include <algorithm>
#include <cctype>
#include <charconv>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <regex>
#include <sstream>

#include "jarvis_core/util.hpp"
#include "rules_parse.hpp"
#include "yaml_util.hpp"

namespace jarvis_core
{

std::string_view to_string(Requester r)
{
  switch (r) {
    case Requester::Rule:
      return "RULE";
    case Requester::Operator:
      return "OPERATOR";
    case Requester::Agent:
      return "AGENT";
    case Requester::Plan:
      return "PLAN";
  }
  return "OPERATOR";
}

namespace
{

std::string upper(std::string_view text)
{
  std::string out(text);
  std::transform(out.begin(), out.end(), out.begin(), [](unsigned char c) {
    return static_cast<char>(std::toupper(c));
  });
  return out;
}

}  // namespace

std::optional<Requester> parse_requester(std::string_view text)
{
  const auto u = upper(text);
  for (auto r : {Requester::Rule, Requester::Operator, Requester::Agent, Requester::Plan}) {
    if (u == to_string(r)) {
      return r;
    }
  }
  return std::nullopt;
}

std::string_view to_string(AgentPolicy p)
{
  switch (p) {
    case AgentPolicy::Never:
      return "never";
    case AgentPolicy::Advise:
      return "advise";
    case AgentPolicy::Supervised:
      return "supervised";
    case AgentPolicy::Autonomous:
      return "autonomous";
  }
  return "never";
}

std::optional<AgentPolicy> parse_agent_policy(std::string_view text)
{
  const auto u = upper(text);
  if (u == "OBSERVE") {
    return AgentPolicy::Never;
  }
  for (auto p :
       {AgentPolicy::Never, AgentPolicy::Advise, AgentPolicy::Supervised,
        AgentPolicy::Autonomous}) {
    if (u == upper(to_string(p))) {
      return p;
    }
  }
  return std::nullopt;
}

const std::set<std::string> & core_actions()
{
  static const std::set<std::string> actions{"wait_for", "delay", "check", "emit", "run"};
  return actions;
}

const PlanStep * Plan::find_step(const std::string & step_id) const
{
  const auto it = std::find_if(
    steps.begin(), steps.end(), [&step_id](const PlanStep & s) { return s.id == step_id; });
  return it == steps.end() ? nullptr : &*it;
}

// -- JSON <-> YAML --------------------------------------------------------------------------------

namespace
{

using namespace detail;

nlohmann::json yaml_to_json(const YAML::Node & node, const std::string & path)
{
  if (!node || node.IsNull()) {
    return nullptr;
  }
  if (node.IsMap()) {
    nlohmann::json out = nlohmann::json::object();
    for (const auto & item : node) {
      const auto key = item.first.as<std::string>();
      out[key] = yaml_to_json(item.second, path + "." + key);
    }
    return out;
  }
  if (node.IsSequence()) {
    nlohmann::json out = nlohmann::json::array();
    for (std::size_t i = 0; i < node.size(); ++i) {
      out.push_back(yaml_to_json(node[i], path + "[" + std::to_string(i) + "]"));
    }
    return out;
  }
  const AttrValue v = as_value(node, path);
  return std::visit([](const auto & x) { return nlohmann::json(x); }, v);
}

YAML::Node json_to_yaml(const nlohmann::json & j)
{
  if (j.is_object()) {
    YAML::Node out(YAML::NodeType::Map);
    for (const auto & [k, v] : j.items()) {
      out[k] = json_to_yaml(v);
    }
    return out;
  }
  if (j.is_array()) {
    YAML::Node out(YAML::NodeType::Sequence);
    for (const auto & v : j) {
      out.push_back(json_to_yaml(v));
    }
    return out;
  }
  if (j.is_null()) {
    return YAML::Node(YAML::NodeType::Null);
  }
  if (j.is_string()) {
    YAML::Node out(j.get<std::string>());
    out.SetTag("!");  // stays a string, even "5" or "true"
    return out;
  }
  return YAML::Node(j.dump());
}

Matcher matcher_impl(const nlohmann::json & tree, const std::string & path)
{
  return parse_matcher(json_to_yaml(tree), path);
}

Guard guard_impl(const nlohmann::json & tree, const std::string & path)
{
  if (!tree.is_object()) {
    fail(path, "expected a mapping (faults_active, faults_inactive, state)");
  }
  return parse_guard(json_to_yaml(tree), path);
}

std::string fnv1a(const std::string & text)
{
  std::uint64_t h = 1469598103934665603ULL;
  for (unsigned char c : text) {
    h ^= c;
    h *= 1099511628211ULL;
  }
  char buf[17];
  std::snprintf(buf, sizeof(buf), "%016llx", static_cast<unsigned long long>(h));
  return buf;
}

// -- parameters -----------------------------------------------------------------------------------

std::optional<std::string> canonical(const ParamSpec & spec, const std::string & value)
{
  switch (spec.type) {
    case ParamSpec::Type::Int: {
      std::int64_t i = 0;
      const auto * end = value.data() + value.size();
      const auto r = std::from_chars(value.data(), end, i);
      if (value.empty() || r.ec != std::errc() || r.ptr != end) {
        return std::nullopt;
      }
      return std::to_string(i);
    }
    case ParamSpec::Type::Number: {
      try {
        std::size_t used = 0;
        const double d = std::stod(value, &used);
        if (used != value.size()) {
          return std::nullopt;
        }
        return nlohmann::json(d).dump();
      } catch (const std::exception &) {
        return std::nullopt;
      }
    }
    case ParamSpec::Type::Bool:
      if (value == "true" || value == "false") {
        return value;
      }
      return std::nullopt;
    case ParamSpec::Type::String:
      return value;
  }
  return std::nullopt;
}

std::string_view type_name(ParamSpec::Type t)
{
  switch (t) {
    case ParamSpec::Type::String:
      return "string";
    case ParamSpec::Type::Int:
      return "int";
    case ParamSpec::Type::Number:
      return "number";
    case ParamSpec::Type::Bool:
      return "bool";
  }
  return "string";
}

/// Why `value` is not acceptable for `spec`, or nothing.
std::optional<std::string> check_value(
  const std::string & name, const ParamSpec & spec, const std::string & value,
  std::string * canonical_out)
{
  const auto c = canonical(spec, value);
  if (!c) {
    return "parameter '" + name + "': '" + value + "' is not a valid " +
           std::string(type_name(spec.type));
  }
  if (!spec.pattern.empty() && !std::regex_search(*c, std::regex(spec.pattern))) {
    return "parameter '" + name + "': '" + value + "' does not match pattern '" + spec.pattern +
           "'";
  }
  if (
    !spec.one_of.empty() &&
    std::find(spec.one_of.begin(), spec.one_of.end(), *c) == spec.one_of.end()) {
    std::string options;
    for (const auto & o : spec.one_of) {
      options += (options.empty() ? "" : ", ") + o;
    }
    return "parameter '" + name + "': '" + value + "' is not one of " + options;
  }
  if (canonical_out) {
    *canonical_out = *c;
  }
  return std::nullopt;
}

bool is_identifier(const std::string & s, bool allow_dash)
{
  if (
    s.empty() || !(std::islower(static_cast<unsigned char>(s[0])) ||
                   std::isdigit(static_cast<unsigned char>(s[0])) || s[0] == '_')) {
    return false;
  }
  return std::all_of(s.begin(), s.end(), [allow_dash](char c) {
    return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || (allow_dash && c == '-');
  });
}

bool is_kebab(const std::string & id)
{
  if (id.empty() || id.front() == '-' || id.back() == '-' || id.find("--") != std::string::npos) {
    return false;
  }
  return std::all_of(id.begin(), id.end(), [](char c) {
    return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-';
  });
}

Duration positive_duration(const YAML::Node & node, const std::string & path)
{
  const Duration d = as_duration(node, path);
  if (d <= 0) {
    fail(path, "must be greater than 0");
  }
  return d;
}

ParamSpec parse_param(const YAML::Node & node, const std::string & path, const std::string & name)
{
  check_keys(node, path, {"type", "required", "default", "pattern", "one_of", "description"});
  ParamSpec p;
  if (node["type"]) {
    const auto t = as_string(node["type"], path + ".type");
    if (t == "string") {
      p.type = ParamSpec::Type::String;
    } else if (t == "int" || t == "integer") {
      p.type = ParamSpec::Type::Int;
    } else if (t == "number") {
      p.type = ParamSpec::Type::Number;
    } else if (t == "bool" || t == "boolean") {
      p.type = ParamSpec::Type::Bool;
    } else {
      fail(path + ".type", "expected string, int, number or bool");
    }
  }
  if (node["required"]) {
    p.required = as_bool(node["required"], path + ".required");
  }
  if (node["description"]) {
    p.description = as_string(node["description"], path + ".description");
  }
  if (node["pattern"]) {
    if (p.type != ParamSpec::Type::String) {
      fail(path + ".pattern", "only string parameters take a pattern");
    }
    p.pattern = as_string(node["pattern"], path + ".pattern");
    try {
      std::regex check(p.pattern);
    } catch (const std::regex_error & e) {
      fail(path + ".pattern", std::string("invalid regex: ") + e.what());
    }
  }
  if (node["one_of"]) {
    if (!node["one_of"].IsSequence() || node["one_of"].size() == 0) {
      fail(path + ".one_of", "expected a non-empty list");
    }
    for (std::size_t i = 0; i < node["one_of"].size(); ++i) {
      const std::string ipath = path + ".one_of[" + std::to_string(i) + "]";
      const auto text = as_string(node["one_of"][i], ipath);
      const auto c = canonical(p, text);
      if (!c) {
        fail(ipath, "not a valid " + std::string(type_name(p.type)));
      }
      p.one_of.push_back(*c);
    }
  }
  if (node["default"]) {
    if (p.required) {
      fail(path + ".default", "a required parameter has no default");
    }
    const auto text = as_string(node["default"], path + ".default");
    std::string c;
    if (const auto err = check_value(name, p, text, &c)) {
      fail(path + ".default", *err);
    }
    p.default_value = c;
  }
  return p;
}

// -- templates ------------------------------------------------------------------------------------

void check_vars(const Template & t, const std::string & path, const std::set<std::string> & params)
{
  for (const auto & var : t.variables()) {
    bool ok =
      var == "plan.id" || var == "execution.id" || var == "correlation_id" || var == "robot";
    if (var.starts_with("params.")) {
      ok = params.count(var.substr(7)) != 0;
    }
    if (!ok) {
      fail(
        path, "unknown template variable '{{ " + var +
                " }}' (params.<name>, plan.id, execution.id, correlation_id, robot)");
    }
  }
}

Template plan_template(
  const YAML::Node & node, const std::string & path, const std::set<std::string> & params)
{
  Template t = parse_template(node, path);
  check_vars(t, path, params);
  return t;
}

bool has_template(const std::string & s) { return s.find("{{") != std::string::npos; }

void check_tree(
  const nlohmann::json & tree, const std::string & path, const std::set<std::string> & params)
{
  if (tree.is_object()) {
    for (const auto & [k, v] : tree.items()) {
      check_tree(v, path + "." + k, params);
    }
  } else if (tree.is_array()) {
    for (std::size_t i = 0; i < tree.size(); ++i) {
      check_tree(tree[i], path + "[" + std::to_string(i) + "]", params);
    }
  } else if (tree.is_string() && has_template(tree.get<std::string>())) {
    try {
      check_vars(Template::parse(tree.get<std::string>()), path, params);
    } catch (const RuleError & e) {
      fail(path, e.what());
    }
  }
}

// -- core action arguments ------------------------------------------------------------------------

void check_keys_json(
  const nlohmann::json & obj, const std::string & path,
  std::initializer_list<std::string_view> keys)
{
  if (!obj.is_object()) {
    fail(path, "expected a mapping");
  }
  for (const auto & [k, v] : obj.items()) {
    if (std::find(keys.begin(), keys.end(), k) == keys.end()) {
      fail(path + "." + k, "unknown key");
    }
  }
}

const std::string * json_string(const nlohmann::json & obj, const std::string & key)
{
  const auto it = obj.find(key);
  return it != obj.end() && it->is_string() ? it->get_ptr<const std::string *>() : nullptr;
}

const std::set<Kind> & emittable_kinds()
{
  static const std::set<Kind> kinds{
    Kind::Log, Kind::State, Kind::FaultRaise, Kind::FaultClear, Kind::System};
  return kinds;
}

void check_core_args(const PlanStep & s, const std::string & path)
{
  const auto & w = s.with;
  const std::string wpath = path + ".with";
  if (s.action == "wait_for") {
    check_keys_json(w, wpath, {"event", "fault", "state"});
    const bool event = w.contains("event");
    const bool fault = w.contains("fault");
    if (event == fault) {
      fail(wpath, "needs either 'event' (a matcher) or 'fault' + 'state'");
    }
    if (event) {
      if (w.contains("state")) {
        fail(wpath + ".state", "only with 'fault'");
      }
      matcher_impl(w["event"], wpath + ".event");
      return;
    }
    check_keys_json(w["fault"], wpath + ".fault", {"code", "component"});
    if (!json_string(w["fault"], "code")) {
      fail(wpath + ".fault", "needs 'code'");
    }
    if (w["fault"].contains("component") && !json_string(w["fault"], "component")) {
      fail(wpath + ".fault.component", "expected a string");
    }
    const auto * state = json_string(w, "state");
    if (!state || (*state != "ACTIVE" && *state != "CLEARED" && *state != "LATCHED")) {
      fail(wpath + ".state", "expected ACTIVE, LATCHED or CLEARED");
    }
  } else if (s.action == "delay") {
    check_keys_json(w, wpath, {"duration"});
    const auto * d = json_string(w, "duration");
    if (!d) {
      fail(wpath, "needs 'duration' (e.g. 2s)");
    }
    if (!has_template(*d) && (!parse_duration(*d) || *parse_duration(*d) < 0)) {
      fail(wpath + ".duration", "invalid duration '" + *d + "'");
    }
  } else if (s.action == "check") {
    if (w.empty()) {
      fail(wpath, "needs a guard (faults_active, faults_inactive, state)");
    }
    guard_impl(w, wpath);
  } else if (s.action == "emit") {
    check_keys_json(w, wpath, {"kind", "code", "severity", "message", "attrs"});
    const auto * code = json_string(w, "code");
    if (!code) {
      fail(wpath, "needs 'code'");
    }
    if (!has_template(*code) && !is_valid_code(*code)) {
      fail(wpath + ".code", "'" + *code + "' is not a dotted lowercase code");
    }
    if (w.contains("kind")) {
      const auto * k = json_string(w, "kind");
      const auto kind = k ? parse_kind(*k) : std::nullopt;
      if (!(k && has_template(*k)) && (!kind || emittable_kinds().count(*kind) == 0)) {
        fail(wpath + ".kind", "plans emit LOG, STATE, FAULT_RAISE, FAULT_CLEAR or SYSTEM events");
      }
    }
    if (w.contains("severity")) {
      const auto * sev = json_string(w, "severity");
      if (!sev || (!has_template(*sev) && !parse_severity(*sev))) {
        fail(wpath + ".severity", "invalid severity (DEBUG, INFO, WARN, ERROR, CRITICAL)");
      }
    }
    if (w.contains("attrs")) {
      if (!w["attrs"].is_object()) {
        fail(wpath + ".attrs", "expected a mapping");
      }
      for (const auto & [k, v] : w["attrs"].items()) {
        if (!v.is_primitive() || v.is_null()) {
          fail(wpath + ".attrs." + k, "expected a scalar");
        }
      }
    }
  } else if (s.action == "run") {
    check_keys_json(w, wpath, {"plan", "args"});
    const auto * plan = json_string(w, "plan");
    if (!plan || has_template(*plan)) {
      fail(
        wpath + ".plan", "needs a plan id (templates are not allowed: plans are checked at load)");
    }
    if (w.contains("args")) {
      if (!w["args"].is_object()) {
        fail(wpath + ".args", "expected a mapping");
      }
      for (const auto & [k, v] : w["args"].items()) {
        if (!v.is_primitive() || v.is_null()) {
          fail(wpath + ".args." + k, "expected a scalar");
        }
      }
    }
  }
}

// -- plans ----------------------------------------------------------------------------------------

PlanStep parse_step(
  const YAML::Node & node, const std::string & path, const std::set<std::string> & params)
{
  check_keys(
    node, path, {"id", "action", "with", "timeout", "retry", "when", "continue_on_failure"});
  if (!node["id"] || !node["action"]) {
    fail(path, "needs 'id' and 'action'");
  }
  PlanStep s;
  s.id = as_string(node["id"], path + ".id");
  if (!is_identifier(s.id, true)) {
    fail(path + ".id", "'" + s.id + "' must be lowercase letters, digits, '_' or '-'");
  }
  s.action = as_string(node["action"], path + ".action");
  if (node["with"]) {
    if (!node["with"].IsMap()) {
      fail(path + ".with", "expected a mapping");
    }
    s.with = yaml_to_json(node["with"], path + ".with");
    check_tree(s.with, path + ".with", params);
  }
  if (node["timeout"]) {
    s.timeout = positive_duration(node["timeout"], path + ".timeout");
  }
  if (const auto r = node["retry"]) {
    const std::string rpath = path + ".retry";
    check_keys(r, rpath, {"attempts", "backoff", "multiplier"});
    if (!r["attempts"]) {
      fail(rpath, "needs 'attempts'");
    }
    s.retry_attempts = as_int(r["attempts"], rpath + ".attempts");
    if (s.retry_attempts < 1 || s.retry_attempts > 10) {
      fail(rpath + ".attempts", "must be between 1 and 10");
    }
    if (r["backoff"]) {
      s.retry_backoff = as_duration(r["backoff"], rpath + ".backoff");
    }
    if (r["multiplier"]) {
      s.retry_multiplier = as_double(r["multiplier"], rpath + ".multiplier");
      if (s.retry_multiplier < 1.0 || s.retry_multiplier > 10.0) {
        fail(rpath + ".multiplier", "must be between 1.0 and 10.0");
      }
    }
  }
  if (node["when"]) {
    s.when = parse_guard(node["when"], path + ".when");
  }
  if (node["continue_on_failure"]) {
    s.continue_on_failure = as_bool(node["continue_on_failure"], path + ".continue_on_failure");
  }
  if (core_actions().count(s.action)) {
    check_core_args(s, path);
  } else if (s.action.find('.') == std::string::npos) {
    fail(
      path + ".action",
      "unknown core action '" + s.action +
        "' (wait_for, delay, check, emit, run; actuators are named <provider>.<name>)");
  }
  return s;
}

FailureStep parse_failure_step(
  const YAML::Node & node, const std::string & path, const std::set<std::string> & params)
{
  check_keys(node, path, {"run", "escalate"});
  if (node.size() != 1) {
    fail(path, "expected exactly one of 'run' or 'escalate'");
  }
  FailureStep f;
  if (const auto run = node["run"]) {
    f.type = FailureStep::Type::Run;
    check_keys(run, path + ".run", {"plan", "args"});
    if (!run["plan"]) {
      fail(path + ".run", "needs 'plan'");
    }
    f.plan = as_string(run["plan"], path + ".run.plan");
    if (const auto args = run["args"]) {
      if (!args.IsMap()) {
        fail(path + ".run.args", "expected a mapping");
      }
      for (const auto & item : args) {
        const auto name = item.first.as<std::string>();
        f.args.emplace(name, plan_template(item.second, path + ".run.args." + name, params));
      }
    }
  } else {
    f.type = FailureStep::Type::Escalate;
    f.to = as_string(node["escalate"], path + ".escalate");
    if (f.to.empty()) {
      fail(path + ".escalate", "expected who to escalate to (e.g. operator)");
    }
  }
  return f;
}

std::set<Requester> parse_requesters(const YAML::Node & node, const std::string & path)
{
  std::set<Requester> out;
  const auto list = as_string_list(node, path);
  for (std::size_t i = 0; i < list.size(); ++i) {
    const auto r = parse_requester(list[i]);
    if (!r) {
      fail(path + "[" + std::to_string(i) + "]", "expected RULE, OPERATOR, AGENT or PLAN");
    }
    out.insert(*r);
  }
  return out;
}

Plan parse_plan(const YAML::Node & node, const std::string & path)
{
  check_keys(
    node, path,
    {"id", "description", "params", "locks", "on_conflict", "queue_ttl", "allowed_requesters",
     "agent_policy", "requires_approval", "preconditions", "max_attempts", "timeout", "steps",
     "on_failure"});
  if (!node["id"]) {
    fail(path, "needs 'id'");
  }
  Plan p;
  p.id = as_string(node["id"], path + ".id");
  if (!is_kebab(p.id)) {
    fail(path + ".id", "'" + p.id + "' must be kebab-case (lowercase letters, digits, '-')");
  }
  p.rev = fnv1a(YAML::Dump(node));
  if (node["description"]) {
    p.description = as_string(node["description"], path + ".description");
  }
  std::set<std::string> names;
  if (const auto params = node["params"]) {
    if (!params.IsMap()) {
      fail(path + ".params", "expected a mapping of parameter names");
    }
    for (const auto & item : params) {
      const auto name = item.first.as<std::string>();
      if (!is_identifier(name, false)) {
        fail(path + ".params." + name, "names are lowercase letters, digits and '_'");
      }
      p.params.emplace(name, parse_param(item.second, path + ".params." + name, name));
      names.insert(name);
    }
  }
  if (node["locks"]) {
    const auto & locks = node["locks"];
    if (!locks.IsSequence()) {
      fail(path + ".locks", "expected a list");
    }
    for (std::size_t i = 0; i < locks.size(); ++i) {
      p.locks.push_back(plan_template(locks[i], path + ".locks[" + std::to_string(i) + "]", names));
    }
  }
  if (node["on_conflict"]) {
    const auto c = as_string(node["on_conflict"], path + ".on_conflict");
    if (c != "reject" && c != "queue") {
      fail(path + ".on_conflict", "expected reject or queue");
    }
    p.queue_on_conflict = c == "queue";
  }
  if (node["queue_ttl"]) {
    p.queue_ttl = positive_duration(node["queue_ttl"], path + ".queue_ttl");
  }
  if (node["allowed_requesters"]) {
    p.allowed_requesters =
      parse_requesters(node["allowed_requesters"], path + ".allowed_requesters");
  }
  if (node["agent_policy"]) {
    const auto text = as_string(node["agent_policy"], path + ".agent_policy");
    const auto policy = parse_agent_policy(text);
    if (!policy) {
      fail(path + ".agent_policy", "expected never, advise, supervised or autonomous");
    }
    p.agent_policy = *policy;
  }
  if (node["requires_approval"]) {
    p.requires_approval = parse_requesters(node["requires_approval"], path + ".requires_approval");
  }
  if (node["preconditions"]) {
    p.preconditions = parse_guard(node["preconditions"], path + ".preconditions");
  }
  if (const auto ma = node["max_attempts"]) {
    check_keys(ma, path + ".max_attempts", {"count", "per"});
    if (!ma["count"] || !ma["per"]) {
      fail(path + ".max_attempts", "needs 'count' and 'per'");
    }
    MaxFires m;
    m.count = as_int(ma["count"], path + ".max_attempts.count");
    if (m.count < 1) {
      fail(path + ".max_attempts.count", "must be >= 1");
    }
    m.per = positive_duration(ma["per"], path + ".max_attempts.per");
    p.max_attempts = m;
  }
  if (node["timeout"]) {
    p.timeout = positive_duration(node["timeout"], path + ".timeout");
  }
  const auto steps = node["steps"];
  if (!steps || !steps.IsSequence() || steps.size() == 0) {
    fail(path + ".steps", "needs at least one step");
  }
  for (std::size_t i = 0; i < steps.size(); ++i) {
    const std::string spath = path + ".steps[" + std::to_string(i) + "]";
    PlanStep s = parse_step(steps[i], spath, names);
    if (p.find_step(s.id)) {
      fail(spath + ".id", "duplicate step id '" + s.id + "'");
    }
    p.steps.push_back(std::move(s));
  }
  if (const auto of = node["on_failure"]) {
    if (!of.IsSequence()) {
      fail(path + ".on_failure", "expected a list");
    }
    for (std::size_t i = 0; i < of.size(); ++i) {
      p.on_failure.push_back(
        parse_failure_step(of[i], path + ".on_failure[" + std::to_string(i) + "]", names));
    }
  }
  return p;
}

std::string read_file(const std::string & path)
{
  std::ifstream in(path);
  if (!in) {
    throw PlanError("cannot open '" + path + "'");
  }
  std::stringstream buffer;
  buffer << in.rdbuf();
  return buffer.str();
}

std::string where(const Plan & p)
{
  return (p.file.empty() ? "" : p.file + ": ") + "plan '" + p.id + "'";
}

}  // namespace

// -- PlanSet --------------------------------------------------------------------------------------

PlanSet PlanSet::from_yaml(const std::string & text, const std::string & file)
{
  PlanSet set;
  try {
    const YAML::Node root = load(text);
    if (!root || root.IsNull()) {
      return set;
    }
    check_keys(root, "document", {"schema", "plans"});
    if (!root["schema"] || as_string(root["schema"], "schema") != "jarvis.plans/v1") {
      fail("schema", "expected 'jarvis.plans/v1'");
    }
    const auto plans = root["plans"];
    if (plans && !plans.IsSequence()) {
      fail("plans", "expected a list");
    }
    for (std::size_t i = 0; plans && i < plans.size(); ++i) {
      const std::string path = "plans[" + std::to_string(i) + "]";
      Plan p = parse_plan(plans[i], path);
      p.file = file;
      if (set.find(p.id)) {
        fail(path + ".id", "duplicate plan id '" + p.id + "'");
      }
      set.plans_.push_back(std::move(p));
    }
  } catch (const YamlError & e) {
    throw PlanError(e.what());
  }
  return set;
}

PlanSet PlanSet::from_file(const std::string & path)
{
  try {
    return from_yaml(read_file(path), path);
  } catch (const PlanError & e) {
    const std::string what = e.what();
    throw PlanError(what.starts_with("cannot open") ? what : path + ": " + what);
  }
}

PlanSet PlanSet::from_directory(const std::string & dir)
{
  std::vector<std::string> files;
  std::error_code ec;
  for (const auto & entry : std::filesystem::directory_iterator(dir, ec)) {
    const auto ext = entry.path().extension();
    if (entry.is_regular_file() && (ext == ".yaml" || ext == ".yml")) {
      files.push_back(entry.path().string());
    }
  }
  if (ec) {
    throw PlanError("cannot read plans directory '" + dir + "': " + ec.message());
  }
  std::sort(files.begin(), files.end());
  PlanSet set;
  for (const auto & f : files) {
    set.merge(from_file(f));
  }
  return set;
}

void PlanSet::merge(PlanSet other)
{
  for (auto & p : other.plans_) {
    if (const Plan * existing = find(p.id)) {
      throw PlanError(
        p.file + ": duplicate plan id '" + p.id + "' (also in " + existing->file + ")");
    }
    plans_.push_back(std::move(p));
  }
}

const Plan * PlanSet::find(const std::string & id) const
{
  const auto it =
    std::find_if(plans_.begin(), plans_.end(), [&id](const Plan & p) { return p.id == id; });
  return it == plans_.end() ? nullptr : &*it;
}

std::vector<std::string> PlanSet::validate(
  const std::set<std::string> & actuators, int max_depth) const
{
  std::vector<std::string> warnings;
  // Each `run` (step or ladder) must name a plan that accepts it with the args it gets.
  auto check_call = [this](
                      const Plan & from, const std::string & what, const std::string & target,
                      const std::set<std::string> & given) {
    const Plan * to = find(target);
    if (!to) {
      throw PlanError(where(from) + " " + what + ": unknown plan '" + target + "'");
    }
    if (to->allowed_requesters.count(Requester::Plan) == 0) {
      throw PlanError(
        where(from) + " " + what + ": plan '" + target +
        "' does not allow PLAN requests (add PLAN to its allowed_requesters)");
    }
    for (const auto & name : given) {
      if (to->params.count(name) == 0) {
        throw PlanError(
          where(from) + " " + what + ": plan '" + target + "' has no parameter '" + name + "'");
      }
    }
    for (const auto & [name, spec] : to->params) {
      if (spec.required && given.count(name) == 0) {
        throw PlanError(
          where(from) + " " + what + ": missing required argument '" + name + "' for plan '" +
          target + "'");
      }
    }
  };
  std::map<std::string, std::vector<std::string>> calls;   // run steps and ladders
  std::map<std::string, std::vector<std::string>> nested;  // run steps only
  for (const auto & p : plans_) {
    bool verifies = false;
    Duration budget = 0;
    for (const auto & s : p.steps) {
      if (!core_actions().count(s.action) && !actuators.count(s.action)) {
        std::string known;
        for (const auto & a : actuators) {
          known += (known.empty() ? "" : ", ") + a;
        }
        throw PlanError(
          where(p) + " step '" + s.id + "': unknown action '" + s.action + "'" +
          (known.empty() ? std::string(" (no actuators registered)") : " (known: " + known + ")"));
      }
      verifies = verifies || s.action == "wait_for" || s.action == "check";
      if (s.action == "run") {
        const auto target = s.with.at("plan").get<std::string>();
        std::set<std::string> given;
        if (s.with.contains("args")) {
          for (const auto & [k, v] : s.with["args"].items()) {
            given.insert(k);
          }
        }
        check_call(p, "step '" + s.id + "'", target, given);
        calls[p.id].push_back(target);
        nested[p.id].push_back(target);
      }
      Duration backoff = s.retry_backoff;
      budget += s.timeout;
      for (int a = 0; a < s.retry_attempts; ++a) {
        budget += s.timeout + backoff;
        backoff = static_cast<Duration>(static_cast<double>(backoff) * s.retry_multiplier);
      }
    }
    for (std::size_t i = 0; i < p.on_failure.size(); ++i) {
      const auto & f = p.on_failure[i];
      if (f.type == FailureStep::Type::Run) {
        std::set<std::string> given;
        for (const auto & [k, v] : f.args) {
          given.insert(k);
        }
        check_call(p, "on_failure[" + std::to_string(i) + "]", f.plan, given);
        calls[p.id].push_back(f.plan);
      }
    }
    if (!verifies) {
      warnings.push_back(where(p) + " never verifies its effect (no wait_for or check step)");
    }
    if (budget > p.timeout) {
      warnings.push_back(
        where(p) + ": its steps can take up to " + format_duration(budget) +
        ", longer than its timeout (" + format_duration(p.timeout) + ")");
    }
  }
  // Cycles through `run` steps or ladders would never end.
  std::map<std::string, int> color;  // 0 new, 1 on the stack, 2 done
  std::vector<std::string> stack;
  std::function<void(const std::string &)> visit = [&](const std::string & id) {
    color[id] = 1;
    stack.push_back(id);
    for (const auto & next : calls[id]) {
      if (color[next] == 1) {
        std::string cycle;
        for (auto it = std::find(stack.begin(), stack.end(), next); it != stack.end(); ++it) {
          cycle += *it + " -> ";
        }
        throw PlanError("plans form a cycle: " + cycle + next);
      }
      if (color[next] == 0) {
        visit(next);
      }
    }
    stack.pop_back();
    color[id] = 2;
  };
  for (const auto & p : plans_) {
    if (color[p.id] == 0) {
      visit(p.id);
    }
  }
  // Nesting depth through `run` steps (acyclic now).
  std::map<std::string, int> depth;
  std::function<int(const std::string &)> depth_of = [&](const std::string & id) {
    if (const auto it = depth.find(id); it != depth.end()) {
      return it->second;
    }
    int d = 1;
    for (const auto & child : nested[id]) {
      d = std::max(d, 1 + depth_of(child));
    }
    return depth[id] = d;
  };
  for (const auto & p : plans_) {
    if (depth_of(p.id) > max_depth) {
      throw PlanError(
        where(p) + " nests " + std::to_string(depth_of(p.id)) + " plans deep through 'run' (max " +
        std::to_string(max_depth) + ")");
    }
  }
  return warnings;
}

// -- runtime helpers ------------------------------------------------------------------------------

std::optional<std::string> bind_params(
  const Plan & plan, const std::map<std::string, std::string> & args,
  std::map<std::string, std::string> & bound)
{
  bound.clear();
  for (const auto & [name, value] : args) {
    if (plan.params.count(name) == 0) {
      return "unknown parameter '" + name + "' for plan '" + plan.id + "'";
    }
  }
  for (const auto & [name, spec] : plan.params) {
    const auto it = args.find(name);
    if (it == args.end()) {
      if (spec.default_value) {
        bound[name] = *spec.default_value;
      } else if (spec.required) {
        return "missing required parameter '" + name + "'";
      }
      continue;
    }
    std::string c;
    if (auto err = check_value(name, spec, it->second, &c)) {
      return err;
    }
    bound[name] = c;
  }
  return std::nullopt;
}

nlohmann::json param_json(const ParamSpec & spec, const std::string & value)
{
  try {
    switch (spec.type) {
      case ParamSpec::Type::Int:
        return std::stoll(value);
      case ParamSpec::Type::Number:
        return std::stod(value);
      case ParamSpec::Type::Bool:
        return value == "true";
      case ParamSpec::Type::String:
        break;
    }
  } catch (const std::exception &) {
  }
  return value;
}

std::optional<std::string> render_tree(nlohmann::json & tree, const JsonLookup & lookup)
{
  if (tree.is_object() || tree.is_array()) {
    for (auto & child : tree) {
      if (auto err = render_tree(child, lookup)) {
        return err;
      }
    }
    return std::nullopt;
  }
  if (!tree.is_string() || !has_template(tree.get<std::string>())) {
    return std::nullopt;
  }
  const std::string text = tree.get<std::string>();
  Template t;
  try {
    t = Template::parse(text);
  } catch (const RuleError &) {
    return text;
  }
  const auto vars = t.variables();
  std::string trimmed = text;
  trimmed.erase(0, trimmed.find_first_not_of(" \t"));
  trimmed.erase(trimmed.find_last_not_of(" \t") + 1);
  if (
    vars.size() == 1 && trimmed.starts_with("{{") && trimmed.ends_with("}}") &&
    trimmed.find("{{", 2) == std::string::npos) {
    auto value = lookup(vars.front());
    if (!value) {
      return text;
    }
    tree = std::move(*value);
    return std::nullopt;
  }
  const auto rendered = t.render([&lookup](const std::string & var) -> std::optional<std::string> {
    const auto v = lookup(var);
    if (!v) {
      return std::nullopt;
    }
    return v->is_string() ? v->get<std::string>() : v->dump();
  });
  if (!rendered) {
    return text;
  }
  tree = *rendered;
  return std::nullopt;
}

Matcher matcher_from_json(const nlohmann::json & tree, const std::string & path)
{
  try {
    return matcher_impl(tree, path);
  } catch (const YamlError & e) {
    throw PlanError(e.what());
  } catch (const std::exception & e) {
    throw PlanError(path + ": " + e.what());
  }
}

Guard guard_from_json(const nlohmann::json & tree, const std::string & path)
{
  try {
    return guard_impl(tree, path);
  } catch (const YamlError & e) {
    throw PlanError(e.what());
  } catch (const std::exception & e) {
    throw PlanError(path + ": " + e.what());
  }
}

}  // namespace jarvis_core
