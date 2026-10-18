// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_core/fault_manager.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <limits>
#include <utility>

#include "jarvis_core/json.hpp"

namespace jarvis_core
{

namespace
{

constexpr std::size_t kMaxReportAttrsInTransitions =
  24;  // leaves room for reserved keys (hub caps at 32)

constexpr std::array<std::pair<FaultState, std::string_view>, 4> kFaultStateNames{{
  {FaultState::Pending, "PENDING"},
  {FaultState::Active, "ACTIVE"},
  {FaultState::Latched, "LATCHED"},
  {FaultState::Cleared, "CLEARED"},
}};

constexpr std::array<std::pair<IncidentState, std::string_view>, 5> kIncidentStateNames{{
  {IncidentState::Open, "OPEN"},
  {IncidentState::Recovering, "RECOVERING"},
  {IncidentState::Escalated, "ESCALATED"},
  {IncidentState::Resolved, "RESOLVED"},
  {IncidentState::Closed, "CLOSED"},
}};

std::string lower(std::string_view s)
{
  std::string out(s);
  std::transform(out.begin(), out.end(), out.begin(), [](unsigned char c) {
    return static_cast<char>(std::tolower(c));
  });
  return out;
}

Severity effective_severity(const FaultPolicy & policy, Severity reported)
{
  // Catalog severity is the floor; a reporter may escalate but never downgrade a cataloged fault.
  return policy.cataloged ? std::max(policy.severity, reported) : reported;
}

void add_unique(std::vector<std::string> & v, const std::string & s)
{
  if (std::find(v.begin(), v.end(), s) == v.end()) {
    v.push_back(s);
  }
}

std::string time_json(Timestamp t) { return t == 0 ? std::string{} : format_iso8601(t); }

Timestamp time_from(const nlohmann::json & j, const char * key)
{
  if (!j.contains(key)) {
    return 0;
  }
  const auto & v = j.at(key);
  if (v.is_number_integer()) {
    return v.get<Timestamp>();
  }
  const auto s = v.get<std::string>();
  if (s.empty()) {
    return 0;
  }
  return parse_iso8601(s).value_or(0);
}

}  // namespace

void merge_effects(Effects & into, Effects more)
{
  bool faults_changed = false;
  bool persist = false;
  std::optional<ExecutionsChanged> executions;
  Effects merged;
  merged.reserve(into.size() + more.size());
  for (auto * list : {&into, &more}) {
    for (auto & fx : *list) {
      if (std::holds_alternative<FaultsChanged>(fx)) {
        faults_changed = true;
      } else if (std::holds_alternative<PersistState>(fx)) {
        persist = true;
      } else if (auto * ec = std::get_if<ExecutionsChanged>(&fx)) {
        if (!executions) {
          executions.emplace();
        }
        for (auto & id : ec->execution_ids) {
          auto & ids = executions->execution_ids;
          if (std::find(ids.begin(), ids.end(), id) == ids.end()) {
            ids.push_back(std::move(id));
          }
        }
      } else {
        merged.push_back(std::move(fx));
      }
    }
  }
  if (faults_changed) {
    merged.emplace_back(FaultsChanged{});
  }
  if (executions) {
    merged.emplace_back(std::move(*executions));
  }
  if (persist) {
    merged.emplace_back(PersistState{});
  }
  into = std::move(merged);
}

std::string_view to_string(FaultState state)
{
  for (const auto & [s, name] : kFaultStateNames) {
    if (s == state) {
      return name;
    }
  }
  return "CLEARED";
}

std::string_view to_string(IncidentState state)
{
  for (const auto & [s, name] : kIncidentStateNames) {
    if (s == state) {
      return name;
    }
  }
  return "OPEN";
}

std::optional<FaultState> parse_fault_state(std::string_view text)
{
  for (const auto & [s, name] : kFaultStateNames) {
    if (name == text) {
      return s;
    }
  }
  return std::nullopt;
}

std::optional<IncidentState> parse_incident_state(std::string_view text)
{
  for (const auto & [s, name] : kIncidentStateNames) {
    if (name == text) {
      return s;
    }
  }
  return std::nullopt;
}

// ---------------------------------------------------------------------------------------------
// JSON (state snapshots and the gateway)

void to_json(nlohmann::json & j, const FaultEpisode & f)
{
  j = nlohmann::json{
    {"fault_id", f.fault_id},
    {"code", f.code},
    {"source", f.source},
    {"reporting_sources", f.reporting_sources},
    {"state", std::string(to_string(f.state))},
    {"severity", std::string(to_string(f.severity))},
    {"occurrences", f.occurrences},
    {"first_seen", time_json(f.first_seen)},
    {"last_seen", time_json(f.last_seen)},
    {"confirmed_at", time_json(f.confirmed_at)},
    {"cleared_at", time_json(f.cleared_at)},
    {"acknowledged", f.acknowledged},
    {"acknowledged_by", f.acknowledged_by},
    {"acknowledged_at", time_json(f.acknowledged_at)},
    {"latch", f.latch},
    {"cataloged", f.cataloged},
    {"muted_by", f.muted_by},
    {"muted_rule", f.muted_rule},
    {"incident_id", f.incident_id},
    {"snapshot_ref", f.snapshot_ref},
    {"description", f.description},
    {"message", f.message},
    {"report_attrs", attrs_to_json(f.report_attrs)},
    {"freeze_frame", attrs_to_json(f.freeze_frame)},
    {"debounce",
     {{"counter", f.counter},
      {"raise_times", f.raise_times},
      {"pending_deadline", f.pending_deadline},
      {"heal_deadline", f.heal_deadline}}},
  };
}

void from_json(const nlohmann::json & j, FaultEpisode & f)
{
  f = FaultEpisode{};
  f.fault_id = j.at("fault_id").get<std::string>();
  f.code = j.at("code").get<std::string>();
  f.source = j.at("source").get<Source>();
  f.reporting_sources = j.value("reporting_sources", std::vector<std::string>{});
  f.state = parse_fault_state(j.at("state").get<std::string>()).value_or(FaultState::Active);
  f.severity = parse_severity(j.at("severity").get<std::string>()).value_or(Severity::Error);
  f.occurrences = j.value("occurrences", 1u);
  f.first_seen = time_from(j, "first_seen");
  f.last_seen = time_from(j, "last_seen");
  f.confirmed_at = time_from(j, "confirmed_at");
  f.cleared_at = time_from(j, "cleared_at");
  f.acknowledged = j.value("acknowledged", false);
  f.acknowledged_by = j.value("acknowledged_by", "");
  f.acknowledged_at = time_from(j, "acknowledged_at");
  f.latch = j.value("latch", false);
  f.cataloged = j.value("cataloged", false);
  f.muted_by = j.value("muted_by", "");
  f.muted_rule = j.value("muted_rule", "");
  f.incident_id = j.value("incident_id", "");
  f.snapshot_ref = j.value("snapshot_ref", "");
  f.description = j.value("description", "");
  f.message = j.value("message", "");
  if (j.contains("report_attrs")) {
    f.report_attrs = attrs_from_json(j.at("report_attrs"));
  }
  if (j.contains("freeze_frame")) {
    f.freeze_frame = attrs_from_json(j.at("freeze_frame"));
  }
  if (j.contains("debounce")) {
    const auto & d = j.at("debounce");
    f.counter = d.value("counter", 0);
    f.raise_times = d.value("raise_times", std::vector<Timestamp>{});
    f.pending_deadline = d.value("pending_deadline", Timestamp{0});
    f.heal_deadline = d.value("heal_deadline", Timestamp{0});
  }
}

void to_json(nlohmann::json & j, const Incident & i)
{
  j = nlohmann::json{
    {"incident_id", i.incident_id},
    {"state", std::string(to_string(i.state))},
    {"severity", std::string(to_string(i.severity))},
    {"title", i.title},
    {"component", i.component},
    {"root_fault_id", i.root_fault_id},
    {"fault_ids", i.fault_ids},
    {"opened_at", time_json(i.opened_at)},
    {"last_activity", time_json(i.last_activity)},
    {"resolved_at", time_json(i.resolved_at)},
    {"closed_at", time_json(i.closed_at)},
  };
}

void from_json(const nlohmann::json & j, Incident & i)
{
  i = Incident{};
  i.incident_id = j.at("incident_id").get<std::string>();
  i.state = parse_incident_state(j.at("state").get<std::string>()).value_or(IncidentState::Open);
  i.severity = parse_severity(j.value("severity", "ERROR")).value_or(Severity::Error);
  i.title = j.value("title", "");
  i.component = j.value("component", "");
  i.root_fault_id = j.value("root_fault_id", "");
  i.fault_ids = j.value("fault_ids", std::vector<std::string>{});
  i.opened_at = time_from(j, "opened_at");
  i.last_activity = time_from(j, "last_activity");
  i.resolved_at = time_from(j, "resolved_at");
  i.closed_at = time_from(j, "closed_at");
}

// ---------------------------------------------------------------------------------------------

FaultManager::FaultManager(FaultCatalog catalog, FaultManagerConfig config, std::uint64_t id_seed)
: catalog_(std::move(catalog)), config_(std::move(config)), ids_(id_seed)
{
}

std::string FaultManager::key_for(const Event & e) const
{
  const std::string & robot = e.source.robot.empty() ? config_.robot_id : e.source.robot;
  return robot + '\x1f' + e.code + '\x1f' + e.source.subject();
}

Event FaultManager::make_event(
  Kind kind, std::string code, Severity severity, std::string message, Attrs attrs,
  const std::string & component, const std::string & correlation_id,
  const std::string & causation_id, Timestamp at)
{
  Event e;
  e.id = ids_.uuid7(at);
  e.stamp = at;
  e.source.robot = config_.robot_id;
  e.source.node = config_.node_name;
  e.source.component = component;
  e.kind = kind;
  e.code = std::move(code);
  e.severity = severity;
  e.message = std::move(message);
  e.attrs = std::move(attrs);
  e.correlation_id = correlation_id;
  e.causation_id = causation_id;
  return e;
}

Effects FaultManager::on_event(const Event & e, Timestamp now)
{
  Effects fx = [&]() -> Effects {
    switch (e.kind) {
      case Kind::FaultRaise:
        return on_raise(e, now);
      case Kind::FaultClear:
        return on_clear(e, now);
      case Kind::State:
        record_state(e);
        return {};
      case Kind::Recovery:
        return on_recovery(e, now);
      case Kind::System:
        if (e.code == "jarvis.incident.open") {
          return on_open_request(e, now);
        }
        if (e.code == "jarvis.blackbox.captured") {
          return on_blackbox_captured(e);
        }
        if (e.code == "jarvis.incident.escalate" && !e.correlation_id.empty()) {
          Effects out;
          if (auto it = incidents_.find(e.correlation_id);
              it != incidents_.end() && it->second.is_live() &&
              it->second.state != IncidentState::Escalated) {
            transition_incident(it->second, IncidentState::Escalated, now, e.id, e.message, out);
          }
          return out;
        }
        return {};
      default:
        return {};
    }
  }();
  merge_effects(fx, {});
  return fx;
}

void FaultManager::record_state(const Event & e)
{
  AttrValue value = e.message;
  if (const auto it = e.attrs.find("value"); it != e.attrs.end()) {
    value = it->second;
  }
  if (state_values_.find(e.code) == state_values_.end()) {
    state_order_.push_back(e.code);
    while (state_order_.size() > config_.state_values_capacity) {
      state_values_.erase(state_order_.front());
      state_order_.pop_front();
    }
  }
  state_values_[e.code] = std::move(value);
}

Effects FaultManager::on_raise(const Event & e, Timestamp now)
{
  Effects fx;
  const FaultPolicy policy = catalog_.policy_for(e.code);
  const Severity reported = effective_severity(policy, e.severity);
  const std::string key = key_for(e);

  const auto existing = open_by_key_.find(key);
  if (existing == open_by_key_.end()) {
    FaultEpisode ep;
    ep.fault_id = ids_.uuid7(now);
    ep.code = e.code;
    ep.source = e.source;
    if (ep.source.robot.empty()) {
      ep.source.robot = config_.robot_id;
    }
    if (ep.source.component.empty()) {
      ep.source.component = e.source.node;
    }
    ep.reporting_sources = {e.source.node};
    ep.state = FaultState::Pending;
    ep.severity = reported;
    ep.occurrences = 1;
    ep.first_seen = now;
    ep.last_seen = now;
    ep.latch = policy.latch;
    ep.cataloged = policy.cataloged;
    ep.description = policy.description;
    ep.message = e.message;
    ep.report_attrs = e.attrs;
    if (!policy.cataloged) {
      ep.report_attrs["uncataloged"] = true;
    }

    const std::string fault_id = ep.fault_id;
    open_by_key_[key] = fault_id;
    auto & stored = open_.emplace(fault_id, std::move(ep)).first->second;

    const auto & d = policy.debounce;
    bool confirm_now =
      stored.severity == Severity::Critical || d.mode == DebouncePolicy::Mode::Immediate;
    if (!confirm_now) {
      switch (d.mode) {
        case DebouncePolicy::Mode::Count:
          stored.raise_times = {now};
          confirm_now = d.count <= 1;
          stored.pending_deadline = now + d.within;
          break;
        case DebouncePolicy::Mode::Persist:
          stored.pending_deadline = now + d.persist;
          break;
        case DebouncePolicy::Mode::Counter:
          stored.counter = -1;
          confirm_now = stored.counter <= d.confirm_at;
          if (policy.heal.after > 0) {
            stored.pending_deadline = now + policy.heal.after;
          }
          break;
        case DebouncePolicy::Mode::Immediate:
          break;
      }
    }
    if (confirm_now) {
      confirm(stored, now, e.id, fx, /*fresh=*/true);
    } else {
      emit_transition(stored, std::nullopt, now, e.id, {}, fx);
      fx.emplace_back(FaultsChanged{});
      fx.emplace_back(PersistState{});
    }
    return fx;
  }

  FaultEpisode & ep = open_.at(existing->second);
  ep.last_seen = now;
  ep.report_attrs = e.attrs;
  if (!ep.cataloged) {
    ep.report_attrs["uncataloged"] = true;
  }
  if (!e.message.empty()) {
    ep.message = e.message;
  }
  bool changed = false;
  if (
    std::find(ep.reporting_sources.begin(), ep.reporting_sources.end(), e.source.node) ==
    ep.reporting_sources.end()) {
    ep.reporting_sources.push_back(e.source.node);
    changed = true;
  }
  if (reported > ep.severity) {
    ep.severity = reported;
    changed = true;
  }

  const auto & d = policy.debounce;
  switch (ep.state) {
    case FaultState::Pending: {
      bool confirm_now = ep.severity == Severity::Critical;
      if (d.mode == DebouncePolicy::Mode::Count) {
        ep.raise_times.push_back(now);
        std::erase_if(ep.raise_times, [&](Timestamp t) { return t <= now - d.within; });
        confirm_now = confirm_now || static_cast<int>(ep.raise_times.size()) >= d.count;
        ep.pending_deadline = ep.raise_times.front() + d.within;
      } else if (d.mode == DebouncePolicy::Mode::Counter) {
        ep.counter -= 1;
        confirm_now = confirm_now || ep.counter <= d.confirm_at;
        if (policy.heal.after > 0) {
          ep.pending_deadline = now + policy.heal.after;
        }
      } else if (d.mode == DebouncePolicy::Mode::Immediate) {
        confirm_now = true;
      }
      if (confirm_now) {
        confirm(ep, now, e.id, fx);
        return fx;
      }
      break;
    }
    case FaultState::Active:
      if (d.mode == DebouncePolicy::Mode::Counter) {
        ep.counter = std::max(d.confirm_at, ep.counter - 1);
      }
      if (policy.heal.after > 0) {
        ep.heal_deadline = now + policy.heal.after;
      }
      break;
    case FaultState::Latched: {
      ep.state = FaultState::Active;
      ep.occurrences += 1;
      ep.counter = d.mode == DebouncePolicy::Mode::Counter ? d.confirm_at : 0;
      ep.heal_deadline = policy.heal.after > 0 ? now + policy.heal.after : 0;
      emit_transition(ep, FaultState::Latched, now, e.id, {{"reactivated", true}}, fx);
      changed = true;
      break;
    }
    case FaultState::Cleared:
      break;
  }
  if (changed) {
    fx.emplace_back(FaultsChanged{});
    fx.emplace_back(PersistState{});
  }
  return fx;
}

Effects FaultManager::on_clear(const Event & e, Timestamp now)
{
  Effects fx;
  const auto existing = open_by_key_.find(key_for(e));
  if (existing == open_by_key_.end()) {
    return fx;
  }
  FaultEpisode & ep = open_.at(existing->second);
  const FaultPolicy policy = catalog_.policy_for(ep.code);
  const auto & d = policy.debounce;

  switch (ep.state) {
    case FaultState::Pending:
      if (d.mode == DebouncePolicy::Mode::Counter) {
        ep.counter += 1;
        if (ep.counter < d.heal_at) {
          fx.emplace_back(PersistState{});
          return fx;
        }
      }
      close(
        ep.fault_id, now, e.id, {{"reason", std::string("cleared before confirmation")}}, true, fx);
      break;
    case FaultState::Active:
      if (d.mode == DebouncePolicy::Mode::Counter) {
        ep.counter = std::min(d.heal_at, ep.counter + 1);
        if (ep.counter >= d.heal_at) {
          heal(ep, now, e.id, fx);
        } else {
          fx.emplace_back(PersistState{});
        }
      } else if (policy.heal.on_clear) {
        heal(ep, now, e.id, fx);
      }
      break;
    case FaultState::Latched:
    case FaultState::Cleared:
      break;
  }
  return fx;
}

Effects FaultManager::on_recovery(const Event & e, Timestamp now)
{
  Effects fx;
  if (e.correlation_id.empty()) {
    return fx;
  }
  const auto it = incidents_.find(e.correlation_id);
  if (it == incidents_.end() || !it->second.is_live()) {
    return fx;
  }
  Incident & inc = it->second;
  const std::string * phase = attr_string(e.attrs, "phase");
  if (phase == nullptr) {
    return fx;
  }
  // Only real, top-level executions move the incident: a dry run changes nothing, and a sub-plan's
  // outcome is its parent's business.
  if (const auto dry = e.attrs.find("dry_run"); dry != e.attrs.end() &&
                                                std::holds_alternative<bool>(dry->second) &&
                                                std::get<bool>(dry->second)) {
    return fx;
  }
  if (const std::string * parent = attr_string(e.attrs, "parent_id"); parent && !parent->empty()) {
    return fx;
  }
  if (*phase == "STARTED" && inc.state == IncidentState::Open) {
    transition_incident(inc, IncidentState::Recovering, now, e.id, "recovery started", fx);
  } else if (
    (*phase == "FAILED" || *phase == "ABORTED" || *phase == "REJECTED" || *phase == "EXPIRED") &&
    inc.state == IncidentState::Recovering) {
    transition_incident(inc, IncidentState::Open, now, e.id, "recovery " + lower(*phase), fx);
  } else if (*phase == "ESCALATED" && inc.state != IncidentState::Escalated) {
    transition_incident(inc, IncidentState::Escalated, now, e.id, "recovery escalated", fx);
  }
  return fx;
}

void FaultManager::emit_transition(
  const FaultEpisode & ep, std::optional<FaultState> prev, Timestamp at,
  const std::string & causation, Attrs extra, Effects & fx)
{
  Attrs attrs;
  std::size_t copied = 0;
  for (const auto & [k, v] : ep.report_attrs) {
    if (copied++ == kMaxReportAttrsInTransitions) {
      break;
    }
    attrs[k] = v;
  }
  attrs["fault_id"] = ep.fault_id;
  attrs["state"] = std::string(to_string(ep.state));
  attrs["prev_state"] = prev ? std::string(to_string(*prev)) : std::string("NONE");
  attrs["occurrences"] = static_cast<std::int64_t>(ep.occurrences);
  if (!ep.muted_by.empty()) {
    attrs["muted_by"] = ep.muted_by;
  }
  if (ep.state == FaultState::Cleared) {
    attrs["confirmed"] = ep.confirmed_at != 0;
  }
  for (auto & [k, v] : extra) {
    attrs[k] = std::move(v);
  }

  std::string message = std::string(to_string(ep.state));
  const std::string & detail = ep.message.empty() ? ep.description : ep.message;
  if (ep.state == FaultState::Cleared) {
    if (const auto * reason = attr_string(attrs, "reason")) {
      message += ": " + *reason;
    }
  } else if (!detail.empty()) {
    message += ": " + detail;
  }
  const Severity severity = ep.state == FaultState::Cleared ? Severity::Info : ep.severity;
  fx.emplace_back(EmitEvent{make_event(
    Kind::FaultState, ep.code, severity, std::move(message), std::move(attrs), ep.source.component,
    ep.incident_id, causation, at)});
}

void FaultManager::confirm(
  FaultEpisode & ep, Timestamp at, const std::string & causation, Effects & fx, bool fresh)
{
  const FaultPolicy policy = catalog_.policy_for(ep.code);
  const std::optional<FaultState> prev = fresh ? std::nullopt : std::optional<FaultState>(ep.state);
  ep.state = FaultState::Active;
  ep.confirmed_at = at;
  ep.pending_deadline = 0;
  ep.raise_times.clear();
  if (policy.debounce.mode == DebouncePolicy::Mode::Counter) {
    ep.counter = policy.debounce.confirm_at;
  }
  ep.heal_deadline = policy.heal.after > 0 ? at + policy.heal.after : 0;

  ep.freeze_frame = ep.report_attrs;
  for (const auto & [code, value] : state_values_) {
    ep.freeze_frame["state." + code] = value;
  }

  // Am I a symptom of an active root cause?
  for (const auto & rule : catalog_.correlation()) {
    if (!rule.mute_symptoms || !rule.is_symptom(ep.code)) {
      continue;
    }
    for (const auto & [id, root] : open_) {
      if (
        id == ep.fault_id || !root.muted_by.empty() || !rule.is_root(root.code) ||
        (root.state != FaultState::Active && root.state != FaultState::Latched) ||
        at - root.confirmed_at > rule.window) {
        continue;
      }
      ep.muted_by = root.fault_id;
      ep.muted_rule = rule.id;
      ep.incident_id = root.incident_id;
      break;
    }
    if (!ep.muted_by.empty()) {
      break;
    }
  }

  if (ep.muted_by.empty()) {
    open_or_join_incident(ep, at, fx);
  } else if (auto it = incidents_.find(ep.incident_id); it != incidents_.end()) {
    add_unique(it->second.fault_ids, ep.fault_id);
    it->second.last_activity = at;
  }
  emit_transition(ep, prev, at, causation, {}, fx);
  if (!ep.muted_by.empty()) {
    // Same notice as the retroactive case below, so muting is announced whatever the order.
    const auto root = open_.find(ep.muted_by);
    fx.emplace_back(EmitEvent{make_event(
      Kind::System, "jarvis.fault.muted", Severity::Info,
      ep.code + " muted as a symptom of " + (root != open_.end() ? root->second.code : ep.muted_by),
      {{"fault_id", ep.fault_id}, {"muted_by", ep.muted_by}, {"rule", ep.muted_rule}},
      ep.source.component, ep.incident_id, causation, at)});
  }

  // Am I a root cause for symptoms that confirmed just before me?
  for (const auto & rule : catalog_.correlation()) {
    if (!rule.mute_symptoms || !rule.is_root(ep.code)) {
      continue;
    }
    for (auto & [id, sym] : open_) {
      if (
        id == ep.fault_id || !sym.muted_by.empty() || sym.state != FaultState::Active ||
        !rule.is_symptom(sym.code) || at - sym.confirmed_at > rule.window) {
        continue;
      }
      sym.muted_by = ep.fault_id;
      sym.muted_rule = rule.id;
      const std::string old_incident = sym.incident_id;
      sym.incident_id = ep.incident_id;
      if (auto it = incidents_.find(ep.incident_id); it != incidents_.end()) {
        add_unique(it->second.fault_ids, sym.fault_id);
      }
      fx.emplace_back(EmitEvent{make_event(
        Kind::System, "jarvis.fault.muted", Severity::Info,
        sym.code + " muted as a symptom of " + ep.code,
        {{"fault_id", sym.fault_id}, {"muted_by", ep.fault_id}, {"rule", rule.id}},
        sym.source.component, ep.incident_id, causation, at)});
      if (auto it = incidents_.find(old_incident);
          !old_incident.empty() && old_incident != ep.incident_id && it != incidents_.end()) {
        auto & ids = it->second.fault_ids;
        std::erase(ids, sym.fault_id);
        if (ids.empty() && it->second.is_live()) {
          transition_incident(
            it->second, IncidentState::Closed, at, causation,
            "merged into " + (ep.incident_id.empty() ? std::string("root fault") : ep.incident_id),
            fx);
        }
      }
    }
  }

  if (policy.capture.blackbox && ep.muted_by.empty()) {
    fx.emplace_back(CaptureBlackbox{ep.fault_id, ep.code, ep.incident_id});
  }
  fx.emplace_back(FaultsChanged{});
  fx.emplace_back(PersistState{});
}

void FaultManager::heal(
  FaultEpisode & ep, Timestamp at, const std::string & causation, Effects & fx)
{
  if (ep.latch) {
    const FaultState prev = ep.state;
    ep.state = FaultState::Latched;
    ep.heal_deadline = 0;
    ep.pending_deadline = 0;
    emit_transition(ep, prev, at, causation, {{"reason", std::string("healed, latched")}}, fx);
    fx.emplace_back(FaultsChanged{});
    fx.emplace_back(PersistState{});
    return;
  }
  close(ep.fault_id, at, causation, {{"reason", std::string("healed")}}, true, fx);
}

void FaultManager::close(
  std::string fault_id, Timestamp at, const std::string & causation, Attrs extra,
  bool clear_symptoms, Effects & fx, std::vector<std::string> * cleared)
{
  auto it = open_.find(fault_id);
  if (it == open_.end()) {
    return;
  }
  FaultEpisode ep = std::move(it->second);
  open_.erase(it);
  for (auto k = open_by_key_.begin(); k != open_by_key_.end(); ++k) {
    if (k->second == fault_id) {
      open_by_key_.erase(k);
      break;
    }
  }

  const FaultState prev = ep.state;
  ep.state = FaultState::Cleared;
  ep.cleared_at = at;
  ep.pending_deadline = 0;
  ep.heal_deadline = 0;
  emit_transition(ep, prev, at, causation, std::move(extra), fx);

  if (clear_symptoms) {
    std::vector<std::string> symptoms;
    for (const auto & [id, sym] : open_) {
      if (sym.muted_by != fault_id) {
        continue;
      }
      const auto & rules = catalog_.correlation();
      const auto rule = std::find_if(rules.begin(), rules.end(), [&sym](const CorrelationRule & r) {
        return r.id == sym.muted_rule;
      });
      if (rule == rules.end() || rule->clear_with_root) {
        symptoms.push_back(id);
      }
    }
    for (const auto & id : symptoms) {
      if (cleared != nullptr) {
        cleared->push_back(open_.at(id).code);
      }
      close(
        id, at, causation,
        {{"reason", std::string("cleared with root cause")}, {"cleared_with_root", fault_id}}, true,
        fx, cleared);
    }
  } else {
    for (auto & [id, sym] : open_) {
      if (sym.muted_by == fault_id) {
        sym.muted_by.clear();
        sym.muted_rule.clear();
      }
    }
  }

  const std::string incident_id = ep.incident_id;
  history_.push_back(std::move(ep));
  while (history_.size() > config_.history_capacity) {
    history_.pop_front();
  }
  if (!incident_id.empty()) {
    check_incident_resolved(incident_id, at, fx);
  }
  fx.emplace_back(FaultsChanged{});
  fx.emplace_back(PersistState{});
}

void FaultManager::open_or_join_incident(FaultEpisode & ep, Timestamp at, Effects & fx, bool forced)
{
  const FaultPolicy policy = catalog_.policy_for(ep.code);
  if (
    !forced &&
    (policy.incident == IncidentPolicy::Never ||
     (policy.incident == IncidentPolicy::Auto && ep.severity < config_.incident_min_severity))) {
    return;
  }
  const std::string & component = ep.source.component;
  for (auto & [id, inc] : incidents_) {
    if (
      inc.is_live() && inc.component == component &&
      at - inc.last_activity <= config_.incident_merge_window) {
      ep.incident_id = id;
      add_unique(inc.fault_ids, ep.fault_id);
      inc.last_activity = at;
      inc.severity = std::max(inc.severity, ep.severity);
      fx.emplace_back(EmitEvent{make_event(
        Kind::System, "jarvis.incident.joined", Severity::Info,
        ep.code + " joined the open incident on " + component,
        {{"incident_id", id},
         {"fault_id", ep.fault_id},
         {"state", std::string(to_string(inc.state))}},
        component, id, {}, at)});
      return;
    }
  }
  Incident inc;
  inc.incident_id = ids_.uuid7(at);
  inc.state = IncidentState::Open;
  inc.severity = ep.severity;
  inc.title = ep.code + " on " + component;
  inc.component = component;
  inc.root_fault_id = ep.fault_id;
  inc.fault_ids = {ep.fault_id};
  inc.opened_at = at;
  inc.last_activity = at;
  ep.incident_id = inc.incident_id;
  fx.emplace_back(EmitEvent{make_event(
    Kind::System, "jarvis.incident.opened", ep.severity, inc.title,
    {{"incident_id", inc.incident_id},
     {"state", std::string("OPEN")},
     {"prev_state", std::string("NONE")},
     {"root_fault_id", ep.fault_id}},
    component, inc.incident_id, {}, at)});
  incidents_.emplace(inc.incident_id, std::move(inc));
}

Effects FaultManager::on_open_request(const Event & e, Timestamp now)
{
  // A rule asks for an incident (`incident: { open: true }`). What it matched may already be in
  // one; a fault below the incident threshold gets one; anything else opens one without a fault,
  // which joins the faults that follow on its component and is closed by an operator.
  Effects fx;
  if (const auto it = incidents_.find(e.correlation_id);
      !e.correlation_id.empty() && it != incidents_.end() && it->second.is_live()) {
    return fx;
  }
  const std::string * rule = attr_string(e.attrs, "rule");
  if (const std::string * fault_id = attr_string(e.attrs, "fault_id")) {
    if (const auto it = open_.find(*fault_id); it != open_.end()) {
      const auto inc = incidents_.find(it->second.incident_id);
      if (inc == incidents_.end() || !inc->second.is_live()) {
        open_or_join_incident(it->second, now, fx, true);
        fx.emplace_back(FaultsChanged{});
        fx.emplace_back(PersistState{});
      }
      return fx;
    }
  }
  const std::string & component = e.source.component;
  for (auto & [id, inc] : incidents_) {
    if (
      inc.is_live() && inc.component == component &&
      now - inc.last_activity <= config_.incident_merge_window) {
      return fx;  // the component's open incident is the one
    }
  }
  const std::string * matched = attr_string(e.attrs, "matched_code");
  Incident inc;
  inc.incident_id = ids_.uuid7(now);
  inc.state = IncidentState::Open;
  inc.severity = e.severity;
  inc.title = (matched ? *matched : std::string("incident")) +
              (component.empty() ? "" : " on " + component) + (rule ? " (rule " + *rule + ")" : "");
  inc.component = component;
  inc.opened_at = now;
  inc.last_activity = now;
  Attrs attrs{
    {"incident_id", inc.incident_id},
    {"state", std::string("OPEN")},
    {"prev_state", std::string("NONE")},
    {"root_fault_id", std::string()}};
  if (rule) {
    attrs["rule"] = *rule;
  }
  fx.emplace_back(EmitEvent{make_event(
    Kind::System, "jarvis.incident.opened", e.severity, inc.title, std::move(attrs), component,
    inc.incident_id, e.id, now)});
  incidents_.emplace(inc.incident_id, std::move(inc));
  fx.emplace_back(FaultsChanged{});
  fx.emplace_back(PersistState{});
  return fx;
}

Effects FaultManager::on_blackbox_captured(const Event & e)
{
  // The fault links to its black-box recording: the event that says it was saved, and when.
  const std::string * fault_id = attr_string(e.attrs, "fault_id");
  if (!fault_id) {
    return {};
  }
  if (const auto it = open_.find(*fault_id); it != open_.end()) {
    it->second.snapshot_ref = e.id;
    return {FaultsChanged{}, PersistState{}};
  }
  for (auto & ep : history_) {
    if (ep.fault_id == *fault_id) {
      ep.snapshot_ref = e.id;  // it healed before the recorder answered
    }
  }
  return {};
}

void FaultManager::transition_incident(
  Incident & inc, IncidentState next, Timestamp at, const std::string & causation,
  const std::string & reason, Effects & fx)
{
  const IncidentState prev = inc.state;
  inc.state = next;
  inc.last_activity = at;
  if (next == IncidentState::Resolved) {
    inc.resolved_at = at;
  }
  if (next == IncidentState::Closed) {
    inc.closed_at = at;
  }
  const Severity severity = next == IncidentState::Escalated
                              ? std::max(inc.severity, Severity::Error)
                              : (next == IncidentState::Open ? inc.severity : Severity::Info);
  Attrs attrs{
    {"incident_id", inc.incident_id},
    {"state", std::string(to_string(next))},
    {"prev_state", std::string(to_string(prev))},
    {"root_fault_id", inc.root_fault_id}};
  if (!reason.empty()) {
    attrs["reason"] = reason;
  }
  fx.emplace_back(EmitEvent{make_event(
    Kind::System, "jarvis.incident." + lower(to_string(next)), severity,
    inc.title + " " + std::string(to_string(next)) + (reason.empty() ? "" : " (" + reason + ")"),
    std::move(attrs), inc.component, inc.incident_id, causation, at)});
  fx.emplace_back(FaultsChanged{});
  fx.emplace_back(PersistState{});
}

void FaultManager::check_incident_resolved(
  const std::string & incident_id, Timestamp at, Effects & fx)
{
  const auto it = incidents_.find(incident_id);
  if (it == incidents_.end() || !it->second.is_live()) {
    return;
  }
  const bool any_open = std::any_of(
    it->second.fault_ids.begin(), it->second.fault_ids.end(),
    [this](const std::string & id) { return open_.count(id) != 0; });
  if (!any_open) {
    transition_incident(it->second, IncidentState::Resolved, at, {}, "all faults cleared", fx);
    close_oldest_resolved(at, fx);
  }
}

void FaultManager::close_oldest_resolved(Timestamp at, Effects & fx)
{
  auto resolved = static_cast<std::size_t>(std::count_if(
    incidents_.begin(), incidents_.end(),
    [](const auto & kv) { return kv.second.state == IncidentState::Resolved; }));
  for (; resolved > config_.max_resolved_incidents; --resolved) {
    auto oldest = incidents_.end();
    for (auto it = incidents_.begin(); it != incidents_.end(); ++it) {
      if (
        it->second.state == IncidentState::Resolved &&
        (oldest == incidents_.end() || it->second.resolved_at < oldest->second.resolved_at)) {
        oldest = it;
      }
    }
    transition_incident(
      oldest->second, IncidentState::Closed, at, {},
      "auto-closed: more than " + std::to_string(config_.max_resolved_incidents) +
        " resolved incidents",
      fx);
    incidents_.erase(oldest);
  }
}

std::optional<Timestamp> FaultManager::next_deadline() const
{
  std::optional<Timestamp> best;
  auto consider = [&best](Timestamp t) {
    if (t > 0 && (!best || t < *best)) {
      best = t;
    }
  };
  for (const auto & [id, ep] : open_) {
    consider(ep.pending_deadline);
    if (ep.state == FaultState::Active) {
      consider(ep.heal_deadline);
    }
  }
  for (const auto & [id, inc] : incidents_) {
    if (inc.state == IncidentState::Resolved) {
      consider(inc.resolved_at + config_.incident_auto_close);
    }
  }
  return best;
}

Effects FaultManager::advance_to(Timestamp now)
{
  Effects fx;
  for (int guard = 0; guard < 100000; ++guard) {
    const auto next = next_deadline();
    if (!next || *next > now) {
      break;
    }
    const Timestamp at = *next;
    bool handled = false;

    for (auto & [id, ep] : open_) {
      if (ep.pending_deadline == at) {
        const FaultPolicy policy = catalog_.policy_for(ep.code);
        const auto & d = policy.debounce;
        if (d.mode == DebouncePolicy::Mode::Persist) {
          confirm(ep, at, {}, fx);
        } else if (d.mode == DebouncePolicy::Mode::Count) {
          std::erase_if(ep.raise_times, [&](Timestamp t) { return t <= at - d.within; });
          if (ep.raise_times.empty()) {
            close(id, at, {}, {{"reason", std::string("debounce window expired")}}, true, fx);
          } else {
            ep.pending_deadline = ep.raise_times.front() + d.within;
          }
        } else {
          close(id, at, {}, {{"reason", std::string("no report for a while")}}, true, fx);
        }
        handled = true;
        break;
      }
      if (ep.state == FaultState::Active && ep.heal_deadline == at) {
        ep.heal_deadline = 0;
        heal(ep, at, {}, fx);
        handled = true;
        break;
      }
    }
    if (handled) {
      continue;
    }
    for (auto & [id, inc] : incidents_) {
      if (
        inc.state == IncidentState::Resolved &&
        inc.resolved_at + config_.incident_auto_close == at) {
        transition_incident(inc, IncidentState::Closed, at, {}, "auto-closed", fx);
        handled = true;
        break;
      }
    }
    if (!handled) {
      break;  // defensive: a deadline nobody owns
    }
  }
  // Closed incidents don't need to stay in memory.
  std::erase_if(
    incidents_, [](const auto & kv) { return kv.second.state == IncidentState::Closed; });
  merge_effects(fx, {});
  return fx;
}

OperatorResult FaultManager::acknowledge(
  const std::string & fault_id, const std::string & user, const std::string & note, Timestamp now)
{
  OperatorResult r;
  auto it = open_.find(fault_id);
  if (it == open_.end()) {
    r.message = "no open fault with id " + fault_id;
    return r;
  }
  FaultEpisode & ep = it->second;
  r.success = true;
  if (ep.acknowledged) {
    r.message = "already acknowledged by " + ep.acknowledged_by;
    return r;
  }
  ep.acknowledged = true;
  ep.acknowledged_by = user;
  ep.acknowledged_at = now;
  r.effects.emplace_back(EmitEvent{make_event(
    Kind::Operator, "fault.acknowledge", Severity::Info, user + " acknowledged " + ep.code,
    {{"user", user}, {"fault_id", fault_id}, {"fault_code", ep.code}, {"note", note}},
    ep.source.component, ep.incident_id, {}, now)});
  r.effects.emplace_back(FaultsChanged{});
  r.effects.emplace_back(PersistState{});
  r.message = "acknowledged";
  return r;
}

OperatorResult FaultManager::clear(
  const std::string & fault_id, const std::string & user, const std::string & reason,
  bool skip_symptoms, Timestamp now)
{
  OperatorResult r;
  auto it = open_.find(fault_id);
  if (it == open_.end()) {
    r.message = "no open fault with id " + fault_id;
    return r;
  }
  const FaultEpisode & ep = it->second;
  Event op = make_event(
    Kind::Operator, "fault.clear", Severity::Info, user + " cleared " + ep.code,
    {{"user", user}, {"fault_id", fault_id}, {"fault_code", ep.code}, {"reason", reason}},
    ep.source.component, ep.incident_id, {}, now);
  const std::string op_id = op.id;
  r.effects.emplace_back(EmitEvent{std::move(op)});
  close(
    fault_id, now, op_id,
    {{"forced", true}, {"by", user}, {"reason", reason.empty() ? std::string("operator") : reason}},
    !skip_symptoms, r.effects, &r.cleared_symptoms);
  merge_effects(r.effects, {});
  r.success = true;
  r.message = "cleared";
  return r;
}

OperatorResult FaultManager::close_incident(
  const std::string & incident_id, const std::string & user, const std::string & note,
  Timestamp now)
{
  OperatorResult r;
  auto it = incidents_.find(incident_id);
  if (it == incidents_.end()) {
    r.message = "no incident with id " + incident_id;
    return r;
  }
  const bool without_faults = it->second.is_live() && it->second.fault_ids.empty();
  if (it->second.state != IncidentState::Resolved && !without_faults) {
    r.message = "incident is " + std::string(to_string(it->second.state)) +
                "; only RESOLVED incidents can be closed (or ones a rule opened without a fault)";
    return r;
  }
  Attrs attrs{{"user", user}, {"incident_id", incident_id}};
  if (!note.empty()) {
    attrs["note"] = note;
  }
  r.effects.emplace_back(EmitEvent{make_event(
    Kind::Operator, "incident.close", Severity::Info, user + " closed " + it->second.title,
    std::move(attrs), it->second.component, incident_id, {}, now)});
  transition_incident(it->second, IncidentState::Closed, now, {}, "closed by " + user, r.effects);
  incidents_.erase(it);
  merge_effects(r.effects, {});
  r.success = true;
  r.message = "closed";
  return r;
}

std::vector<FaultEpisode> FaultManager::faults(bool include_cleared) const
{
  std::vector<FaultEpisode> out;
  out.reserve(open_.size() + (include_cleared ? history_.size() : 0));
  for (const auto & [id, ep] : open_) {
    out.push_back(ep);
  }
  std::sort(out.begin(), out.end(), [](const FaultEpisode & a, const FaultEpisode & b) {
    return a.first_seen < b.first_seen;
  });
  if (include_cleared) {
    out.insert(out.end(), history_.rbegin(), history_.rend());
  }
  return out;
}

std::vector<Incident> FaultManager::incidents(bool include_closed) const
{
  std::vector<Incident> out;
  for (const auto & [id, inc] : incidents_) {
    if (include_closed || inc.state != IncidentState::Closed) {
      out.push_back(inc);
    }
  }
  std::sort(out.begin(), out.end(), [](const Incident & a, const Incident & b) {
    return a.opened_at < b.opened_at;
  });
  return out;
}

const FaultEpisode * FaultManager::find_fault(const std::string & fault_id) const
{
  if (const auto it = open_.find(fault_id); it != open_.end()) {
    return &it->second;
  }
  for (auto it = history_.rbegin(); it != history_.rend(); ++it) {
    if (it->fault_id == fault_id) {
      return &*it;
    }
  }
  return nullptr;
}

const Incident * FaultManager::find_incident(const std::string & incident_id) const
{
  const auto it = incidents_.find(incident_id);
  return it == incidents_.end() ? nullptr : &it->second;
}

nlohmann::json FaultManager::snapshot() const
{
  nlohmann::json faults = nlohmann::json::array();
  for (const auto & [id, ep] : open_) {
    faults.push_back(ep);
  }
  nlohmann::json incidents = nlohmann::json::array();
  for (const auto & [id, inc] : incidents_) {
    if (inc.state != IncidentState::Closed) {
      incidents.push_back(inc);
    }
  }
  nlohmann::json states = nlohmann::json::object();
  for (const auto & code : state_order_) {
    std::visit([&](const auto & v) { states[code] = v; }, state_values_.at(code));
  }
  return nlohmann::json{
    {"schema", "jarvis.fault_manager.state/v1"},
    {"faults", std::move(faults)},
    {"incidents", std::move(incidents)},
    {"states", std::move(states)},
  };
}

Effects FaultManager::restore(const nlohmann::json & state, Timestamp now)
{
  if (state.value("schema", "") != "jarvis.fault_manager.state/v1") {
    throw std::invalid_argument("unknown fault manager state schema");
  }
  open_.clear();
  open_by_key_.clear();
  incidents_.clear();
  state_values_.clear();
  state_order_.clear();

  for (const auto & jf : state.at("faults")) {
    FaultEpisode ep = jf.get<FaultEpisode>();
    if (!ep.is_open()) {
      continue;
    }
    Event probe;
    probe.code = ep.code;
    probe.source = ep.source;
    open_by_key_[key_for(probe)] = ep.fault_id;
    open_.emplace(ep.fault_id, std::move(ep));
  }
  for (const auto & ji : state.at("incidents")) {
    Incident inc = ji.get<Incident>();
    incidents_.emplace(inc.incident_id, std::move(inc));
  }
  if (state.contains("states")) {
    for (const auto & [code, value] : attrs_from_json(state.at("states"))) {
      state_values_[code] = value;
      state_order_.push_back(code);
    }
  }

  Effects fx;
  fx.emplace_back(EmitEvent{make_event(
    Kind::System, "jarvis.fault_manager.restored", Severity::Info,
    "Restored " + std::to_string(open_.size()) + " open faults and " +
      std::to_string(incidents_.size()) + " incidents",
    {{"open_faults", static_cast<std::int64_t>(open_.size())},
     {"incidents", static_cast<std::int64_t>(incidents_.size())}},
    {}, {}, {}, now)});
  fx.emplace_back(FaultsChanged{});
  return fx;
}

}  // namespace jarvis_core
