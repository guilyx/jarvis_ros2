// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// `jarvis`: offline tools, no ROS needed (docs/design/05-rules-and-recovery.md §12).
//
//   jarvis lint [--rules DIR] [--plans DIR] [--catalog FILE] [--actuators a,b,...]
//   jarvis backtest --rules DIR [--plans DIR] [--robot ID] [--json] [--expect-same] EVENTS.jsonl
//
// Exit codes: 0 fine, 1 errors found (or, with --expect-same, decisions differ), 2 usage.

#include <fstream>
#include <iostream>
#include <jarvis_core/tooling.hpp>
#include <jarvis_core/util.hpp>
#include <map>
#include <sstream>
#include <string>
#include <vector>

using namespace jarvis_core;

namespace
{

constexpr const char * kUsage = R"(usage:
  jarvis lint [--rules DIR] [--plans DIR] [--catalog FILE] [--actuators a,b,...]
      Checks rules, plans and the fault catalog, and how they fit together.
  jarvis backtest --rules DIR [--plans DIR] [--robot ID] [--json] [--expect-same] EVENTS.jsonl
      Replays recorded events (JSON Lines, e.g. from `ros2 jarvis export`) through the rules
      and, with --plans, the plans in dry run; compares with the decisions recorded live.
)";

struct Args
{
  std::map<std::string, std::string> options;
  std::vector<std::string> positional;
};

int usage(const std::string & problem)
{
  std::cerr << "jarvis: " << problem << "\n" << kUsage;
  return 2;
}

Args parse(int argc, char ** argv, const std::set<std::string> & flags)
{
  Args a;
  for (int i = 2; i < argc; ++i) {
    std::string arg = argv[i];
    if (arg.starts_with("--")) {
      const auto eq = arg.find('=');
      std::string name = arg.substr(2, eq == std::string::npos ? std::string::npos : eq - 2);
      if (flags.count(name)) {
        a.options[name] = "true";
      } else if (eq != std::string::npos) {
        a.options[name] = arg.substr(eq + 1);
      } else if (i + 1 < argc) {
        a.options[name] = argv[++i];
      } else {
        throw std::invalid_argument("--" + name + " needs a value");
      }
    } else {
      a.positional.push_back(std::move(arg));
    }
  }
  return a;
}

std::string get(const Args & a, const std::string & name)
{
  const auto it = a.options.find(name);
  return it == a.options.end() ? "" : it->second;
}

int run_lint(const Args & a)
{
  LintInput in;
  in.rules_dir = get(a, "rules");
  in.plans_dir = get(a, "plans");
  in.catalog_path = get(a, "catalog");
  if (in.rules_dir.empty() && in.plans_dir.empty() && in.catalog_path.empty()) {
    return usage("lint needs at least one of --rules, --plans, --catalog");
  }
  if (a.options.count("actuators")) {
    in.actuators.clear();
    std::stringstream list(get(a, "actuators"));
    for (std::string action; std::getline(list, action, ',');) {
      if (!action.empty()) {
        in.actuators.insert(action);
      }
    }
  }
  const auto report = lint(in);
  for (const auto & f : report.findings) {
    std::cout << (f.error ? "error: " : "warning: ") << f.message << "\n";
  }
  std::cout << report.rules << " rules, " << report.plans << " plans, " << report.faults
            << " cataloged faults: " << report.errors() << " error(s), "
            << report.findings.size() - report.errors() << " warning(s)\n";
  return report.ok() ? 0 : 1;
}

int run_backtest(const Args & a)
{
  if (get(a, "rules").empty() || a.positional.size() != 1) {
    return usage("backtest needs --rules DIR and one events file");
  }
  std::ifstream in(a.positional[0]);
  if (!in) {
    std::cerr << "jarvis: cannot open " << a.positional[0] << "\n";
    return 1;
  }
  auto events = read_event_lines(in);
  auto rules = RuleSet::from_directory(get(a, "rules"));
  std::optional<PlanSet> plans;
  if (!get(a, "plans").empty()) {
    plans = PlanSet::from_directory(get(a, "plans"));
  }
  BacktestOptions options;
  if (!get(a, "robot").empty()) {
    options.robot_id = get(a, "robot");
  }
  options.simulate_recovery = plans.has_value();
  const auto report =
    backtest(std::move(rules), plans ? &*plans : nullptr, std::move(events), options);

  if (a.options.count("json")) {
    std::cout << report.to_json().dump(2) << "\n";
  } else {
    std::cout << "replayed " << report.events << " events";
    if (report.events) {
      std::cout << " from " << format_iso8601(report.from) << " to " << format_iso8601(report.to);
    }
    std::cout << "\n\nrules\n";
    for (const auto & s : report.stats) {
      std::cout << "  " << s.id << ": " << s.fires << " fired, " << s.dry_fires << " in dry run, "
                << s.suppressed << " suppressed, " << s.cooled_down << " cooled down, "
                << s.guard_blocked << " blocked by guard\n";
    }
    if (!report.recoveries.empty()) {
      std::cout << "\nrecoveries" << (plans ? " (dry run)" : " (not simulated: no --plans)")
                << "\n";
      for (const auto & r : report.recoveries) {
        std::cout << "  " << format_iso8601(r.at) << "  " << r.rule << " -> " << r.plan;
        for (const auto & [k, v] : r.args) {
          std::cout << " " << k << "=" << v;
        }
        if (plans) {
          std::cout << "  " << to_string(r.state) << (r.reason.empty() ? "" : ": " + r.reason);
        }
        std::cout << "\n";
      }
    }
    std::cout << "\nlive decisions\n";
    if (!report.compared()) {
      std::cout << "  none recorded: nothing to compare\n";
    } else if (report.reproduces()) {
      std::cout << "  all " << report.recorded << " reproduced\n";
    } else {
      for (const auto & m : report.missing) {
        std::cout << "  - only live:   " << m << "\n";
      }
      for (const auto & x : report.extra) {
        std::cout << "  + only replay: " << x << "\n";
      }
    }
  }
  if (a.options.count("expect-same") && report.compared() && !report.reproduces()) {
    return 1;
  }
  return 0;
}

}  // namespace

int main(int argc, char ** argv)
{
  if (argc < 2 || std::string(argv[1]) == "-h" || std::string(argv[1]) == "--help") {
    std::cout << kUsage;
    return argc < 2 ? 2 : 0;
  }
  const std::string command = argv[1];
  try {
    if (command == "lint") {
      return run_lint(parse(argc, argv, {}));
    }
    if (command == "backtest") {
      return run_backtest(parse(argc, argv, {"json", "expect-same"}));
    }
  } catch (const std::invalid_argument & e) {
    return usage(e.what());
  } catch (const std::exception & e) {
    std::cerr << "jarvis: " << e.what() << "\n";
    return 1;
  }
  return usage("unknown command '" + command + "'");
}
