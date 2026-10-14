// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Offline tools over the same code the robot runs (docs/design/05-rules-and-recovery.md §12):
//   lint      cross-checks rules, plans and the fault catalog before they ship;
//   backtest  replays a recorded event stream through rules (and plans, in dry run) and compares
//             the decisions with the ones recorded live.

#pragma once

#include <map>
#include <nlohmann/json.hpp>
#include <set>
#include <string>
#include <vector>

#include "jarvis_core/orchestrator.hpp"
#include "jarvis_core/rule_engine.hpp"

namespace jarvis_core
{

// -- lint -----------------------------------------------------------------------------------------

/// The actions of the built-in and jarvis_nav2 actuators (what /jarvis/recovery loads by default
/// on a Nav2 robot).
const std::set<std::string> & default_actuator_actions();

struct LintInput
{
  std::string rules_dir;
  std::string plans_dir;
  std::string catalog_path;
  std::set<std::string> actuators = default_actuator_actions();
};

struct LintFinding
{
  bool error{true};
  std::string message;
};

struct LintReport
{
  std::size_t rules{0};
  std::size_t plans{0};
  std::size_t faults{0};
  std::vector<LintFinding> findings;

  bool ok() const;
  std::size_t errors() const;
};

LintReport lint(const LintInput & input);

// -- backtest -------------------------------------------------------------------------------------

struct BacktestOptions
{
  std::string robot_id{"robot"};
  /// The live rule engine: the replay recomputes its events (so it drops them from the input),
  /// and its RULE_MATCH events are the decisions it compares against.
  std::string rule_engine_node{"/jarvis/rule_engine"};
  /// The live orchestrator: with simulate_recovery its events are recomputed too; without, its
  /// recorded outcomes stay in the stream (rules that react to recovery see what happened).
  std::string recovery_node{"/jarvis/recovery"};
  /// Run requested plans through the orchestrator in DRY_RUN (gates and steps, nothing moves).
  /// Needs plans.
  bool simulate_recovery{true};
};

struct BacktestMatch
{
  Timestamp at{0};
  std::string rule;
  std::string match_id;
  std::string cause_id;
  std::string correlation_id;
  bool dry_run{false};
};

struct BacktestRecovery
{
  Timestamp at{0};
  std::string rule;
  std::string plan;
  std::map<std::string, std::string> args;
  std::string execution_id;
  ExecState state{ExecState::Rejected};
  std::string reason;
};

struct BacktestReport
{
  std::size_t events{0};  ///< replayed (after dropping the live engines' own events)
  Timestamp from{0};
  Timestamp to{0};
  std::vector<BacktestMatch> matches;
  std::vector<BacktestRecovery> recoveries;
  std::vector<RuleStats> stats;
  /// The comparison with the live decisions, as "rule <- cause" keys (`chained` for matches caused
  /// by events the engines produced themselves, whose ids differ between runs).
  std::size_t recorded{0};
  std::vector<std::string> missing;  ///< decided live, not reproduced
  std::vector<std::string> extra;    ///< reproduced, not decided live

  bool compared() const { return recorded > 0; }
  bool reproduces() const { return missing.empty() && extra.empty(); }
  nlohmann::json to_json() const;
};

/// Events are replayed in `seq` order (recv_stamp when seq is 0) on their recv_stamp clock.
BacktestReport backtest(
  RuleSet rules, const PlanSet * plans, std::vector<Event> events,
  const BacktestOptions & options = {});

/// Reads JSON Lines of events (schemas/event.schema.json); blank lines and `#` comments are
/// skipped. Throws std::runtime_error naming the line.
std::vector<Event> read_event_lines(std::istream & in);

}  // namespace jarvis_core
