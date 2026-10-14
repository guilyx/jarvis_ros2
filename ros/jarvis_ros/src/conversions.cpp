// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_ros/conversions.hpp"

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

}  // namespace jarvis_ros
