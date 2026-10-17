// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// The recovery orchestrator: admission gates, locks, approvals, plan execution and the `on_failure`
// escalation ladder (docs/design/05-rules-and-recovery.md §8-11). Pure and deterministic like the
// rule engine: the shell feeds it requests, events, actuator results and the time, and performs
// the effects it returns. It never touches the robot itself: actuator steps come out as
// StartAction effects.

#pragma once

#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <nlohmann/json.hpp>
#include <optional>
#include <set>
#include <string>
#include <string_view>
#include <vector>

#include "jarvis_core/effects.hpp"
#include "jarvis_core/plans.hpp"
#include "jarvis_core/util.hpp"

namespace jarvis_core
{

enum class RecoveryMode { Enabled, DryRun, Disabled };
std::string_view to_string(RecoveryMode m);
std::optional<RecoveryMode> parse_recovery_mode(std::string_view text);

enum class ExecState {
  PendingApproval,
  Queued,
  Running,
  Succeeded,
  Failed,
  Aborted,
  Rejected,
  Expired,
};
std::string_view to_string(ExecState s);
std::optional<ExecState> parse_exec_state(std::string_view text);
bool is_terminal(ExecState s);

enum class StepState { Pending, Running, Retrying, Succeeded, Failed, Skipped, Aborted };
std::string_view to_string(StepState s);
std::optional<StepState> parse_step_state(std::string_view text);

/// One ExecuteRecovery goal, whoever sends it.
struct RecoveryRequest
{
  std::string plan_id;
  std::map<std::string, std::string> args;
  Requester requester{Requester::Operator};
  std::string requester_id;  ///< rule id, operator name, insight id, parent execution id
  std::string correlation_id;
  std::string causation_id;
  std::string rationale;
  bool dry_run{false};
  std::optional<double> confidence;  ///< agent proposals
};

/// A rule's `recover` action as a request.
RecoveryRequest to_request(const RequestRecovery & r);

struct GateResult
{
  std::string gate;  ///< mode, plan, requester, preconditions, rate_limit, locks, approval
  bool passed{true};
  std::string detail;
};

struct StepRecord
{
  std::string id;
  std::string action;
  StepState state{StepState::Pending};
  int attempts{0};
  Timestamp started{0};
  Timestamp finished{0};
  std::string message;
};

/// The audit record of one execution (docs/design/03-data-model.md §7).
struct Execution
{
  std::string execution_id;
  std::string plan_id;
  std::string plan_rev;
  std::map<std::string, std::string> args;  ///< bound: validated, defaults filled in
  Requester requester{Requester::Operator};
  std::string requester_id;
  std::string correlation_id;
  std::string causation_id;
  std::string rationale;
  std::optional<double> confidence;
  bool dry_run{false};
  ExecState state{ExecState::Rejected};
  std::string reason;  ///< why it ended, or what it waits for
  std::vector<GateResult> gates;
  bool needs_approval{false};
  std::string approved_by;
  std::vector<std::string> locks;  ///< rendered
  std::vector<StepRecord> steps;
  std::size_t current_step{0};
  std::string failed_step;
  Timestamp created{0};
  Timestamp started{0};
  Timestamp finished{0};
  Timestamp expires{0};  ///< PENDING_APPROVAL and QUEUED deadline
  int depth{1};
  std::string parent_id;  ///< the execution whose `run` step started this one
  std::string child_id;   ///< the sub-plan a `run` step waits for
  std::string ladder_of;  ///< the failed execution whose `on_failure` started this one
  bool ladder_running{false};
  std::size_t ladder_next{0};
  std::string ladder_child;
  bool escalated{false};
  std::string last_event_id;  ///< causation for the next event about this execution
};

void to_json(nlohmann::json & j, const Execution & x);
void from_json(const nlohmann::json & j, Execution & x);

/// What the orchestrator knows about a fault (from FAULT_STATE events or the /jarvis/faults
/// snapshot).
struct FaultView
{
  std::string code;
  std::string component;
  std::string state;  ///< PENDING, ACTIVE, LATCHED, CLEARED
};

struct OrchestratorConfig
{
  std::string robot_id{"robot"};
  std::string node_name{"/jarvis/recovery"};
  RecoveryMode mode{RecoveryMode::Enabled};
  /// Root executions running at once; sub-plans run inside their parent's slot.
  std::size_t max_concurrent{4};
  Duration approval_ttl{5 * kMinute};
  /// Deployment ceiling for agent requests; the plan's agent_policy can only lower it.
  AgentPolicy agent_autonomy{AgentPolicy::Supervised};
  double min_confidence{0.8};
  int max_depth{3};
  std::size_t history_capacity{200};
  /// Events kept so a `wait_for` also sees what happened while the previous step ran.
  std::size_t lookback_events{1024};
  std::uint64_t id_seed{0};
};

struct CommandResult
{
  bool success{false};
  std::string message;
  Effects effects;
};

struct Submitted
{
  std::string execution_id;
  ExecState state{ExecState::Rejected};
  std::string reason;
  Effects effects;
};

class Orchestrator
{
public:
  Orchestrator(OrchestratorConfig config, PlanSet plans);

  /// Replaces the plans. Running executions finish with the plan they started with; waiting ones
  /// whose plan changed or disappeared are rejected.
  Effects reload(PlanSet plans, Timestamp now, const std::string & operator_name = "");

  /// Runs the gates and starts, queues, parks for approval or rejects the request.
  Submitted submit(const RecoveryRequest & request, Timestamp now);
  /// Feeds one event from the canonical stream (`wait_for`, fault and state tracking).
  Effects on_event(const Event & e, Timestamp now);
  /// The outcome of a StartAction. Results for attempts that already ended are ignored.
  Effects on_action_result(
    const std::string & execution_id, const std::string & step_id, int attempt, bool success,
    const std::string & message, Timestamp now);
  /// Runs timers due by `now`: step and plan timeouts, delays, retries, approval and queue TTLs.
  Effects advance_to(Timestamp now);
  std::optional<Timestamp> next_deadline() const;

  /// Replaces the known faults (fault_id -> view), e.g. from the latched /jarvis/faults.
  Effects sync_faults(std::map<std::string, FaultView> faults, Timestamp now);

  CommandResult decide(
    const std::string & execution_id, bool approve, const std::string & operator_name,
    const std::string & note, Timestamp now);
  /// Aborts a waiting or running execution, or stops the escalation ladder of a failed one.
  CommandResult cancel(
    const std::string & execution_id, const std::string & operator_name, const std::string & reason,
    Timestamp now);
  /// The kill switch. DISABLED rejects whatever waits and, with `cancel_running`, aborts what runs.
  CommandResult set_mode(
    RecoveryMode mode, const std::string & operator_name, const std::string & reason,
    bool cancel_running, Timestamp now);
  RecoveryMode mode() const { return mode_; }

  const Execution * find(const std::string & execution_id) const;
  /// Executions still in play: waiting, running, or failed with their ladder in progress.
  std::vector<Execution> live() const;
  /// Recently settled executions, oldest first.
  const std::deque<Execution> & history() const { return history_; }
  const PlanSet & plans() const { return plans_; }

  /// What survives a restart: mode, rate-limit history, waiting executions and ladders.
  nlohmann::json snapshot() const;
  /// Executions that were running are not resumed (resuming blind is unsafe): they end ABORTED
  /// with `jarvis.recovery.interrupted` and their incident is escalated.
  Effects restore(const nlohmann::json & state, Timestamp now);

private:
  enum class Wait { None, Action, Event, Fault, Delay, Retry, Child };

  struct Live
  {
    Execution x;
    std::shared_ptr<const Plan> plan;
    Wait wait{Wait::None};
    Timestamp deadline{0};
    std::optional<Matcher> matcher;
    FaultView fault;  ///< Wait::Fault: code glob, component (optional) and the awaited state
    std::uint64_t step_mark{0};
    std::uint64_t prev_mark{0};
    std::uint64_t order{0};  ///< creation order (FIFO queue, deterministic iteration)
    bool ended{false};
    std::string stop_reason;  ///< set when an operator or the kill switch stops the ladder
  };

  void index_plans();
  Live & create(const RecoveryRequest & request, Timestamp at);
  void admit(Live & l, Timestamp at, Effects & out);
  void proceed(Live & l, Timestamp at, Effects & out);
  void start(Live & l, Timestamp at, Effects & out);
  void begin_step(Live & l, std::size_t index, Timestamp at, Effects & out);
  void begin_attempt(Live & l, Timestamp at, Effects & out);
  void step_succeeded(Live & l, const std::string & message, Timestamp at, Effects & out);
  void attempt_failed(Live & l, const std::string & message, Timestamp at, Effects & out);
  void step_failed(Live & l, const std::string & message, Timestamp at, Effects & out);
  void finish(Live & l, ExecState state, const std::string & reason, Timestamp at, Effects & out);
  void settle(Live & l, Timestamp at, Effects & out);
  void ladder_advance(Live & owner, Timestamp at, Effects & out);
  void ladder_done(Live & owner, const std::string & outcome, Timestamp at, Effects & out);
  void escalate(Live & l, const std::string & to, Timestamp at, Effects & out);
  void pump_queue(Timestamp at, Effects & out);
  std::optional<Timestamp> timer_of(const Live & l) const;
  void fire_timer(Live & l, Timestamp at, Effects & out);
  void check_waits(const Event * e, Timestamp at, Effects & out);
  void stop(Live & l, const std::string & reason, Timestamp at, Effects & out);
  std::vector<std::string> select(const std::function<bool(const Live &)> & pred) const;
  void collect();

  void reject(
    Live & l, const std::string & gate, const std::string & why, Timestamp at, Effects & out);
  void set_gate(Live & l, const std::string & gate, bool passed, const std::string & detail);
  std::optional<std::string> lock_conflict(const Live & l) const;
  std::string rate_key(const Execution & x) const;
  bool rate_exceeded(const Live & l, Timestamp at);
  bool fault_wait_satisfied(const FaultView & want) const;
  std::map<std::string, std::string> active_faults() const;
  std::optional<nlohmann::json> lookup(const Live & l, const std::string & var) const;
  Live * get(const std::string & id);
  const Live * get(const std::string & id) const;
  Live & ladder_root(Live & l);

  void emit(
    Live & l, std::string_view phase, Severity severity, std::string message, Attributes extra,
    Timestamp at, Effects & out, std::string code = "");
  void changed(const Live & l, Effects & out);
  Event make_event(
    Kind kind, std::string code, Severity severity, std::string message, Timestamp at) const;

  OrchestratorConfig config_;
  PlanSet plans_;
  std::map<std::string, std::shared_ptr<const Plan>> by_id_;
  RecoveryMode mode_;
  std::map<std::string, Live> live_;  ///< by execution id (UUIDv7: creation order)
  std::deque<Execution> history_;
  std::map<std::string, std::deque<Timestamp>> attempts_;  ///< rate-limit key -> start times
  std::map<std::string, FaultView> faults_;                ///< open faults by fault_id
  std::map<std::string, AttrValue> states_;                ///< STATE code -> latest value
  std::deque<std::pair<std::uint64_t, Event>> recent_;     ///< lookback for wait_for
  std::uint64_t event_count_{0};
  std::uint64_t serial_{0};
  mutable IdGenerator ids_;
};

}  // namespace jarvis_core
