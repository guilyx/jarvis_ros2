// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include <gtest/gtest.h>

#include <algorithm>
#include <fstream>
#include <functional>
#include <map>
#include <queue>
#include <random>
#include <sstream>
#include <tuple>

#include "jarvis_core/event_hub.hpp"
#include "jarvis_core/fault_manager.hpp"
#include "jarvis_core/orchestrator.hpp"
#include "jarvis_core/rule_engine.hpp"

using namespace jarvis_core;

namespace
{

constexpr Timestamp kT0 = 1'000 * kSecond;

std::string header(const std::string & plans)
{
  return "schema: jarvis.plans/v1\nplans:\n" + plans;
}

constexpr const char * kRestart = R"(
  - id: restart
    params:
      node: { required: true, pattern: "^/" }
      component: { default: relay }
    locks: ["node:{{ params.node }}"]
    max_attempts: { count: 3, per: 1h }
    timeout: 90s
    steps:
      - id: restart
        action: lifecycle.restart
        with: { node: "{{ params.node }}" }
        timeout: 20s
        retry: { attempts: 2, backoff: 2s, multiplier: 2.0 }
      - id: verify
        action: wait_for
        with: { fault: { code: ros.topic.stale, component: "{{ params.component }}" }, state: CLEARED }
        timeout: 15s
)";

std::string attr(const Event & e, const std::string & key)
{
  const auto it = e.attrs.find(key);
  return it == e.attrs.end() ? "" : attr_to_string(it->second);
}

class OrchestratorTest : public ::testing::Test
{
protected:
  void load(const std::string & plans, OrchestratorConfig config = {})
  {
    config.robot_id = "bot";
    config.id_seed = 42;
    o_ = std::make_unique<Orchestrator>(config, PlanSet::from_yaml(header(plans)));
  }

  void log(const Effects & fx)
  {
    for (const auto & f : fx) {
      if (const auto * e = std::get_if<EmitEvent>(&f)) {
        events_.push_back(e->event);
      } else if (const auto * s = std::get_if<StartAction>(&f)) {
        starts_.push_back(*s);
      } else if (const auto * c = std::get_if<CancelAction>(&f)) {
        cancels_.push_back(*c);
      }
    }
  }

  Submitted submit(RecoveryRequest r)
  {
    auto s = o_->submit(r, now_);
    log(s.effects);
    return s;
  }

  Submitted submit(
    const std::string & plan, std::map<std::string, std::string> args = {},
    Requester requester = Requester::Operator)
  {
    RecoveryRequest r;
    r.plan_id = plan;
    r.args = std::move(args);
    r.requester = requester;
    r.requester_id = "alice";
    r.correlation_id = "inc-1";
    r.causation_id = "cause-1";
    return submit(r);
  }

  void result(const StartAction & s, bool ok, const std::string & message = "")
  {
    log(o_->on_action_result(s.execution_id, s.step_id, s.attempt, ok, message, now_));
  }

  void tick(Duration d)
  {
    now_ += d;
    log(o_->advance_to(now_));
  }

  void feed(const Event & e) { log(o_->on_event(e, now_)); }

  static Event fault(
    const std::string & code, const std::string & component, const std::string & state,
    const std::string & fault_id = "f1")
  {
    Event e;
    e.id = "evt-" + fault_id + "-" + state;
    e.kind = Kind::FaultState;
    e.code = code;
    e.source.component = component;
    e.attrs["fault_id"] = fault_id;
    e.attrs["state"] = state;
    return e;
  }

  static Event state(const std::string & code, const std::string & value)
  {
    static int n = 0;
    Event e;
    e.id = "state-" + std::to_string(++n);
    e.kind = Kind::State;
    e.code = code;
    e.attrs["value"] = value;
    return e;
  }

  /// The phases of one execution's events, in order.
  std::vector<std::string> phases(const std::string & execution_id) const
  {
    std::vector<std::string> out;
    for (const auto & e : events_) {
      if (attr(e, "execution_id") == execution_id && e.kind == Kind::Recovery) {
        out.push_back(attr(e, "phase"));
      }
    }
    return out;
  }

  std::vector<const Event *> with_phase(const std::string & phase) const
  {
    std::vector<const Event *> out;
    for (const auto & e : events_) {
      if (e.kind == Kind::Recovery && attr(e, "phase") == phase) {
        out.push_back(&e);
      }
    }
    return out;
  }

  const Execution & exec(const std::string & id) const
  {
    const Execution * x = o_->find(id);
    EXPECT_NE(x, nullptr) << id;
    return *x;
  }

  std::unique_ptr<Orchestrator> o_;
  Timestamp now_{kT0};
  std::vector<Event> events_;
  std::vector<StartAction> starts_;
  std::vector<CancelAction> cancels_;
};

using V = std::vector<std::string>;

}  // namespace

// -- steps ----------------------------------------------------------------------------------------

TEST_F(OrchestratorTest, RunsStepsAndVerifiesTheFaultClears)
{
  load(kRestart);
  feed(fault("ros.topic.stale", "relay", "ACTIVE"));
  const auto s = submit("restart", {{"node", "/relay"}}, Requester::Rule);
  ASSERT_EQ(s.state, ExecState::Running) << s.reason;
  ASSERT_EQ(starts_.size(), 1u);
  EXPECT_EQ(starts_[0].action, "lifecycle.restart");
  EXPECT_EQ(starts_[0].args, (nlohmann::json{{"node", "/relay"}}));
  EXPECT_EQ(starts_[0].attempt, 1);
  EXPECT_EQ(starts_[0].timeout, 20 * kSecond);

  result(starts_[0], true, "active again");
  EXPECT_EQ(exec(s.execution_id).steps[1].state, StepState::Running) << "fault still active";
  feed(fault("ros.topic.stale", "relay", "CLEARED"));

  const auto & x = exec(s.execution_id);
  EXPECT_EQ(x.state, ExecState::Succeeded);
  EXPECT_EQ(
    x.args, (std::map<std::string, std::string>{{"node", "/relay"}, {"component", "relay"}}));
  EXPECT_EQ(x.locks, V{"node:/relay"});
  EXPECT_EQ(
    phases(s.execution_id), (V{"STARTED", "STEP_SUCCEEDED", "STEP_SUCCEEDED", "SUCCEEDED"}));
  V gates;
  for (const auto & g : x.gates) {
    EXPECT_TRUE(g.passed) << g.gate << ": " << g.detail;
    gates.push_back(g.gate);
  }
  EXPECT_EQ(
    gates, (V{"mode", "plan", "requester", "preconditions", "rate_limit", "locks", "approval"}));
  EXPECT_EQ(x.steps[0].message, "active again");
  EXPECT_TRUE(o_->live().empty());
  ASSERT_EQ(o_->history().size(), 1u);

  // The audit chain: correlation on every event, each caused by the previous one.
  std::string previous = "cause-1";
  for (const auto & e : events_) {
    if (attr(e, "execution_id") != s.execution_id) {
      continue;
    }
    EXPECT_EQ(e.correlation_id, "inc-1");
    EXPECT_EQ(e.causation_id, previous);
    std::string phase = attr(e, "phase");
    std::transform(phase.begin(), phase.end(), phase.begin(), [](unsigned char ch) {
      return static_cast<char>(std::tolower(ch));
    });
    EXPECT_EQ(e.code, "jarvis.recovery." + phase);
    EXPECT_EQ(attr(e, "requester"), "RULE");
    EXPECT_EQ(attr(e, "dry_run"), "false");
    previous = e.id;
  }
}

TEST_F(OrchestratorTest, AFaultThatIsAlreadyClearVerifiesImmediately)
{
  load(kRestart);
  const auto s = submit("restart", {{"node", "/relay"}});
  result(starts_[0], true);
  EXPECT_EQ(exec(s.execution_id).state, ExecState::Succeeded);
  EXPECT_EQ(exec(s.execution_id).steps[1].message, "fault ros.topic.stale is CLEARED");
}

TEST_F(OrchestratorTest, WaitForSeesWhatHappenedDuringThePreviousStep)
{
  load(R"(
  - id: reset
    steps:
      - { id: startup, action: nav2.manage_lifecycle, with: { command: STARTUP } }
      - id: verify
        action: wait_for
        with: { event: { kind: STATE, code: nav.stack.state, attrs.value: ACTIVE } }
        timeout: 5s
)");
  feed(state("nav.stack.state", "ACTIVE"));  // before the plan: does not count
  const auto a = submit("reset");
  feed(state("nav.stack.state", "INACTIVE"));
  feed(state("nav.stack.state", "ACTIVE"));  // while startup runs
  result(starts_.back(), true);
  EXPECT_EQ(exec(a.execution_id).state, ExecState::Succeeded);

  const auto b = submit("reset");
  result(starts_.back(), true);
  EXPECT_EQ(exec(b.execution_id).state, ExecState::Running) << "the old ACTIVE is not this run's";
  tick(2 * kSecond);
  feed(state("nav.stack.state", "ACTIVE"));
  EXPECT_EQ(exec(b.execution_id).state, ExecState::Succeeded);

  const auto c = submit("reset");
  result(starts_.back(), true);
  tick(5 * kSecond);
  EXPECT_EQ(exec(c.execution_id).state, ExecState::Failed);
  EXPECT_EQ(exec(c.execution_id).failed_step, "verify");
  EXPECT_EQ(exec(c.execution_id).steps[1].message, "no match within 5s");
}

TEST_F(OrchestratorTest, RetriesWithExponentialBackoff)
{
  load(kRestart);
  const auto s = submit("restart", {{"node", "/relay"}});
  result(starts_[0], false, "ChangeState refused");
  EXPECT_EQ(o_->next_deadline(), now_ + 2 * kSecond);
  EXPECT_EQ(exec(s.execution_id).steps[0].state, StepState::Retrying);
  tick(2 * kSecond);
  ASSERT_EQ(starts_.size(), 2u);
  EXPECT_EQ(starts_[1].attempt, 2);
  result(starts_[1], false);
  EXPECT_EQ(o_->next_deadline(), now_ + 4 * kSecond) << "backoff x multiplier";
  tick(4 * kSecond);
  ASSERT_EQ(starts_.size(), 3u);
  result(starts_[2], false, "still refused");

  const auto & x = exec(s.execution_id);
  EXPECT_EQ(x.state, ExecState::Failed);
  EXPECT_EQ(x.failed_step, "restart");
  EXPECT_EQ(x.reason, "step restart failed: still refused");
  EXPECT_EQ(x.steps[0].attempts, 3);
  EXPECT_EQ(
    phases(s.execution_id),
    (V{"STARTED", "STEP_RETRYING", "STEP_RETRYING", "STEP_FAILED", "FAILED"}));
}

TEST_F(OrchestratorTest, TimeoutsCancelTheAttemptAndLateResultsAreIgnored)
{
  load(kRestart);
  const auto s = submit("restart", {{"node", "/relay"}});
  tick(20 * kSecond);
  ASSERT_EQ(cancels_.size(), 1u);
  EXPECT_EQ(cancels_[0].attempt, 1);
  EXPECT_EQ(exec(s.execution_id).steps[0].message, "timed out after 20s");
  result(starts_[0], true);  // too late
  EXPECT_EQ(exec(s.execution_id).steps[0].state, StepState::Retrying);
  tick(2 * kSecond);
  ASSERT_EQ(starts_.size(), 2u);
  result(starts_[1], true);
  EXPECT_EQ(exec(s.execution_id).steps[0].state, StepState::Succeeded);
}

TEST_F(OrchestratorTest, ALateWakeUpStillFiresEachTimerAtItsDeadline)
{
  load(kRestart);
  submit("restart", {{"node", "/relay"}});
  tick(25 * kSecond);  // the shell overslept: the timeout was due at 20 s, the retry at 22 s
  ASSERT_EQ(with_phase("STEP_RETRYING").size(), 1u);
  EXPECT_EQ(with_phase("STEP_RETRYING")[0]->stamp, kT0 + 20 * kSecond);
  ASSERT_EQ(starts_.size(), 2u);
  EXPECT_EQ(o_->next_deadline(), kT0 + 42 * kSecond) << "attempt 2 started at 22 s, not 25 s";
}

TEST_F(OrchestratorTest, ThePlanTimeoutEndsEverything)
{
  load(R"(
  - id: slow
    timeout: 5s
    steps: [{ id: work, action: x.work, timeout: 10s }]
)");
  const auto s = submit("slow");
  tick(5 * kSecond);
  const auto & x = exec(s.execution_id);
  EXPECT_EQ(x.state, ExecState::Failed);
  EXPECT_EQ(x.reason, "plan timed out after 5s");
  EXPECT_EQ(x.failed_step, "work");
  EXPECT_EQ(x.steps[0].state, StepState::Failed);
  EXPECT_EQ(cancels_.size(), 1u);
}

TEST_F(OrchestratorTest, CoreActionsDelayCheckAndEmit)
{
  load(R"(
  - id: core
    params: { level: { default: WARN } }
    steps:
      - { id: nap, action: delay, with: { duration: 2s } }
      - id: still
        action: check
        with: { state: { robot.motion: STOPPED } }
        retry: { attempts: 3, backoff: 1s }
      - id: note
        action: emit
        with: { code: demo.note, severity: "{{ params.level }}", message: "done on {{ robot }}", attrs: { n: 3 } }
)");
  const auto s = submit("core");
  EXPECT_EQ(o_->next_deadline(), now_ + 2 * kSecond);
  tick(2 * kSecond);
  EXPECT_EQ(exec(s.execution_id).steps[1].state, StepState::Retrying) << "robot.motion unknown";
  feed(state("robot.motion", "STOPPED"));
  tick(kSecond);  // `check` with `retry` polls
  const auto & x = exec(s.execution_id);
  EXPECT_EQ(x.state, ExecState::Succeeded) << x.reason;
  EXPECT_EQ(x.steps[1].attempts, 2);
  const auto note = std::find_if(
    events_.begin(), events_.end(), [](const Event & e) { return e.code == "demo.note"; });
  ASSERT_NE(note, events_.end());
  EXPECT_EQ(note->kind, Kind::Log);
  EXPECT_EQ(note->severity, Severity::Warn);
  EXPECT_EQ(note->message, "done on bot");
  EXPECT_EQ(attr(*note, "n"), "3");
  EXPECT_EQ(note->correlation_id, "inc-1");
  EXPECT_EQ(attr(*note, "execution_id"), s.execution_id);
}

TEST_F(OrchestratorTest, WhenSkipsAndContinueOnFailureCarriesOn)
{
  load(R"(
  - id: steps
    steps:
      - { id: only-if-estop, action: x.a, when: { faults_active: [safety.*] } }
      - { id: best-effort, action: x.b, continue_on_failure: true }
      - { id: last, action: x.c }
)");
  const auto s = submit("steps");
  ASSERT_EQ(starts_.size(), 1u);
  EXPECT_EQ(starts_[0].step_id, "best-effort");
  result(starts_[0], false, "nope");
  result(starts_[1], true);
  const auto & x = exec(s.execution_id);
  EXPECT_EQ(x.state, ExecState::Succeeded);
  EXPECT_EQ(x.steps[0].state, StepState::Skipped);
  EXPECT_EQ(x.steps[1].state, StepState::Failed);
  EXPECT_EQ(x.steps[2].state, StepState::Succeeded);
  EXPECT_TRUE(x.failed_step.empty());
}

TEST_F(OrchestratorTest, ARunStepIsASubPlanThatSharesItsParentsLocks)
{
  load(
    R"(
  - id: parent
    locks: [nav2]
    steps:
      - { id: sub, action: run, with: { plan: child, args: { which: local } } }
      - { id: after, action: x.after }
  - id: child
    params: { which: { required: true } }
    locks: [nav2, "costmap:{{ params.which }}"]
    steps: [{ id: clear, action: x.clear, with: { which: "{{ params.which }}" } }]
    on_failure: [{ escalate: operator }]
)",
    [] {
      OrchestratorConfig c;
      c.max_concurrent = 1;
      return c;
    }());
  const auto p = submit("parent");
  ASSERT_EQ(starts_.size(), 1u) << "the child runs in the parent's slot, with the parent's lock";
  const auto & child = exec(starts_[0].execution_id);
  EXPECT_EQ(child.parent_id, p.execution_id);
  EXPECT_EQ(child.requester, Requester::Plan);
  EXPECT_EQ(child.requester_id, p.execution_id);
  EXPECT_EQ(child.depth, 2);
  EXPECT_EQ(child.correlation_id, "inc-1");
  EXPECT_EQ(starts_[0].args, (nlohmann::json{{"which", "local"}}));
  result(starts_[0], true);
  ASSERT_EQ(starts_.size(), 2u);
  EXPECT_EQ(starts_[1].step_id, "after");

  // A failing child fails the step; the child's own ladder does not run.
  const auto q = submit("parent");
  EXPECT_EQ(q.state, ExecState::Rejected) << "max_concurrent = 1 and the first parent still runs";
  result(starts_[1], true);
  const auto r = submit("parent");
  ASSERT_EQ(r.state, ExecState::Running);
  result(starts_.back(), false, "costmap busy");
  EXPECT_EQ(exec(r.execution_id).state, ExecState::Failed);
  EXPECT_EQ(
    exec(r.execution_id).reason,
    "step sub failed: plan child failed: step clear failed: costmap busy");
  EXPECT_TRUE(with_phase("ESCALATED").empty());
  for (const auto & e : events_) {
    if (attr(e, "plan") == "child") {
      EXPECT_EQ(attr(e, "parent_id").empty(), false) << "child events name their parent";
    }
  }
}

// -- gates ----------------------------------------------------------------------------------------

TEST_F(OrchestratorTest, LocksRejectOrQueue)
{
  load(std::string(kRestart) + R"(
  - id: queued
    params: { node: { required: true } }
    locks: ["node:{{ params.node }}"]
    on_conflict: queue
    queue_ttl: 10s
    steps: [{ id: work, action: x.work, timeout: 1m }]
)");
  const auto a = submit("restart", {{"node", "/relay"}});
  const auto b = submit("restart", {{"node", "/relay"}});
  EXPECT_EQ(b.state, ExecState::Rejected);
  EXPECT_EQ(b.reason, "lock 'node:/relay' is held by restart (" + a.execution_id + ")");
  EXPECT_EQ(submit("restart", {{"node", "/other"}}).state, ExecState::Running)
    << "per rendered lock";

  const auto c = submit("queued", {{"node", "/relay"}});
  EXPECT_EQ(c.state, ExecState::Queued);
  result(starts_[0], true);  // `a` finishes (fault already clear) and frees the lock
  EXPECT_EQ(exec(a.execution_id).state, ExecState::Succeeded);
  EXPECT_EQ(exec(c.execution_id).state, ExecState::Running);
  EXPECT_EQ(phases(c.execution_id), (V{"QUEUED", "STARTED"}));

  const auto d = submit("queued", {{"node", "/relay"}});
  EXPECT_EQ(d.state, ExecState::Queued);
  tick(10 * kSecond);
  EXPECT_EQ(exec(d.execution_id).state, ExecState::Expired);
  EXPECT_EQ(exec(d.execution_id).reason, "still waiting for locks after 10s");
}

TEST_F(OrchestratorTest, RateLimitsCountStartsPerPlanAndArgs)
{
  load(R"(
  - id: quick
    params: { node: {} }
    max_attempts: { count: 2, per: 1h }
    steps: [{ id: ok, action: check, with: { faults_inactive: [x.*] } }]
)");
  EXPECT_EQ(submit("quick", {{"node", "/a"}}).state, ExecState::Succeeded);
  EXPECT_EQ(submit("quick", {{"node", "/a"}}).state, ExecState::Succeeded);
  const auto third = submit("quick", {{"node", "/a"}});
  EXPECT_EQ(third.state, ExecState::Rejected);
  EXPECT_EQ(third.reason, "max_attempts reached (2 per 1h)");
  EXPECT_EQ(submit("quick", {{"node", "/b"}}).state, ExecState::Succeeded) << "other args";
  now_ += kHour;
  EXPECT_EQ(submit("quick", {{"node", "/a"}}).state, ExecState::Succeeded) << "window slid";
}

TEST_F(OrchestratorTest, PreconditionsAndParamsAreGates)
{
  load(std::string(kRestart) + R"(
  - id: careful
    preconditions: { faults_inactive: [safety.*] }
    steps: [{ id: ok, action: check, with: { faults_inactive: [nothing] } }]
)");
  feed(fault("safety.estop.engaged", "base", "LATCHED", "f9"));
  auto s = submit("careful");
  EXPECT_EQ(s.state, ExecState::Rejected);
  EXPECT_EQ(s.reason, "preconditions not met");
  feed(fault("safety.estop.engaged", "base", "CLEARED", "f9"));
  EXPECT_EQ(submit("careful").state, ExecState::Succeeded);

  EXPECT_EQ(submit("restart", {}).reason, "missing required parameter 'node'");
  EXPECT_EQ(
    submit("restart", {{"node", "relay"}}).reason,
    "parameter 'node': 'relay' does not match pattern '^/'");
  EXPECT_EQ(submit("nope").reason, "unknown plan 'nope'");
  EXPECT_FALSE(exec(submit("nope").execution_id).gates.back().passed);
}

TEST_F(OrchestratorTest, AgentAutonomyIsTheLowerOfDeploymentAndPlan)
{
  struct Case
  {
    AgentPolicy deployment;
    std::string plan_policy;
    std::optional<double> confidence;
    ExecState expected;
  };
  const std::vector<Case> cases{
    {AgentPolicy::Autonomous, "autonomous", 0.9, ExecState::Running},
    {AgentPolicy::Autonomous, "autonomous", 0.5, ExecState::PendingApproval},
    {AgentPolicy::Autonomous, "autonomous", std::nullopt, ExecState::PendingApproval},
    {AgentPolicy::Supervised, "autonomous", 0.99, ExecState::PendingApproval},
    {AgentPolicy::Autonomous, "supervised", 0.99, ExecState::PendingApproval},
    {AgentPolicy::Autonomous, "advise", 0.99, ExecState::Rejected},
    {AgentPolicy::Autonomous, "never", 0.99, ExecState::Rejected},
    {AgentPolicy::Never, "autonomous", 0.99, ExecState::Rejected},
  };
  for (const auto & c : cases) {
    OrchestratorConfig config;
    config.agent_autonomy = c.deployment;
    load(
      "  - { id: p, agent_policy: " + c.plan_policy + ", steps: [{ id: s, action: x.y }] }\n",
      config);
    RecoveryRequest r;
    r.plan_id = "p";
    r.requester = Requester::Agent;
    r.requester_id = "insight-1";
    r.confidence = c.confidence;
    const auto s = submit(r);
    EXPECT_EQ(s.state, c.expected) << to_string(c.deployment) << " x " << c.plan_policy << " @ "
                                   << c.confidence.value_or(-1) << ": " << s.reason;
  }
  load(
    "  - { id: p, agent_policy: autonomous, allowed_requesters: [OPERATOR], steps: [{ id: s, "
    "action: x.y }] }\n");
  EXPECT_EQ(submit("p", {}, Requester::Agent).reason, "plan does not accept AGENT requests");
}

TEST_F(OrchestratorTest, ApprovalsWaitDecideAndExpire)
{
  load(R"(
  - id: gated
    requires_approval: [RULE]
    locks: [arm]
    steps: [{ id: s, action: x.y }]
  - id: holder
    locks: [arm]
    steps: [{ id: s, action: x.hold }]
)");
  EXPECT_EQ(submit("gated", {}, Requester::Operator).state, ExecState::Running)
    << "operators need none";
  result(starts_.back(), true);

  const auto a = submit("gated", {}, Requester::Rule);
  ASSERT_EQ(a.state, ExecState::PendingApproval);
  ASSERT_EQ(with_phase("PENDING_APPROVAL").size(), 1u);
  EXPECT_EQ(
    attr(*with_phase("PENDING_APPROVAL")[0], "expires"), format_iso8601(now_ + 5 * kMinute));
  EXPECT_TRUE(starts_.size() == 1u) << "nothing runs before approval";
  auto d = o_->decide(a.execution_id, true, "alice", "go", now_);
  log(d.effects);
  EXPECT_TRUE(d.success);
  EXPECT_EQ(exec(a.execution_id).state, ExecState::Running);
  EXPECT_EQ(exec(a.execution_id).approved_by, "alice");
  EXPECT_EQ(phases(a.execution_id), (V{"PENDING_APPROVAL", "APPROVED", "STARTED"}));
  EXPECT_FALSE(o_->decide(a.execution_id, true, "bob", "", now_).success) << "not waiting any more";
  result(starts_.back(), true);

  const auto b = submit("gated", {}, Requester::Rule);
  d = o_->decide(b.execution_id, false, "alice", "not now", now_);
  log(d.effects);
  EXPECT_EQ(exec(b.execution_id).state, ExecState::Rejected);
  EXPECT_EQ(exec(b.execution_id).reason, "rejected by alice: not now");

  const auto c = submit("gated", {}, Requester::Rule);
  tick(5 * kMinute);
  EXPECT_EQ(exec(c.execution_id).state, ExecState::Expired);
  EXPECT_FALSE(o_->decide(c.execution_id, true, "alice", "", now_).success);

  // Approved while another plan holds the lock: it rejects (on_conflict: reject).
  const auto e = submit("gated", {}, Requester::Rule);
  submit("holder");
  d = o_->decide(e.execution_id, true, "alice", "", now_);
  log(d.effects);
  EXPECT_EQ(exec(e.execution_id).state, ExecState::Rejected);
  EXPECT_NE(exec(e.execution_id).reason.find("lock 'arm' is held by holder"), std::string::npos);
}

TEST_F(OrchestratorTest, DryRunSimulatesEveryStepAndTouchesNothing)
{
  OrchestratorConfig config;
  config.mode = RecoveryMode::DryRun;
  load(
    std::string(kRestart) + R"(
  - id: gated
    requires_approval: [RULE]
    max_attempts: { count: 1, per: 1h }
    steps:
      - { id: sub, action: run, with: { plan: restart, args: { node: /relay } } }
      - { id: wait, action: delay, with: { duration: 1h } }
)",
    config);
  const auto s = submit("gated", {}, Requester::Rule);
  EXPECT_EQ(s.state, ExecState::Succeeded) << s.reason;
  EXPECT_TRUE(starts_.empty());
  const auto & x = exec(s.execution_id);
  EXPECT_TRUE(x.dry_run);
  EXPECT_EQ(x.gates.back().detail, "would need approval (dry run)");
  EXPECT_EQ(x.steps[1].message, "simulated (dry run)");
  for (const auto & e : events_) {
    EXPECT_EQ(attr(e, "dry_run"), "true") << e.code;
  }
  EXPECT_EQ(submit("gated", {}, Requester::Rule).state, ExecState::Rejected) << "dry rate limit";

  log(o_->set_mode(RecoveryMode::Enabled, "alice", "", false, now_).effects);
  RecoveryRequest r;
  r.plan_id = "gated";
  r.requester = Requester::Rule;
  EXPECT_EQ(submit(r).state, ExecState::PendingApproval)
    << "dry runs did not use the live max_attempts budget";
  r.dry_run = true;
  EXPECT_EQ(submit(r).state, ExecState::Rejected) << "a dry run requested while ENABLED";
}

TEST_F(OrchestratorTest, TheKillSwitchStopsEverything)
{
  load(std::string(kRestart) + R"(
  - id: gated
    requires_approval: [OPERATOR]
    steps: [{ id: s, action: x.y }]
  - id: queued
    locks: ["node:/relay"]
    on_conflict: queue
    steps: [{ id: s, action: x.y }]
)");
  const auto running = submit("restart", {{"node", "/relay"}});
  const auto pending = submit("gated");
  const auto queued = submit("queued");
  ASSERT_EQ(queued.state, ExecState::Queued);

  auto r = o_->set_mode(RecoveryMode::Disabled, "alice", "maintenance", true, now_);
  log(r.effects);
  EXPECT_TRUE(r.success);
  EXPECT_EQ(r.message, "ENABLED -> DISABLED");
  EXPECT_EQ(exec(running.execution_id).state, ExecState::Aborted);
  EXPECT_EQ(exec(running.execution_id).reason, "recovery disabled by alice: maintenance");
  EXPECT_EQ(cancels_.size(), 1u) << "the attempt in flight is cancelled";
  EXPECT_EQ(exec(pending.execution_id).state, ExecState::Rejected);
  EXPECT_EQ(exec(queued.execution_id).state, ExecState::Rejected)
    << "not started by the freed lock";
  const auto mode = std::find_if(events_.begin(), events_.end(), [](const Event & e) {
    return e.code == "jarvis.recovery.mode";
  });
  ASSERT_NE(mode, events_.end());
  EXPECT_EQ(mode->kind, Kind::Operator);
  EXPECT_EQ(mode->severity, Severity::Warn);
  EXPECT_EQ(attr(*mode, "previous_mode"), "ENABLED");
  EXPECT_EQ(submit("gated").reason, "recovery is DISABLED");

  log(o_->set_mode(RecoveryMode::Enabled, "alice", "", false, now_).effects);
  const auto again = submit("restart", {{"node", "/relay"}});
  EXPECT_EQ(again.state, ExecState::Running);
  log(o_->set_mode(RecoveryMode::Disabled, "alice", "", false, now_).effects);
  EXPECT_EQ(exec(again.execution_id).state, ExecState::Running) << "without cancel_running";
  result(starts_.back(), true);
  EXPECT_EQ(exec(again.execution_id).state, ExecState::Succeeded);
}

TEST_F(OrchestratorTest, OperatorsCanCancel)
{
  load(kRestart);
  const auto s = submit("restart", {{"node", "/relay"}});
  auto r = o_->cancel(s.execution_id, "alice", "wrong node", now_);
  log(r.effects);
  EXPECT_TRUE(r.success);
  EXPECT_EQ(exec(s.execution_id).state, ExecState::Aborted);
  EXPECT_EQ(exec(s.execution_id).reason, "cancelled by alice: wrong node");
  EXPECT_EQ(exec(s.execution_id).steps[0].state, StepState::Aborted);
  EXPECT_EQ(cancels_.size(), 1u);
  EXPECT_FALSE(o_->cancel(s.execution_id, "alice", "", now_).success);
}

// -- escalation ladder ----------------------------------------------------------------------------

namespace
{

constexpr const char * kLadder = R"(
  - id: l1
    params: { node: { default: /relay } }
    steps: [{ id: a, action: x.l1 }]
    on_failure:
      - run: { plan: l2, args: { target: "{{ params.node }}" } }
      - escalate: operator
  - id: l2
    params: { target: { required: true } }
    steps: [{ id: a, action: x.l2 }]
    on_failure:
      - run: { plan: l3 }
      - escalate: operator
  - id: l3
    steps: [{ id: a, action: x.l3 }]
    on_failure:
      - escalate: operator
)";

}  // namespace

TEST_F(OrchestratorTest, TheLadderStopsAtTheFirstSuccess)
{
  load(kLadder);
  const auto s = submit("l1", {}, Requester::Rule);
  result(starts_[0], false);
  ASSERT_EQ(starts_.size(), 2u);
  const auto & l2 = exec(starts_[1].execution_id);
  EXPECT_EQ(l2.plan_id, "l2");
  EXPECT_EQ(l2.ladder_of, s.execution_id);
  EXPECT_EQ(l2.requester, Requester::Plan);
  EXPECT_EQ(l2.args.at("target"), "/relay") << "args rendered from the failed plan's params";
  EXPECT_EQ(l2.correlation_id, "inc-1");
  EXPECT_TRUE(exec(s.execution_id).ladder_running);
  EXPECT_EQ(o_->live().size(), 2u) << "the failed owner stays live while its ladder runs";
  result(starts_[1], true);
  EXPECT_TRUE(with_phase("ESCALATED").empty());
  ASSERT_EQ(with_phase("LADDER_DONE").size(), 1u);
  EXPECT_EQ(attr(*with_phase("LADDER_DONE")[0], "outcome"), "recovered by l2");
  EXPECT_TRUE(o_->live().empty());
}

TEST_F(OrchestratorTest, NestedLaddersRunToTheHumanExactlyOnce)
{
  load(kLadder);
  // L1 fails, L2 fails, L3 (safe state) succeeds: L2's ladder is done, L1's continues to a human.
  const auto s = submit("l1", {}, Requester::Rule);
  result(starts_[0], false);
  result(starts_[1], false);
  ASSERT_EQ(starts_.size(), 3u);
  EXPECT_EQ(starts_[2].action, "x.l3");
  result(starts_[2], true);
  ASSERT_EQ(with_phase("ESCALATED").size(), 1u);
  const Event & escalated = *with_phase("ESCALATED")[0];
  EXPECT_EQ(attr(escalated, "execution_id"), s.execution_id) << "L1's own ladder escalates";
  EXPECT_EQ(attr(escalated, "to"), "operator");
  EXPECT_EQ(escalated.correlation_id, "inc-1");
  EXPECT_EQ(escalated.severity, Severity::Error);
  EXPECT_TRUE(exec(s.execution_id).escalated);

  // Everything fails: L3's ladder escalates first, and the others do not page again.
  events_.clear();
  starts_.clear();
  submit("l1", {}, Requester::Rule);
  result(starts_[0], false);
  result(starts_[1], false);
  result(starts_[2], false);
  EXPECT_EQ(with_phase("ESCALATED").size(), 1u);
  EXPECT_EQ(with_phase("LADDER_DONE").size(), 3u);
  EXPECT_TRUE(o_->live().empty());
}

TEST_F(OrchestratorTest, AbortingALadderEntryStopsTheLadder)
{
  load(kLadder);
  const auto s = submit("l1");
  result(starts_[0], false);
  log(o_->cancel(starts_[1].execution_id, "alice", "", now_).effects);
  EXPECT_TRUE(with_phase("ESCALATED").empty()) << "a human intervened";
  EXPECT_FALSE(exec(s.execution_id).ladder_running);

  // Stopping the failed owner stops the entry it waits for.
  starts_.clear();
  const auto t = submit("l1");
  result(starts_[0], false);
  const auto entry = starts_[1].execution_id;
  auto r = o_->cancel(t.execution_id, "alice", "enough", now_);
  log(r.effects);
  EXPECT_TRUE(r.success);
  EXPECT_EQ(r.message, "escalation ladder stopped");
  EXPECT_EQ(exec(entry).state, ExecState::Aborted);
  EXPECT_TRUE(with_phase("ESCALATED").empty());
  EXPECT_TRUE(o_->live().empty());
}

TEST_F(OrchestratorTest, ARejectedLadderEntryMovesOn)
{
  load(R"(
  - id: first
    steps: [{ id: a, action: x.a }]
    on_failure:
      - run: { plan: picky }
      - escalate: oncall
  - id: picky
    preconditions: { faults_active: [never.happens] }
    steps: [{ id: a, action: x.b }]
)");
  submit("first");
  result(starts_[0], false);
  EXPECT_EQ(starts_.size(), 1u);
  ASSERT_EQ(with_phase("REJECTED").size(), 1u);
  ASSERT_EQ(with_phase("ESCALATED").size(), 1u);
  EXPECT_EQ(attr(*with_phase("ESCALATED")[0], "to"), "oncall");
}

// -- persistence and reload -----------------------------------------------------------------------

TEST_F(OrchestratorTest, RestartKeepsWaitersAndInterruptsWhatRan)
{
  load(std::string(kRestart) + R"(
  - id: gated
    requires_approval: [RULE]
    steps: [{ id: s, action: x.y }]
)");
  const auto running = submit("restart", {{"node", "/relay"}});
  const auto pending = submit("gated", {}, Requester::Rule);
  submit("restart", {{"node", "/other"}});
  result(starts_[1], true);  // a second attempt counts for /other
  log(o_->set_mode(RecoveryMode::DryRun, "alice", "", false, now_).effects);
  const auto snap = o_->snapshot();
  EXPECT_EQ(snap["schema"], "jarvis.recovery.state/v1");

  OrchestratorConfig config;
  config.robot_id = "bot";
  config.id_seed = 7;
  Orchestrator restored(config, PlanSet::from_yaml(header(std::string(kRestart) + R"(
  - id: gated
    requires_approval: [RULE]
    steps: [{ id: s, action: x.y }]
)")));
  now_ += 3 * kSecond;
  events_.clear();
  log(restored.restore(snap, now_));
  EXPECT_EQ(restored.mode(), RecoveryMode::DryRun);

  const Execution * r = restored.find(running.execution_id);
  ASSERT_NE(r, nullptr);
  EXPECT_EQ(r->state, ExecState::Aborted);
  EXPECT_EQ(r->steps[0].state, StepState::Aborted);
  ASSERT_EQ(events_.size(), 2u);
  EXPECT_EQ(events_[0].code, "jarvis.recovery.interrupted");
  EXPECT_EQ(attr(events_[0], "phase"), "ABORTED") << "so the incident leaves RECOVERING";
  EXPECT_EQ(events_[0].severity, Severity::Error);
  EXPECT_EQ(attr(events_[1], "phase"), "ESCALATED");
  EXPECT_EQ(events_[1].correlation_id, "inc-1");

  const Execution * p = restored.find(pending.execution_id);
  ASSERT_NE(p, nullptr);
  EXPECT_EQ(p->state, ExecState::PendingApproval);
  log(restored.set_mode(RecoveryMode::Enabled, "alice", "", false, now_).effects);
  const auto d = restored.decide(pending.execution_id, true, "alice", "", now_);
  EXPECT_TRUE(d.success);
  EXPECT_EQ(restored.find(pending.execution_id)->state, ExecState::Running);

  // The rate limit survived: the interrupted run used one of the three attempts on /relay.
  RecoveryRequest req;
  req.plan_id = "restart";
  req.args = {{"node", "/relay"}};
  EXPECT_EQ(restored.submit(req, now_).state, ExecState::Running);
  restored.cancel(restored.live().back().execution_id, "t", "", now_);
  EXPECT_EQ(restored.submit(req, now_).state, ExecState::Running);
  restored.cancel(restored.live().back().execution_id, "t", "", now_);
  EXPECT_EQ(restored.submit(req, now_).reason, "max_attempts reached (3 per 1h)");

  EXPECT_THROW(restored.restore(nlohmann::json{{"schema", "nope"}}, now_), std::invalid_argument);
}

TEST_F(OrchestratorTest, AnInterruptedLadderEntryStopsItsLadderAndEscalatesOnce)
{
  load(kLadder);
  submit("l1");
  result(starts_[0], false);  // l2 now runs as the ladder's first entry
  const auto snap = o_->snapshot();
  EXPECT_EQ(snap["executions"].size(), 2u);
  load(kLadder);
  events_.clear();
  log(o_->restore(snap, now_));
  EXPECT_EQ(with_phase("ESCALATED").size(), 1u);
  EXPECT_TRUE(o_->live().empty());
}

TEST_F(OrchestratorTest, ReloadRejectsWaitersWhosePlanChanged)
{
  const std::string gated = R"(
  - id: gated
    requires_approval: [OPERATOR]
    steps: [{ id: s, action: x.y }]
  - id: other
    requires_approval: [OPERATOR]
    steps: [{ id: s, action: x.y }]
  - id: long
    steps: [{ id: a, action: x.a }, { id: b, action: x.b }]
)";
  load(gated);
  const auto a = submit("gated");
  const auto b = submit("other");
  const auto c = submit("long");
  std::string changed = gated;
  changed.replace(changed.find("action: x.y"), 11, "action: x.z");
  log(o_->reload(PlanSet::from_yaml(header(changed + "\n")), now_));
  EXPECT_EQ(exec(a.execution_id).state, ExecState::Rejected);
  EXPECT_EQ(exec(a.execution_id).reason, "plan changed while waiting");
  EXPECT_EQ(exec(b.execution_id).state, ExecState::PendingApproval);
  result(starts_.back(), true);
  EXPECT_EQ(starts_.back().step_id, "b") << "a running execution keeps the plan it started with";
  EXPECT_NE(
    std::find_if(
      events_.begin(), events_.end(),
      [](const Event & e) { return e.code == "jarvis.recovery.reloaded"; }),
    events_.end());
  (void)c;
}

TEST(ExecutionJson, RoundTripsAndMatchesTheSchemaExample)
{
  std::ifstream in(std::string(JARVIS_SCHEMA_EXAMPLES_DIR) + "/execution.failed.json");
  ASSERT_TRUE(in) << "example not found";
  std::stringstream buffer;
  buffer << in.rdbuf();
  const auto example = nlohmann::json::parse(buffer.str());
  const auto x = example.get<Execution>();
  EXPECT_EQ(x.state, ExecState::Failed);
  EXPECT_EQ(x.requester, Requester::Rule);
  ASSERT_EQ(x.steps.size(), 2u);
  EXPECT_EQ(x.steps[0].state, StepState::Succeeded);
  EXPECT_EQ(x.steps[1].attempts, 1);
  EXPECT_TRUE(x.ladder_running);
  EXPECT_EQ(nlohmann::json(x), example) << "write(read(example)) == example";
}

// -- the whole loop, in the core --------------------------------------------------------------------

TEST(RecoveryLoop, AStalledTopicIsRestartedVerifiedAndTheIncidentResolves)
{
  // Hub -> fault manager -> rule engine -> orchestrator -> (simulated) actuator, all pure.
  EventHub hub(EventHubConfig{}, 1, 1);
  FaultManagerConfig fmc;
  fmc.robot_id = "bot";
  FaultManager fm(
    FaultCatalog::from_yaml(R"(
schema: jarvis.faults/v1
defaults: { severity: ERROR, debounce: immediate, heal: { on_clear: true } }
faults: [{ code: ros.topic.stale }]
)"),
    fmc, 2);
  RuleEngine rules(RuleEngineConfig{"bot"}, RuleSet::from_yaml(R"(
schema: jarvis.rules/v1
rules:
  - id: scan-stale-restart
    when: { event: { kind: FAULT_STATE, code: ros.topic.stale, attrs.state: ACTIVE } }
    then: [{ recover: { plan: restart, args: { node: "/{{ event.source.component }}" } } }]
)"));
  OrchestratorConfig oc;
  oc.robot_id = "bot";
  oc.id_seed = 3;
  Orchestrator orch(oc, PlanSet::from_yaml(header(kRestart)));

  Timestamp now = kT0;
  std::vector<Event> stream;
  std::vector<StartAction> actions;
  std::deque<Event> inbox;
  std::function<void(const Effects &)> perform = [&](const Effects & fx) {
    for (const auto & f : fx) {
      if (const auto * e = std::get_if<EmitEvent>(&f)) {
        inbox.push_back(e->event);
      } else if (const auto * r = std::get_if<RequestRecovery>(&f)) {
        perform(orch.submit(to_request(*r), now).effects);
      } else if (const auto * s = std::get_if<StartAction>(&f)) {
        actions.push_back(*s);
      }
    }
  };
  auto pump = [&] {
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
  };
  auto report = [&](Kind kind) {
    Event e;
    e.id = "report-" + std::to_string(stream.size());
    e.kind = kind;
    e.code = "ros.topic.stale";
    e.severity = Severity::Error;
    e.source.node = "/jarvis/watchdog";
    e.source.component = "relay";
    inbox.push_back(e);
    pump();
  };

  report(Kind::FaultRaise);
  ASSERT_EQ(actions.size(), 1u) << "the rule fired and the plan started";
  EXPECT_EQ(actions[0].args["node"], "/relay");
  const std::string incident = fm.incidents().front().incident_id;
  EXPECT_EQ(fm.find_incident(incident)->state, IncidentState::Recovering);

  now += 2 * kSecond;
  perform(orch.on_action_result(actions[0].execution_id, actions[0].step_id, 1, true, "", now));
  pump();
  report(Kind::FaultClear);  // the relay publishes again

  const Execution * x = orch.find(actions[0].execution_id);
  ASSERT_NE(x, nullptr);
  EXPECT_EQ(x->state, ExecState::Succeeded);
  EXPECT_EQ(x->correlation_id, incident) << "the rule passed the incident on";
  EXPECT_EQ(x->requester_id, "scan-stale-restart");
  EXPECT_EQ(fm.find_incident(incident)->state, IncidentState::Resolved);
  V codes;
  for (const auto & e : stream) {
    codes.push_back(e.code);
  }
  EXPECT_EQ(
    codes,
    (
      V{"ros.topic.stale", "jarvis.incident.opened", "ros.topic.stale", "jarvis.rule.matched",
        "jarvis.recovery.started", "jarvis.incident.recovering", "jarvis.recovery.step_succeeded",
        "ros.topic.stale", "ros.topic.stale", "jarvis.incident.resolved",
        "jarvis.recovery.step_succeeded", "jarvis.recovery.succeeded"}));
}

// -- properties -----------------------------------------------------------------------------------

namespace
{

constexpr const char * kPropertyPlans = R"(
  - id: a
    params: { n: { default: "1" } }
    locks: ["n:{{ params.n }}"]
    on_conflict: queue
    queue_ttl: 20s
    max_attempts: { count: 4, per: 2m }
    timeout: 40s
    steps:
      - { id: act, action: x.act, timeout: 5s, retry: { attempts: 2, backoff: 1s, multiplier: 2.0 } }
      - id: verify
        action: wait_for
        with: { fault: { code: "f.*", component: "c{{ params.n }}" }, state: CLEARED }
        timeout: 6s
    on_failure: [{ run: { plan: b, args: { n: "{{ params.n }}" } } }, { escalate: operator }]
  - id: b
    params: { n: { default: "1" } }
    locks: ["n:{{ params.n }}"]
    requires_approval: [PLAN]
    steps:
      - { id: pause, action: delay, with: { duration: 3s } }
      - { id: sub, action: run, with: { plan: c } }
      - { id: look, action: check, with: { state: { s.mode: OK } }, retry: { attempts: 2, backoff: 2s } }
    on_failure: [{ escalate: operator }]
  - id: c
    steps:
      - { id: act, action: x.c, timeout: 4s }
      - { id: see, action: wait_for, with: { event: { kind: STATE, code: s.mode } }, timeout: 3s }
)";

struct Op
{
  Timestamp at;
  std::uint64_t seq;
  std::function<Effects(Orchestrator &, Timestamp)> run;
  bool operator>(const Op & o) const { return std::tie(at, seq) > std::tie(o.at, o.seq); }
};

/// Drives an orchestrator through a random but reproducible scenario. The environment reacts to
/// StartAction effects (a result arrives after a delay derived from the attempt, or never), so the
/// harness wakes at next_deadline() like the ROS shell. `grid` adds spurious wake-ups.
std::vector<std::string> simulate(std::uint64_t seed, Duration grid)
{
  OrchestratorConfig config;
  config.robot_id = "bot";
  config.id_seed = seed;
  config.max_concurrent = 2;
  Orchestrator o(config, PlanSet::from_yaml(header(kPropertyPlans)));
  std::mt19937_64 rng(seed);
  std::priority_queue<Op, std::vector<Op>, std::greater<Op>> ops;
  std::uint64_t seq = 0;
  const Timestamp end = kT0 + 10 * kMinute;
  for (int i = 0; i < 40; ++i) {
    const Timestamp at = kT0 + static_cast<Timestamp>(rng() % (8 * 60)) * kSecond;
    const std::string n = std::to_string(rng() % 3);
    const auto requester = rng() % 2 ? Requester::Rule : Requester::Operator;
    const bool dry = rng() % 8 == 0;
    ops.push({at, seq++, [n, requester, dry](Orchestrator & orch, Timestamp now) {
                RecoveryRequest r;
                r.plan_id = "a";
                r.args = {{"n", n}};
                r.requester = requester;
                r.correlation_id = "inc-" + n;
                r.dry_run = dry;
                return orch.submit(r, now).effects;
              }});
  }
  for (int i = 0; i < 60; ++i) {
    const Timestamp at = kT0 + static_cast<Timestamp>(rng() % (9 * 60 * 10)) * 100 * kMillisecond;
    const auto n = std::to_string(rng() % 3);
    const auto kind = rng() % 3;
    const bool flag = rng() % 2;
    ops.push({at, seq++, [n, kind, flag, i](Orchestrator & orch, Timestamp now) {
                Event e;
                e.id = "e" + std::to_string(i);
                if (kind == 0) {
                  e.kind = Kind::FaultState;
                  e.code = "f.stale";
                  e.source.component = "c" + n;
                  e.attrs["fault_id"] = "fault-" + n;
                  e.attrs["state"] = flag ? std::string("ACTIVE") : std::string("CLEARED");
                } else {
                  e.kind = Kind::State;
                  e.code = "s.mode";
                  e.attrs["value"] = flag ? std::string("OK") : std::string("BAD");
                }
                return orch.on_event(e, now);
              }});
    if (i % 10 == 0) {
      ops.push({at, seq++, [](Orchestrator & orch, Timestamp now) {
                  Effects fx;
                  for (const auto & x : orch.live()) {
                    if (x.state == ExecState::PendingApproval) {
                      auto d = orch.decide(x.execution_id, x.created % 2 == 0, "op", "", now);
                      fx.insert(fx.end(), d.effects.begin(), d.effects.end());
                    }
                  }
                  return fx;
                }});
    }
  }

  std::vector<std::string> log;
  auto record = [&](const Effects & fx, Timestamp now) {
    for (const auto & f : fx) {
      if (const auto * e = std::get_if<EmitEvent>(&f)) {
        log.push_back(
          format_iso8601(e->event.stamp) + " " + e->event.id + " " + e->event.code + " " +
          e->event.message);
      } else if (const auto * s = std::get_if<StartAction>(&f)) {
        log.push_back(
          "start " + s->execution_id + " " + s->step_id + "#" + std::to_string(s->attempt));
        // The environment: a result after 0.5-7.5 s, or never, decided by the attempt itself.
        const auto h = std::hash<std::string>{}(s->execution_id + s->step_id) +
                       static_cast<std::size_t>(s->attempt) * 7919;
        if (h % 5 != 0) {
          const auto delay = static_cast<Duration>(h % 15 + 1) * 500 * kMillisecond;
          const bool ok = h % 3 != 0;
          const StartAction start = *s;
          ops.push({now + delay, seq++, [start, ok](Orchestrator & orch, Timestamp t) {
                      return orch.on_action_result(
                        start.execution_id, start.step_id, start.attempt, ok, "", t);
                    }});
        }
      } else if (const auto * c = std::get_if<CancelAction>(&f)) {
        log.push_back(
          "cancel " + c->execution_id + " " + c->step_id + "#" + std::to_string(c->attempt));
      }
    }
  };
  auto check_invariants = [&] {
    std::size_t roots = 0;
    std::map<std::string, std::string> held;
    for (const auto & x : o.live()) {
      if (x.state != ExecState::Running) {
        continue;
      }
      roots += x.parent_id.empty() ? 1 : 0;
      for (const auto & lock : x.locks) {
        if (const auto it = held.find(lock); it != held.end()) {
          const Execution * other = o.find(it->second);
          ASSERT_TRUE(x.parent_id == it->second || (other && other->parent_id == x.execution_id))
            << "lock " << lock << " held twice";
        }
        held[lock] = x.execution_id;
      }
    }
    ASSERT_LE(roots, 2u) << "max_concurrent";
  };

  Timestamp now = kT0;
  for (;;) {
    record(o.advance_to(now), now);
    bool ran = false;
    while (!ops.empty() && ops.top().at <= now) {
      Op op = ops.top();
      ops.pop();
      record(op.run(o, now), now);
      check_invariants();
      ran = true;
    }
    if (ran) {
      continue;  // what the ops did may be due right now
    }
    if (now >= end) {
      break;
    }
    Timestamp next = end;
    if (!ops.empty()) {
      next = std::min(next, ops.top().at);
    }
    if (const auto d = o.next_deadline()) {
      next = std::min(next, *d);
    }
    if (grid > 0) {
      next = std::min(next, now + grid);
    }
    now = next;
  }
  record(o.advance_to(end + kHour), end + kHour);
  for (const auto & x : o.live()) {
    ADD_FAILURE() << "still live after an hour of quiet: " << x.execution_id << " "
                  << to_string(x.state);
  }
  for (const auto & x : o.history()) {
    EXPECT_TRUE(is_terminal(x.state));
  }
  return log;
}

}  // namespace

TEST(OrchestratorProperties, SameInputsSameDecisions)
{
  for (std::uint64_t seed = 1; seed <= 10; ++seed) {
    EXPECT_EQ(simulate(seed, 0), simulate(seed, 0)) << "seed " << seed;
  }
}

TEST(OrchestratorProperties, ExtraWakeUpsDoNotChangeDecisions)
{
  for (std::uint64_t seed = 1; seed <= 10; ++seed) {
    const auto lazy = simulate(seed, 0);
    EXPECT_GT(lazy.size(), 50u) << "the scenario exercises the orchestrator";
    EXPECT_EQ(lazy, simulate(seed, 100 * kMillisecond)) << "seed " << seed;
    EXPECT_EQ(lazy, simulate(seed, 1700 * kMillisecond)) << "seed " << seed;
  }
}
