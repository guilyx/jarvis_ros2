// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_core/orchestrator.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <stdexcept>
#include <utility>

#include "jarvis_core/json.hpp"

namespace jarvis_core
{

namespace
{

constexpr const char * kSchema = "jarvis.recovery.state/v1";

std::string upper(std::string_view text)
{
  std::string out(text);
  std::transform(out.begin(), out.end(), out.begin(), [](unsigned char c) {
    return static_cast<char>(std::toupper(c));
  });
  return out;
}

std::string lower(std::string_view text)
{
  std::string out(text);
  std::transform(out.begin(), out.end(), out.begin(), [](unsigned char c) {
    return static_cast<char>(std::tolower(c));
  });
  return out;
}

template <typename E, std::size_t N>
std::optional<E> parse_enum(std::string_view text, const std::array<E, N> & all)
{
  const auto u = upper(text);
  for (const E e : all) {
    if (u == to_string(e)) {
      return e;
    }
  }
  return std::nullopt;
}

nlohmann::json ts_json(Timestamp t)
{
  return t == 0 ? nlohmann::json(nullptr) : nlohmann::json(format_iso8601(t));
}

Timestamp ts_from(const nlohmann::json & j)
{
  if (j.is_null()) {
    return 0;
  }
  const auto t = parse_iso8601(j.get<std::string>());
  if (!t) {
    throw std::invalid_argument("invalid timestamp '" + j.get<std::string>() + "'");
  }
  return *t;
}

std::string json_text(const nlohmann::json & v)
{
  return v.is_string() ? v.get<std::string>() : v.dump();
}

AttrValue json_attr(const nlohmann::json & v)
{
  if (v.is_boolean()) {
    return v.get<bool>();
  }
  if (v.is_number_integer()) {
    return v.get<std::int64_t>();
  }
  if (v.is_number()) {
    return v.get<double>();
  }
  return json_text(v);
}

std::string with_note(std::string text, const std::string & note)
{
  return note.empty() ? text : text + ": " + note;
}

}  // namespace

// -- enums ----------------------------------------------------------------------------------------

std::string_view to_string(RecoveryMode m)
{
  switch (m) {
    case RecoveryMode::Enabled:
      return "ENABLED";
    case RecoveryMode::DryRun:
      return "DRY_RUN";
    case RecoveryMode::Disabled:
      return "DISABLED";
  }
  return "DISABLED";
}

std::optional<RecoveryMode> parse_recovery_mode(std::string_view text)
{
  return parse_enum(
    text, std::array{RecoveryMode::Enabled, RecoveryMode::DryRun, RecoveryMode::Disabled});
}

std::string_view to_string(ExecState s)
{
  switch (s) {
    case ExecState::PendingApproval:
      return "PENDING_APPROVAL";
    case ExecState::Queued:
      return "QUEUED";
    case ExecState::Running:
      return "RUNNING";
    case ExecState::Succeeded:
      return "SUCCEEDED";
    case ExecState::Failed:
      return "FAILED";
    case ExecState::Aborted:
      return "ABORTED";
    case ExecState::Rejected:
      return "REJECTED";
    case ExecState::Expired:
      return "EXPIRED";
  }
  return "REJECTED";
}

std::optional<ExecState> parse_exec_state(std::string_view text)
{
  return parse_enum(
    text, std::array{
            ExecState::PendingApproval, ExecState::Queued, ExecState::Running, ExecState::Succeeded,
            ExecState::Failed, ExecState::Aborted, ExecState::Rejected, ExecState::Expired});
}

bool is_terminal(ExecState s)
{
  return s != ExecState::PendingApproval && s != ExecState::Queued && s != ExecState::Running;
}

std::string_view to_string(StepState s)
{
  switch (s) {
    case StepState::Pending:
      return "PENDING";
    case StepState::Running:
      return "RUNNING";
    case StepState::Retrying:
      return "RETRYING";
    case StepState::Succeeded:
      return "SUCCEEDED";
    case StepState::Failed:
      return "FAILED";
    case StepState::Skipped:
      return "SKIPPED";
    case StepState::Aborted:
      return "ABORTED";
  }
  return "PENDING";
}

std::optional<StepState> parse_step_state(std::string_view text)
{
  return parse_enum(
    text, std::array{
            StepState::Pending, StepState::Running, StepState::Retrying, StepState::Succeeded,
            StepState::Failed, StepState::Skipped, StepState::Aborted});
}

RecoveryRequest to_request(const RequestRecovery & r)
{
  RecoveryRequest req;
  req.plan_id = r.plan;
  req.args = r.args;
  req.requester = Requester::Rule;
  req.requester_id = r.rule_id;
  req.correlation_id = r.correlation_id;
  req.causation_id = r.match_id;
  req.rationale = r.rationale;
  return req;
}

// -- JSON -----------------------------------------------------------------------------------------

void to_json(nlohmann::json & j, const Execution & x)
{
  nlohmann::json gates = nlohmann::json::array();
  for (const auto & g : x.gates) {
    gates.push_back({{"gate", g.gate}, {"passed", g.passed}, {"detail", g.detail}});
  }
  nlohmann::json steps = nlohmann::json::array();
  for (const auto & s : x.steps) {
    steps.push_back(
      {{"id", s.id},
       {"action", s.action},
       {"state", to_string(s.state)},
       {"attempts", s.attempts},
       {"started", ts_json(s.started)},
       {"finished", ts_json(s.finished)},
       {"message", s.message}});
  }
  j = {
    {"execution_id", x.execution_id},
    {"plan_id", x.plan_id},
    {"plan_rev", x.plan_rev},
    {"args", x.args},
    {"requester", {{"type", to_string(x.requester)}, {"id", x.requester_id}}},
    {"correlation_id", x.correlation_id},
    {"causation_id", x.causation_id},
    {"rationale", x.rationale},
    {"confidence", x.confidence ? nlohmann::json(*x.confidence) : nlohmann::json(nullptr)},
    {"dry_run", x.dry_run},
    {"state", to_string(x.state)},
    {"reason", x.reason},
    {"gates", gates},
    {"needs_approval", x.needs_approval},
    {"approved_by", x.approved_by},
    {"locks", x.locks},
    {"steps", steps},
    {"current_step", x.current_step},
    {"failed_step", x.failed_step},
    {"created", ts_json(x.created)},
    {"started", ts_json(x.started)},
    {"finished", ts_json(x.finished)},
    {"expires", ts_json(x.expires)},
    {"depth", x.depth},
    {"parent_id", x.parent_id},
    {"child_id", x.child_id},
    {"ladder_of", x.ladder_of},
    {"ladder", {{"running", x.ladder_running}, {"next", x.ladder_next}, {"child", x.ladder_child}}},
    {"escalated", x.escalated},
    {"last_event_id", x.last_event_id},
  };
}

void from_json(const nlohmann::json & j, Execution & x)
{
  x = Execution{};
  x.execution_id = j.at("execution_id").get<std::string>();
  x.plan_id = j.at("plan_id").get<std::string>();
  x.plan_rev = j.value("plan_rev", "");
  x.args = j.value("args", std::map<std::string, std::string>{});
  const auto & req = j.at("requester");
  const auto requester = parse_requester(req.at("type").get<std::string>());
  if (!requester) {
    throw std::invalid_argument("invalid requester type");
  }
  x.requester = *requester;
  x.requester_id = req.value("id", "");
  x.correlation_id = j.value("correlation_id", "");
  x.causation_id = j.value("causation_id", "");
  x.rationale = j.value("rationale", "");
  if (j.contains("confidence") && !j["confidence"].is_null()) {
    x.confidence = j["confidence"].get<double>();
  }
  x.dry_run = j.value("dry_run", false);
  const auto state = parse_exec_state(j.at("state").get<std::string>());
  if (!state) {
    throw std::invalid_argument("invalid execution state");
  }
  x.state = *state;
  x.reason = j.value("reason", "");
  for (const auto & g : j.value("gates", nlohmann::json::array())) {
    x.gates.push_back(
      {g.at("gate").get<std::string>(), g.at("passed").get<bool>(), g.value("detail", "")});
  }
  x.needs_approval = j.value("needs_approval", false);
  x.approved_by = j.value("approved_by", "");
  x.locks = j.value("locks", std::vector<std::string>{});
  for (const auto & s : j.value("steps", nlohmann::json::array())) {
    StepRecord r;
    r.id = s.at("id").get<std::string>();
    r.action = s.at("action").get<std::string>();
    const auto ss = parse_step_state(s.at("state").get<std::string>());
    if (!ss) {
      throw std::invalid_argument("invalid step state");
    }
    r.state = *ss;
    r.attempts = s.value("attempts", 0);
    r.started = ts_from(s.value("started", nlohmann::json(nullptr)));
    r.finished = ts_from(s.value("finished", nlohmann::json(nullptr)));
    r.message = s.value("message", "");
    x.steps.push_back(std::move(r));
  }
  x.current_step = j.value("current_step", std::size_t{0});
  x.failed_step = j.value("failed_step", "");
  x.created = ts_from(j.value("created", nlohmann::json(nullptr)));
  x.started = ts_from(j.value("started", nlohmann::json(nullptr)));
  x.finished = ts_from(j.value("finished", nlohmann::json(nullptr)));
  x.expires = ts_from(j.value("expires", nlohmann::json(nullptr)));
  x.depth = j.value("depth", 1);
  x.parent_id = j.value("parent_id", "");
  x.child_id = j.value("child_id", "");
  x.ladder_of = j.value("ladder_of", "");
  if (j.contains("ladder")) {
    const auto & l = j["ladder"];
    x.ladder_running = l.value("running", false);
    x.ladder_next = l.value("next", std::size_t{0});
    x.ladder_child = l.value("child", "");
  }
  x.escalated = j.value("escalated", false);
  x.last_event_id = j.value("last_event_id", "");
}

// -- orchestrator ---------------------------------------------------------------------------------

Orchestrator::Orchestrator(OrchestratorConfig config, PlanSet plans)
: config_(std::move(config)), plans_(std::move(plans)), mode_(config_.mode), ids_(config_.id_seed)
{
  index_plans();
}

void Orchestrator::index_plans()
{
  by_id_.clear();
  for (const auto & p : plans_.plans()) {
    by_id_[p.id] = std::make_shared<const Plan>(p);
  }
}

Orchestrator::Live * Orchestrator::get(const std::string & id)
{
  const auto it = live_.find(id);
  return it == live_.end() ? nullptr : &it->second;
}

const Orchestrator::Live * Orchestrator::get(const std::string & id) const
{
  const auto it = live_.find(id);
  return it == live_.end() ? nullptr : &it->second;
}

std::vector<std::string> Orchestrator::select(const std::function<bool(const Live &)> & pred) const
{
  std::vector<const Live *> hits;
  for (const auto & [id, l] : live_) {
    if (pred(l)) {
      hits.push_back(&l);
    }
  }
  std::sort(
    hits.begin(), hits.end(), [](const Live * a, const Live * b) { return a->order < b->order; });
  std::vector<std::string> ids;
  for (const auto * l : hits) {
    ids.push_back(l->x.execution_id);
  }
  return ids;
}

const Execution * Orchestrator::find(const std::string & execution_id) const
{
  if (const Live * l = get(execution_id)) {
    return &l->x;
  }
  const auto it = std::find_if(history_.rbegin(), history_.rend(), [&](const Execution & x) {
    return x.execution_id == execution_id;
  });
  return it == history_.rend() ? nullptr : &*it;
}

std::vector<Execution> Orchestrator::live() const
{
  std::vector<Execution> out;
  for (const auto & id : select([](const Live &) { return true; })) {
    out.push_back(get(id)->x);
  }
  return out;
}

Event Orchestrator::make_event(
  Kind kind, std::string code, Severity severity, std::string message, Timestamp at) const
{
  Event e;
  e.id = ids_.uuid7(at);
  e.stamp = at;
  e.source.robot = config_.robot_id;
  e.source.node = config_.node_name;
  e.kind = kind;
  e.code = std::move(code);
  e.severity = severity;
  e.message = std::move(message);
  return e;
}

void Orchestrator::emit(
  Live & l, std::string_view phase, Severity severity, std::string message, Attributes extra,
  Timestamp at, Effects & out, std::string code)
{
  auto & x = l.x;
  Event e = make_event(
    Kind::Recovery, code.empty() ? "jarvis.recovery." + lower(phase) : std::move(code), severity,
    std::move(message), at);
  e.correlation_id = x.correlation_id;
  e.causation_id = x.last_event_id.empty() ? x.causation_id : x.last_event_id;
  e.attrs["phase"] = std::string(phase);
  e.attrs["plan"] = x.plan_id;
  e.attrs["execution_id"] = x.execution_id;
  e.attrs["requester"] = std::string(to_string(x.requester));
  if (!x.requester_id.empty()) {
    e.attrs["requester_id"] = x.requester_id;
  }
  e.attrs["dry_run"] = x.dry_run;
  if (!x.parent_id.empty()) {
    e.attrs["parent_id"] = x.parent_id;
  }
  if (!x.ladder_of.empty()) {
    e.attrs["ladder_of"] = x.ladder_of;
  }
  for (auto & [k, v] : extra) {
    e.attrs[k] = std::move(v);
  }
  x.last_event_id = e.id;
  out.emplace_back(EmitEvent{std::move(e)});
}

void Orchestrator::changed(const Live & l, Effects & out)
{
  out.emplace_back(ExecutionsChanged{{l.x.execution_id}});
  out.emplace_back(PersistState{});
}

void Orchestrator::set_gate(
  Live & l, const std::string & gate, bool passed, const std::string & detail)
{
  auto & gates = l.x.gates;
  const auto it =
    std::find_if(gates.begin(), gates.end(), [&](const GateResult & g) { return g.gate == gate; });
  if (it == gates.end()) {
    gates.push_back({gate, passed, detail});
  } else {
    it->passed = passed;
    it->detail = detail;
  }
}

std::map<std::string, std::string> Orchestrator::active_faults() const
{
  std::map<std::string, std::string> out;
  for (const auto & [id, f] : faults_) {
    if (is_active_fault_state(f.state)) {
      out.emplace(id, f.code);
    }
  }
  return out;
}

bool Orchestrator::fault_wait_satisfied(const FaultView & want) const
{
  auto same = [&want](const FaultView & f) {
    return glob_match(want.code, f.code) &&
           (want.component.empty() || glob_match(want.component, f.component));
  };
  if (want.state == "CLEARED") {
    return std::none_of(
      faults_.begin(), faults_.end(), [&](const auto & f) { return same(f.second); });
  }
  return std::any_of(faults_.begin(), faults_.end(), [&](const auto & f) {
    return same(f.second) && (want.state == "LATCHED" ? f.second.state == "LATCHED"
                                                      : is_active_fault_state(f.second.state));
  });
}

std::optional<nlohmann::json> Orchestrator::lookup(const Live & l, const std::string & var) const
{
  const auto & x = l.x;
  if (var.starts_with("params.")) {
    const auto name = var.substr(7);
    const auto it = x.args.find(name);
    if (it == x.args.end()) {
      return std::nullopt;
    }
    if (l.plan) {
      if (const auto spec = l.plan->params.find(name); spec != l.plan->params.end()) {
        return param_json(spec->second, it->second);
      }
    }
    return nlohmann::json(it->second);
  }
  if (var == "plan.id") {
    return nlohmann::json(x.plan_id);
  }
  if (var == "execution.id") {
    return nlohmann::json(x.execution_id);
  }
  if (var == "correlation_id") {
    return nlohmann::json(x.correlation_id);
  }
  if (var == "robot") {
    return nlohmann::json(config_.robot_id);
  }
  return std::nullopt;
}

Orchestrator::Live & Orchestrator::ladder_root(Live & l)
{
  Live * cur = &l;
  while (!cur->x.ladder_of.empty()) {
    Live * owner = get(cur->x.ladder_of);
    if (!owner) {
      break;
    }
    cur = owner;
  }
  return *cur;
}

std::string Orchestrator::rate_key(const Execution & x) const
{
  std::string key = (x.dry_run ? "dry:" : "") + x.plan_id + "?";
  for (const auto & [k, v] : x.args) {
    key += k + "=" + v + "&";
  }
  return key;
}

bool Orchestrator::rate_exceeded(const Live & l, Timestamp at)
{
  const auto & limit = l.plan->max_attempts;
  if (!limit) {
    return false;
  }
  const auto it = attempts_.find(rate_key(l.x));
  if (it == attempts_.end()) {
    return false;
  }
  auto & starts = it->second;
  while (!starts.empty() && at - starts.front() >= limit->per) {
    starts.pop_front();
  }
  if (starts.empty()) {
    attempts_.erase(it);
    return false;
  }
  return starts.size() >= static_cast<std::size_t>(limit->count);
}

std::optional<std::string> Orchestrator::lock_conflict(const Live & l) const
{
  std::set<std::string> ancestors;
  for (const Live * p = get(l.x.parent_id); p; p = get(p->x.parent_id)) {
    ancestors.insert(p->x.execution_id);
  }
  std::size_t running_roots = 0;
  for (const auto & id :
       select([](const Live & o) { return o.x.state == ExecState::Running && !o.ended; })) {
    const Live & o = *get(id);
    if (&o == &l) {
      continue;
    }
    if (o.x.parent_id.empty()) {
      ++running_roots;
    }
    if (ancestors.count(id)) {
      continue;
    }
    for (const auto & lock : l.x.locks) {
      if (std::find(o.x.locks.begin(), o.x.locks.end(), lock) != o.x.locks.end()) {
        return "lock '" + lock + "' is held by " + o.x.plan_id + " (" + id + ")";
      }
    }
  }
  if (l.x.parent_id.empty() && running_roots >= config_.max_concurrent) {
    return "max_concurrent reached (" + std::to_string(config_.max_concurrent) + " running)";
  }
  return std::nullopt;
}

// -- admission ------------------------------------------------------------------------------------

Orchestrator::Live & Orchestrator::create(const RecoveryRequest & r, Timestamp at)
{
  Live l;
  auto & x = l.x;
  x.execution_id = ids_.uuid7(at);
  x.plan_id = r.plan_id;
  x.args = r.args;
  x.requester = r.requester;
  x.requester_id = r.requester_id;
  x.correlation_id = r.correlation_id;
  x.causation_id = r.causation_id;
  x.rationale = r.rationale;
  x.confidence = r.confidence;
  x.dry_run = r.dry_run;
  x.created = at;
  l.order = serial_++;
  const auto id = x.execution_id;
  return live_.emplace(id, std::move(l)).first->second;
}

void Orchestrator::reject(
  Live & l, const std::string & gate, const std::string & why, Timestamp at, Effects & out)
{
  set_gate(l, gate, false, why);
  finish(l, ExecState::Rejected, why, at, out);
}

void Orchestrator::admit(Live & l, Timestamp at, Effects & out)
{
  auto & x = l.x;
  // 1. mode
  if (mode_ == RecoveryMode::Disabled) {
    return reject(l, "mode", "recovery is DISABLED", at, out);
  }
  x.dry_run = x.dry_run || mode_ == RecoveryMode::DryRun;
  set_gate(
    l, "mode", true,
    !x.dry_run                      ? "ENABLED"
    : mode_ == RecoveryMode::DryRun ? "DRY_RUN: steps are simulated"
                                    : "dry run requested: steps are simulated");
  // 2. plan known, params valid
  const auto it = by_id_.find(x.plan_id);
  if (it == by_id_.end()) {
    return reject(l, "plan", "unknown plan '" + x.plan_id + "'", at, out);
  }
  l.plan = it->second;
  const Plan & plan = *l.plan;
  x.plan_rev = plan.rev;
  for (const auto & s : plan.steps) {
    x.steps.push_back({s.id, s.action, StepState::Pending, 0, 0, 0, ""});
  }
  std::map<std::string, std::string> bound;
  if (const auto err = bind_params(plan, x.args, bound)) {
    return reject(l, "plan", *err, at, out);
  }
  x.args = std::move(bound);
  if (x.depth > config_.max_depth) {
    return reject(
      l, "plan", "nested deeper than max_depth (" + std::to_string(config_.max_depth) + ")", at,
      out);
  }
  for (const auto & lock : plan.locks) {
    const auto name = lock.render([&](const std::string & var) -> std::optional<std::string> {
      const auto v = lookup(l, var);
      return v ? std::optional<std::string>(json_text(*v)) : std::nullopt;
    });
    if (!name) {
      return reject(l, "plan", "cannot render lock '" + lock.text() + "'", at, out);
    }
    x.locks.push_back(*name);
  }
  set_gate(l, "plan", true, "rev " + plan.rev);
  // 3. requester
  if (plan.allowed_requesters.count(x.requester) == 0) {
    return reject(
      l, "requester", "plan does not accept " + std::string(to_string(x.requester)) + " requests",
      at, out);
  }
  std::string detail = std::string(to_string(x.requester)) + " allowed";
  if (x.requester == Requester::Agent) {
    const AgentPolicy effective = std::min(config_.agent_autonomy, plan.agent_policy);
    switch (effective) {
      case AgentPolicy::Never:
        return reject(l, "requester", "agent autonomy is 'never' for this plan", at, out);
      case AgentPolicy::Advise:
        return reject(
          l, "requester",
          "agent autonomy is 'advise': shown to operators as a recommendation, not run", at, out);
      case AgentPolicy::Supervised:
        x.needs_approval = true;
        detail = "agent, supervised: needs approval";
        break;
      case AgentPolicy::Autonomous:
        if (!x.confidence || *x.confidence < config_.min_confidence) {
          x.needs_approval = true;
          detail = "agent confidence " +
                   (x.confidence ? nlohmann::json(*x.confidence).dump() : std::string("unknown")) +
                   " < " + nlohmann::json(config_.min_confidence).dump() + ": needs approval";
        } else {
          detail = "agent, autonomous";
        }
        break;
    }
  }
  set_gate(l, "requester", true, detail);
  if (plan.requires_approval.count(x.requester)) {
    x.needs_approval = true;
  }
  proceed(l, at, out);
}

void Orchestrator::proceed(Live & l, Timestamp at, Effects & out)
{
  auto & x = l.x;
  const Plan & plan = *l.plan;
  const bool waiting = x.state == ExecState::PendingApproval || x.state == ExecState::Queued;
  if (waiting && mode_ == RecoveryMode::Disabled) {
    return reject(l, "mode", "recovery is DISABLED", at, out);
  }
  // 4. preconditions
  if (!plan.preconditions.passes(active_faults(), states_)) {
    return reject(l, "preconditions", "preconditions not met", at, out);
  }
  set_gate(l, "preconditions", true, plan.preconditions.empty() ? "none" : "met");
  // 5. rate limit
  if (rate_exceeded(l, at)) {
    return reject(
      l, "rate_limit",
      "max_attempts reached (" + std::to_string(plan.max_attempts->count) + " per " +
        format_duration(plan.max_attempts->per) + ")",
      at, out);
  }
  set_gate(l, "rate_limit", true, plan.max_attempts ? "within max_attempts" : "none");
  // 6. locks and slots
  if (const auto conflict = lock_conflict(l)) {
    if (!plan.queue_on_conflict) {
      return reject(l, "locks", *conflict, at, out);
    }
    set_gate(l, "locks", false, *conflict + "; queued");
    if (x.state != ExecState::Queued) {
      x.state = ExecState::Queued;
      x.expires = at + plan.queue_ttl;
      x.reason = *conflict;
      emit(l, "QUEUED", Severity::Info, "queued: " + *conflict, {}, at, out);
      changed(l, out);
    }
    return;
  }
  std::string held;
  for (const auto & lock : x.locks) {
    held += (held.empty() ? "" : ", ") + lock;
  }
  set_gate(l, "locks", true, held.empty() ? "none" : "acquired " + held);
  // 7. approval
  if (x.needs_approval && x.approved_by.empty()) {
    if (!x.dry_run) {
      set_gate(l, "approval", false, "waiting for an operator");
      x.state = ExecState::PendingApproval;
      x.expires = at + config_.approval_ttl;
      x.reason = "waiting for approval";
      emit(
        l, "PENDING_APPROVAL", Severity::Warn,
        with_note("approval needed to run " + x.plan_id, x.rationale),
        {{"expires", format_iso8601(x.expires)}}, at, out);
      changed(l, out);
      return;
    }
    set_gate(l, "approval", true, "would need approval (dry run)");
  } else {
    set_gate(
      l, "approval", true, x.approved_by.empty() ? "not required" : "approved by " + x.approved_by);
  }
  start(l, at, out);
}

// -- execution ------------------------------------------------------------------------------------

void Orchestrator::start(Live & l, Timestamp at, Effects & out)
{
  auto & x = l.x;
  x.state = ExecState::Running;
  x.started = at;
  x.expires = 0;
  x.reason.clear();
  if (l.plan->max_attempts) {
    attempts_[rate_key(x)].push_back(at);
  }
  l.step_mark = event_count_;
  emit(
    l, "STARTED", Severity::Info,
    with_note("started " + x.plan_id + (x.dry_run ? " (dry run)" : ""), x.rationale), {}, at, out);
  changed(l, out);
  begin_step(l, 0, at, out);
}

void Orchestrator::begin_step(Live & l, std::size_t index, Timestamp at, Effects & out)
{
  auto & x = l.x;
  const Plan & plan = *l.plan;
  for (; index < plan.steps.size(); ++index) {
    x.current_step = index;
    const PlanStep & s = plan.steps[index];
    StepRecord & rec = x.steps[index];
    l.prev_mark = l.step_mark;
    l.step_mark = event_count_;
    if (s.when && !s.when->passes(active_faults(), states_)) {
      rec.state = StepState::Skipped;
      rec.started = rec.finished = at;
      rec.message = "condition false";
      emit(
        l, "STEP_SKIPPED", Severity::Info, "step " + s.id + " skipped: condition false",
        {{"step", s.id}}, at, out);
      changed(l, out);
      continue;
    }
    rec.started = at;
    begin_attempt(l, at, out);
    return;
  }
  finish(l, ExecState::Succeeded, "all steps succeeded", at, out);
}

void Orchestrator::begin_attempt(Live & l, Timestamp at, Effects & out)
{
  auto & x = l.x;
  const PlanStep & s = l.plan->steps[x.current_step];
  StepRecord & rec = x.steps[x.current_step];
  ++rec.attempts;
  rec.state = StepState::Running;
  rec.message.clear();
  changed(l, out);

  nlohmann::json args = s.with;
  if (const auto bad = render_tree(args, [&](const std::string & var) { return lookup(l, var); })) {
    return step_failed(l, "cannot render '" + *bad + "'", at, out);
  }
  if (x.dry_run && s.action != "run") {
    return step_succeeded(l, "simulated (dry run)", at, out);
  }
  const std::string where = "steps." + s.id + ".with";
  if (s.action == "wait_for") {
    if (args.contains("event")) {
      try {
        l.matcher = matcher_from_json(args["event"], where + ".event");
      } catch (const PlanError & e) {
        return step_failed(l, e.what(), at, out);
      }
      // Whatever happened since the previous step started counts: the effect of an action is
      // often visible before the actuator reports back.
      for (const auto & [n, e] : recent_) {
        if (n > l.prev_mark && l.matcher->matches(e)) {
          l.matcher.reset();
          return step_succeeded(l, "matched " + e.code + " (" + e.id + ")", at, out);
        }
      }
      l.wait = Wait::Event;
    } else {
      const auto & f = args["fault"];
      l.fault = FaultView{
        json_text(f.at("code")), f.contains("component") ? json_text(f["component"]) : "",
        json_text(args.at("state"))};
      if (fault_wait_satisfied(l.fault)) {
        return step_succeeded(l, "fault " + l.fault.code + " is " + l.fault.state, at, out);
      }
      l.wait = Wait::Fault;
    }
    l.deadline = at + s.timeout;
  } else if (s.action == "delay") {
    const auto & d = args.at("duration");
    const auto duration = d.is_number() ? std::optional<Duration>(static_cast<Duration>(
                                            d.get<double>() * static_cast<double>(kSecond)))
                                        : parse_duration(json_text(d));
    if (!duration || *duration < 0) {
      return step_failed(l, "invalid duration '" + json_text(d) + "'", at, out);
    }
    l.wait = Wait::Delay;
    l.deadline = at + *duration;
  } else if (s.action == "check") {
    Guard guard;
    try {
      guard = guard_from_json(args, where);
    } catch (const PlanError & e) {
      return step_failed(l, e.what(), at, out);
    }
    if (guard.passes(active_faults(), states_)) {
      return step_succeeded(l, "condition holds", at, out);
    }
    return attempt_failed(l, "condition does not hold", at, out);
  } else if (s.action == "emit") {
    const auto code = json_text(args.at("code"));
    if (!is_valid_code(code)) {
      return step_failed(l, "'" + code + "' is not a dotted lowercase code", at, out);
    }
    const auto kind = parse_kind(args.contains("kind") ? json_text(args["kind"]) : "LOG");
    const auto severity =
      parse_severity(args.contains("severity") ? json_text(args["severity"]) : "INFO");
    if (
      !kind || (*kind != Kind::Log && *kind != Kind::State && *kind != Kind::FaultRaise &&
                *kind != Kind::FaultClear && *kind != Kind::System)) {
      return step_failed(
        l, "cannot emit kind '" + json_text(args.value("kind", "")) + "'", at, out);
    }
    if (!severity) {
      return step_failed(
        l, "invalid severity '" + json_text(args.value("severity", "")) + "'", at, out);
    }
    Event e = make_event(
      *kind, code, *severity, args.contains("message") ? json_text(args["message"]) : code, at);
    e.correlation_id = x.correlation_id;
    e.causation_id = x.last_event_id;
    if (args.contains("attrs")) {
      for (const auto & [k, v] : args["attrs"].items()) {
        e.attrs[k] = json_attr(v);
      }
    }
    e.attrs["execution_id"] = x.execution_id;
    e.attrs["plan"] = x.plan_id;
    out.emplace_back(EmitEvent{std::move(e)});
    return step_succeeded(l, "emitted " + code, at, out);
  } else if (s.action == "run") {
    RecoveryRequest r;
    r.plan_id = json_text(args.at("plan"));
    if (args.contains("args")) {
      for (const auto & [k, v] : args["args"].items()) {
        r.args[k] = json_text(v);
      }
    }
    r.requester = Requester::Plan;
    r.requester_id = x.execution_id;
    r.correlation_id = x.correlation_id;
    r.causation_id = x.last_event_id;
    r.rationale = "step " + s.id + " of " + x.plan_id;
    r.dry_run = x.dry_run;
    Live & child = create(r, at);
    child.x.parent_id = x.execution_id;
    child.x.depth = x.depth + 1;
    x.child_id = child.x.execution_id;
    l.wait = Wait::Child;
    // May finish synchronously and resume this execution through settle().
    admit(child, at, out);
  } else {
    l.wait = Wait::Action;
    l.deadline = at + s.timeout;
    out.emplace_back(StartAction{x.execution_id, s.id, rec.attempts, s.action, args, s.timeout});
  }
}

void Orchestrator::step_succeeded(
  Live & l, const std::string & message, Timestamp at, Effects & out)
{
  auto & x = l.x;
  l.wait = Wait::None;
  l.matcher.reset();
  x.child_id.clear();
  StepRecord & rec = x.steps[x.current_step];
  rec.state = StepState::Succeeded;
  rec.finished = at;
  rec.message = message;
  emit(
    l, "STEP_SUCCEEDED", Severity::Info, "step " + rec.id + ": " + message,
    {{"step", rec.id}, {"attempt", static_cast<std::int64_t>(rec.attempts)}}, at, out);
  changed(l, out);
  begin_step(l, x.current_step + 1, at, out);
}

void Orchestrator::attempt_failed(
  Live & l, const std::string & message, Timestamp at, Effects & out)
{
  auto & x = l.x;
  l.wait = Wait::None;
  l.matcher.reset();
  x.child_id.clear();
  const PlanStep & s = l.plan->steps[x.current_step];
  StepRecord & rec = x.steps[x.current_step];
  if (rec.attempts > s.retry_attempts) {
    return step_failed(l, message, at, out);
  }
  const auto backoff = static_cast<Duration>(
    static_cast<double>(s.retry_backoff) * std::pow(s.retry_multiplier, rec.attempts - 1));
  rec.state = StepState::Retrying;
  rec.message = message;
  emit(
    l, "STEP_RETRYING", Severity::Warn,
    "step " + s.id + " attempt " + std::to_string(rec.attempts) + " failed: " + message +
      "; retrying in " + format_duration(backoff),
    {{"step", s.id}, {"attempt", static_cast<std::int64_t>(rec.attempts)}}, at, out);
  changed(l, out);
  if (backoff <= 0) {
    return begin_attempt(l, at, out);
  }
  l.wait = Wait::Retry;
  l.deadline = at + backoff;
}

void Orchestrator::step_failed(Live & l, const std::string & message, Timestamp at, Effects & out)
{
  auto & x = l.x;
  l.wait = Wait::None;
  l.matcher.reset();
  x.child_id.clear();
  const PlanStep & s = l.plan->steps[x.current_step];
  StepRecord & rec = x.steps[x.current_step];
  rec.state = StepState::Failed;
  rec.finished = at;
  rec.message = message;
  emit(
    l, "STEP_FAILED", Severity::Warn, "step " + s.id + " failed: " + message,
    {{"step", s.id}, {"attempt", static_cast<std::int64_t>(rec.attempts)}}, at, out);
  changed(l, out);
  if (s.continue_on_failure) {
    return begin_step(l, x.current_step + 1, at, out);
  }
  x.failed_step = s.id;
  finish(l, ExecState::Failed, "step " + s.id + " failed: " + message, at, out);
}

void Orchestrator::finish(
  Live & l, ExecState state, const std::string & reason, Timestamp at, Effects & out)
{
  if (l.ended) {
    return;
  }
  l.ended = true;
  auto & x = l.x;
  const Wait was = l.wait;
  const std::string child = x.child_id;
  if (was == Wait::Action) {
    const auto & rec = x.steps[x.current_step];
    out.emplace_back(CancelAction{x.execution_id, rec.id, rec.attempts});
  }
  l.wait = Wait::None;
  l.matcher.reset();
  if (x.state == ExecState::Running && x.current_step < x.steps.size()) {
    auto & rec = x.steps[x.current_step];
    if (rec.state == StepState::Running || rec.state == StepState::Retrying) {
      rec.state = state == ExecState::Failed ? StepState::Failed : StepState::Aborted;
      rec.finished = at;
      if (rec.message.empty()) {
        rec.message = reason;
      }
    }
  }
  x.state = state;
  x.reason = reason;
  x.finished = at;
  x.expires = 0;
  const Severity severity = state == ExecState::Succeeded ? Severity::Info
                            : state == ExecState::Failed  ? Severity::Error
                                                          : Severity::Warn;
  Attributes extra{{"reason", reason}};
  if (!x.failed_step.empty()) {
    extra["failed_step"] = x.failed_step;
  }
  emit(
    l, to_string(state), severity, x.plan_id + " " + lower(to_string(state)) + ": " + reason,
    std::move(extra), at, out);
  changed(l, out);
  if (was == Wait::Child && !child.empty()) {
    if (Live * c = get(child); c && !c->ended) {
      finish(*c, ExecState::Aborted, "parent " + lower(to_string(state)), at, out);
    }
  }
  if (state == ExecState::Failed && x.parent_id.empty() && !l.plan->on_failure.empty()) {
    x.ladder_running = true;
    x.ladder_next = 0;
    ladder_advance(l, at, out);
  }
  if (!x.ladder_running) {
    settle(l, at, out);
  }
  pump_queue(at, out);
}

void Orchestrator::settle(Live & l, Timestamp at, Effects & out)
{
  const auto & x = l.x;
  if (!x.parent_id.empty()) {
    Live * p = get(x.parent_id);
    if (p && !p->ended && p->wait == Wait::Child && p->x.child_id == x.execution_id) {
      if (x.state == ExecState::Succeeded) {
        step_succeeded(*p, "plan " + x.plan_id + " succeeded", at, out);
      } else {
        attempt_failed(
          *p, "plan " + x.plan_id + " " + lower(to_string(x.state)) + ": " + x.reason, at, out);
      }
    }
  }
  if (!x.ladder_of.empty()) {
    Live * owner = get(x.ladder_of);
    if (owner && owner->x.ladder_running && owner->x.ladder_child == x.execution_id) {
      owner->x.ladder_child.clear();
      if (x.state == ExecState::Succeeded) {
        ladder_done(*owner, "recovered by " + x.plan_id, at, out);
      } else if (x.state == ExecState::Aborted) {
        ladder_done(*owner, "stopped: " + x.plan_id + " was aborted", at, out);
      } else {
        ladder_advance(*owner, at, out);
      }
    }
  }
}

// -- escalation ladder ----------------------------------------------------------------------------

void Orchestrator::ladder_advance(Live & owner, Timestamp at, Effects & out)
{
  auto & x = owner.x;
  const Plan & plan = *owner.plan;
  while (owner.stop_reason.empty() && x.ladder_next < plan.on_failure.size()) {
    const FailureStep & f = plan.on_failure[x.ladder_next++];
    changed(owner, out);
    if (f.type == FailureStep::Type::Escalate) {
      escalate(owner, f.to, at, out);
      continue;
    }
    RecoveryRequest r;
    r.plan_id = f.plan;
    for (const auto & [name, t] : f.args) {
      // An optional parameter the failed plan did not get is simply not passed on.
      if (const auto v = t.render([&](const std::string & var) -> std::optional<std::string> {
            const auto j = lookup(owner, var);
            return j ? std::optional<std::string>(json_text(*j)) : std::nullopt;
          })) {
        r.args[name] = *v;
      }
    }
    r.requester = Requester::Plan;
    r.requester_id = x.execution_id;
    r.correlation_id = x.correlation_id;
    r.causation_id = x.last_event_id;
    r.rationale = "on_failure of " + x.plan_id + " (" + x.reason + ")";
    r.dry_run = x.dry_run;
    Live & entry = create(r, at);
    entry.x.ladder_of = x.execution_id;
    x.ladder_child = entry.x.execution_id;
    // May settle synchronously and continue this ladder through settle().
    admit(entry, at, out);
    return;
  }
  ladder_done(owner, owner.stop_reason.empty() ? "ladder exhausted" : owner.stop_reason, at, out);
}

void Orchestrator::ladder_done(
  Live & owner, const std::string & outcome, Timestamp at, Effects & out)
{
  if (!owner.x.ladder_running) {
    return;
  }
  owner.x.ladder_running = false;
  owner.x.ladder_child.clear();
  emit(
    owner, "LADDER_DONE", Severity::Info,
    "escalation ladder of " + owner.x.plan_id + ": " + outcome, {{"outcome", outcome}}, at, out);
  changed(owner, out);
  settle(owner, at, out);
}

void Orchestrator::escalate(Live & l, const std::string & to, Timestamp at, Effects & out)
{
  Live & root = ladder_root(l);
  if (root.x.escalated) {
    return;  // once per incident chain: the human already knows
  }
  root.x.escalated = true;
  emit(
    l, "ESCALATED", Severity::Error,
    "escalated to " + to + ": " + root.x.plan_id + " did not recover (" + root.x.reason + ")",
    {{"to", to}}, at, out);
  changed(root, out);
}

void Orchestrator::stop(Live & l, const std::string & reason, Timestamp at, Effects & out)
{
  if (!l.ended) {
    l.stop_reason = reason;
    finish(l, ExecState::Aborted, reason, at, out);
    return;
  }
  if (!l.x.ladder_running) {
    return;
  }
  l.stop_reason = reason;
  if (Live * entry = get(l.x.ladder_child)) {
    stop(*entry, reason, at, out);  // settles and comes back through ladder_advance()
  }
  ladder_done(l, reason, at, out);
}

void Orchestrator::pump_queue(Timestamp at, Effects & out)
{
  for (const auto & id :
       select([](const Live & l) { return !l.ended && l.x.state == ExecState::Queued; })) {
    Live * q = get(id);
    if (q && !q->ended && q->x.state == ExecState::Queued) {
      proceed(*q, at, out);
    }
  }
}

// -- inputs ---------------------------------------------------------------------------------------

Submitted Orchestrator::submit(const RecoveryRequest & request, Timestamp now)
{
  Submitted s;
  s.effects = advance_to(now);
  Live & l = create(request, now);
  admit(l, now, s.effects);
  s.execution_id = l.x.execution_id;
  s.state = l.x.state;
  s.reason = l.x.reason;
  collect();
  merge_effects(s.effects, {});
  return s;
}

void Orchestrator::check_waits(const Event * e, Timestamp at, Effects & out)
{
  for (const auto & id : select([](const Live & l) {
         return !l.ended && (l.wait == Wait::Event || l.wait == Wait::Fault);
       })) {
    Live * l = get(id);
    if (!l || l->ended) {
      continue;
    }
    if (l->wait == Wait::Event && e && l->matcher && l->matcher->matches(*e)) {
      step_succeeded(*l, "matched " + e->code + " (" + e->id + ")", at, out);
    } else if (l->wait == Wait::Fault && fault_wait_satisfied(l->fault)) {
      step_succeeded(*l, "fault " + l->fault.code + " is " + l->fault.state, at, out);
    }
  }
}

Effects Orchestrator::on_event(const Event & e, Timestamp now)
{
  Effects out = advance_to(now);
  recent_.emplace_back(++event_count_, e);
  while (recent_.size() > config_.lookback_events) {
    recent_.pop_front();
  }
  if (e.kind == Kind::FaultState) {
    const std::string * fault_id = attr_string(e.attrs, "fault_id");
    const std::string * state = attr_string(e.attrs, "state");
    if (fault_id && state) {
      if (*state == "CLEARED") {
        faults_.erase(*fault_id);
      } else {
        faults_[*fault_id] = FaultView{e.code, e.source.component, *state};
      }
    }
  } else if (e.kind == Kind::State) {
    states_[e.code] = state_value(e);
  }
  check_waits(&e, now, out);
  collect();
  merge_effects(out, {});
  return out;
}

Effects Orchestrator::sync_faults(std::map<std::string, FaultView> faults, Timestamp now)
{
  Effects out = advance_to(now);
  std::erase_if(faults, [](const auto & f) { return f.second.state == "CLEARED"; });
  faults_ = std::move(faults);
  check_waits(nullptr, now, out);
  collect();
  merge_effects(out, {});
  return out;
}

Effects Orchestrator::on_action_result(
  const std::string & execution_id, const std::string & step_id, int attempt, bool success,
  const std::string & message, Timestamp now)
{
  Effects out = advance_to(now);
  Live * l = get(execution_id);
  if (l && !l->ended && l->wait == Wait::Action) {
    const auto & rec = l->x.steps[l->x.current_step];
    if (rec.id == step_id && rec.attempts == attempt) {
      if (success) {
        step_succeeded(*l, message.empty() ? "ok" : message, now, out);
      } else {
        attempt_failed(*l, message.empty() ? "failed" : message, now, out);
      }
    }
  }
  collect();
  merge_effects(out, {});
  return out;
}

std::optional<Timestamp> Orchestrator::timer_of(const Live & l) const
{
  if (l.ended) {
    return std::nullopt;
  }
  switch (l.x.state) {
    case ExecState::PendingApproval:
    case ExecState::Queued:
      return l.x.expires;
    case ExecState::Running: {
      Timestamp t = l.x.started + l.plan->timeout;
      if (l.wait != Wait::None && l.wait != Wait::Child) {
        t = std::min(t, l.deadline);
      }
      return t;
    }
    default:
      return std::nullopt;
  }
}

void Orchestrator::fire_timer(Live & l, Timestamp at, Effects & out)
{
  auto & x = l.x;
  if (x.state == ExecState::PendingApproval) {
    set_gate(l, "approval", false, "no decision in time");
    return finish(
      l, ExecState::Expired, "no decision within " + format_duration(config_.approval_ttl), at,
      out);
  }
  if (x.state == ExecState::Queued) {
    return finish(
      l, ExecState::Expired, "still waiting for locks after " + format_duration(l.plan->queue_ttl),
      at, out);
  }
  const PlanStep & s = l.plan->steps[x.current_step];
  if (x.started + l.plan->timeout <= at) {
    x.failed_step = s.id;
    return finish(
      l, ExecState::Failed, "plan timed out after " + format_duration(l.plan->timeout), at, out);
  }
  switch (l.wait) {
    case Wait::Action: {
      const auto & rec = x.steps[x.current_step];
      out.emplace_back(CancelAction{x.execution_id, rec.id, rec.attempts});
      l.wait = Wait::None;
      return attempt_failed(l, "timed out after " + format_duration(s.timeout), at, out);
    }
    case Wait::Event:
      return attempt_failed(l, "no match within " + format_duration(s.timeout), at, out);
    case Wait::Fault:
      return attempt_failed(
        l,
        "fault " + l.fault.code + " not " + l.fault.state + " within " + format_duration(s.timeout),
        at, out);
    case Wait::Delay:
      return step_succeeded(l, "waited", at, out);
    case Wait::Retry:
      l.wait = Wait::None;
      return begin_attempt(l, at, out);
    default:
      return;
  }
}

Effects Orchestrator::advance_to(Timestamp now)
{
  Effects out;
  for (;;) {
    Live * due = nullptr;
    Timestamp when = 0;
    for (auto & [id, l] : live_) {
      const auto t = timer_of(l);
      if (t && *t <= now && (!due || *t < when || (*t == when && l.order < due->order))) {
        due = &l;
        when = *t;
      }
    }
    if (!due) {
      break;
    }
    fire_timer(*due, when, out);
  }
  collect();
  merge_effects(out, {});
  return out;
}

std::optional<Timestamp> Orchestrator::next_deadline() const
{
  std::optional<Timestamp> best;
  for (const auto & [id, l] : live_) {
    if (const auto t = timer_of(l); t && (!best || *t < *best)) {
      best = t;
    }
  }
  return best;
}

// -- operator commands ----------------------------------------------------------------------------

CommandResult Orchestrator::decide(
  const std::string & execution_id, bool approve, const std::string & operator_name,
  const std::string & note, Timestamp now)
{
  CommandResult r;
  r.effects = advance_to(now);
  Live * l = get(execution_id);
  const std::string who = operator_name.empty() ? "operator" : operator_name;
  if (!l) {
    r.message = "unknown or finished execution '" + execution_id + "'";
  } else if (l->ended || l->x.state != ExecState::PendingApproval) {
    r.message = "execution is " + std::string(to_string(l->x.state)) + ", not waiting for approval";
  } else if (approve) {
    l->x.approved_by = who;
    emit(
      *l, "APPROVED", Severity::Info, with_note("approved by " + who, note), {{"approved_by", who}},
      now, r.effects);
    changed(*l, r.effects);
    proceed(*l, now, r.effects);
    r.success = true;
    r.message = "approved";
  } else {
    set_gate(*l, "approval", false, "rejected by " + who);
    finish(*l, ExecState::Rejected, with_note("rejected by " + who, note), now, r.effects);
    r.success = true;
    r.message = "rejected";
  }
  collect();
  merge_effects(r.effects, {});
  return r;
}

CommandResult Orchestrator::cancel(
  const std::string & execution_id, const std::string & operator_name, const std::string & reason,
  Timestamp now)
{
  CommandResult r;
  r.effects = advance_to(now);
  Live * l = get(execution_id);
  const std::string why = with_note(
    "cancelled by " + (operator_name.empty() ? std::string("operator") : operator_name), reason);
  if (!l || (l->ended && !l->x.ladder_running)) {
    r.message = "unknown or finished execution '" + execution_id + "'";
  } else {
    r.message = l->ended ? "escalation ladder stopped" : "cancelled";
    stop(*l, why, now, r.effects);
    r.success = true;
  }
  collect();
  merge_effects(r.effects, {});
  return r;
}

CommandResult Orchestrator::set_mode(
  RecoveryMode mode, const std::string & operator_name, const std::string & reason,
  bool cancel_running, Timestamp now)
{
  CommandResult r;
  r.effects = advance_to(now);
  const RecoveryMode previous = mode_;
  mode_ = mode;
  const std::string who = operator_name.empty() ? "operator" : operator_name;
  Event e = make_event(
    Kind::Operator, "jarvis.recovery.mode",
    mode == RecoveryMode::Disabled ? Severity::Warn : Severity::Info,
    with_note(
      "recovery mode " + std::string(to_string(previous)) + " -> " + std::string(to_string(mode)) +
        " by " + who,
      reason),
    now);
  e.attrs["mode"] = std::string(to_string(mode));
  e.attrs["previous_mode"] = std::string(to_string(previous));
  e.attrs["operator"] = who;
  e.attrs["cancel_running"] = cancel_running;
  r.effects.emplace_back(EmitEvent{std::move(e)});
  r.effects.emplace_back(ExecutionsChanged{});  // the snapshot carries the mode
  r.effects.emplace_back(PersistState{});
  if (mode == RecoveryMode::Disabled) {
    const std::string why = with_note("recovery disabled by " + who, reason);
    // Ladders stop climbing first, then nothing waiting may start, then what runs is aborted.
    if (cancel_running) {
      for (const auto & id : select([](const Live & l) { return l.x.ladder_running; })) {
        get(id)->stop_reason = why;
      }
    }
    for (const auto & id : select([](const Live & l) {
           return !l.ended &&
                  (l.x.state == ExecState::PendingApproval || l.x.state == ExecState::Queued);
         })) {
      if (Live * l = get(id); l && !l->ended) {
        reject(*l, "mode", why, now, r.effects);
      }
    }
    if (cancel_running) {
      for (const auto & id :
           select([](const Live & l) { return l.x.parent_id.empty() && l.x.ladder_of.empty(); })) {
        if (Live * l = get(id); l && (!l->ended || l->x.ladder_running)) {
          stop(*l, why, now, r.effects);
        }
      }
    }
  }
  r.success = true;
  r.message = std::string(to_string(previous)) + " -> " + std::string(to_string(mode));
  collect();
  merge_effects(r.effects, {});
  return r;
}

Effects Orchestrator::reload(PlanSet plans, Timestamp now)
{
  Effects out = advance_to(now);
  plans_ = std::move(plans);
  index_plans();
  for (const auto & id : select([](const Live & l) {
         return !l.ended &&
                (l.x.state == ExecState::PendingApproval || l.x.state == ExecState::Queued);
       })) {
    Live * l = get(id);
    if (!l || l->ended) {
      continue;
    }
    const auto it = by_id_.find(l->x.plan_id);
    if (it == by_id_.end() || it->second->rev != l->x.plan_rev) {
      reject(*l, "plan", "plan changed while waiting", now, out);
    } else {
      l->plan = it->second;
    }
  }
  Event e = make_event(
    Kind::System, "jarvis.recovery.reloaded", Severity::Info,
    "loaded " + std::to_string(plans_.plans().size()) + " plans", now);
  e.attrs["plans"] = static_cast<std::int64_t>(plans_.plans().size());
  out.emplace_back(EmitEvent{std::move(e)});
  collect();
  merge_effects(out, {});
  return out;
}

void Orchestrator::collect()
{
  std::vector<Live *> done;
  for (auto & [id, l] : live_) {
    if (l.ended && !l.x.ladder_running) {
      done.push_back(&l);
    }
  }
  std::sort(done.begin(), done.end(), [](const Live * a, const Live * b) {
    return a->x.finished != b->x.finished ? a->x.finished < b->x.finished : a->order < b->order;
  });
  for (Live * l : done) {
    const std::string id = l->x.execution_id;
    history_.push_back(std::move(l->x));
    live_.erase(id);
  }
  while (history_.size() > config_.history_capacity) {
    history_.pop_front();
  }
}

// -- persistence ----------------------------------------------------------------------------------

nlohmann::json Orchestrator::snapshot() const
{
  nlohmann::json attempts = nlohmann::json::object();
  for (const auto & [key, starts] : attempts_) {
    nlohmann::json list = nlohmann::json::array();
    for (const auto t : starts) {
      list.push_back(format_iso8601(t));
    }
    attempts[key] = list;
  }
  nlohmann::json executions = nlohmann::json::array();
  for (const auto & x : live()) {
    executions.push_back(x);
  }
  return {
    {"schema", kSchema},
    {"mode", to_string(mode_)},
    {"attempts", attempts},
    {"executions", executions},
  };
}

Effects Orchestrator::restore(const nlohmann::json & state, Timestamp now)
{
  if (state.value("schema", "") != kSchema) {
    throw std::invalid_argument("unsupported recovery state schema");
  }
  Effects out;
  if (const auto m = parse_recovery_mode(state.value("mode", "ENABLED"))) {
    mode_ = *m;
  }
  attempts_.clear();
  const auto attempts = state.value("attempts", nlohmann::json::object());
  for (const auto & [key, list] : attempts.items()) {
    for (const auto & t : list) {
      attempts_[key].push_back(ts_from(t));
    }
  }
  live_.clear();
  std::vector<Execution> loaded;
  for (const auto & j : state.value("executions", nlohmann::json::array())) {
    loaded.push_back(j.get<Execution>());
  }
  std::stable_sort(loaded.begin(), loaded.end(), [](const Execution & a, const Execution & b) {
    return a.created < b.created;
  });
  for (auto & x : loaded) {
    Live l;
    l.order = serial_++;
    l.ended = is_terminal(x.state);
    if (const auto it = by_id_.find(x.plan_id); it != by_id_.end()) {
      l.plan = it->second;
    }
    const auto id = x.execution_id;
    l.x = std::move(x);
    live_.emplace(id, std::move(l));
  }
  // Running executions are not resumed: what an actuator did meanwhile is unknown.
  const auto interrupted = select([](const Live & l) { return l.x.state == ExecState::Running; });
  const std::string why = "interrupted: the orchestrator restarted while it ran";
  for (const auto & id : interrupted) {
    Live & l = *get(id);
    auto & x = l.x;
    l.ended = true;
    l.wait = Wait::None;
    if (x.current_step < x.steps.size()) {
      auto & rec = x.steps[x.current_step];
      if (rec.state == StepState::Running || rec.state == StepState::Retrying) {
        rec.state = StepState::Aborted;
        rec.finished = now;
        rec.message = why;
      }
    }
    x.state = ExecState::Aborted;
    x.reason = why;
    x.finished = now;
    x.child_id.clear();
    emit(
      l, "ABORTED", Severity::Error, x.plan_id + " " + why, {{"reason", why}}, now, out,
      "jarvis.recovery.interrupted");
    changed(l, out);
  }
  for (const auto & id : interrupted) {
    Live & l = *get(id);
    if (l.x.parent_id.empty()) {
      escalate(l, "operator", now, out);
    }
  }
  for (const auto & id : interrupted) {
    settle(*get(id), now, out);
  }
  // Waiting executions survive, unless their parent was interrupted or their plan changed.
  for (const auto & id : select([](const Live & l) { return !l.ended; })) {
    Live * l = get(id);
    if (!l || l->ended) {
      continue;
    }
    const Live * parent = get(l->x.parent_id);
    if (!l->x.parent_id.empty() && (!parent || parent->ended)) {
      finish(*l, ExecState::Aborted, "parent interrupted", now, out);
    } else if (!l->plan || l->plan->rev != l->x.plan_rev) {
      reject(*l, "plan", "plan changed while the orchestrator was down", now, out);
    }
  }
  // Ladders whose current entry is gone continue with the next one.
  for (const auto & id : select([](const Live & l) { return l.ended && l.x.ladder_running; })) {
    Live * owner = get(id);
    if (!owner || !owner->x.ladder_running) {
      continue;
    }
    if (!owner->plan) {
      ladder_done(*owner, "plan no longer exists", now, out);
    } else if (owner->x.ladder_child.empty() || !get(owner->x.ladder_child)) {
      owner->x.ladder_child.clear();
      ladder_advance(*owner, now, out);
    }
  }
  pump_queue(now, out);
  out.emplace_back(PersistState{});
  collect();
  merge_effects(out, {});
  return out;
}

}  // namespace jarvis_core
