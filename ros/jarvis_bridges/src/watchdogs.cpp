// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_bridges/watchdogs.hpp"

#include <rmw/qos_profiles.h>

#include <algorithm>
#include <cctype>
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

/// Whether a transition that starts in `start` can come after the node was seen in `known`.
bool can_follow(std::uint8_t known, std::uint8_t start)
{
  switch (start) {
    case State::TRANSITION_STATE_CONFIGURING:
      return known == State::PRIMARY_STATE_UNCONFIGURED;
    case State::TRANSITION_STATE_CLEANINGUP:
    case State::TRANSITION_STATE_ACTIVATING:
      return known == State::PRIMARY_STATE_INACTIVE;
    case State::TRANSITION_STATE_DEACTIVATING:
      return known == State::PRIMARY_STATE_ACTIVE;
    case State::TRANSITION_STATE_SHUTTINGDOWN:
      return known != State::PRIMARY_STATE_FINALIZED;
    case State::TRANSITION_STATE_ERRORPROCESSING:
      return true;
    default:  // a primary state
      return start == known;
  }
}

/// The transition an event is about. ROS publishes the event's `transition` empty (id 0, no
/// label): the intermediate state it started from names it (deactivating: deactivate).
std::string transition_name(const lifecycle_msgs::msg::TransitionEvent & ev)
{
  if (!ev.transition.label.empty()) {
    return ev.transition.label;
  }
  switch (ev.start_state.id) {
    case State::TRANSITION_STATE_CONFIGURING:
      return "configure";
    case State::TRANSITION_STATE_CLEANINGUP:
      return "cleanup";
    case State::TRANSITION_STATE_SHUTTINGDOWN:
      return "shutdown";
    case State::TRANSITION_STATE_ACTIVATING:
      return "activate";
    case State::TRANSITION_STATE_DEACTIVATING:
      return "deactivate";
    case State::TRANSITION_STATE_ERRORPROCESSING:
      return "error processing";
    default:
      return "";
  }
}

/// The component of a topic's QoS faults: the whole name, so /front/scan and /rear/scan stay apart.
std::string topic_component(std::string_view topic)
{
  while (!topic.empty() && topic.front() == '/') {
    topic.remove_prefix(1);
  }
  return to_segment(topic);
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

// -- QoS watchdog ---------------------------------------------------------------------------------

std::string QosWatchdog::incompatibility(
  const rmw_qos_profile_t & publisher, const rmw_qos_profile_t & subscription)
{
  rmw_qos_compatibility_type_t compatibility = RMW_QOS_COMPATIBILITY_OK;
  char reason[512] = {0};
  if (
    rmw_qos_profile_check_compatible(
      publisher, subscription, &compatibility, reason, sizeof(reason)) != RMW_RET_OK ||
    compatibility != RMW_QOS_COMPATIBILITY_ERROR) {
    return {};
  }
  // "ERROR: Best effort publisher and reliable subscription;ERROR: ..." -> "best effort publisher
  // and reliable subscription; ..."
  std::string out;
  std::string_view rest(reason);
  while (!rest.empty()) {
    const auto end = rest.find(';');
    std::string_view part = rest.substr(0, end);
    rest = end == std::string_view::npos ? std::string_view() : rest.substr(end + 1);
    if (const auto colon = part.find(": "); colon != std::string_view::npos) {
      part.remove_prefix(colon + 2);
    }
    if (part.empty()) {
      continue;
    }
    std::string text(part);
    text[0] = static_cast<char>(std::tolower(static_cast<unsigned char>(text[0])));
    out += (out.empty() ? "" : "; ") + text;
  }
  return out.empty() ? "incompatible QoS" : out;
}

std::vector<Event> QosWatchdog::tick(
  Timestamp now, const std::map<std::string, TopicEndpoints> & graph)
{
  std::vector<Event> out;
  std::set<std::string> seen;
  for (const auto & [topic, endpoints] : graph) {
    std::vector<std::string> pairs;
    std::string first_pub;
    std::string first_sub;
    std::string first_reason;
    for (const auto & sub : endpoints.subscriptions) {
      for (const auto & pub : endpoints.publishers) {
        const std::string reason = incompatibility(pub.qos, sub.qos);
        if (reason.empty()) {
          continue;
        }
        if (pairs.empty()) {
          first_pub = pub.node;
          first_sub = sub.node;
          first_reason = reason;
        }
        pairs.push_back(pub.node + " -> " + sub.node + ": " + reason);
      }
    }
    if (pairs.empty()) {
      continue;
    }
    seen.insert(topic);
    std::sort(pairs.begin(), pairs.end());
    std::string signature;
    for (const auto & p : pairs) {
      signature += p + '\n';
    }
    auto & raised = raised_[topic];
    if (raised == signature) {
      continue;
    }
    raised = signature;
    Event e;
    e.stamp = now;
    e.kind = Kind::FaultRaise;
    e.code = "ros.topic.qos_incompatible";
    e.severity = severity_;
    e.source.component = topic_component(topic);
    e.attrs["topic"] = topic;
    e.attrs["publisher"] = first_pub;
    e.attrs["subscription"] = first_sub;
    e.attrs["reason"] = first_reason;
    e.attrs["pairs"] = static_cast<std::int64_t>(pairs.size());
    e.message = topic + ": " + first_sub + " gets nothing from " + first_pub + " (" + first_reason +
                ")" +
                (pairs.size() > 1 ? ", and " + std::to_string(pairs.size() - 1) + " more pair" +
                                      (pairs.size() > 2 ? "s" : "")
                                  : "");
    out.push_back(std::move(e));
  }
  for (auto it = raised_.begin(); it != raised_.end();) {
    if (seen.count(it->first) != 0) {
      ++it;
      continue;
    }
    Event e;
    e.stamp = now;
    e.kind = Kind::FaultClear;
    e.code = "ros.topic.qos_incompatible";
    e.severity = Severity::Info;
    e.source.component = topic_component(it->first);
    e.attrs["topic"] = it->first;
    e.message = it->first + ": its publishers and subscriptions match again";
    out.push_back(std::move(e));
    it = raised_.erase(it);
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
  const std::string transition = transition_name(ev);
  if (goal == State::TRANSITION_STATE_ERRORPROCESSING && !t.failed) {
    t.failed = true;
    Event e = make(node, Kind::FaultRaise, "ros.lifecycle.failed", Severity::Error, now);
    e.attrs["transition"] = transition;
    e.attrs["from"] = ev.start_state.label;
    e.message = node + (transition.empty() ? ": a transition" : ": '" + transition + "'") +
                " failed, error processing";
    out.push_back(std::move(e));
  }
  if (goal < State::PRIMARY_STATE_UNCONFIGURED || goal > State::PRIMARY_STATE_FINALIZED) {
    return out;  // intermediate states are not reported
  }
  // The GetState baseline, read when the watch starts, can be newer than transitions still on
  // their way: one that cannot follow the state we know is older than it (configuring -> inactive
  // when the node is known active), and is skipped rather than read as the node leaving active.
  if (t.primary != 0 && !can_follow(t.primary, ev.start_state.id)) {
    return out;
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
    e.message = node + " left active: " + ev.goal_state.label +
                (transition.empty() ? "" : " after '" + transition + "'");
    out.push_back(std::move(e));
  }
  return out;
}

}  // namespace jarvis_bridges
