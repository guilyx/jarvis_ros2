// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// `jarvis_load`: Jarvis under load, end to end (docs/design/01-requirements.md §5, NFR-1/2/5).
//
//   ros2 run jarvis_ros jarvis_load [--rate 1000] [--seconds 30] [--sources 200] [--faults 2]
//                                   [--burst 0] [--process NAME] [--check] [--max-p99-ms 50]
//                                   [--max-rss-mb 0] [--json FILE]
//
// Publishes synthetic events on /jarvis/ingest as a fleet of `--sources` nodes would (mostly INFO
// logs, some warnings and states, `--faults` % faults raised and cleared), reads them back on
// /jarvis/events (then, with --burst N, N more as fast as it can: a burst to buffer),
// and reports the rate it got through, the ingest -> publish -> delivery latency, what was lost
// (sent, not delivered and not counted in a suppression summary), and the RSS and CPU of the
// Jarvis processes (those whose executable's name has `--process`, by default the containers).
// With --check it exits 1 when something was lost, p99 is over --max-p99-ms, less than 99 % of the
// rate got through, or the RSS is over --max-rss-mb (0: not checked).

#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <jarvis_interfaces/msg/event.hpp>
#include <mutex>
#include <nlohmann/json.hpp>
#include <optional>
#include <rclcpp/rclcpp.hpp>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

using jarvis_interfaces::msg::Attribute;
using jarvis_interfaces::msg::Event;
using SteadyClock = std::chrono::steady_clock;

namespace
{

constexpr const char * kUsage =
  "usage: jarvis_load [--rate R] [--seconds S] [--sources N] [--faults PERCENT] [--burst N]\n"
  "                   [--process NAME] [--check] [--max-p99-ms MS] [--max-rss-mb MB] [--json "
  "FILE]\n";

struct Options
{
  double rate{1000.0};
  double seconds{30.0};
  std::size_t sources{200};
  std::uint64_t faults{2};  // percent of the events
  std::uint64_t burst{0};
  std::string process{"component_container"};
  bool check{false};
  double max_p99_ms{50.0};
  double max_rss_mb{0.0};
  std::string json;
};

/// RSS and CPU of the processes whose executable's name has `needle`, summed, from /proc.
class ProcSampler
{
public:
  explicit ProcSampler(const std::string & needle) : pids_(find(needle)) {}

  const std::vector<int> & pids() const { return pids_; }

  struct Sample
  {
    double t_s;
    double rss_mb;
    double cpu_s;
  };

  std::optional<Sample> sample(double t_s) const
  {
    if (pids_.empty()) {
      return std::nullopt;
    }
    Sample total{t_s, 0.0, 0.0};
    for (const int pid : pids_) {
      const auto one = sample_one(pid);
      if (!one) {
        return std::nullopt;
      }
      total.rss_mb += one->first;
      total.cpu_s += one->second;
    }
    return total;
  }

private:
  static std::optional<std::pair<double, double>> sample_one(int pid)
  {
    std::ifstream status("/proc/" + std::to_string(pid) + "/status");
    double rss_kb = -1;
    for (std::string line; std::getline(status, line);) {
      if (line.rfind("VmRSS:", 0) == 0) {
        rss_kb = std::stod(line.substr(6));
      }
    }
    std::ifstream stat("/proc/" + std::to_string(pid) + "/stat");
    std::string all((std::istreambuf_iterator<char>(stat)), std::istreambuf_iterator<char>());
    const auto close = all.rfind(')');  // the command name may contain spaces
    if (rss_kb < 0 || close == std::string::npos) {
      return std::nullopt;
    }
    std::istringstream rest(all.substr(close + 2));
    std::vector<std::string> fields;
    for (std::string f; rest >> f;) {
      fields.push_back(f);
    }
    if (fields.size() < 13) {
      return std::nullopt;
    }
    const double ticks = static_cast<double>(sysconf(_SC_CLK_TCK));
    // After the name: state(0) ... utime(11) stime(12).
    return std::make_pair(rss_kb / 1024.0, (std::stod(fields[11]) + std::stod(fields[12])) / ticks);
  }

  static std::vector<int> find(const std::string & needle)
  {
    std::vector<int> out;
    if (needle.empty()) {
      return out;
    }
    const auto self = std::to_string(getpid());
    const auto digit = [](unsigned char c) { return std::isdigit(c) != 0; };
    for (const auto & entry : std::filesystem::directory_iterator("/proc")) {
      const auto name = entry.path().filename().string();
      if (name.empty() || !std::all_of(name.begin(), name.end(), digit) || name == self) {
        continue;
      }
      // The executable's name, not its arguments (a launch command names the container too).
      std::ifstream f(entry.path() / "cmdline");
      std::string argv0;
      std::getline(f, argv0, '\0');
      if (std::filesystem::path(argv0).filename().string().find(needle) != std::string::npos) {
        out.push_back(std::stoi(name));
      }
    }
    return out;
  }

  std::vector<int> pids_;
};

Attribute attr_string(const std::string & key, const std::string & value)
{
  Attribute a;
  a.key = key;
  a.type = Attribute::TYPE_STRING;
  a.string_value = value;
  return a;
}

Attribute attr_int(const std::string & key, std::int64_t value)
{
  Attribute a;
  a.key = key;
  a.type = Attribute::TYPE_INT;
  a.int_value = value;
  return a;
}

/// What a fleet sends: `faults` % faults raised and cleared, 8 % warnings, 5 % states, the rest
/// INFO logs.
Event make_event(
  std::uint64_t n, std::size_t sources, std::uint64_t faults, const std::string & run,
  const rclcpp::Time & now)
{
  const std::size_t i = n % sources;
  const std::string app = "app" + std::to_string(i);
  Event e;
  e.id = run + "-" + std::to_string(n);
  e.stamp = now;
  e.source.node = "/load/" + app;
  e.source.component = "load" + std::to_string(i % 20);
  e.attrs = {attr_int("n", static_cast<std::int64_t>(n)), attr_string("topic", "/load/" + app)};
  const auto bucket = n % 100;
  if (bucket < faults) {
    const bool raise = (n / 100) % 2 == 0;
    e.kind = raise ? Event::KIND_FAULT_RAISE : Event::KIND_FAULT_CLEAR;
    e.code = "load.fault.f" + std::to_string(i % 20);
    e.severity = raise ? Event::SEVERITY_ERROR : Event::SEVERITY_INFO;
    e.message = raise ? "the widget went out of range" : "the widget is back in range";
  } else if (bucket < faults + 8) {
    e.kind = Event::KIND_LOG;
    e.code = "load." + app + ".warn";
    e.severity = Event::SEVERITY_WARN;
    e.message = "retrying the request to the widget after a timeout";
  } else if (bucket < faults + 13) {
    e.kind = Event::KIND_STATE;
    e.code = "load." + app + ".mode";
    e.severity = Event::SEVERITY_INFO;
    e.message = (n / 100) % 2 ? "ACTIVE" : "IDLE";
  } else {
    e.kind = Event::KIND_LOG;
    e.code = "load." + app + ".log";
    e.severity = Event::SEVERITY_INFO;
    e.message = "processed a batch of 128 items in 3.2 ms";
  }
  return e;
}

double percentile(std::vector<double> v, double q)
{
  if (v.empty()) {
    return 0.0;
  }
  std::sort(v.begin(), v.end());
  return v[std::min(v.size() - 1, static_cast<std::size_t>(q * static_cast<double>(v.size())))];
}

/// MB per minute, least squares over the samples.
double slope_mb_per_min(const std::vector<ProcSampler::Sample> & s)
{
  if (s.size() < 3) {
    return 0.0;
  }
  double mt = 0, mr = 0;
  for (const auto & x : s) {
    mt += x.t_s;
    mr += x.rss_mb;
  }
  mt /= static_cast<double>(s.size());
  mr /= static_cast<double>(s.size());
  double num = 0, den = 0;
  for (const auto & x : s) {
    num += (x.t_s - mt) * (x.rss_mb - mr);
    den += (x.t_s - mt) * (x.t_s - mt);
  }
  return den > 0 ? num / den * 60.0 : 0.0;
}

bool parse(int argc, char ** argv, Options & o)
{
  for (int i = 1; i < argc; ++i) {
    const std::string a = argv[i];
    if (a == "--ros-args") {
      break;
    }
    auto value = [&]() {
      if (i + 1 >= argc) {
        throw std::invalid_argument(a + " needs a value");
      }
      return std::string(argv[++i]);
    };
    if (a == "--rate") {
      o.rate = std::stod(value());
    } else if (a == "--seconds") {
      o.seconds = std::stod(value());
    } else if (a == "--sources") {
      o.sources = std::max<std::size_t>(1, std::stoul(value()));
    } else if (a == "--faults") {
      o.faults = std::min<std::uint64_t>(87, std::stoul(value()));
    } else if (a == "--burst") {
      o.burst = std::stoul(value());
    } else if (a == "--process") {
      o.process = value();
    } else if (a == "--check") {
      o.check = true;
    } else if (a == "--max-p99-ms") {
      o.max_p99_ms = std::stod(value());
    } else if (a == "--max-rss-mb") {
      o.max_rss_mb = std::stod(value());
    } else if (a == "--json") {
      o.json = value();
    } else {
      throw std::invalid_argument(a == "--help" || a == "-h" ? "" : "unknown option " + a);
    }
  }
  if (o.rate <= 0 || o.seconds <= 0) {
    throw std::invalid_argument("--rate and --seconds must be > 0");
  }
  return true;
}

}  // namespace

int main(int argc, char ** argv)
{
  Options o;
  try {
    parse(argc, argv, o);
  } catch (const std::exception & e) {
    if (*e.what()) {
      std::cerr << e.what() << "\n";
    }
    std::cerr << kUsage;
    return 2;
  }
  rclcpp::init(argc, argv);
  auto node = std::make_shared<rclcpp::Node>("jarvis_load");
  // Deep enough that the tool itself never drops: a burst waits in its history, not in the hub's.
  const auto depth = std::max<std::size_t>(10000, o.burst + o.burst / 10);
  const auto qos = rclcpp::QoS(rclcpp::KeepLast(depth)).reliable().durability_volatile();
  auto pub = node->create_publisher<Event>("/jarvis/ingest", qos);

  const std::string run = "load" + std::to_string(getpid());
  std::mutex mu;
  std::vector<double> latency_ms;
  latency_ms.reserve(static_cast<std::size_t>(o.rate * o.seconds) + 16);
  std::atomic<std::uint64_t> received{0};
  std::atomic<std::uint64_t> suppressed{0};
  auto sub = node->create_subscription<Event>("/jarvis/events", qos, [&](Event::UniquePtr e) {
    if (e->code == "jarvis.ingest.suppressed") {
      for (const auto & a : e->attrs) {
        if (a.key == "producer" && a.string_value.rfind("/load/", 0) != 0) {
          return;
        }
      }
      for (const auto & a : e->attrs) {
        if (a.key == "count") {
          suppressed += static_cast<std::uint64_t>(a.int_value);
        }
      }
      return;
    }
    if (e->id.rfind(run + "-", 0) != 0) {
      return;
    }
    const auto now = node->now();
    const double ms = (now - rclcpp::Time(e->stamp, now.get_clock_type())).seconds() * 1e3;
    std::lock_guard<std::mutex> lock(mu);
    latency_ms.push_back(ms);
    ++received;
  });
  rclcpp::executors::SingleThreadedExecutor executor;
  executor.add_node(node);
  std::thread spin([&] { executor.spin(); });

  const auto deadline = SteadyClock::now() + std::chrono::seconds(15);
  while ((pub->get_subscription_count() == 0 || sub->get_publisher_count() == 0) &&
         SteadyClock::now() < deadline) {
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }
  if (pub->get_subscription_count() == 0 || sub->get_publisher_count() == 0) {
    std::cerr << "nobody reads /jarvis/ingest or publishes /jarvis/events: is Jarvis running?\n";
    executor.cancel();
    spin.join();
    rclcpp::shutdown();
    return 2;
  }
  std::this_thread::sleep_for(std::chrono::seconds(1));  // every endpoint matched

  ProcSampler proc(o.process);
  std::vector<ProcSampler::Sample> samples;
  std::atomic<bool> sending{true};
  const auto t0 = SteadyClock::now();
  auto elapsed = [&] { return std::chrono::duration<double>(SteadyClock::now() - t0).count(); };
  std::thread sampler([&] {
    while (sending) {
      if (auto s = proc.sample(elapsed())) {
        samples.push_back(*s);
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(500));
    }
  });

  // Send in 1 ms ticks: rate / 1000 events each, on schedule (catching up after a stall).
  const auto total = static_cast<std::uint64_t>(o.rate * o.seconds);
  std::uint64_t sent = 0;
  auto next_tick = t0;
  while (sent < total) {
    next_tick += std::chrono::microseconds(1000);
    const auto due = std::min<std::uint64_t>(
      total,
      static_cast<std::uint64_t>(o.rate * std::chrono::duration<double>(next_tick - t0).count()));
    for (; sent < due; ++sent) {
      pub->publish(make_event(sent, o.sources, o.faults, run, node->now()));
    }
    std::this_thread::sleep_until(next_tick);
  }
  const double send_s = elapsed();
  // The burst: as fast as the publisher goes. Its latency is how long the hub takes to catch up,
  // so only the steady part counts for the latency check.
  std::size_t steady_received = 0;
  {
    std::lock_guard<std::mutex> lock(mu);
    steady_received = latency_ms.size();
  }
  const std::uint64_t burst_end = sent + o.burst;
  for (; sent < burst_end; ++sent) {
    pub->publish(make_event(sent, o.sources, o.faults, run, node->now()));
  }
  // Drain: what is in flight, and a suppression summary window (10 s by default) if needed.
  const auto drain_until =
    SteadyClock::now() + std::chrono::seconds(12 + static_cast<int>(o.burst / 2000));
  while (received + suppressed < sent && SteadyClock::now() < drain_until) {
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  sending = false;
  sampler.join();
  executor.cancel();
  spin.join();

  std::vector<double> lat;
  {
    std::lock_guard<std::mutex> lock(mu);
    const auto steady = static_cast<std::ptrdiff_t>(std::min(steady_received, latency_ms.size()));
    lat.assign(latency_ms.begin(), latency_ms.begin() + steady);
  }
  const std::uint64_t got = received, shed = suppressed;
  const std::uint64_t lost = sent > got + shed ? sent - got - shed : 0;
  double rss_start = 0, rss_end = 0, rss_max = 0, cpu = 0;
  if (!samples.empty()) {
    rss_start = samples.front().rss_mb;
    rss_end = samples.back().rss_mb;
    for (const auto & s : samples) {
      rss_max = std::max(rss_max, s.rss_mb);
    }
    const double span = samples.back().t_s - samples.front().t_s;
    cpu = span > 0 ? (samples.back().cpu_s - samples.front().cpu_s) / span * 100.0 : 0.0;
  }
  nlohmann::json r = {
    {"rate", o.rate},
    {"seconds", o.seconds},
    {"sources", o.sources},
    {"fault_percent", o.faults},
    {"burst", o.burst},
    {"sent", sent},
    {"sent_per_s", send_s > 0 ? static_cast<double>(sent - o.burst) / send_s : 0.0},
    {"delivered", got},
    {"suppressed", shed},
    {"lost", lost},
    {"latency_ms",
     {{"p50", percentile(lat, 0.5)},
      {"p99", percentile(lat, 0.99)},
      {"max", lat.empty() ? 0.0 : *std::max_element(lat.begin(), lat.end())}}},
    {"process",
     {{"name", o.process},
      {"pids", proc.pids()},
      {"rss_mb_start", rss_start},
      {"rss_mb_end", rss_end},
      {"rss_mb_max", rss_max},
      {"rss_mb_per_min", slope_mb_per_min(samples)},
      {"cpu_percent", cpu}}},
  };
  std::vector<std::pair<std::string, bool>> checks = {
    {"nothing lost", lost == 0},
    {"p99 <= " + std::to_string(static_cast<int>(o.max_p99_ms)) + " ms (NFR-2)",
     r["latency_ms"]["p99"].get<double>() <= o.max_p99_ms},
    {">= 99 % of the rate got through (NFR-1 at 10000/s)",
     static_cast<double>(got + shed) >= 0.99 * static_cast<double>(sent) &&
       r["sent_per_s"].get<double>() >= 0.99 * o.rate},
  };
  if (o.max_rss_mb > 0) {
    checks.emplace_back(
      "RSS <= " + std::to_string(static_cast<int>(o.max_rss_mb)) + " MB (NFR-5)",
      !proc.pids().empty() && rss_max <= o.max_rss_mb);
  }
  for (const auto & [what, ok] : checks) {
    r["checks"][what] = ok;
  }

  std::cout << std::fixed << std::setprecision(1) << "sent " << sent << " at "
            << r["sent_per_s"].get<double>() << "/s from " << o.sources << " sources; delivered "
            << got << ", suppressed " << shed << ", lost " << lost << "\n"
            << "latency ms: p50 " << r["latency_ms"]["p50"].get<double>() << ", p99 "
            << r["latency_ms"]["p99"].get<double>() << ", max "
            << r["latency_ms"]["max"].get<double>() << "\n";
  if (!proc.pids().empty()) {
    std::cout << o.process << " (" << proc.pids().size() << " process(es)): RSS " << rss_start
              << " -> " << rss_end << " MB (max " << rss_max << ", " << std::setprecision(2)
              << r["process"]["rss_mb_per_min"].get<double>() << " MB/min), CPU "
              << std::setprecision(1) << cpu << " %\n";
  } else {
    std::cout << "no process matches '" << o.process << "': no RSS or CPU\n";
  }
  for (const auto & [what, ok] : checks) {
    std::cout << (ok ? "  ok    " : "  MISS  ") << what << "\n";
  }
  if (!o.json.empty()) {
    std::ofstream(o.json) << r.dump(1) << "\n";
  }
  rclcpp::shutdown();
  const bool all =
    std::all_of(checks.begin(), checks.end(), [](const auto & c) { return c.second; });
  return o.check && !all ? 1 : 0;
}
