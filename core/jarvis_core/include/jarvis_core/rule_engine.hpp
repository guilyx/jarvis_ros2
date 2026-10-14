// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// The rule engine: complex event processing over the canonical event stream
// (docs/design/05-rules-and-recovery.md §3-6). Pure and deterministic: the shell feeds it events in
// `seq` order and the time, and performs the effects it returns. Live, replay and backtest run the
// same code, so they make the same decisions.

#pragma once

#include <deque>
#include <map>
#include <memory>
#include <nlohmann/json.hpp>
#include <optional>
#include <set>
#include <string>
#include <vector>

#include "jarvis_core/effects.hpp"
#include "jarvis_core/rules.hpp"
#include "jarvis_core/util.hpp"

namespace jarvis_core
{

struct RuleEngineConfig
{
  std::string robot_id;
  std::string node_name{"/jarvis/rule_engine"};
  /// Partitions kept per rule; the least recently active one is evicted beyond this.
  std::size_t max_partitions{1000};
};

struct RuleStats
{
  std::string id;
  bool enabled{true};
  bool dry_run{false};
  std::uint64_t fires{0};
  std::uint64_t dry_fires{0};
  std::uint64_t suppressed{0};   ///< max_fires reached
  std::uint64_t cooled_down{0};  ///< within the cooldown
  std::uint64_t guard_blocked{0};
  std::uint64_t action_failures{0};
  std::size_t partitions{0};
};

/// What a pattern produced when it completed.
struct PatternFire
{
  Timestamp at{0};
  Event last;                          ///< the last matched event
  std::map<std::string, Event> steps;  ///< sequence steps by id
  std::int64_t count{1};
  std::vector<std::string> event_ids;  ///< every event that took part
};

/// Matching state for one pattern in one partition. Built from a Pattern by make_matcher_state().
class PatternState
{
public:
  virtual ~PatternState() = default;
  /// Feeds one event (at time `t` on the rule's time basis). Returns a fire when the pattern completes.
  virtual std::optional<PatternFire> feed(const Event & e, Timestamp t) = 0;
  /// Advances timers (absent steps, expiry) to `now`.
  virtual std::optional<PatternFire> tick(Timestamp now) = 0;
  virtual std::optional<Timestamp> deadline() const = 0;
  /// True when nothing is in progress (the partition can be evicted).
  virtual bool idle() const = 0;
  virtual void reset() = 0;
};

std::unique_ptr<PatternState> make_matcher_state(const Pattern & pattern);

class RuleEngine
{
public:
  RuleEngine(RuleEngineConfig config, RuleSet rules);

  /// Replaces the rules atomically. Matching state of every rule is reset; fire history (cooldowns,
  /// max_fires) is kept for rules whose id survives.
  Effects reload(RuleSet rules, Timestamp now);

  /// Feeds one event from the canonical stream. Events must arrive in `seq` order.
  Effects on_event(const Event & e, Timestamp now);
  /// Runs timers due by `now` (absent steps complete, partial matches expire).
  Effects advance_to(Timestamp now);
  std::optional<Timestamp> next_deadline() const;

  /// Replaces the known active faults (from the latched /jarvis/faults snapshot): fault_id -> code.
  void sync_active_faults(std::map<std::string, std::string> active);

  /// Enables/disables a rule or toggles its shadow mode at runtime. False if the id is unknown.
  bool set_rule_state(
    const std::string & id, std::optional<bool> enabled, std::optional<bool> dry_run);

  std::vector<RuleStats> stats() const;
  const RuleSet & rules() const { return rules_; }

  /// Fire history that must survive a restart (cooldowns and max_fires windows).
  nlohmann::json snapshot() const;
  void restore(const nlohmann::json & state);

private:
  struct Partition
  {
    std::unique_ptr<PatternState> state;
    std::map<std::string, std::string> values;  ///< partition_by field -> value
    std::vector<Timestamp> fires;               ///< within the max_fires window
    std::optional<Timestamp> last_fire;
    Timestamp last_activity{0};
    bool suppression_notified{false};
  };

  struct RuleRuntime
  {
    const Rule * rule{nullptr};
    bool enabled{true};
    bool dry_run{false};
    Duration horizon{0};  ///< idle partitions are evicted after this
    std::map<std::string, Partition> partitions;
    RuleStats stats;
  };

  void build();
  Timestamp time_of(const RuleRuntime & rr, const Event & e) const;
  std::string partition_key(
    const Rule & rule, const Event & e, std::map<std::string, std::string> * values) const;
  void handle_fire(
    RuleRuntime & rr, Partition & p, const std::string & key, PatternFire fire, Effects & out);
  bool guard_passes(const Guard & guard) const;
  void run_actions(
    RuleRuntime & rr, const Partition & p, const PatternFire & fire, const Event & match,
    Effects & out);
  Event make_event(
    Kind kind, std::string code, Severity severity, std::string message, const Event & cause,
    const std::string & causation_id, Timestamp at) const;
  void track_state(const Event & e);
  void evict(RuleRuntime & rr, Timestamp now);

  RuleEngineConfig config_;
  RuleSet rules_;
  std::vector<RuleRuntime> runtimes_;  ///< in priority order (higher first), then file order
  std::map<std::string, std::string> active_faults_;  ///< fault_id -> code
  std::map<std::string, AttrValue> states_;           ///< STATE code -> latest value
  std::map<std::string, std::string> origin_;         ///< event id -> rule whose match caused it
  std::deque<std::string> origin_order_;
  mutable IdGenerator ids_;
};

}  // namespace jarvis_core
