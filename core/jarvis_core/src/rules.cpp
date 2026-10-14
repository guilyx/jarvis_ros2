// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_core/rules.hpp"

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <set>
#include <sstream>

#include "jarvis_core/util.hpp"
#include "rules_parse.hpp"
#include "yaml_util.hpp"

namespace jarvis_core
{

namespace
{

std::optional<double> as_number(const AttrValue & v)
{
  if (const auto * i = std::get_if<std::int64_t>(&v)) {
    return static_cast<double>(*i);
  }
  if (const auto * d = std::get_if<double>(&v)) {
    return *d;
  }
  if (const auto * s = std::get_if<std::string>(&v)) {
    try {
      std::size_t used = 0;
      const double d = std::stod(*s, &used);
      if (used == s->size()) {
        return d;
      }
    } catch (const std::exception &) {
    }
  }
  return std::nullopt;
}

bool values_equal(const AttrValue & actual, const AttrValue & expected)
{
  const bool both_numbers =
    !std::holds_alternative<std::string>(expected) && !std::holds_alternative<bool>(expected) &&
    !std::holds_alternative<std::string>(actual) && !std::holds_alternative<bool>(actual);
  if (both_numbers) {
    return as_number(actual) == as_number(expected);
  }
  return attr_to_string(actual) == attr_to_string(expected);
}

bool is_glob(const std::string & s) { return s.find_first_of("*?") != std::string::npos; }

}  // namespace

// -- Test -----------------------------------------------------------------------------------------

Test Test::equals(AttrValue v)
{
  Test t;
  t.op_ = Op::Equals;
  t.value_ = std::move(v);
  return t;
}

Test Test::glob(std::string pattern)
{
  Test t;
  t.op_ = Op::Glob;
  t.value_ = std::move(pattern);
  return t;
}

Test Test::in(std::vector<Test> options)
{
  Test t;
  t.op_ = Op::In;
  t.children_ = std::move(options);
  return t;
}

Test Test::negate(Test inner)
{
  Test t;
  t.op_ = Op::Not;
  t.children_.push_back(std::move(inner));
  return t;
}

Test Test::all_of(std::vector<Test> tests)
{
  Test t;
  t.op_ = Op::AllOf;
  t.children_ = std::move(tests);
  return t;
}

Test Test::regex(const std::string & pattern)
{
  Test t;
  t.op_ = Op::Regex;
  t.value_ = pattern;
  t.regex_ = std::make_shared<const std::regex>(pattern, std::regex::ECMAScript);
  return t;
}

Test Test::compare(Op op, double bound)
{
  Test t;
  t.op_ = op;
  t.bound_ = bound;
  return t;
}

Test Test::exists(bool expected)
{
  Test t;
  t.op_ = Op::Exists;
  t.flag_ = expected;
  return t;
}

Test Test::severity(Op op, Severity bound)
{
  Test t;
  t.op_ = op;
  t.bound_ = static_cast<double>(bound);
  return t;
}

Test Test::has_all(std::vector<std::string> items)
{
  Test t;
  t.op_ = Op::HasAll;
  t.items_ = std::move(items);
  return t;
}

bool Test::matches(
  const std::optional<AttrValue> & value, const std::vector<std::string> * items) const
{
  switch (op_) {
    case Op::Exists:
      return value.has_value() == flag_;
    case Op::HasAll:
      return items && std::all_of(items_.begin(), items_.end(), [items](const std::string & i) {
               return std::find(items->begin(), items->end(), i) != items->end();
             });
    case Op::Not:
      return !children_.front().matches(value, items);
    case Op::In:
      return std::any_of(children_.begin(), children_.end(), [&](const Test & t) {
        return t.matches(value, items);
      });
    case Op::AllOf:
      return std::all_of(children_.begin(), children_.end(), [&](const Test & t) {
        return t.matches(value, items);
      });
    default:
      break;
  }
  if (!value) {
    return false;
  }
  switch (op_) {
    case Op::Equals:
      return values_equal(*value, value_);
    case Op::Glob:
      return glob_match(std::get<std::string>(value_), attr_to_string(*value));
    case Op::Regex:
      return std::regex_search(attr_to_string(*value), *regex_);
    case Op::Gt:
    case Op::Gte:
    case Op::Lt:
    case Op::Lte: {
      const auto n = as_number(*value);
      if (!n) {
        return false;
      }
      return op_ == Op::Gt    ? *n > bound_
             : op_ == Op::Gte ? *n >= bound_
             : op_ == Op::Lt  ? *n < bound_
                              : *n <= bound_;
    }
    case Op::SeverityAtLeast:
    case Op::SeverityAbove:
    case Op::SeverityAtMost:
    case Op::SeverityBelow: {
      const auto s = parse_severity(attr_to_string(*value));
      if (!s) {
        return false;
      }
      const auto n = static_cast<double>(*s);
      return op_ == Op::SeverityAtLeast  ? n >= bound_
             : op_ == Op::SeverityAbove  ? n > bound_
             : op_ == Op::SeverityAtMost ? n <= bound_
                                         : n < bound_;
    }
    default:
      return false;
  }
}

// -- event fields and matchers --------------------------------------------------------------------

std::optional<AttrValue> event_field(const Event & e, std::string_view path)
{
  if (path.starts_with("attrs.")) {
    const auto it = e.attrs.find(path.substr(6));
    return it == e.attrs.end() ? std::nullopt : std::optional<AttrValue>(it->second);
  }
  auto text = [](const std::string & s) -> std::optional<AttrValue> {
    return s.empty() ? std::nullopt : std::optional<AttrValue>(s);
  };
  if (path == "kind") return std::string(to_string(e.kind));
  if (path == "code") return text(e.code);
  if (path == "severity") return std::string(to_string(e.severity));
  if (path == "message") return text(e.message);
  if (path == "id") return text(e.id);
  if (path == "seq") return static_cast<std::int64_t>(e.seq);
  if (path == "correlation_id") return text(e.correlation_id);
  if (path == "causation_id") return text(e.causation_id);
  if (path == "source.robot") return text(e.source.robot);
  if (path == "source.node") return text(e.source.node);
  if (path == "source.component") return text(e.source.component);
  if (path == "source.host") return text(e.source.host);
  return std::nullopt;
}

bool is_event_field(std::string_view path)
{
  static const std::set<std::string_view> fields{
    "kind",         "code",        "severity",         "message",
    "id",           "seq",         "correlation_id",   "causation_id",
    "source.robot", "source.node", "source.component", "source.host"};
  return fields.count(path) != 0 || (path.starts_with("attrs.") && path.size() > 6);
}

bool Matcher::matches(const Event & e) const
{
  return std::all_of(tests.begin(), tests.end(), [&e](const auto & ft) {
    return ft.first == "tags" ? ft.second.matches(std::nullopt, &e.tags)
                              : ft.second.matches(event_field(e, ft.first));
  });
}

// -- guards ---------------------------------------------------------------------------------------

bool Guard::passes(
  const std::map<std::string, std::string> & active_faults,
  const std::map<std::string, AttrValue> & states) const
{
  auto any_active = [&active_faults](const std::string & glob) {
    return std::any_of(active_faults.begin(), active_faults.end(), [&glob](const auto & f) {
      return glob_match(glob, f.second);
    });
  };
  if (!std::all_of(faults_active.begin(), faults_active.end(), any_active)) {
    return false;
  }
  if (std::any_of(faults_inactive.begin(), faults_inactive.end(), any_active)) {
    return false;
  }
  return std::all_of(state.begin(), state.end(), [&states](const auto & cond) {
    const auto it = states.find(cond.first);
    return cond.second.matches(
      it == states.end() ? std::nullopt : std::optional<AttrValue>(it->second));
  });
}

bool is_active_fault_state(std::string_view state)
{
  return state == "ACTIVE" || state == "LATCHED";
}

AttrValue state_value(const Event & e)
{
  const auto it = e.attrs.find("value");
  return it != e.attrs.end() ? it->second : AttrValue(e.message);
}

// -- templates ------------------------------------------------------------------------------------

Template Template::parse(const std::string & text)
{
  Template t;
  t.text_ = text;
  std::size_t pos = 0;
  while (pos < text.size()) {
    const auto open = text.find("{{", pos);
    if (open == std::string::npos) {
      t.parts_.emplace_back(false, text.substr(pos));
      break;
    }
    if (open > pos) {
      t.parts_.emplace_back(false, text.substr(pos, open - pos));
    }
    const auto close = text.find("}}", open + 2);
    if (close == std::string::npos) {
      throw RuleError("'{{' without '}}' in \"" + text + "\"");
    }
    std::string var = text.substr(open + 2, close - open - 2);
    var.erase(0, var.find_first_not_of(" \t"));
    var.erase(var.find_last_not_of(" \t") + 1);
    const bool valid = !var.empty() && std::all_of(var.begin(), var.end(), [](char c) {
      return std::isalnum(static_cast<unsigned char>(c)) || c == '_' || c == '.' || c == '-';
    });
    if (!valid) {
      throw RuleError(
        "invalid template variable '{{" + text.substr(open + 2, close - open - 2) + "}}' in \"" +
        text + "\"");
    }
    t.parts_.emplace_back(true, std::move(var));
    pos = close + 2;
  }
  return t;
}

std::optional<std::string> Template::render(
  const std::function<std::optional<std::string>(const std::string &)> & lookup) const
{
  std::string out;
  for (const auto & [is_var, text] : parts_) {
    if (!is_var) {
      out += text;
      continue;
    }
    const auto value = lookup(text);
    if (!value) {
      return std::nullopt;
    }
    out += *value;
  }
  if (out.empty()) {
    return std::nullopt;
  }
  return out;
}

std::vector<std::string> Template::variables() const
{
  std::vector<std::string> out;
  for (const auto & [is_var, text] : parts_) {
    if (is_var) {
      out.push_back(text);
    }
  }
  return out;
}

bool Template::is_literal() const
{
  return std::none_of(parts_.begin(), parts_.end(), [](const auto & p) { return p.first; });
}

std::string_view to_string(RuleAction::Type type)
{
  switch (type) {
    case RuleAction::Type::Recover:
      return "recover";
    case RuleAction::Type::Emit:
      return "emit";
    case RuleAction::Type::Incident:
      return "incident";
    case RuleAction::Type::Notify:
      return "notify";
    case RuleAction::Type::AskAgent:
      return "ask_agent";
  }
  return "?";
}

// -- shared parsers -------------------------------------------------------------------------------

namespace detail
{

namespace
{

std::optional<Test> parse_severity_comparison(const std::string & text)
{
  struct Prefix
  {
    std::string_view text;
    Test::Op op;
  };
  for (const Prefix p :
       {Prefix{">=", Test::Op::SeverityAtLeast}, Prefix{"<=", Test::Op::SeverityAtMost},
        Prefix{">", Test::Op::SeverityAbove}, Prefix{"<", Test::Op::SeverityBelow}}) {
    if (text.starts_with(p.text)) {
      const auto s = parse_severity(text.substr(p.text.size()));
      if (!s) {
        return std::nullopt;
      }
      return Test::severity(p.op, *s);
    }
  }
  return std::nullopt;
}

}  // namespace

Test parse_test(const YAML::Node & node, const std::string & path, const std::string & field)
{
  if (field == "tags") {
    return Test::has_all(as_string_list(node, path));
  }
  if (node.IsScalar()) {
    AttrValue value = as_value(node, path);
    if (const auto * s = std::get_if<std::string>(&value)) {
      if (field == "severity") {
        if (s->find_first_of("<>") == 0) {
          auto t = parse_severity_comparison(*s);
          if (!t) {
            fail(path, "invalid severity comparison '" + *s + "' (e.g. \">=ERROR\")");
          }
          return *t;
        }
        const auto sev = parse_severity(*s);
        if (!sev) {
          fail(path, "invalid severity '" + *s + "' (DEBUG, INFO, WARN, ERROR, CRITICAL)");
        }
        return Test::equals(std::string(to_string(*sev)));
      }
      if (is_glob(*s)) {
        return Test::glob(*s);
      }
      if (field == "kind") {
        const auto kind = parse_kind(*s);
        if (!kind) {
          fail(
            path, "unknown kind '" + *s +
                    "' (LOG, STATE, FAULT_RAISE, FAULT_CLEAR, FAULT_STATE, "
                    "RULE_MATCH, RECOVERY, INSIGHT, OPERATOR, SYSTEM)");
        }
        return Test::equals(std::string(to_string(*kind)));
      }
    }
    return Test::equals(std::move(value));
  }
  if (node.IsSequence()) {
    fail(path, "a list is only allowed for tags; use { in: [...] } for alternatives");
  }
  if (!node.IsMap() || node.size() == 0) {
    fail(
      path,
      "expected a value or an operator mapping (in, not, glob, regex, gt, gte, lt, lte, exists)");
  }
  std::vector<Test> tests;
  for (const auto & item : node) {
    const auto op = item.first.as<std::string>();
    const std::string opath = path + "." + op;
    const YAML::Node arg = item.second;
    if (op == "in") {
      if (!arg.IsSequence() || arg.size() == 0) {
        fail(opath, "expected a non-empty list");
      }
      std::vector<Test> options;
      for (std::size_t i = 0; i < arg.size(); ++i) {
        options.push_back(parse_test(arg[i], opath + "[" + std::to_string(i) + "]", field));
      }
      tests.push_back(Test::in(std::move(options)));
    } else if (op == "not") {
      tests.push_back(Test::negate(parse_test(arg, opath, field)));
    } else if (op == "glob") {
      tests.push_back(Test::glob(as_string(arg, opath)));
    } else if (op == "regex") {
      const auto pattern = as_string(arg, opath);
      try {
        tests.push_back(Test::regex(pattern));
      } catch (const std::regex_error & e) {
        fail(opath, "invalid regex '" + pattern + "': " + e.what());
      }
    } else if (op == "gt" || op == "gte" || op == "lt" || op == "lte") {
      const Test::Op cmp = op == "gt"    ? Test::Op::Gt
                           : op == "gte" ? Test::Op::Gte
                           : op == "lt"  ? Test::Op::Lt
                                         : Test::Op::Lte;
      tests.push_back(Test::compare(cmp, as_double(arg, opath)));
    } else if (op == "exists") {
      tests.push_back(Test::exists(as_bool(arg, opath)));
    } else {
      fail(opath, "unknown operator (in, not, glob, regex, gt, gte, lt, lte, exists)");
    }
  }
  return tests.size() == 1 ? std::move(tests.front()) : Test::all_of(std::move(tests));
}

Matcher parse_matcher(const YAML::Node & node, const std::string & path)
{
  if (!node.IsMap()) {
    fail(path, "expected a mapping of event fields");
  }
  if (node.size() == 0) {
    fail(path, "an empty matcher would match every event; name at least one field");
  }
  Matcher m;
  for (const auto & item : node) {
    const auto field = item.first.as<std::string>();
    if (field != "tags" && !is_event_field(field)) {
      fail(
        path + "." + field,
        "unknown field (kind, code, severity, message, source.component, source.node, "
        "source.robot, attrs.<key>, tags, correlation_id, ...)");
    }
    m.tests.emplace_back(field, parse_test(item.second, path + "." + field, field));
  }
  return m;
}

Guard parse_guard(const YAML::Node & node, const std::string & path)
{
  check_keys(node, path, {"faults_active", "faults_inactive", "state"});
  Guard g;
  if (node["faults_active"]) {
    g.faults_active = as_string_list(node["faults_active"], path + ".faults_active");
  }
  if (node["faults_inactive"]) {
    g.faults_inactive = as_string_list(node["faults_inactive"], path + ".faults_inactive");
  }
  if (const auto state = node["state"]) {
    if (!state.IsMap()) {
      fail(path + ".state", "expected a mapping of STATE codes to values");
    }
    for (const auto & item : state) {
      const auto code = item.first.as<std::string>();
      if (!is_valid_code(code)) {
        fail(path + ".state." + code, "not a dotted lowercase code");
      }
      g.state.emplace_back(code, parse_test(item.second, path + ".state." + code, "value"));
    }
  }
  return g;
}

Template parse_template(const YAML::Node & node, const std::string & path)
{
  if (!node.IsScalar()) {
    fail(path, "expected a string");
  }
  try {
    return Template::parse(node.as<std::string>());
  } catch (const RuleError & e) {
    fail(path, e.what());
  }
}

}  // namespace detail

// -- rule parser ----------------------------------------------------------------------------------

namespace
{

using namespace detail;

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

/// What templates of a rule may refer to.
struct Scope
{
  std::set<std::string> steps;
  std::set<std::string> partition;
};

void collect_steps(const Pattern & p, std::set<std::string> & out)
{
  if (const auto * seq = std::get_if<SequencePattern>(&p.v)) {
    for (const auto & s : seq->steps) {
      out.insert(s.id);
    }
  } else if (const auto * all = std::get_if<AllPattern>(&p.v)) {
    for (const auto & sub : all->of) {
      collect_steps(sub, out);
    }
  } else if (const auto * any = std::get_if<AnyPattern>(&p.v)) {
    for (const auto & sub : any->of) {
      collect_steps(sub, out);
    }
  }
}

void check_template(const Template & t, const std::string & path, const Scope & scope)
{
  for (const auto & var : t.variables()) {
    bool ok = var == "count" || var == "robot" || var == "rule.id";
    if (var.starts_with("event.")) {
      ok = is_event_field(std::string_view(var).substr(6));
    } else if (var.starts_with("steps.")) {
      const auto rest = var.substr(6);
      const auto dot = rest.find('.');
      ok = dot != std::string::npos && scope.steps.count(rest.substr(0, dot)) != 0 &&
           is_event_field(std::string_view(rest).substr(dot + 1));
    } else if (var.starts_with("partition.")) {
      ok = scope.partition.count(var.substr(10)) != 0;
    }
    if (!ok) {
      fail(
        path, "unknown template variable '{{ " + var +
                " }}' (event.<field>, steps.<id>.<field>, count, partition.<key>, rule.id, robot)");
    }
  }
}

Pattern parse_pattern(const YAML::Node & node, const std::string & path);

SequencePattern parse_sequence(const YAML::Node & node, const std::string & path)
{
  check_keys(node, path, {"within", "steps"});
  if (!node["within"] || !node["steps"]) {
    fail(path, "needs 'within' and 'steps'");
  }
  SequencePattern seq;
  seq.within = positive_duration(node["within"], path + ".within");
  const auto steps = node["steps"];
  if (!steps.IsSequence() || steps.size() < 2) {
    fail(path + ".steps", "expected at least two steps");
  }
  std::set<std::string> ids;
  for (std::size_t i = 0; i < steps.size(); ++i) {
    const std::string spath = path + ".steps[" + std::to_string(i) + "]";
    const auto & sn = steps[i];
    check_keys(sn, spath, {"id", "event", "absent", "for"});
    SequenceStep step;
    step.id = sn["id"] ? as_string(sn["id"], spath + ".id") : "step" + std::to_string(i + 1);
    if (!ids.insert(step.id).second) {
      fail(spath + ".id", "duplicate step id '" + step.id + "'");
    }
    if (sn["event"] && sn["absent"]) {
      fail(spath, "a step is either 'event' or 'absent', not both");
    }
    if (sn["event"]) {
      if (sn["for"]) {
        fail(spath + ".for", "only absent steps have 'for'");
      }
      step.matcher = parse_matcher(sn["event"], spath + ".event");
    } else if (sn["absent"]) {
      if (i == 0) {
        fail(spath, "a sequence cannot start with an absent step");
      }
      if (!sn["for"]) {
        fail(spath, "an absent step needs 'for'");
      }
      step.absent = true;
      step.matcher = parse_matcher(sn["absent"], spath + ".absent");
      step.for_duration = positive_duration(sn["for"], spath + ".for");
      if (step.for_duration >= seq.within) {
        fail(spath + ".for", "must be shorter than the sequence's 'within'");
      }
    } else {
      fail(spath, "needs 'event' or 'absent'");
    }
    seq.steps.push_back(std::move(step));
  }
  return seq;
}

std::vector<Pattern> parse_sub_patterns(const YAML::Node & node, const std::string & path)
{
  if (!node.IsSequence() || node.size() < 2) {
    fail(path, "expected a list of at least two patterns");
  }
  std::vector<Pattern> out;
  for (std::size_t i = 0; i < node.size(); ++i) {
    out.push_back(parse_pattern(node[i], path + "[" + std::to_string(i) + "]"));
  }
  return out;
}

Pattern parse_pattern(const YAML::Node & node, const std::string & path)
{
  check_keys(node, path, {"event", "count", "sequence", "all", "any"});
  if (node.size() != 1) {
    fail(path, "expected exactly one of event, count, sequence, all, any");
  }
  const auto key = node.begin()->first.as<std::string>();
  const YAML::Node body = node.begin()->second;
  const std::string bpath = path + "." + key;
  Pattern p;
  if (key == "event") {
    p.v = EventPattern{parse_matcher(body, bpath)};
  } else if (key == "count") {
    check_keys(body, bpath, {"event", "threshold", "within"});
    if (!body["event"] || !body["threshold"] || !body["within"]) {
      fail(bpath, "needs 'event', 'threshold' and 'within'");
    }
    CountPattern c;
    c.matcher = parse_matcher(body["event"], bpath + ".event");
    c.threshold = as_int(body["threshold"], bpath + ".threshold");
    if (c.threshold < 1) {
      fail(bpath + ".threshold", "must be >= 1");
    }
    c.within = positive_duration(body["within"], bpath + ".within");
    p.v = std::move(c);
  } else if (key == "sequence") {
    p.v = parse_sequence(body, bpath);
  } else if (key == "all") {
    check_keys(body, bpath, {"within", "of"});
    if (!body["within"] || !body["of"]) {
      fail(bpath, "needs 'within' and 'of'");
    }
    AllPattern a;
    a.within = positive_duration(body["within"], bpath + ".within");
    a.of = parse_sub_patterns(body["of"], bpath + ".of");
    p.v = std::move(a);
  } else {
    p.v = AnyPattern{parse_sub_patterns(body, bpath)};
  }
  return p;
}

void put_template(
  std::map<std::string, Template> & out, const std::string & key, const YAML::Node & node,
  const std::string & path, const Scope & scope)
{
  Template t = parse_template(node, path);
  check_template(t, path, scope);
  out.emplace(key, std::move(t));
}

RuleAction parse_action(const YAML::Node & node, const std::string & path, const Scope & scope)
{
  check_keys(node, path, {"recover", "emit", "incident", "notify", "ask_agent"});
  if (node.size() != 1) {
    fail(path, "expected exactly one of recover, emit, incident, notify, ask_agent");
  }
  const auto key = node.begin()->first.as<std::string>();
  const YAML::Node body = node.begin()->second;
  const std::string bpath = path + "." + key;
  RuleAction a;
  auto args = [&](const YAML::Node & map, const std::string & apath) {
    if (!map.IsMap()) {
      fail(apath, "expected a mapping");
    }
    for (const auto & item : map) {
      const auto name = item.first.as<std::string>();
      put_template(a.args, name, item.second, apath + "." + name, scope);
    }
  };
  if (key == "recover") {
    a.type = RuleAction::Type::Recover;
    check_keys(body, bpath, {"plan", "args", "rationale"});
    if (!body["plan"]) {
      fail(bpath, "needs 'plan'");
    }
    put_template(a.fields, "plan", body["plan"], bpath + ".plan", scope);
    if (body["rationale"]) {
      put_template(a.fields, "rationale", body["rationale"], bpath + ".rationale", scope);
    }
    if (body["args"]) {
      args(body["args"], bpath + ".args");
    }
  } else if (key == "emit") {
    a.type = RuleAction::Type::Emit;
    check_keys(body, bpath, {"kind", "code", "severity", "message", "attrs"});
    if (!body["kind"] || !body["code"]) {
      fail(bpath, "needs 'kind' and 'code'");
    }
    const auto kind_text = as_string(body["kind"], bpath + ".kind");
    const auto kind = parse_kind(kind_text);
    static const std::set<Kind> emittable{
      Kind::Log, Kind::State, Kind::FaultRaise, Kind::FaultClear, Kind::System};
    if (!kind || emittable.count(*kind) == 0) {
      fail(bpath + ".kind", "rules emit LOG, STATE, FAULT_RAISE, FAULT_CLEAR or SYSTEM events");
    }
    a.fields.emplace("kind", Template::parse(std::string(to_string(*kind))));
    put_template(a.fields, "code", body["code"], bpath + ".code", scope);
    if (a.fields.at("code").is_literal() && !is_valid_code(a.fields.at("code").text())) {
      fail(bpath + ".code", "'" + a.fields.at("code").text() + "' is not a dotted lowercase code");
    }
    const Severity sev =
      body["severity"] ? as_severity(body["severity"], bpath + ".severity") : Severity::Info;
    a.fields.emplace("severity", Template::parse(std::string(to_string(sev))));
    if (body["message"]) {
      put_template(a.fields, "message", body["message"], bpath + ".message", scope);
    }
    if (body["attrs"]) {
      args(body["attrs"], bpath + ".attrs");
    }
  } else if (key == "incident") {
    a.type = RuleAction::Type::Incident;
    check_keys(body, bpath, {"open", "escalate"});
    const bool open = body["open"] && as_bool(body["open"], bpath + ".open");
    const bool escalate = body["escalate"] && as_bool(body["escalate"], bpath + ".escalate");
    if (open == escalate) {
      fail(bpath, "set exactly one of 'open: true' or 'escalate: true'");
    }
    a.fields.emplace("op", Template::parse(open ? "open" : "escalate"));
  } else if (key == "notify") {
    a.type = RuleAction::Type::Notify;
    check_keys(body, bpath, {"channel", "message"});
    if (!body["message"]) {
      fail(bpath, "needs 'message'");
    }
    a.fields.emplace(
      "channel",
      Template::parse(
        body["channel"] ? as_string(body["channel"], bpath + ".channel") : std::string("default")));
    put_template(a.fields, "message", body["message"], bpath + ".message", scope);
  } else {
    a.type = RuleAction::Type::AskAgent;
    check_keys(body, bpath, {"focus"});
    if (!body["focus"]) {
      fail(bpath, "needs 'focus'");
    }
    put_template(a.fields, "focus", body["focus"], bpath + ".focus", scope);
  }
  return a;
}

RulePolicy parse_policy(const YAML::Node & node, const std::string & path, RulePolicy p)
{
  check_keys(node, path, {"cooldown", "max_fires", "priority", "dry_run"});
  if (node["cooldown"]) {
    p.cooldown = as_duration(node["cooldown"], path + ".cooldown");
  }
  if (const auto mf = node["max_fires"]) {
    check_keys(mf, path + ".max_fires", {"count", "per"});
    if (!mf["count"] || !mf["per"]) {
      fail(path + ".max_fires", "needs 'count' and 'per'");
    }
    MaxFires m;
    m.count = as_int(mf["count"], path + ".max_fires.count");
    if (m.count < 1) {
      fail(path + ".max_fires.count", "must be >= 1");
    }
    m.per = positive_duration(mf["per"], path + ".max_fires.per");
    p.max_fires = m;
  }
  if (node["priority"]) {
    p.priority = as_int(node["priority"], path + ".priority");
  }
  if (node["dry_run"]) {
    p.dry_run = as_bool(node["dry_run"], path + ".dry_run");
  }
  return p;
}

Rule parse_rule(const YAML::Node & node, const std::string & path)
{
  check_keys(
    node, path,
    {"id", "description", "labels", "enabled", "dry_run", "partition_by", "time_basis", "when",
     "guard", "then", "policy"});
  Rule r;
  if (!node["id"]) {
    fail(path, "missing 'id'");
  }
  r.id = as_string(node["id"], path + ".id");
  if (!is_kebab(r.id)) {
    fail(path + ".id", "'" + r.id + "' is not kebab-case (e.g. scan-stale-restart)");
  }
  if (node["description"]) {
    r.description = as_string(node["description"], path + ".description");
  }
  if (const auto labels = node["labels"]) {
    if (!labels.IsMap()) {
      fail(path + ".labels", "expected a mapping");
    }
    for (const auto & item : labels) {
      const auto key = item.first.as<std::string>();
      r.labels[key] = as_string(item.second, path + ".labels." + key);
    }
  }
  if (node["enabled"]) {
    r.enabled = as_bool(node["enabled"], path + ".enabled");
  }
  if (node["dry_run"]) {
    r.policy.dry_run = as_bool(node["dry_run"], path + ".dry_run");
  }
  Scope scope;
  if (node["partition_by"]) {
    r.partition_by = as_string_list(node["partition_by"], path + ".partition_by");
    for (std::size_t i = 0; i < r.partition_by.size(); ++i) {
      if (!is_event_field(r.partition_by[i])) {
        fail(
          path + ".partition_by[" + std::to_string(i) + "]",
          "'" + r.partition_by[i] + "' is not an event field (e.g. source.component)");
      }
      scope.partition.insert(r.partition_by[i]);
    }
  }
  if (node["time_basis"]) {
    const auto basis = as_string(node["time_basis"], path + ".time_basis");
    if (basis != "received" && basis != "source") {
      fail(path + ".time_basis", "expected 'received' or 'source'");
    }
    r.time_basis = basis == "source" ? TimeBasis::Source : TimeBasis::Received;
  }
  if (!node["when"]) {
    fail(path, "missing 'when'");
  }
  r.when = parse_pattern(node["when"], path + ".when");
  collect_steps(r.when, scope.steps);
  if (node["guard"]) {
    r.guard = parse_guard(node["guard"], path + ".guard");
  }
  const auto then = node["then"];
  if (!then || !then.IsSequence() || then.size() == 0) {
    fail(path + ".then", "expected a non-empty list of actions");
  }
  for (std::size_t i = 0; i < then.size(); ++i) {
    r.then.push_back(parse_action(then[i], path + ".then[" + std::to_string(i) + "]", scope));
  }
  if (node["policy"]) {
    r.policy = parse_policy(node["policy"], path + ".policy", r.policy);
  }
  return r;
}

std::string read_file(const std::string & path)
{
  std::ifstream in(path);
  if (!in) {
    throw RuleError("cannot open '" + path + "'");
  }
  std::stringstream buffer;
  buffer << in.rdbuf();
  return buffer.str();
}

}  // namespace

RuleSet RuleSet::from_yaml(const std::string & text, const std::string & file)
{
  RuleSet set;
  try {
    const YAML::Node root = load(text);
    if (!root || root.IsNull()) {
      return set;
    }
    check_keys(root, "document", {"schema", "rules"});
    if (!root["schema"] || as_string(root["schema"], "schema") != "jarvis.rules/v1") {
      fail("schema", "expected 'jarvis.rules/v1'");
    }
    const auto rules = root["rules"];
    if (rules && !rules.IsSequence()) {
      fail("rules", "expected a list");
    }
    for (std::size_t i = 0; rules && i < rules.size(); ++i) {
      const std::string path = "rules[" + std::to_string(i) + "]";
      Rule r = parse_rule(rules[i], path);
      r.file = file;
      if (set.find(r.id)) {
        fail(path + ".id", "duplicate rule id '" + r.id + "'");
      }
      set.rules_.push_back(std::move(r));
    }
  } catch (const YamlError & e) {
    throw RuleError(e.what());
  }
  return set;
}

RuleSet RuleSet::from_file(const std::string & path)
{
  try {
    return from_yaml(read_file(path), path);
  } catch (const RuleError & e) {
    const std::string what = e.what();
    throw RuleError(what.starts_with("cannot open") ? what : path + ": " + what);
  }
}

RuleSet RuleSet::from_directory(const std::string & dir)
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
    throw RuleError("cannot read rules directory '" + dir + "': " + ec.message());
  }
  std::sort(files.begin(), files.end());
  RuleSet set;
  for (const auto & f : files) {
    set.merge(from_file(f));
  }
  return set;
}

void RuleSet::merge(RuleSet other)
{
  for (auto & r : other.rules_) {
    if (const Rule * existing = find(r.id)) {
      throw RuleError(
        r.file + ": duplicate rule id '" + r.id + "' (also in " + existing->file + ")");
    }
    rules_.push_back(std::move(r));
  }
}

const Rule * RuleSet::find(const std::string & id) const
{
  const auto it =
    std::find_if(rules_.begin(), rules_.end(), [&id](const Rule & r) { return r.id == id; });
  return it == rules_.end() ? nullptr : &*it;
}

}  // namespace jarvis_core
