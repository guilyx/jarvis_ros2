// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// `jarvis-bench`: the core's hot paths against the targets of docs/design/01-requirements.md §5,
// without ROS. Synthetic events from many sources on a simulated clock, timed on the wall clock.
//
//   jarvis-bench [--events N] [--rate R] [--sources N] [--rules N] [--json] [--check]
//
//   hub     EventHub::ingest of N events at R events/s (simulated): throughput, per-event latency
//   flood   one source at 20x that rate next to fault clears: the bucket holds, clears all pass
//   rules   RuleEngine::on_event with N rules (event, count and sequence patterns): per event
//   faults  FaultManager::on_event on raises and clears of 50 faults
//
// --check exits 1 when a target is missed: ingest >= 10 000 events/s (NFR-1), p99 <= 5 ms per
// event with the rules (NFR-3), the flood suppressed, every clear accepted, the buffer in budget.

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <jarvis_core/event_hub.hpp>
#include <jarvis_core/fault_catalog.hpp>
#include <jarvis_core/fault_manager.hpp>
#include <jarvis_core/rule_engine.hpp>
#include <jarvis_core/rules.hpp>
#include <map>
#include <nlohmann/json.hpp>
#include <sstream>
#include <string>
#include <vector>

using namespace jarvis_core;
using Clock = std::chrono::steady_clock;

namespace
{

constexpr const char * kUsage =
  "usage: jarvis-bench [--events N] [--rate R] [--sources N] [--rules N] [--json] [--check]\n";

struct Options
{
  std::size_t events{200'000};
  double rate{10'000.0};
  std::size_t sources{500};
  std::size_t rules{200};
  bool json{false};
  bool check{false};
};

double us(std::int64_t ns) { return static_cast<double>(ns) / 1e3; }
double mb(std::size_t bytes) { return static_cast<double>(bytes) / 1048576.0; }

struct Timing
{
  std::vector<std::int64_t> ns;  // per call
  double wall_s{0.0};

  nlohmann::json summary() const
  {
    auto sorted = ns;
    std::sort(sorted.begin(), sorted.end());
    auto at = [&](double q) {
      const auto i = static_cast<std::size_t>(q * static_cast<double>(sorted.size()));
      return sorted.empty() ? 0.0 : us(sorted[std::min(sorted.size() - 1, i)]);
    };
    return {
      {"calls", ns.size()},
      {"per_s", wall_s > 0 ? static_cast<double>(ns.size()) / wall_s : 0.0},
      {"p50_us", at(0.50)},
      {"p99_us", at(0.99)},
      {"max_us", sorted.empty() ? 0.0 : us(sorted.back())},
    };
  }
};

template <typename F>
void timed(Timing & t, F && f)
{
  const auto start = Clock::now();
  f();
  t.ns.push_back(
    std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - start).count());
}

constexpr Timestamp kT0 = 1'800'000'000 * kSecond;

/// Events as a fleet of nodes sends them: mostly INFO logs and states, some warnings, a fault
/// raised or cleared every tenth event. Each source keeps under its rate limit.
class Generator
{
public:
  Generator(std::size_t sources, double rate)
  : sources_(sources), step_(static_cast<Duration>(1e9 / rate))
  {
  }

  Event next()
  {
    const std::size_t i = n_ % sources_;
    const std::string app = "app" + std::to_string(i);
    Event e;
    e.id = "bench-" + std::to_string(n_);
    e.stamp = kT0 + static_cast<Timestamp>(n_) * step_;
    e.source = {"bench", "/bench/" + app, "c" + std::to_string(i % 50), "host"};
    e.attrs = {
      {"value", static_cast<std::int64_t>(n_ % 1000)}, {"topic", std::string("/bench/") + app}};
    switch (n_ % 10) {
      case 0:
      case 5: {
        const bool raise = (n_ / 10) % 2 == 0;
        e.kind = raise ? Kind::FaultRaise : Kind::FaultClear;
        e.code = "bench.fault.f" + std::to_string(i % 50);
        e.severity = raise ? Severity::Error : Severity::Info;
        e.message = raise ? "the widget went out of range" : "the widget is back in range";
        break;
      }
      case 1:
      case 2:
        e.kind = Kind::Log;
        e.code = "bench." + app + ".warn";
        e.severity = Severity::Warn;
        e.message = "retrying the request to the widget after a timeout";
        break;
      case 3:
      case 4:
        e.kind = Kind::State;
        e.code = "bench." + app + ".mode";
        e.severity = Severity::Info;
        e.message = (n_ / 10) % 2 ? "ACTIVE" : "IDLE";
        break;
      default:
        e.kind = Kind::Log;
        e.code = "bench." + app + ".log";
        e.severity = Severity::Info;
        e.message = "processed a batch of 128 items in 3.2 ms";
    }
    ++n_;
    return e;
  }

private:
  std::size_t sources_;
  Duration step_;
  std::uint64_t n_{0};
};

std::string rules_yaml(std::size_t n)
{
  std::ostringstream y;
  y << "schema: jarvis.rules/v1\nrules:\n";
  for (std::size_t i = 0; i < n; ++i) {
    const auto f = i % 50;
    y << "  - id: bench-" << i << "\n";
    if (i % 10 < 6) {  // 60 %: one event
      y << "    when: { event: { kind: FAULT_RAISE, code: bench.fault.f" << f
        << ", severity: ERROR } }\n";
      y << "    policy: { cooldown: 1s }\n";
    } else if (i % 10 < 9) {  // 30 %: a count, per component
      y << "    partition_by: [source.component]\n";
      y << "    when: { count: { event: { code: \"bench.app" << i % 500
        << ".*\", severity: WARN }, " << "threshold: 5, within: 10s } }\n";
    } else {  // 10 %: a sequence
      y << "    when:\n      sequence:\n        within: 30s\n        steps:\n";
      y << "          - { id: a, event: { code: bench.fault.f" << f << " } }\n";
      y << "          - { id: b, event: { code: bench.fault.f" << (f + 1) % 50 << " } }\n";
    }
    y << "    then: [ { notify: { message: \"bench {{ event.code }}\" } } ]\n";
  }
  return y.str();
}

nlohmann::json bench_hub(const Options & o)
{
  EventHub hub(EventHubConfig{}, 1);
  Generator gen(o.sources, o.rate);
  Timing t;
  const auto start = Clock::now();
  for (std::size_t n = 0; n < o.events; ++n) {
    auto e = gen.next();
    const auto now = e.stamp + kMillisecond;
    timed(t, [&] { hub.ingest(std::move(e), now); });
  }
  t.wall_s = std::chrono::duration<double>(Clock::now() - start).count();
  const auto & s = hub.stats();
  auto out = t.summary();
  out.update(
    {{"accepted", s.accepted},
     {"suppressed", s.suppressed},
     {"rejected", s.rejected},
     {"buffered_events", s.buffered_events},
     {"buffered_mb", mb(s.buffered_bytes)},
     {"evicted", s.evicted}});
  return out;
}

nlohmann::json bench_flood(const Options & o)
{
  EventHubConfig config;
  EventHub hub(config, 1);
  const double flood_rate = 20 * o.rate;
  const Duration step = static_cast<Duration>(1e9 / flood_rate);
  const std::size_t n = static_cast<std::size_t>(flood_rate * 5);  // 5 s of flood
  std::uint64_t clears = 0, clears_accepted = 0;
  Timing t;
  const auto start = Clock::now();
  for (std::size_t i = 0; i < n; ++i) {
    Event e;
    e.id = "flood-" + std::to_string(i);
    e.stamp = kT0 + static_cast<Timestamp>(i) * step;
    e.source = {"bench", "/bench/chatty", "", "host"};
    const bool clear = i % 100 == 0;  // 1 %: Jarvis must never drop these
    e.kind = clear ? Kind::FaultClear : Kind::Log;
    e.code = clear ? "bench.chatty.fault" : "bench.chatty.log";
    e.severity = clear ? Severity::Info : Severity::Warn;
    e.message = "the same line again and again";
    bool accepted = false;
    timed(t, [&] {
      accepted = hub.ingest(std::move(e), kT0 + static_cast<Timestamp>(i) * step).accepted;
    });
    clears += clear;
    clears_accepted += clear && accepted;
  }
  t.wall_s = std::chrono::duration<double>(Clock::now() - start).count();
  const auto summaries = hub.advance_to(kT0 + 6 * kSecond).size();
  const auto & s = hub.stats();
  const double allowed = config.rate_limit_burst + config.rate_limit_per_sec * 5;  // 5 s
  auto out = t.summary();
  out.update(
    {{"flood_per_s", flood_rate},
     {"accepted", s.accepted},
     {"suppressed", s.suppressed},
     {"allowed_by_bucket", allowed + static_cast<double>(clears)},
     {"clears", clears},
     {"clears_accepted", clears_accepted},
     {"summaries", summaries},
     {"buffered_mb", mb(s.buffered_bytes)},
     {"budget_mb", mb(config.buffer_capacity_bytes)}});
  return out;
}

nlohmann::json bench_rules(const Options & o)
{
  RuleEngine engine(
    {"bench", "/jarvis/rule_engine", 1000}, RuleSet::from_yaml(rules_yaml(o.rules)));
  EventHub hub(EventHubConfig{}, 1);  // the engine sees sequenced events, as on /jarvis/events
  Generator gen(o.sources, o.rate);
  Timing t;
  std::size_t effects = 0;
  for (std::size_t n = 0; n < o.events; ++n) {
    auto e = gen.next();
    const auto now = e.stamp + kMillisecond;
    for (auto & published : hub.ingest(std::move(e), now).publish) {
      timed(t, [&] { effects += engine.on_event(published, now).size(); });
    }
  }
  for (const auto ns : t.ns) {  // the engine's time only, not the hub's
    t.wall_s += static_cast<double>(ns) / 1e9;
  }
  auto out = t.summary();
  out.update({{"rules", o.rules}, {"effects", effects}});
  return out;
}

nlohmann::json bench_faults(const Options & o)
{
  FaultManager fm(FaultCatalog{}, FaultManagerConfig{}, 1);
  EventHub hub(EventHubConfig{}, 1);
  Generator gen(o.sources, o.rate);
  Timing t;
  for (std::size_t n = 0; n < o.events; ++n) {
    auto e = gen.next();
    if (e.kind != Kind::FaultRaise && e.kind != Kind::FaultClear) {
      continue;
    }
    const auto now = e.stamp + kMillisecond;
    for (auto & published : hub.ingest(std::move(e), now).publish) {
      timed(t, [&] { fm.on_event(published, now); });
    }
  }
  for (const auto ns : t.ns) {  // the fault manager's time only
    t.wall_s += static_cast<double>(ns) / 1e9;
  }
  auto out = t.summary();
  out.update({{"open_faults", fm.faults().size()}, {"incidents", fm.incidents(true).size()}});
  return out;
}

struct Check
{
  std::string what;
  bool ok;
};

std::vector<Check> checks(const nlohmann::json & r, const Options & o)
{
  std::vector<Check> out;
  out.push_back({"hub: >= 10000 events/s (NFR-1)", r["hub"]["per_s"].get<double>() >= 10'000.0});
  out.push_back({"hub: nothing suppressed at a fleet's normal rates", r["hub"]["suppressed"] == 0});
  const auto & f = r["flood"];
  out.push_back(
    {"flood: the bucket holds",
     f["accepted"].get<double>() <= f["allowed_by_bucket"].get<double>() + 1.0});
  out.push_back({"flood: every fault clear accepted", f["clears_accepted"] == f["clears"]});
  out.push_back(
    {"flood: buffer within budget",
     f["buffered_mb"].get<double>() <= f["budget_mb"].get<double>()});
  out.push_back(
    {"rules: p99 <= 5 ms per event with " + std::to_string(o.rules) + " rules (NFR-3)",
     r["rules"]["p99_us"].get<double>() <= 5'000.0});
  out.push_back({"faults: >= 10000 events/s", r["faults"]["per_s"].get<double>() >= 10'000.0});
  out.push_back(
    {"faults: flapping faults keep the incidents bounded",
     r["faults"]["incidents"].get<std::size_t>() <=
       FaultManagerConfig{}.max_resolved_incidents + 50});
  return out;
}

void print_table(const nlohmann::json & r)
{
  std::cout << std::left << std::setw(8) << "bench" << std::right << std::setw(10) << "calls"
            << std::setw(12) << "per s" << std::setw(10) << "p50 us" << std::setw(10) << "p99 us"
            << std::setw(10) << "max us" << "  notes\n";
  for (const auto & name : {"hub", "flood", "rules", "faults"}) {
    const auto & b = r[name];
    std::cout << std::left << std::setw(8) << name << std::right << std::setw(10)
              << b["calls"].get<std::size_t>() << std::setw(12) << std::fixed
              << std::setprecision(0) << b["per_s"].get<double>() << std::setw(10)
              << std::setprecision(1) << b["p50_us"].get<double>() << std::setw(10)
              << b["p99_us"].get<double>() << std::setw(10) << b["max_us"].get<double>() << "  ";
    if (std::string(name) == "hub") {
      std::cout << b["accepted"] << " accepted, " << b["suppressed"] << " suppressed, "
                << std::setprecision(1) << b["buffered_mb"].get<double>() << " MB buffered";
    } else if (std::string(name) == "flood") {
      std::cout << b["accepted"] << " of " << b["calls"] << " accepted at " << std::setprecision(0)
                << b["flood_per_s"].get<double>() << "/s, " << b["clears_accepted"] << "/"
                << b["clears"] << " clears";
    } else if (std::string(name) == "rules") {
      std::cout << b["rules"] << " rules, " << b["effects"] << " effects";
    } else {
      std::cout << b["open_faults"] << " open faults, " << b["incidents"] << " incidents";
    }
    std::cout << "\n";
  }
}

}  // namespace

int main(int argc, char ** argv)
{
  Options o;
  for (int i = 1; i < argc; ++i) {
    const std::string a = argv[i];
    auto value = [&]() -> std::string {
      if (i + 1 >= argc) {
        std::cerr << a << " needs a value\n" << kUsage;
        std::exit(2);
      }
      return argv[++i];
    };
    try {
      if (a == "--events") {
        o.events = std::stoul(value());
      } else if (a == "--rate") {
        o.rate = std::stod(value());
      } else if (a == "--sources") {
        o.sources = std::max<std::size_t>(1, std::stoul(value()));
      } else if (a == "--rules") {
        o.rules = std::stoul(value());
      } else if (a == "--json") {
        o.json = true;
      } else if (a == "--check") {
        o.check = true;
      } else {
        std::cerr << kUsage;
        return a == "--help" || a == "-h" ? 0 : 2;
      }
    } catch (const std::exception &) {
      std::cerr << a << ": not a number\n" << kUsage;
      return 2;
    }
  }
  if (o.rate <= 0.0) {
    std::cerr << "--rate must be > 0\n";
    return 2;
  }

  nlohmann::json r = {
    {"hub", bench_hub(o)},
    {"flood", bench_flood(o)},
    {"rules", bench_rules(o)},
    {"faults", bench_faults(o)}};
  const auto results = checks(r, o);
  if (o.json) {
    for (const auto & c : results) {
      r["checks"][c.what] = c.ok;
    }
    std::cout << r.dump(1) << "\n";
  } else {
    print_table(r);
    for (const auto & c : results) {
      std::cout << (c.ok ? "  ok    " : "  MISS  ") << c.what << "\n";
    }
  }
  const bool all =
    std::all_of(results.begin(), results.end(), [](const Check & c) { return c.ok; });
  return o.check && !all ? 1 : 0;
}
