// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_core/tooling.hpp"

#include <algorithm>
#include <deque>
#include <istream>
#include <stdexcept>

#include "jarvis_core/fault_catalog.hpp"
#include "jarvis_core/json.hpp"
#include "jarvis_core/util.hpp"

namespace jarvis_core
{

// -- lint -----------------------------------------------------------------------------------------

const std::set<std::string> & default_actuator_actions()
{
  static const std::set<std::string> actions{
    "lifecycle.transition",  "lifecycle.restart",   "service.trigger",
    "service.set_bool",      "service.empty",       "service.call",
    "action.goal",           "param.set",           "bag.snapshot",
    "nav2.manage_lifecycle", "nav2.clear_costmaps", "nav2.cancel_navigation",
    "nav2.navigate_to_pose", "container.restart",   "systemd.restart"};
  return actions;
}

bool LintReport::ok() const { return errors() == 0; }

std::size_t LintReport::errors() const
{
  return static_cast<std::size_t>(
    std::count_if(findings.begin(), findings.end(), [](const LintFinding & f) { return f.error; }));
}

namespace
{

/// The literal value a matcher requires for `field`, if it requires exactly one.
std::optional<std::string> literal(const Matcher & m, const std::string & field)
{
  for (const auto & [f, test] : m.tests) {
    if (f == field && test.op() == Test::Op::Equals) {
      if (const auto * s = std::get_if<std::string>(&test.value())) {
        return *s;
      }
    }
  }
  return std::nullopt;
}

void collect_matchers(const Pattern & p, std::vector<const Matcher *> & out)
{
  if (const auto * e = std::get_if<EventPattern>(&p.v)) {
    out.push_back(&e->matcher);
  } else if (const auto * c = std::get_if<CountPattern>(&p.v)) {
    out.push_back(&c->matcher);
  } else if (const auto * s = std::get_if<SequencePattern>(&p.v)) {
    for (const auto & step : s->steps) {
      out.push_back(&step.matcher);
    }
  } else if (const auto * a = std::get_if<AllPattern>(&p.v)) {
    for (const auto & sub : a->of) {
      collect_matchers(sub, out);
    }
  } else if (const auto * any = std::get_if<AnyPattern>(&p.v)) {
    for (const auto & sub : any->of) {
      collect_matchers(sub, out);
    }
  }
}

std::string where(const Rule & r)
{
  return (r.file.empty() ? "" : r.file + ": ") + "rule '" + r.id + "'";
}

}  // namespace

LintReport lint(const LintInput & input)
{
  LintReport report;
  auto error = [&report](std::string m) { report.findings.push_back({true, std::move(m)}); };
  auto warn = [&report](std::string m) { report.findings.push_back({false, std::move(m)}); };

  std::optional<RuleSet> rules;
  std::optional<PlanSet> plans;
  std::optional<FaultCatalog> catalog;
  if (!input.rules_dir.empty()) {
    try {
      rules = RuleSet::from_directory(input.rules_dir);
      report.rules = rules->rules().size();
    } catch (const RuleError & e) {
      error(e.what());
    }
  }
  if (!input.plans_dir.empty()) {
    try {
      plans = PlanSet::from_directory(input.plans_dir);
      report.plans = plans->plans().size();
      for (auto & w : plans->validate(input.actuators)) {
        warn(std::move(w));
      }
    } catch (const PlanError & e) {
      error(e.what());
    }
  }
  if (!input.catalog_path.empty()) {
    try {
      catalog = FaultCatalog::from_file(input.catalog_path);
      report.faults = catalog->size();
    } catch (const std::exception & e) {
      error(e.what());
    }
  }
  if (!rules) {
    return report;
  }
  for (const auto & r : rules->rules()) {
    // A rule's recover actions must name a plan that accepts it, with the arguments it needs.
    for (const auto & a : r.then) {
      if (a.type != RuleAction::Type::Recover || !plans) {
        continue;
      }
      const auto & plan_t = a.fields.at("plan");
      if (!plan_t.is_literal()) {
        warn(
          where(r) + ": the plan is a template ('" + plan_t.text() +
          "'), checked only at run time");
        continue;
      }
      const Plan * p = plans->find(plan_t.text());
      if (!p) {
        error(where(r) + ": recovers with unknown plan '" + plan_t.text() + "'");
        continue;
      }
      if (p->allowed_requesters.count(Requester::Rule) == 0) {
        error(where(r) + ": plan '" + p->id + "' does not accept RULE requests");
      }
      for (const auto & [name, value] : a.args) {
        if (p->params.count(name) == 0) {
          error(where(r) + ": plan '" + p->id + "' has no parameter '" + name + "'");
        }
      }
      for (const auto & [name, spec] : p->params) {
        if (spec.required && a.args.count(name) == 0) {
          error(where(r) + ": plan '" + p->id + "' needs the argument '" + name + "'");
        }
      }
    }
    // A watched fault that is not in the catalog would run with the default policy.
    if (catalog) {
      std::vector<const Matcher *> matchers;
      collect_matchers(r.when, matchers);
      for (const Matcher * m : matchers) {
        const auto kind = literal(*m, "kind");
        const auto code = literal(*m, "code");
        if (kind && code && kind->starts_with("FAULT_") && !catalog->contains(*code)) {
          warn(
            where(r) + ": watches '" + *code +
            "', which is not in the catalog (it would get the default policy)");
        }
      }
    }
  }
  return report;
}

// -- backtest -------------------------------------------------------------------------------------

nlohmann::json BacktestReport::to_json() const
{
  nlohmann::json m = nlohmann::json::array();
  for (const auto & x : matches) {
    m.push_back(
      {{"at", format_iso8601(x.at)},
       {"rule", x.rule},
       {"match_id", x.match_id},
       {"cause_id", x.cause_id},
       {"correlation_id", x.correlation_id},
       {"dry_run", x.dry_run}});
  }
  nlohmann::json r = nlohmann::json::array();
  for (const auto & x : recoveries) {
    r.push_back(
      {{"at", format_iso8601(x.at)},
       {"rule", x.rule},
       {"plan", x.plan},
       {"args", x.args},
       {"execution_id", x.execution_id},
       {"state", to_string(x.state)},
       {"reason", x.reason}});
  }
  nlohmann::json s = nlohmann::json::array();
  for (const auto & x : stats) {
    s.push_back(
      {{"rule", x.id},
       {"fires", x.fires},
       {"dry_fires", x.dry_fires},
       {"suppressed", x.suppressed},
       {"cooled_down", x.cooled_down},
       {"guard_blocked", x.guard_blocked}});
  }
  return {
    {"events", events},
    {"from", from ? nlohmann::json(format_iso8601(from)) : nlohmann::json(nullptr)},
    {"to", to ? nlohmann::json(format_iso8601(to)) : nlohmann::json(nullptr)},
    {"matches", m},
    {"recoveries", r},
    {"rules", s},
    {"comparison",
     {{"recorded", recorded},
      {"reproduces", compared() ? nlohmann::json(reproduces()) : nlohmann::json(nullptr)},
      {"missing", missing},
      {"extra", extra}}},
  };
}

BacktestReport backtest(
  RuleSet rules, const PlanSet * plans, std::vector<Event> events, const BacktestOptions & options)
{
  BacktestReport report;
  std::stable_sort(events.begin(), events.end(), [](const Event & a, const Event & b) {
    if (a.seq != 0 && b.seq != 0) {
      return a.seq < b.seq;
    }
    return a.recv_stamp < b.recv_stamp;
  });
  RuleEngineConfig ec;
  ec.robot_id = options.robot_id;
  ec.node_name = options.rule_engine_node;
  RuleEngine engine(ec, std::move(rules));
  std::unique_ptr<Orchestrator> orch;
  if (plans && options.simulate_recovery) {
    OrchestratorConfig oc;
    oc.robot_id = options.robot_id;
    oc.node_name = options.recovery_node;
    oc.mode = RecoveryMode::DryRun;
    oc.id_seed = 1;
    orch = std::make_unique<Orchestrator>(oc, *plans);
  }

  std::multiset<std::string> live;
  std::multiset<std::string> replayed;
  std::set<std::string> original;
  auto key = [&original](const std::string & rule, const std::string & cause) {
    return rule + " <- " + (original.count(cause) ? cause : std::string("chained"));
  };
  // The live rule engine's output is always recomputed; the orchestrator's only when simulated
  // (otherwise its recorded outcomes stay in the stream, for rules that react to them).
  for (const auto & e : events) {
    if (
      e.source.node != options.rule_engine_node &&
      (!orch || e.source.node != options.recovery_node)) {
      original.insert(e.id);
    }
  }

  // What the engines emit goes back in, as it would through the hub, after the current event.
  std::deque<Event> queue;
  auto handle = [&](Effects fx, Timestamp t) {
    for (auto & f : fx) {
      if (auto * emit = std::get_if<EmitEvent>(&f)) {
        Event ev = std::move(emit->event);
        if (ev.recv_stamp == 0) {
          ev.recv_stamp = t;
        }
        if (ev.kind == Kind::RuleMatch) {
          BacktestMatch m;
          m.at = t;
          const std::string * rule = attr_string(ev.attrs, "rule");
          m.rule = rule ? *rule : "";
          m.match_id = ev.id;
          m.cause_id = ev.causation_id;
          m.correlation_id = ev.correlation_id;
          const auto dry = ev.attrs.find("dry_run");
          m.dry_run = dry != ev.attrs.end() && std::holds_alternative<bool>(dry->second) &&
                      std::get<bool>(dry->second);
          report.matches.push_back(m);
        }
        queue.push_back(std::move(ev));
      } else if (auto * r = std::get_if<RequestRecovery>(&f)) {
        BacktestRecovery rec;
        rec.at = t;
        rec.rule = r->rule_id;
        rec.plan = r->plan;
        rec.args = r->args;
        if (orch) {
          auto s = orch->submit(to_request(*r), t);
          rec.execution_id = s.execution_id;
          rec.state = s.state;
          rec.reason = s.reason;
          for (auto & of : s.effects) {
            if (auto * oe = std::get_if<EmitEvent>(&of)) {
              Event ev = std::move(oe->event);
              ev.recv_stamp = ev.recv_stamp ? ev.recv_stamp : t;
              queue.push_back(std::move(ev));
            }
          }
        } else {
          rec.reason = "not simulated (no plans)";
        }
        report.recoveries.push_back(std::move(rec));
      }
    }
  };
  auto feed = [&](const Event & e, Timestamp t) {
    handle(engine.on_event(e, t), t);
    if (orch) {
      handle(orch->on_event(e, t), t);
    }
  };

  for (const auto & e : events) {
    if (e.source.node == options.rule_engine_node && e.kind == Kind::RuleMatch) {
      if (const std::string * rule = attr_string(e.attrs, "rule")) {
        live.insert(key(*rule, e.causation_id));
      }
    }
    if (!original.count(e.id)) {
      continue;  // recomputed by this replay
    }
    const Timestamp t = e.recv_stamp ? e.recv_stamp : e.stamp;
    if (report.events == 0) {
      report.from = t;
    }
    report.to = std::max(report.to, t);
    ++report.events;
    handle(engine.advance_to(t), t);
    if (orch) {
      handle(orch->advance_to(t), t);
    }
    feed(e, t);
    for (std::size_t n = 0; !queue.empty() && n < 10'000; ++n) {
      Event next = std::move(queue.front());
      queue.pop_front();
      feed(next, t);
    }
    queue.clear();
  }

  for (const auto & m : report.matches) {
    replayed.insert(key(m.rule, m.cause_id));
  }
  report.recorded = live.size();
  // Multiset differences, both ways.
  std::vector<std::string> missing;
  std::vector<std::string> extra;
  std::set_difference(
    live.begin(), live.end(), replayed.begin(), replayed.end(), std::back_inserter(missing));
  std::set_difference(
    replayed.begin(), replayed.end(), live.begin(), live.end(), std::back_inserter(extra));
  report.missing = std::move(missing);
  report.extra = std::move(extra);
  report.stats = engine.stats();
  return report;
}

std::vector<Event> read_event_lines(std::istream & in)
{
  std::vector<Event> out;
  std::string line;
  for (std::size_t n = 1; std::getline(in, line); ++n) {
    const auto first = line.find_first_not_of(" \t\r");
    if (first == std::string::npos || line[first] == '#') {
      continue;
    }
    try {
      out.push_back(nlohmann::json::parse(line).get<Event>());
    } catch (const std::exception & e) {
      throw std::runtime_error("line " + std::to_string(n) + ": " + e.what());
    }
  }
  return out;
}

}  // namespace jarvis_core
