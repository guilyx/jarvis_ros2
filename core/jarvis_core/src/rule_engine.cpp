// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_core/rule_engine.hpp"

#include <algorithm>
#include <deque>

#include "jarvis_core/json.hpp"

namespace jarvis_core
{

namespace
{

constexpr std::size_t kOriginMemory = 10'000;

// -- pattern states -------------------------------------------------------------------------------

PatternFire single(const Event & e, Timestamp t)
{
  PatternFire f;
  f.at = t;
  f.last = e;
  f.event_ids = {e.id};
  return f;
}

class EventState : public PatternState
{
public:
  explicit EventState(const EventPattern & p) : p_(p) {}
  std::optional<PatternFire> feed(const Event & e, Timestamp t) override
  {
    return p_.matcher.matches(e) ? std::optional<PatternFire>(single(e, t)) : std::nullopt;
  }
  std::optional<PatternFire> tick(Timestamp) override { return std::nullopt; }
  std::optional<Timestamp> deadline() const override { return std::nullopt; }
  bool idle() const override { return true; }
  void reset() override {}

private:
  const EventPattern & p_;
};

/// N matching events within a sliding window. Keeps at most `threshold` timestamps.
class CountState : public PatternState
{
public:
  explicit CountState(const CountPattern & p) : p_(p) {}
  std::optional<PatternFire> feed(const Event & e, Timestamp t) override
  {
    drop_before(t - p_.within);
    if (!p_.matcher.matches(e)) {
      return std::nullopt;
    }
    hits_.push_back({t, e.id});
    if (hits_.size() > static_cast<std::size_t>(p_.threshold)) {
      hits_.pop_front();
    }
    if (hits_.size() < static_cast<std::size_t>(p_.threshold)) {
      return std::nullopt;
    }
    PatternFire f = single(e, t);
    f.count = static_cast<std::int64_t>(hits_.size());
    f.event_ids.clear();
    for (const auto & h : hits_) {
      f.event_ids.push_back(h.second);
    }
    hits_.clear();
    return f;
  }
  std::optional<PatternFire> tick(Timestamp now) override
  {
    drop_before(now - p_.within);
    return std::nullopt;
  }
  std::optional<Timestamp> deadline() const override
  {
    return hits_.empty() ? std::nullopt : std::optional<Timestamp>(hits_.front().first + p_.within);
  }
  bool idle() const override { return hits_.empty(); }
  void reset() override { hits_.clear(); }

private:
  void drop_before(Timestamp horizon)
  {
    while (!hits_.empty() && hits_.front().first < horizon) {
      hits_.pop_front();
    }
  }

  const CountPattern & p_;
  std::deque<std::pair<Timestamp, std::string>> hits_;
};

/// Steps in order within `within` of the first, skip-till-next-match, one partial match at a time
/// (the earliest start wins). Absent steps complete on a timer.
class SequenceState : public PatternState
{
public:
  explicit SequenceState(const SequencePattern & p) : p_(p) {}

  std::optional<PatternFire> feed(const Event & e, Timestamp t) override
  {
    if (auto fired = tick(t)) {
      return fired;  // a due absent step completed the sequence before this event
    }
    if (!active_) {
      if (!p_.steps.front().matcher.matches(e)) {
        return std::nullopt;
      }
      active_ = true;
      start_ = t;
      next_ = 0;
      fire_ = PatternFire{};
      return bind(e, t);
    }
    const SequenceStep & step = p_.steps[next_];
    if (step.absent) {
      if (step.matcher.matches(e)) {
        reset();            // the thing that had to stay away showed up: this attempt failed
        return feed(e, t);  // the event may start a new attempt
      }
      return std::nullopt;
    }
    return step.matcher.matches(e) ? bind(e, t) : std::nullopt;
  }

  std::optional<PatternFire> tick(Timestamp now) override
  {
    if (!active_) {
      return std::nullopt;
    }
    if (
      p_.steps[next_].absent && absent_deadline_ <= now && absent_deadline_ <= start_ + p_.within) {
      fire_.steps[p_.steps[next_].id] = fire_.last;
      ++next_;
      if (next_ == p_.steps.size()) {
        PatternFire f = std::move(fire_);
        f.at = absent_deadline_;
        reset();
        return f;
      }
      if (p_.steps[next_].absent) {
        absent_deadline_ = absent_deadline_ + p_.steps[next_].for_duration;
      }
      return tick(now);
    }
    if (now > start_ + p_.within) {
      reset();
    }
    return std::nullopt;
  }

  std::optional<Timestamp> deadline() const override
  {
    if (!active_) {
      return std::nullopt;
    }
    const Timestamp expiry = start_ + p_.within + 1;
    return p_.steps[next_].absent ? std::min(absent_deadline_, expiry) : expiry;
  }
  bool idle() const override { return !active_; }
  void reset() override
  {
    active_ = false;
    next_ = 0;
    fire_ = PatternFire{};
  }

private:
  std::optional<PatternFire> bind(const Event & e, Timestamp t)
  {
    fire_.steps[p_.steps[next_].id] = e;
    fire_.last = e;
    fire_.event_ids.push_back(e.id);
    ++next_;
    if (next_ == p_.steps.size()) {
      PatternFire f = std::move(fire_);
      f.at = t;
      reset();
      return f;
    }
    if (p_.steps[next_].absent) {
      absent_deadline_ = t + p_.steps[next_].for_duration;
    }
    return std::nullopt;
  }

  const SequencePattern & p_;
  bool active_{false};
  Timestamp start_{0};
  std::size_t next_{0};
  Timestamp absent_deadline_{0};
  PatternFire fire_;
};

/// Every sub-pattern completes, in any order, within `within`.
class AllState : public PatternState
{
public:
  explicit AllState(const AllPattern & p) : p_(p)
  {
    for (const auto & sub : p_.of) {
      subs_.push_back(make_matcher_state(sub));
    }
    done_.resize(subs_.size());
  }
  std::optional<PatternFire> feed(const Event & e, Timestamp t) override
  {
    expire(t);
    for (std::size_t i = 0; i < subs_.size(); ++i) {
      if (auto f = subs_[i]->feed(e, t)) {
        done_[i] = std::move(*f);
      }
    }
    return complete(t);
  }
  std::optional<PatternFire> tick(Timestamp now) override
  {
    for (std::size_t i = 0; i < subs_.size(); ++i) {
      if (auto f = subs_[i]->tick(now)) {
        done_[i] = std::move(*f);
      }
    }
    expire(now);
    return complete(now);
  }
  std::optional<Timestamp> deadline() const override
  {
    std::optional<Timestamp> d;
    auto take = [&d](Timestamp t) { d = d ? std::min(*d, t) : t; };
    for (std::size_t i = 0; i < subs_.size(); ++i) {
      if (auto sd = subs_[i]->deadline()) {
        take(*sd);
      }
      if (done_[i]) {
        take(done_[i]->at + p_.within + 1);
      }
    }
    return d;
  }
  bool idle() const override
  {
    return std::all_of(done_.begin(), done_.end(), [](const auto & d) { return !d; }) &&
           std::all_of(subs_.begin(), subs_.end(), [](const auto & s) { return s->idle(); });
  }
  void reset() override
  {
    for (auto & s : subs_) {
      s->reset();
    }
    std::fill(done_.begin(), done_.end(), std::nullopt);
  }

private:
  void expire(Timestamp now)
  {
    for (auto & d : done_) {
      if (d && now - d->at > p_.within) {
        d.reset();
      }
    }
  }
  std::optional<PatternFire> complete(Timestamp now)
  {
    if (!std::all_of(done_.begin(), done_.end(), [](const auto & d) { return d.has_value(); })) {
      return std::nullopt;
    }
    PatternFire f;
    f.at = now;
    f.count = 0;
    Timestamp latest = done_.front()->at;
    for (auto & d : done_) {
      for (auto & [id, ev] : d->steps) {
        f.steps[id] = ev;
      }
      f.event_ids.insert(f.event_ids.end(), d->event_ids.begin(), d->event_ids.end());
      f.count += d->count;
      if (d->at >= latest) {
        latest = d->at;
        f.last = d->last;
      }
    }
    reset();
    return f;
  }

  const AllPattern & p_;
  std::vector<std::unique_ptr<PatternState>> subs_;
  std::vector<std::optional<PatternFire>> done_;
};

/// The first sub-pattern to complete wins.
class AnyState : public PatternState
{
public:
  explicit AnyState(const AnyPattern & p)
  {
    for (const auto & sub : p.of) {
      subs_.push_back(make_matcher_state(sub));
    }
  }
  std::optional<PatternFire> feed(const Event & e, Timestamp t) override
  {
    for (auto & s : subs_) {
      if (auto f = s->feed(e, t)) {
        reset();
        return f;
      }
    }
    return std::nullopt;
  }
  std::optional<PatternFire> tick(Timestamp now) override
  {
    for (auto & s : subs_) {
      if (auto f = s->tick(now)) {
        reset();
        return f;
      }
    }
    return std::nullopt;
  }
  std::optional<Timestamp> deadline() const override
  {
    std::optional<Timestamp> d;
    for (const auto & s : subs_) {
      if (auto sd = s->deadline()) {
        d = d ? std::min(*d, *sd) : *sd;
      }
    }
    return d;
  }
  bool idle() const override
  {
    return std::all_of(subs_.begin(), subs_.end(), [](const auto & s) { return s->idle(); });
  }
  void reset() override
  {
    for (auto & s : subs_) {
      s->reset();
    }
  }

private:
  std::vector<std::unique_ptr<PatternState>> subs_;
};

/// Does any non-absent matcher of the pattern select this event? Only then is a partition created.
bool interested(const Pattern & p, const Event & e)
{
  if (const auto * ev = std::get_if<EventPattern>(&p.v)) {
    return ev->matcher.matches(e);
  }
  if (const auto * c = std::get_if<CountPattern>(&p.v)) {
    return c->matcher.matches(e);
  }
  if (const auto * s = std::get_if<SequencePattern>(&p.v)) {
    return s->steps.front().matcher.matches(e);
  }
  if (const auto * a = std::get_if<AllPattern>(&p.v)) {
    return std::any_of(
      a->of.begin(), a->of.end(), [&e](const Pattern & sub) { return interested(sub, e); });
  }
  const auto & any = std::get<AnyPattern>(p.v);
  return std::any_of(
    any.of.begin(), any.of.end(), [&e](const Pattern & sub) { return interested(sub, e); });
}

/// Longest window of a pattern: idle partitions are kept 10x that long (cooldowns aside).
Duration window_of(const Pattern & p)
{
  if (const auto * c = std::get_if<CountPattern>(&p.v)) {
    return c->within;
  }
  if (const auto * s = std::get_if<SequencePattern>(&p.v)) {
    return s->within;
  }
  if (const auto * a = std::get_if<AllPattern>(&p.v)) {
    Duration w = a->within;
    for (const auto & sub : a->of) {
      w = std::max(w, window_of(sub));
    }
    return w;
  }
  if (const auto * any = std::get_if<AnyPattern>(&p.v)) {
    Duration w = 0;
    for (const auto & sub : any->of) {
      w = std::max(w, window_of(sub));
    }
    return w;
  }
  return 0;
}

}  // namespace

std::unique_ptr<PatternState> make_matcher_state(const Pattern & pattern)
{
  if (const auto * e = std::get_if<EventPattern>(&pattern.v)) {
    return std::make_unique<EventState>(*e);
  }
  if (const auto * c = std::get_if<CountPattern>(&pattern.v)) {
    return std::make_unique<CountState>(*c);
  }
  if (const auto * s = std::get_if<SequencePattern>(&pattern.v)) {
    return std::make_unique<SequenceState>(*s);
  }
  if (const auto * a = std::get_if<AllPattern>(&pattern.v)) {
    return std::make_unique<AllState>(*a);
  }
  return std::make_unique<AnyState>(std::get<AnyPattern>(pattern.v));
}

// -- engine ---------------------------------------------------------------------------------------

RuleEngine::RuleEngine(RuleEngineConfig config, RuleSet rules)
: config_(std::move(config)), rules_(std::move(rules))
{
  build();
}

void RuleEngine::build()
{
  runtimes_.clear();
  for (const auto & r : rules_.rules()) {
    RuleRuntime rr;
    rr.rule = &r;
    rr.enabled = r.enabled;
    rr.dry_run = r.policy.dry_run;
    rr.horizon = std::max<Duration>(
      {10 * window_of(r.when), r.policy.cooldown,
       r.policy.max_fires ? r.policy.max_fires->per : 0});
    rr.stats.id = r.id;
    runtimes_.push_back(std::move(rr));
  }
  std::stable_sort(
    runtimes_.begin(), runtimes_.end(), [](const RuleRuntime & a, const RuleRuntime & b) {
      return a.rule->policy.priority > b.rule->policy.priority;
    });
}

Effects RuleEngine::reload(RuleSet rules, Timestamp now)
{
  // Keep each surviving rule's fire history, keyed by rule id then partition key.
  std::map<
    std::string, std::map<std::string, std::pair<std::vector<Timestamp>, std::optional<Timestamp>>>>
    history;
  std::map<std::string, RuleStats> old_stats;
  for (const auto & rr : runtimes_) {
    for (const auto & [key, p] : rr.partitions) {
      history[rr.rule->id][key] = {p.fires, p.last_fire};
    }
    old_stats[rr.rule->id] = rr.stats;
  }
  rules_ = std::move(rules);
  build();
  for (auto & rr : runtimes_) {
    if (auto it = old_stats.find(rr.rule->id); it != old_stats.end()) {
      rr.stats = it->second;
      rr.stats.enabled = rr.enabled;
      rr.stats.dry_run = rr.dry_run;
    }
    for (const auto & [key, h] : history[rr.rule->id]) {
      Partition p;
      p.state = make_matcher_state(rr.rule->when);
      p.fires = h.first;
      p.last_fire = h.second;
      p.last_activity = now;
      rr.partitions.emplace(key, std::move(p));
    }
  }
  Event e = make_event(
    Kind::System, "jarvis.rules.reloaded", Severity::Info,
    "Loaded " + std::to_string(rules_.rules().size()) + " rules", Event{}, "", now);
  e.attrs["rules"] = static_cast<std::int64_t>(rules_.rules().size());
  return {EmitEvent{std::move(e)}, PersistState{}};
}

Timestamp RuleEngine::time_of(const RuleRuntime & rr, const Event & e) const
{
  if (rr.rule->time_basis == TimeBasis::Source) {
    return e.stamp;
  }
  return e.recv_stamp != 0 ? e.recv_stamp : e.stamp;
}

std::string RuleEngine::partition_key(
  const Rule & rule, const Event & e, std::map<std::string, std::string> * values) const
{
  std::string key;
  for (const auto & field : rule.partition_by) {
    const auto v = event_field(e, field);
    const std::string text = v ? attr_to_string(*v) : "";
    if (values) {
      (*values)[field] = text;
    }
    key += text;
    key += '\x1f';
  }
  return key;
}

void RuleEngine::track_state(const Event & e)
{
  if (e.kind == Kind::FaultState) {
    const std::string * fault_id = attr_string(e.attrs, "fault_id");
    const std::string * state = attr_string(e.attrs, "state");
    if (fault_id && state) {
      if (is_active_fault_state(*state)) {
        active_faults_[*fault_id] = e.code;
      } else {
        active_faults_.erase(*fault_id);
      }
    }
  } else if (e.kind == Kind::State) {
    states_[e.code] = state_value(e);
  }
  // Remember what our own matches caused, so a rule never feeds on its own consequences.
  if (!e.causation_id.empty() && !e.id.empty()) {
    if (const auto it = origin_.find(e.causation_id); it != origin_.end()) {
      if (origin_.emplace(e.id, it->second).second) {
        origin_order_.push_back(e.id);
      }
    }
  }
  while (origin_order_.size() > kOriginMemory) {
    origin_.erase(origin_order_.front());
    origin_order_.pop_front();
  }
}

void RuleEngine::sync_active_faults(std::map<std::string, std::string> active)
{
  active_faults_ = std::move(active);
}

bool RuleEngine::guard_passes(const Guard & guard) const
{
  return guard.passes(active_faults_, states_);
}

Effects RuleEngine::on_event(const Event & e, Timestamp now)
{
  Effects out;
  track_state(e);
  const auto origin = origin_.find(e.id);
  for (auto & rr : runtimes_) {
    if (!rr.enabled) {
      continue;
    }
    if (origin != origin_.end() && origin->second == rr.rule->id) {
      continue;  // self-loop protection
    }
    const Timestamp t = time_of(rr, e);
    std::map<std::string, std::string> values;
    const std::string key = partition_key(*rr.rule, e, &values);
    auto it = rr.partitions.find(key);
    if (it == rr.partitions.end()) {
      if (!interested(rr.rule->when, e)) {
        continue;
      }
      Partition p;
      p.state = make_matcher_state(rr.rule->when);
      p.values = std::move(values);
      p.last_activity = t;
      it = rr.partitions.emplace(key, std::move(p)).first;
      evict(rr, now);
      it = rr.partitions.find(key);
      if (it == rr.partitions.end()) {
        continue;  // evicted at once: the rule is over its partition cap
      }
    }
    Partition & p = it->second;
    p.last_activity = t;
    if (auto due = p.state->tick(t)) {
      handle_fire(rr, p, key, std::move(*due), out);
    }
    if (auto fired = p.state->feed(e, t)) {
      handle_fire(rr, p, key, std::move(*fired), out);
    }
  }
  return out;
}

Effects RuleEngine::advance_to(Timestamp now)
{
  Effects out;
  for (auto & rr : runtimes_) {
    if (!rr.enabled) {
      continue;
    }
    for (auto & [key, p] : rr.partitions) {
      if (auto due = p.state->tick(now)) {
        handle_fire(rr, p, key, std::move(*due), out);
      }
    }
    evict(rr, now);
  }
  return out;
}

std::optional<Timestamp> RuleEngine::next_deadline() const
{
  std::optional<Timestamp> d;
  for (const auto & rr : runtimes_) {
    if (!rr.enabled) {
      continue;
    }
    for (const auto & [key, p] : rr.partitions) {
      if (auto pd = p.state->deadline()) {
        d = d ? std::min(*d, *pd) : *pd;
      }
    }
  }
  return d;
}

void RuleEngine::evict(RuleRuntime & rr, Timestamp now)
{
  for (auto it = rr.partitions.begin(); it != rr.partitions.end();) {
    const bool stale = it->second.state->idle() && now - it->second.last_activity > rr.horizon;
    it = stale ? rr.partitions.erase(it) : std::next(it);
  }
  while (rr.partitions.size() > config_.max_partitions) {
    auto oldest = std::min_element(
      rr.partitions.begin(), rr.partitions.end(), [](const auto & a, const auto & b) {
        return a.second.last_activity < b.second.last_activity;
      });
    rr.partitions.erase(oldest);
  }
  rr.stats.partitions = rr.partitions.size();
}

void RuleEngine::handle_fire(
  RuleRuntime & rr, Partition & p, const std::string &, PatternFire fire, Effects & out)
{
  const Rule & rule = *rr.rule;
  const Timestamp t = fire.at;
  if (!guard_passes(rule.guard)) {
    ++rr.stats.guard_blocked;
    return;
  }
  if (p.last_fire && rule.policy.cooldown > 0 && t - *p.last_fire < rule.policy.cooldown) {
    ++rr.stats.cooled_down;
    return;
  }
  if (const auto & mf = rule.policy.max_fires) {
    std::erase_if(p.fires, [&](Timestamp f) { return t - f >= mf->per; });
    if (p.fires.empty()) {
      p.suppression_notified = false;
    }
    if (p.fires.size() >= static_cast<std::size_t>(mf->count)) {
      ++rr.stats.suppressed;
      if (!p.suppression_notified) {
        p.suppression_notified = true;
        Event s = make_event(
          Kind::System, "jarvis.rule.suppressed", Severity::Warn,
          "Rule " + rule.id + " fired " + std::to_string(mf->count) + " times within " +
            format_duration(mf->per) + "; suppressed for the rest of the window",
          fire.last, fire.last.id, t);
        s.attrs["rule"] = rule.id;
        s.attrs["max_fires"] = static_cast<std::int64_t>(mf->count);
        for (const auto & [field, value] : p.values) {
          s.attrs["partition." + field] = value;
        }
        out.push_back(EmitEvent{std::move(s)});
      }
      return;
    }
  }

  p.last_fire = t;
  p.fires.push_back(t);
  out.push_back(PersistState{});  // cooldowns and max_fires must survive a restart
  // The match belongs to the incident of its matched events: the last one, else any earlier step
  // (a sequence that completes on an absent step often ends on an event outside the incident).
  Event cause = fire.last;
  for (auto it = fire.steps.begin(); cause.correlation_id.empty() && it != fire.steps.end(); ++it) {
    cause.correlation_id = it->second.correlation_id;
  }
  Event match = make_event(
    Kind::RuleMatch, "jarvis.rule.matched", Severity::Info,
    rule.id + (rr.dry_run ? " matched (dry run)" : " matched"), cause, fire.last.id, t);
  match.attrs["rule"] = rule.id;
  match.attrs["dry_run"] = rr.dry_run;
  match.attrs["count"] = fire.count;
  for (const auto & [field, value] : p.values) {
    match.attrs["partition." + field] = value;
  }
  nlohmann::json payload{{"rule", rule.id}, {"events", fire.event_ids}, {"count", fire.count}};
  for (const auto & [id, ev] : fire.steps) {
    payload["steps"][id] = ev.id;
  }
  match.payload = payload.dump();
  match.payload_schema = "jarvis.rule_match/v1";
  origin_[match.id] = rule.id;
  origin_order_.push_back(match.id);
  out.push_back(EmitEvent{match});

  if (rr.dry_run) {
    ++rr.stats.dry_fires;
    return;
  }
  ++rr.stats.fires;
  run_actions(rr, p, fire, match, out);
}

void RuleEngine::run_actions(
  RuleRuntime & rr, const Partition & p, const PatternFire & fire, const Event & match,
  Effects & out)
{
  const Rule & rule = *rr.rule;
  const std::string robot = !config_.robot_id.empty() ? config_.robot_id : fire.last.source.robot;
  auto lookup = [&](const std::string & var) -> std::optional<std::string> {
    if (var == "robot") {
      return robot;
    }
    if (var == "rule.id") {
      return rule.id;
    }
    if (var == "count") {
      return std::to_string(fire.count);
    }
    if (var.starts_with("partition.")) {
      const auto it = p.values.find(var.substr(10));
      return it == p.values.end() ? std::nullopt : std::optional<std::string>(it->second);
    }
    const Event * source = &fire.last;
    std::string field;
    if (var.starts_with("event.")) {
      field = var.substr(6);
    } else if (var.starts_with("steps.")) {
      const auto rest = var.substr(6);
      const auto dot = rest.find('.');
      const auto it = fire.steps.find(rest.substr(0, dot));
      if (it == fire.steps.end()) {
        return std::nullopt;
      }
      source = &it->second;
      field = rest.substr(dot + 1);
    }
    const auto v = event_field(*source, field);
    return v ? std::optional<std::string>(attr_to_string(*v)) : std::nullopt;
  };

  for (const auto & action : rule.then) {
    std::map<std::string, std::string> fields;
    std::map<std::string, std::string> args;
    std::string failed;
    for (const auto & [name, tmpl] : action.fields) {
      if (auto v = tmpl.render(lookup)) {
        fields[name] = *v;
      } else {
        failed = name + " = \"" + tmpl.text() + "\"";
      }
    }
    for (const auto & [name, tmpl] : action.args) {
      if (auto v = tmpl.render(lookup)) {
        args[name] = *v;
      } else {
        failed = name + " = \"" + tmpl.text() + "\"";
      }
    }
    if (
      action.type == RuleAction::Type::Incident && fields.count("op") &&
      fields["op"] == "escalate" && match.correlation_id.empty()) {
      failed = "no incident to escalate (the matched event has no correlation id)";
    }
    if (!failed.empty()) {
      ++rr.stats.action_failures;
      Event f = make_event(
        Kind::System, "jarvis.rule.action_failed", Severity::Warn,
        "Rule " + rule.id + ": " + std::string(to_string(action.type)) + " not run, " + failed +
          " rendered empty",
        match, match.id, match.stamp);
      f.attrs["rule"] = rule.id;
      f.attrs["action"] = std::string(to_string(action.type));
      out.push_back(EmitEvent{std::move(f)});
      continue;
    }

    switch (action.type) {
      case RuleAction::Type::Recover:
        out.push_back(RequestRecovery{
          rule.id, fields["plan"], std::move(args),
          fields.count("rationale") ? fields["rationale"] : rule.description, match.id,
          match.correlation_id});
        break;
      case RuleAction::Type::Emit: {
        Event e = make_event(
          *parse_kind(fields["kind"]), fields["code"], *parse_severity(fields["severity"]),
          fields["message"], match, match.id, match.stamp);
        for (auto & [k, v] : args) {
          e.attrs[k] = v;
        }
        e.attrs["rule"] = rule.id;
        out.push_back(EmitEvent{std::move(e)});
        break;
      }
      case RuleAction::Type::Incident: {
        const bool open = fields["op"] == "open";
        Event e = make_event(
          Kind::System, open ? "jarvis.incident.open" : "jarvis.incident.escalate",
          open ? fire.last.severity : Severity::Error,
          "Rule " + rule.id + " asked to " + fields["op"], match, match.id, match.stamp);
        e.attrs["rule"] = rule.id;
        out.push_back(EmitEvent{std::move(e)});
        break;
      }
      case RuleAction::Type::Notify: {
        Event e = make_event(
          Kind::System, "jarvis.notify", Severity::Info, fields["message"], match, match.id,
          match.stamp);
        e.attrs["channel"] = fields["channel"];
        e.attrs["rule"] = rule.id;
        out.push_back(EmitEvent{std::move(e)});
        break;
      }
      case RuleAction::Type::AskAgent: {
        Event e = make_event(
          Kind::System, "jarvis.agent.request", Severity::Info, fields["focus"], match, match.id,
          match.stamp);
        e.attrs["focus"] = fields["focus"];
        e.attrs["rule"] = rule.id;
        out.push_back(EmitEvent{std::move(e)});
        break;
      }
    }
  }
}

Event RuleEngine::make_event(
  Kind kind, std::string code, Severity severity, std::string message, const Event & cause,
  const std::string & causation_id, Timestamp at) const
{
  Event e;
  e.id = ids_.uuid7(at);
  e.stamp = at;
  e.source.robot = config_.robot_id;
  e.source.node = config_.node_name;
  e.source.component = cause.source.component;
  e.kind = kind;
  e.code = std::move(code);
  e.severity = severity;
  e.message = std::move(message);
  e.correlation_id = cause.correlation_id;
  e.causation_id = causation_id;
  return e;
}

bool RuleEngine::set_rule_state(
  const std::string & id, std::optional<bool> enabled, std::optional<bool> dry_run)
{
  for (auto & rr : runtimes_) {
    if (rr.rule->id != id) {
      continue;
    }
    if (enabled) {
      rr.enabled = *enabled;
      if (!*enabled) {
        rr.partitions.clear();
      }
    }
    if (dry_run) {
      rr.dry_run = *dry_run;
    }
    return true;
  }
  return false;
}

std::vector<RuleStats> RuleEngine::stats() const
{
  std::vector<RuleStats> out;
  for (const auto & rr : runtimes_) {
    RuleStats s = rr.stats;
    s.enabled = rr.enabled;
    s.dry_run = rr.dry_run;
    s.partitions = rr.partitions.size();
    out.push_back(s);
  }
  return out;
}

nlohmann::json RuleEngine::snapshot() const
{
  nlohmann::json rules = nlohmann::json::object();
  for (const auto & rr : runtimes_) {
    nlohmann::json parts = nlohmann::json::array();
    for (const auto & [key, p] : rr.partitions) {
      if (p.fires.empty() && !p.last_fire) {
        continue;
      }
      parts.push_back(
        {{"key", key},
         {"values", p.values},
         {"fires", p.fires},
         {"last_fire", p.last_fire ? *p.last_fire : 0}});
    }
    rules[rr.rule->id] = {{"enabled", rr.enabled}, {"dry_run", rr.dry_run}, {"partitions", parts}};
  }
  return {{"schema", "jarvis.rule_engine.state/v1"}, {"rules", rules}};
}

void RuleEngine::restore(const nlohmann::json & state)
{
  if (state.value("schema", "") != "jarvis.rule_engine.state/v1") {
    return;
  }
  const auto & rules = state.at("rules");
  for (auto & rr : runtimes_) {
    if (!rules.contains(rr.rule->id)) {
      continue;
    }
    const auto & r = rules.at(rr.rule->id);
    rr.enabled = r.value("enabled", rr.enabled);
    rr.dry_run = r.value("dry_run", rr.dry_run);
    for (const auto & part : r.at("partitions")) {
      Partition p;
      p.state = make_matcher_state(rr.rule->when);
      p.values = part.at("values").get<std::map<std::string, std::string>>();
      p.fires = part.at("fires").get<std::vector<Timestamp>>();
      const Timestamp last = part.value("last_fire", Timestamp{0});
      if (last != 0) {
        p.last_fire = last;
        p.last_activity = last;
      }
      rr.partitions[part.at("key").get<std::string>()] = std::move(p);
    }
  }
}

}  // namespace jarvis_core
