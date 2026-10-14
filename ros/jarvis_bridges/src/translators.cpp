// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_bridges/translators.hpp"

#include <cctype>
#include <charconv>
#include <cmath>
#include <cstdio>
#include <jarvis_core/util.hpp>
#include <utility>

namespace jarvis_bridges
{

namespace
{

using diagnostic_msgs::msg::DiagnosticStatus;
using jarvis_core::Kind;

constexpr std::size_t kMaxAttrs = 32;    // EventHubConfig::max_attrs
constexpr std::size_t kMaxAttrKey = 64;  // EventHubConfig::max_attr_key_length

std::string normalize(std::string_view text, bool split)
{
  std::string out;
  bool gap = false;  // a separator is pending inside the current segment
  for (const char raw : text) {
    const char c = static_cast<char>(std::tolower(static_cast<unsigned char>(raw)));
    const bool alnum = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9');
    if (alnum) {
      if (gap) {
        out += '_';
      }
      gap = false;
      out += c;
    } else if (split && (c == ':' || c == '/' || c == '.')) {
      if (!out.empty() && out.back() != '.') {
        out += '.';
      }
      gap = false;
    } else if (!out.empty() && out.back() != '.') {
      gap = true;
    }
  }
  if (!out.empty() && out.back() == '.') {
    out.pop_back();
  }
  return out;
}

bool matches_any(const std::vector<std::string> & globs, std::string_view text)
{
  for (const auto & glob : globs) {
    if (jarvis_core::glob_match(glob, text)) {
      return true;
    }
  }
  return false;
}

Severity diagnostic_severity(uint8_t level)
{
  switch (level) {
    case DiagnosticStatus::OK:
      return Severity::Info;
    case DiagnosticStatus::WARN:
      return Severity::Warn;
    case DiagnosticStatus::STALE:
      return Severity::Critical;
    default:
      return Severity::Error;
  }
}

std::string_view log_level_name(uint8_t level)
{
  if (level >= rcl_interfaces::msg::Log::FATAL) {
    return "fatal";
  }
  if (level >= rcl_interfaces::msg::Log::ERROR) {
    return "error";
  }
  if (level >= rcl_interfaces::msg::Log::WARN) {
    return "warn";
  }
  if (level >= rcl_interfaces::msg::Log::INFO) {
    return "info";
  }
  return "debug";
}

Severity log_severity(uint8_t level)
{
  if (level >= rcl_interfaces::msg::Log::FATAL) {
    return Severity::Critical;
  }
  if (level >= rcl_interfaces::msg::Log::ERROR) {
    return Severity::Error;
  }
  if (level >= rcl_interfaces::msg::Log::WARN) {
    return Severity::Warn;
  }
  if (level >= rcl_interfaces::msg::Log::INFO) {
    return Severity::Info;
  }
  return Severity::Debug;
}

Timestamp to_ns(const builtin_interfaces::msg::Time & t)
{
  return static_cast<Timestamp>(t.sec) * jarvis_core::kSecond + t.nanosec;
}

/// True when a repeat is due. A negative gap means sim time went backwards: treat it as due.
bool due(Timestamp now, Timestamp last, Duration period)
{
  const Duration elapsed = now - last;
  return elapsed < 0 || elapsed >= period;
}

}  // namespace

std::string to_code(std::string_view text) { return normalize(text, true); }

std::string to_segment(std::string_view text) { return normalize(text, false); }

std::string make_code(std::string_view prefix, std::string_view suffix, std::size_t max_length)
{
  std::string code(prefix);
  if (!code.empty()) {
    code += '.';
  }
  code += suffix.empty() ? std::string_view("unnamed") : suffix;
  if (code.size() > max_length) {
    code.resize(max_length);
  }
  while (!code.empty() && (code.back() == '.' || code.back() == '_')) {
    code.pop_back();
  }
  return code;
}

jarvis_core::AttrValue parse_value(const std::string & text)
{
  if (text == "true" || text == "True" || text == "TRUE") {
    return true;
  }
  if (text == "false" || text == "False" || text == "FALSE") {
    return false;
  }
  const char * first = text.data();
  const char * last = text.data() + text.size();
  int64_t i = 0;
  if (auto [ptr, ec] = std::from_chars(first, last, i); ec == std::errc() && ptr == last) {
    return i;
  }
  double d = 0.0;
  if (auto [ptr, ec] = std::from_chars(first, last, d); ec == std::errc() && ptr == last) {
    if (std::isfinite(d)) {
      return d;
    }
  }
  return text;
}

std::string uuid_to_string(const std::array<uint8_t, 16> & u)
{
  char buf[37];
  std::snprintf(
    buf, sizeof(buf), "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x", u[0],
    u[1], u[2], u[3], u[4], u[5], u[6], u[7], u[8], u[9], u[10], u[11], u[12], u[13], u[14], u[15]);
  return buf;
}

std::string_view goal_status_name(int8_t status)
{
  using action_msgs::msg::GoalStatus;
  switch (status) {
    case GoalStatus::STATUS_ACCEPTED:
      return "ACCEPTED";
    case GoalStatus::STATUS_EXECUTING:
      return "EXECUTING";
    case GoalStatus::STATUS_CANCELING:
      return "CANCELING";
    case GoalStatus::STATUS_SUCCEEDED:
      return "SUCCEEDED";
    case GoalStatus::STATUS_CANCELED:
      return "CANCELED";
    case GoalStatus::STATUS_ABORTED:
      return "ABORTED";
    default:
      return "UNKNOWN";
  }
}

// -- diagnostics ----------------------------------------------------------------------------------

std::vector<Event> DiagnosticsTranslator::on_array(
  const diagnostic_msgs::msg::DiagnosticArray & array, Timestamp now)
{
  const Timestamp header = to_ns(array.header.stamp);
  std::vector<Event> out;
  for (const auto & status : array.status) {
    if (auto e = on_status(status, header > 0 ? header : now, now)) {
      out.push_back(std::move(*e));
    }
  }
  return out;
}

std::optional<Event> DiagnosticsTranslator::on_status(
  const DiagnosticStatus & status, Timestamp stamp, Timestamp now)
{
  if (
    matches_any(config_.ignore_hardware_ids, status.hardware_id) ||
    matches_any(config_.ignore_names, status.name)) {
    return std::nullopt;
  }
  const uint8_t level = status.level;
  const bool ok = level == DiagnosticStatus::OK;
  auto [it, fresh] = seen_.try_emplace(status.hardware_id + '\x1f' + status.name);
  Seen & seen = it->second;
  // The first OK of a status is sent too: it heals a fault the fault manager restored at startup.
  const bool send =
    fresh || level != seen.level ||
    (!ok && config_.repeat_period > 0 && due(now, seen.last_sent, config_.repeat_period));
  seen.level = level;
  if (!send) {
    return std::nullopt;
  }
  seen.last_sent = now;

  const std::string name = to_code(status.name);
  Event e;
  e.stamp = stamp;
  e.kind = ok ? Kind::FaultClear : Kind::FaultRaise;
  e.severity = diagnostic_severity(level);
  e.code = make_code(config_.code_prefix, name);
  e.source.component = !status.hardware_id.empty()
                         ? status.hardware_id
                         : (name.empty() ? "" : name.substr(0, name.find('.')));
  e.message = status.message;
  e.attrs["diag.name"] = status.name;
  e.attrs["diag.hardware_id"] = status.hardware_id;
  e.attrs["diag.level"] = static_cast<int64_t>(level);
  for (const auto & kv : status.values) {
    // Keep one slot for the truncation marker, so the event stays within the hub's limit.
    if (e.attrs.size() >= kMaxAttrs - 1) {
      e.attrs["jarvis.truncated"] = true;
      break;
    }
    std::string key = to_segment(kv.key).substr(0, kMaxAttrKey);
    if (!key.empty()) {
      e.attrs.try_emplace(std::move(key), parse_value(kv.value));
    }
  }
  return e;
}

// -- rosout ---------------------------------------------------------------------------------------

std::optional<Event> RosoutTranslator::on_log(const rcl_interfaces::msg::Log & log, Timestamp now)
{
  if (
    log.level < static_cast<uint8_t>(config_.min_level) ||
    matches_any(config_.ignore_loggers, log.name)) {
    return std::nullopt;
  }
  const bool fault = log.level >= static_cast<uint8_t>(config_.fault_level);
  std::string code = make_code("ros.log", log_level_name(log.level));
  if (fault) {
    auto [it, fresh] = last_fault_.try_emplace(log.name + '\x1f' + code, now);
    if (!fresh && !due(now, it->second, config_.fault_repeat_period)) {
      return std::nullopt;
    }
    it->second = now;
  }
  Event e;
  const Timestamp stamp = to_ns(log.stamp);
  e.stamp = stamp > 0 ? stamp : now;
  e.kind = fault ? Kind::FaultRaise : Kind::Log;
  e.severity = log_severity(log.level);
  e.code = std::move(code);
  e.source.component = log.name;
  e.message = log.msg;
  e.attrs["log.file"] = log.file;
  e.attrs["log.function"] = log.function;
  e.attrs["log.line"] = static_cast<int64_t>(log.line);
  return e;
}

// -- action status --------------------------------------------------------------------------------

std::vector<Event> ActionStatusTranslator::on_status(
  const std::string & action, const action_msgs::msg::GoalStatusArray & array, Timestamp now)
{
  using action_msgs::msg::GoalStatus;
  auto [it, baseline] = actions_.try_emplace(action);
  Track & track = it->second;
  // Leave room for the ".aborted" / ".status" suffix.
  const std::string base = make_code("ros.action", to_code(action), 128 - 8);

  auto make = [&](
                Kind kind, std::string code, Severity severity, std::string message,
                const std::string & goal_id) {
    Event e;
    e.stamp = now;
    e.kind = kind;
    e.code = std::move(code);
    e.severity = severity;
    e.message = std::move(message);
    e.source.component = action;
    e.attrs["action"] = action;
    e.attrs["goal_id"] = goal_id;
    return e;
  };

  std::vector<Event> out;
  std::unordered_map<std::string, int8_t> goals;
  for (const auto & goal : array.status_list) {
    const std::string id = uuid_to_string(goal.goal_info.goal_id.uuid);
    goals[id] = goal.status;
    if (baseline) {
      continue;
    }
    const auto prev = track.goals.find(id);
    if (prev != track.goals.end() && prev->second == goal.status) {
      continue;
    }
    const std::string status(goal_status_name(goal.status));
    Event state = make(Kind::State, base + ".status", Severity::Info, "goal " + status, id);
    state.attrs["value"] = status;
    out.push_back(std::move(state));

    if (goal.status == GoalStatus::STATUS_ABORTED) {
      out.push_back(make(Kind::FaultRaise, base + ".aborted", Severity::Error, "goal aborted", id));
      track.raised = true;
    } else if (goal.status == GoalStatus::STATUS_SUCCEEDED && (track.raised || !track.synced)) {
      out.push_back(
        make(Kind::FaultClear, base + ".aborted", Severity::Info, "goal succeeded", id));
      track.raised = false;
      track.synced = true;
    }
  }
  // Goals the server forgot (result timeout) are dropped here too.
  track.goals = std::move(goals);
  return out;
}

}  // namespace jarvis_bridges
