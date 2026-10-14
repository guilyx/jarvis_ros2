// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_bridges/watchdogs.hpp"

#include <cmath>
#include <cstdio>
#include <lifecycle_msgs/msg/state.hpp>

#include "jarvis_bridges/translators.hpp"

namespace jarvis_bridges
{

using jarvis_core::Kind;
using lifecycle_msgs::msg::State;

namespace
{

std::string seconds(Duration d)
{
  char buf[32];
  std::snprintf(buf, sizeof(buf), "%.1f s", static_cast<double>(d) / 1e9);
  return buf;
}

std::string hz(double rate)
{
  char buf[32];
  std::snprintf(buf, sizeof(buf), "%.1f Hz", rate);
  return buf;
}

}  // namespace

std::string base_name(std::string_view name)
{
  while (!name.empty() && name.back() == '/') {
    name.remove_suffix(1);
  }
  const auto slash = name.rfind('/');
  return std::string(slash == std::string_view::npos ? name : name.substr(slash + 1));
}

// -- topic watchdog -------------------------------------------------------------------------------

TopicWatch::TopicWatch(TopicWatchConfig config, Timestamp started)
: config_(std::move(config)), started_(started)
{
  if (config_.component.empty()) {
    config_.component = to_segment(base_name(config_.topic));
  }
}

Event TopicWatch::make(Kind kind, const std::string & code, Severity severity, Timestamp at) const
{
  Event e;
  e.stamp = at;
  e.kind = kind;
  e.code = code;
  e.severity = severity;
  e.source.component = config_.component;
  e.attrs["topic"] = config_.topic;
  return e;
}

double TopicWatch::rate_hz(Timestamp now)
{
  while (!arrivals_.empty() && now - arrivals_.front() > config_.rate_window) {
    arrivals_.pop_front();
  }
  return static_cast<double>(arrivals_.size()) / (static_cast<double>(config_.rate_window) / 1e9);
}

std::vector<Event> TopicWatch::on_message(Timestamp now)
{
  std::vector<Event> out;
  last_ = now;
  arrivals_.push_back(now);
  if (stale_) {
    stale_ = false;
    Event e = make(Kind::FaultClear, "ros.topic.stale", Severity::Info, now);
    e.message = config_.topic + ": messages again";
    out.push_back(std::move(e));
  }
  return out;
}

std::vector<Event> TopicWatch::tick(Timestamp now, std::size_t publishers)
{
  std::vector<Event> out;
  if (config_.stale_after > 0 && !stale_) {
    const Timestamp since = last_ ? *last_ : started_;
    const Duration limit =
      last_ ? config_.stale_after : std::max(config_.stale_after, config_.startup_grace);
    if (now - since >= limit) {
      stale_ = true;
      Event e = make(Kind::FaultRaise, "ros.topic.stale", config_.severity, now);
      e.attrs["silence_ms"] = static_cast<std::int64_t>((now - since) / jarvis_core::kMillisecond);
      e.attrs["publishers"] = static_cast<std::int64_t>(publishers);
      e.attrs["reason"] = std::string(publishers == 0 ? "no_publisher" : "silent");
      e.message = publishers == 0 ? config_.topic + ": no publisher for " + seconds(now - since)
                                  : config_.topic + ": no message for " + seconds(now - since) +
                                      " (" + std::to_string(publishers) + " publisher" +
                                      (publishers == 1 ? "" : "s") + ")";
      out.push_back(std::move(e));
    }
  }
  if (config_.min_rate_hz > 0.0 && now - started_ >= config_.startup_grace + config_.rate_window) {
    const double rate = rate_hz(now);
    if (!slow_ && !stale_ && last_ && rate < config_.min_rate_hz) {
      slow_ = true;
      Event e = make(Kind::FaultRaise, "ros.topic.slow", Severity::Warn, now);
      e.attrs["rate_hz"] = std::round(rate * 10.0) / 10.0;
      e.attrs["min_rate_hz"] = config_.min_rate_hz;
      e.message =
        config_.topic + ": " + hz(rate) + ", expected at least " + hz(config_.min_rate_hz);
      out.push_back(std::move(e));
    } else if (slow_ && rate >= config_.min_rate_hz) {
      slow_ = false;
      Event e = make(Kind::FaultClear, "ros.topic.slow", Severity::Info, now);
      e.attrs["rate_hz"] = std::round(rate * 10.0) / 10.0;
      e.message = config_.topic + ": back to " + hz(rate);
      out.push_back(std::move(e));
    }
  }
  return out;
}

// -- node watchdog --------------------------------------------------------------------------------

NodeWatchdog::NodeWatchdog(NodeWatchConfig config, Timestamp started) : config_(std::move(config))
{
  for (const auto & n : config_.nodes) {
    nodes_[n].last_seen = started;
  }
}

std::vector<Event> NodeWatchdog::tick(Timestamp now, const std::set<std::string> & present)
{
  std::vector<Event> out;
  for (auto & [name, t] : nodes_) {
    const bool here = present.count(name) != 0;
    if (here) {
      t.last_seen = now;
      if (!t.raised) {
        continue;
      }
    } else if (t.raised || now - t.last_seen < config_.grace) {
      continue;
    }
    Event e;
    e.stamp = now;
    e.code = "ros.node.missing";
    e.source.component = to_segment(base_name(name));
    e.attrs["node"] = name;
    if (here) {
      t.raised = false;
      e.kind = Kind::FaultClear;
      e.severity = Severity::Info;
      e.message = name + " is back";
    } else {
      t.raised = true;
      e.kind = Kind::FaultRaise;
      e.severity = config_.severity;
      e.attrs["missing_ms"] =
        static_cast<std::int64_t>((now - t.last_seen) / jarvis_core::kMillisecond);
      e.message = name + " is not on the graph (for " + seconds(now - t.last_seen) + ")";
    }
    out.push_back(std::move(e));
  }
  return out;
}

// -- lifecycle ------------------------------------------------------------------------------------

std::string LifecycleTranslator::state_code(const std::string & node)
{
  return make_code("ros.lifecycle", to_code(node) + ".state");
}

Event LifecycleTranslator::make(
  const std::string & node, Kind kind, std::string code, Severity severity, Timestamp at) const
{
  Event e;
  e.stamp = at;
  e.kind = kind;
  e.code = std::move(code);
  e.severity = severity;
  e.source.component = to_segment(base_name(node));
  e.attrs["node"] = node;
  return e;
}

std::vector<Event> LifecycleTranslator::on_state(
  const std::string & node, std::uint8_t id, const std::string & label, Timestamp now)
{
  std::vector<Event> out;
  if (id < State::PRIMARY_STATE_UNCONFIGURED || id > State::PRIMARY_STATE_FINALIZED) {
    return out;
  }
  auto & t = nodes_[node];
  if (t.primary != 0) {
    return out;  // a transition already told us more recently
  }
  t.primary = id;
  Event s = make(node, Kind::State, state_code(node), Severity::Info, now);
  s.attrs["value"] = label;
  s.message = node + " is " + label;
  out.push_back(std::move(s));
  return out;
}

std::vector<Event> LifecycleTranslator::on_transition(
  const std::string & node, const lifecycle_msgs::msg::TransitionEvent & ev, Timestamp now)
{
  std::vector<Event> out;
  auto & t = nodes_[node];
  const auto goal = ev.goal_state.id;
  const std::string transition = ev.transition.label;
  if (goal == State::TRANSITION_STATE_ERRORPROCESSING && !t.failed) {
    t.failed = true;
    Event e = make(node, Kind::FaultRaise, "ros.lifecycle.failed", Severity::Error, now);
    e.attrs["transition"] = transition;
    e.attrs["from"] = ev.start_state.label;
    e.message = node + ": '" + transition + "' failed, error processing";
    out.push_back(std::move(e));
  }
  if (goal < State::PRIMARY_STATE_UNCONFIGURED || goal > State::PRIMARY_STATE_FINALIZED) {
    return out;  // intermediate states are not reported
  }
  const auto previous = t.primary;
  t.primary = goal;
  Event s = make(node, Kind::State, state_code(node), Severity::Info, now);
  s.attrs["value"] = ev.goal_state.label;
  s.attrs["transition"] = transition;
  s.message = node + " is " + ev.goal_state.label;
  out.push_back(std::move(s));
  if (goal == State::PRIMARY_STATE_ACTIVE) {
    for (auto [raised, code] :
         {std::pair{&t.inactive, "ros.lifecycle.inactive"},
          std::pair{&t.failed, "ros.lifecycle.failed"}}) {
      if (*raised) {
        *raised = false;
        Event c = make(node, Kind::FaultClear, code, Severity::Info, now);
        c.message = node + " is active again";
        out.push_back(std::move(c));
      }
    }
  } else if (previous == State::PRIMARY_STATE_ACTIVE && !t.inactive) {
    t.inactive = true;
    Event e = make(node, Kind::FaultRaise, "ros.lifecycle.inactive", Severity::Warn, now);
    e.attrs["state"] = ev.goal_state.label;
    e.attrs["transition"] = transition;
    e.message = node + " left active: " + ev.goal_state.label + " after '" + transition + "'";
    out.push_back(std::move(e));
  }
  return out;
}

}  // namespace jarvis_bridges
