// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Rules: `when` (a pattern over events) + `guard` (current state) + `then` (actions) + `policy`.
// This header holds the model, the YAML parser and the building blocks shared with plans:
// matchers, tests and `{{ }}` templates (docs/design/05-rules-and-recovery.md §1-6).

#pragma once

#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <regex>
#include <stdexcept>
#include <string>
#include <variant>
#include <vector>

#include "jarvis_core/types.hpp"

namespace jarvis_core
{

class RuleError : public std::runtime_error
{
public:
  using std::runtime_error::runtime_error;
};

// -- tests and matchers ---------------------------------------------------------------------------

/// One check on one value: `ACTIVE`, `{ in: [...] }`, `{ gte: 5 }`, `">=ERROR"`...
class Test
{
public:
  enum class Op {
    Equals,
    Glob,
    In,
    Not,
    AllOf,
    Regex,
    Gt,
    Gte,
    Lt,
    Lte,
    Exists,
    SeverityAtLeast,
    SeverityAbove,
    SeverityAtMost,
    SeverityBelow,
    HasAll,
  };

  static Test equals(AttrValue v);
  static Test glob(std::string pattern);
  static Test in(std::vector<Test> options);
  static Test negate(Test inner);
  static Test all_of(std::vector<Test> tests);
  static Test regex(const std::string & pattern);
  static Test compare(Op op, double bound);
  static Test exists(bool expected);
  static Test severity(Op op, Severity bound);
  static Test has_all(std::vector<std::string> items);

  /// `value` is empty when the field is absent. `items` is used by HasAll (tags).
  bool matches(
    const std::optional<AttrValue> & value, const std::vector<std::string> * items = nullptr) const;
  Op op() const { return op_; }

private:
  Op op_{Op::Equals};
  AttrValue value_;
  double bound_{0.0};
  bool flag_{false};
  std::vector<Test> children_;
  std::vector<std::string> items_;
  std::shared_ptr<const std::regex> regex_;
};

/// A matcher selects events: every (field, test) pair must hold. Fields are `kind`, `code`,
/// `severity`, `message`, `source.robot|node|component|host`, `attrs.<key>`, `tags`,
/// `correlation_id` and `causation_id`.
struct Matcher
{
  std::vector<std::pair<std::string, Test>> tests;

  bool matches(const Event & e) const;
};

/// The value of a field path on an event, as a typed value (kind and severity as their names).
std::optional<AttrValue> event_field(const Event & e, std::string_view path);
bool is_event_field(std::string_view path);

// -- templates ------------------------------------------------------------------------------------

/// `"restart {{ event.attrs.node }} on {{ robot }}"`: literals and variable paths, no logic.
class Template
{
public:
  static Template parse(const std::string & text);  ///< throws RuleError on `{{` without `}}`

  /// Resolves each variable; nullopt if one is unknown or the result is empty.
  std::optional<std::string> render(
    const std::function<std::optional<std::string>(const std::string &)> & lookup) const;
  /// The variable paths it uses, for load-time validation.
  std::vector<std::string> variables() const;
  const std::string & text() const { return text_; }
  bool is_literal() const;

private:
  std::string text_;
  std::vector<std::pair<bool, std::string>> parts_;  // (is variable, literal text or path)
};

// -- patterns -------------------------------------------------------------------------------------

struct Pattern;

struct EventPattern
{
  Matcher matcher;
};

struct CountPattern
{
  Matcher matcher;
  int threshold{1};
  Duration within{0};
};

struct SequenceStep
{
  std::string id;
  Matcher matcher;
  bool absent{false};
  Duration for_duration{0};  ///< absent steps: how long nothing may match
};

struct SequencePattern
{
  Duration within{0};
  std::vector<SequenceStep> steps;
};

struct AllPattern
{
  Duration within{0};
  std::vector<Pattern> of;
};

struct AnyPattern
{
  std::vector<Pattern> of;
};

struct Pattern
{
  std::variant<EventPattern, CountPattern, SequencePattern, AllPattern, AnyPattern> v;
};

// -- guard, actions, policy, rule -----------------------------------------------------------------

/// Evaluated at fire time against the active faults and the latest STATE values.
struct Guard
{
  std::vector<std::string> faults_active;           ///< code globs; each must match an active fault
  std::vector<std::string> faults_inactive;         ///< code globs; none may match an active fault
  std::vector<std::pair<std::string, Test>> state;  ///< STATE code -> test on its latest value

  bool empty() const { return faults_active.empty() && faults_inactive.empty() && state.empty(); }
  /// `active_faults`: fault_id -> code of every open fault. `states`: STATE code -> latest value.
  bool passes(
    const std::map<std::string, std::string> & active_faults,
    const std::map<std::string, AttrValue> & states) const;
};

/// FAULT_STATE `attrs.state` values that count as an active fault for guards.
bool is_active_fault_state(std::string_view state);
/// The value a STATE event sets: `attrs.value` if present, else the message.
AttrValue state_value(const Event & e);

struct RuleAction
{
  enum class Type { Recover, Emit, Incident, Notify, AskAgent };
  Type type{Type::Notify};
  /// recover: plan · emit: kind, code, severity, message · incident: open | escalate ·
  /// notify: channel, message · ask_agent: focus
  std::map<std::string, Template> fields;
  /// recover: plan args · emit: event attrs
  std::map<std::string, Template> args;
};

std::string_view to_string(RuleAction::Type type);

struct MaxFires
{
  int count{1};
  Duration per{0};
};

struct RulePolicy
{
  Duration cooldown{0};
  std::optional<MaxFires> max_fires;
  int priority{0};
  bool dry_run{false};
};

enum class TimeBasis {
  Received,  ///< recv_stamp: the event hub's clock (default, no cross-host skew)
  Source,    ///< stamp: the producer's clock
};

struct Rule
{
  std::string id;
  std::string description;
  std::map<std::string, std::string> labels;
  bool enabled{true};
  std::vector<std::string> partition_by;
  TimeBasis time_basis{TimeBasis::Received};
  Pattern when;
  Guard guard;
  std::vector<RuleAction> then;
  RulePolicy policy;
  std::string file;  ///< where it was loaded from, for messages
};

/// A validated set of rules (`jarvis.rules/v1`), possibly from several files.
class RuleSet
{
public:
  /// Parses one document. Throws RuleError naming the offending path, e.g.
  /// `rules[1].when.count.threshold: must be >= 1`.
  static RuleSet from_yaml(const std::string & text, const std::string & file = "");
  static RuleSet from_file(const std::string & path);
  /// Every `*.yaml` in a directory, in name order; rule ids must be unique across files.
  static RuleSet from_directory(const std::string & dir);

  void merge(RuleSet other);
  const std::vector<Rule> & rules() const { return rules_; }
  const Rule * find(const std::string & id) const;

private:
  std::vector<Rule> rules_;
};

}  // namespace jarvis_core
