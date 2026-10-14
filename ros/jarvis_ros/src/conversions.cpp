// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_ros/conversions.hpp"

#include <cmath>
#include <limits>
#include <type_traits>

namespace jarvis_ros
{

using jarvis_interfaces::msg::Attribute;

builtin_interfaces::msg::Time to_msg_time(jarvis_core::Timestamp t)
{
  builtin_interfaces::msg::Time out;
  if (t <= 0) {
    return out;
  }
  out.sec = static_cast<int32_t>(t / jarvis_core::kSecond);
  out.nanosec = static_cast<uint32_t>(t % jarvis_core::kSecond);
  return out;
}

jarvis_core::Timestamp from_msg_time(const builtin_interfaces::msg::Time & t)
{
  return static_cast<jarvis_core::Timestamp>(t.sec) * jarvis_core::kSecond +
         static_cast<jarvis_core::Timestamp>(t.nanosec);
}

std::vector<Attribute> to_msg(const jarvis_core::Attributes & attrs)
{
  std::vector<Attribute> out;
  out.reserve(attrs.size());
  for (const auto & [key, value] : attrs) {
    Attribute a;
    a.key = key;
    std::visit(
      [&a](const auto & v) {
        using T = std::decay_t<decltype(v)>;
        if constexpr (std::is_same_v<T, std::string>) {
          a.type = Attribute::TYPE_STRING;
          a.string_value = v;
        } else if constexpr (std::is_same_v<T, std::int64_t>) {
          a.type = Attribute::TYPE_INT;
          a.int_value = v;
        } else if constexpr (std::is_same_v<T, double>) {
          a.type = Attribute::TYPE_DOUBLE;
          a.double_value = v;
        } else {
          a.type = Attribute::TYPE_BOOL;
          a.bool_value = v;
        }
      },
      value);
    out.push_back(std::move(a));
  }
  return out;
}

jarvis_core::Attributes from_msg(const std::vector<Attribute> & attrs)
{
  jarvis_core::Attributes out;
  for (const auto & a : attrs) {
    switch (a.type) {
      case Attribute::TYPE_INT:
        out[a.key] = a.int_value;
        break;
      case Attribute::TYPE_DOUBLE:
        out[a.key] = a.double_value;
        break;
      case Attribute::TYPE_BOOL:
        out[a.key] = a.bool_value;
        break;
      default:
        out[a.key] = a.string_value;
        break;
    }
  }
  return out;
}

jarvis_interfaces::msg::Source to_msg(const jarvis_core::Source & source)
{
  jarvis_interfaces::msg::Source out;
  out.robot = source.robot;
  out.node = source.node;
  out.component = source.component;
  out.host = source.host;
  return out;
}

jarvis_core::Source from_msg(const jarvis_interfaces::msg::Source & source)
{
  return jarvis_core::Source{source.robot, source.node, source.component, source.host};
}

void to_msg(const jarvis_core::Event & e, jarvis_interfaces::msg::Event & msg)
{
  msg.id = e.id;
  msg.seq = e.seq;
  msg.stamp = to_msg_time(e.stamp);
  msg.recv_stamp = to_msg_time(e.recv_stamp);
  msg.source = to_msg(e.source);
  msg.kind = static_cast<uint8_t>(e.kind);
  msg.code = e.code;
  msg.severity = static_cast<uint8_t>(e.severity);
  msg.message = e.message;
  msg.attrs = to_msg(e.attrs);
  msg.tags = e.tags;
  msg.payload = e.payload;
  msg.payload_schema = e.payload_schema;
  msg.correlation_id = e.correlation_id;
  msg.causation_id = e.causation_id;
}

void from_msg(const jarvis_interfaces::msg::Event & msg, jarvis_core::Event & e)
{
  e.id = msg.id;
  e.seq = msg.seq;
  e.stamp = from_msg_time(msg.stamp);
  e.recv_stamp = from_msg_time(msg.recv_stamp);
  e.source = from_msg(msg.source);
  e.kind = static_cast<jarvis_core::Kind>(msg.kind);
  e.code = msg.code;
  e.severity = static_cast<jarvis_core::Severity>(msg.severity);
  e.message = msg.message;
  e.attrs = from_msg(msg.attrs);
  e.tags = msg.tags;
  e.payload = msg.payload;
  e.payload_schema = msg.payload_schema;
  e.correlation_id = msg.correlation_id;
  e.causation_id = msg.causation_id;
}

jarvis_interfaces::msg::Fault to_msg(const jarvis_core::FaultEpisode & f)
{
  jarvis_interfaces::msg::Fault out;
  out.fault_id = f.fault_id;
  out.code = f.code;
  out.source = to_msg(f.source);
  out.reporting_sources = f.reporting_sources;
  out.state = static_cast<uint8_t>(f.state);
  out.severity = static_cast<uint8_t>(f.severity);
  out.occurrences = f.occurrences;
  out.first_seen = to_msg_time(f.first_seen);
  out.last_seen = to_msg_time(f.last_seen);
  out.confirmed_at = to_msg_time(f.confirmed_at);
  out.cleared_at = to_msg_time(f.cleared_at);
  out.acknowledged = f.acknowledged;
  out.acknowledged_by = f.acknowledged_by;
  out.latch = f.latch;
  out.cataloged = f.cataloged;
  out.muted_by = f.muted_by;
  out.incident_id = f.incident_id;
  out.snapshot_ref = f.snapshot_ref;
  out.description = f.description;
  out.message = f.message;
  out.freeze_frame = to_msg(f.freeze_frame);
  return out;
}

std::pair<std::string, jarvis_core::FaultView> to_view(const jarvis_interfaces::msg::Fault & f)
{
  const auto state = static_cast<jarvis_core::FaultState>(f.state);
  return {
    f.fault_id,
    jarvis_core::FaultView{f.code, f.source.component, std::string(jarvis_core::to_string(state))}};
}

std::uint8_t to_msg(jarvis_core::Requester r)
{
  using jarvis_interfaces::msg::Execution;
  switch (r) {
    case jarvis_core::Requester::Rule:
      return Execution::REQUESTER_RULE;
    case jarvis_core::Requester::Agent:
      return Execution::REQUESTER_AGENT;
    case jarvis_core::Requester::Plan:
      return Execution::REQUESTER_PLAN;
    case jarvis_core::Requester::Operator:
      break;
  }
  return Execution::REQUESTER_OPERATOR;
}

std::optional<jarvis_core::Requester> requester_from_msg(std::uint8_t value)
{
  using jarvis_interfaces::msg::Execution;
  switch (value) {
    case 0:
    case Execution::REQUESTER_OPERATOR:
      return jarvis_core::Requester::Operator;
    case Execution::REQUESTER_RULE:
      return jarvis_core::Requester::Rule;
    case Execution::REQUESTER_AGENT:
      return jarvis_core::Requester::Agent;
    case Execution::REQUESTER_PLAN:
      return jarvis_core::Requester::Plan;
    default:
      return std::nullopt;
  }
}

jarvis_interfaces::msg::Execution to_msg(const jarvis_core::Execution & x)
{
  jarvis_interfaces::msg::Execution out;
  out.execution_id = x.execution_id;
  out.plan_id = x.plan_id;
  out.plan_rev = x.plan_rev;
  for (const auto & [k, v] : x.args) {
    Attribute a;
    a.key = k;
    a.type = Attribute::TYPE_STRING;
    a.string_value = v;
    out.args.push_back(std::move(a));
  }
  out.requester_type = to_msg(x.requester);
  out.requester_id = x.requester_id;
  out.correlation_id = x.correlation_id;
  out.causation_id = x.causation_id;
  out.rationale = x.rationale;
  out.confidence = x.confidence.value_or(std::numeric_limits<double>::quiet_NaN());
  out.dry_run = x.dry_run;
  // Execution.STATE_* and StepProgress.STATE_* follow the order of the core enums, from 1.
  out.state = static_cast<std::uint8_t>(static_cast<int>(x.state) + 1);
  out.reason = x.reason;
  for (const auto & g : x.gates) {
    jarvis_interfaces::msg::GateResult r;
    r.gate = g.gate;
    r.passed = g.passed;
    r.detail = g.detail;
    out.gates.push_back(std::move(r));
  }
  out.needs_approval = x.needs_approval;
  out.approved_by = x.approved_by;
  out.locks = x.locks;
  for (const auto & s : x.steps) {
    jarvis_interfaces::msg::StepProgress p;
    p.id = s.id;
    p.action = s.action;
    p.state = static_cast<std::uint8_t>(static_cast<int>(s.state) + 1);
    p.attempts = static_cast<std::uint16_t>(s.attempts);
    p.started = to_msg_time(s.started);
    p.finished = to_msg_time(s.finished);
    p.message = s.message;
    out.steps.push_back(std::move(p));
  }
  out.current_step = static_cast<std::uint16_t>(x.current_step);
  out.failed_step = x.failed_step;
  out.created = to_msg_time(x.created);
  out.started = to_msg_time(x.started);
  out.finished = to_msg_time(x.finished);
  out.expires = to_msg_time(x.expires);
  out.depth = static_cast<std::uint8_t>(x.depth);
  out.parent_id = x.parent_id;
  out.ladder_of = x.ladder_of;
  out.ladder_running = x.ladder_running;
  out.escalated = x.escalated;
  return out;
}

std::map<std::string, std::string> args_from_msg(const std::vector<Attribute> & attrs)
{
  std::map<std::string, std::string> out;
  for (const auto & [k, v] : from_msg(attrs)) {
    out[k] = jarvis_core::attr_to_string(v);
  }
  return out;
}

jarvis_interfaces::msg::RuleStatus to_msg(
  const jarvis_core::RuleStats & stats, const jarvis_core::Rule * rule)
{
  jarvis_interfaces::msg::RuleStatus out;
  out.id = stats.id;
  if (rule) {
    out.description = rule->description;
    out.file = rule->file;
  }
  out.enabled = stats.enabled;
  out.dry_run = stats.dry_run;
  out.fires = stats.fires;
  out.dry_fires = stats.dry_fires;
  out.suppressed = stats.suppressed;
  out.cooled_down = stats.cooled_down;
  out.guard_blocked = stats.guard_blocked;
  out.action_failures = stats.action_failures;
  out.partitions = static_cast<std::uint32_t>(stats.partitions);
  return out;
}

}  // namespace jarvis_ros
