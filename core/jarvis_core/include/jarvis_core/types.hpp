// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Domain types shared by every Jarvis component. No ROS here (ADR-0001).

#pragma once

#include <compare>
#include <cstdint>
#include <functional>
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

namespace jarvis_core
{

/// Nanoseconds since the epoch of whatever clock drives the system (ROS time, sim time or wall time).
using Timestamp = std::int64_t;
/// Nanoseconds.
using Duration = std::int64_t;

inline constexpr Duration kMillisecond = 1'000'000;
inline constexpr Duration kSecond = 1'000 * kMillisecond;
inline constexpr Duration kMinute = 60 * kSecond;
inline constexpr Duration kHour = 60 * kMinute;

enum class Kind : std::uint8_t {
  Unspecified = 0,
  Log = 1,
  State = 2,
  FaultRaise = 3,
  FaultClear = 4,
  FaultState = 5,
  RuleMatch = 6,
  Recovery = 7,
  Insight = 8,
  Operator = 9,
  System = 10,
};

/// Values match ROS log levels (FATAL maps to Critical).
enum class Severity : std::uint8_t {
  Debug = 10,
  Info = 20,
  Warn = 30,
  Error = 40,
  Critical = 50,
};

std::string_view to_string(Kind kind);
std::string_view to_string(Severity severity);
std::optional<Kind> parse_kind(std::string_view text);
/// Case-insensitive. Accepts WARNING for WARN and FATAL for CRITICAL.
std::optional<Severity> parse_severity(std::string_view text);
bool is_valid(Kind kind);
bool is_valid(Severity severity);
// Scoped enums compare by underlying value, so `severity >= Severity::Error` works as expected.

using AttrValue = std::variant<std::string, std::int64_t, double, bool>;
using Attributes = std::map<std::string, AttrValue, std::less<>>;

/// Human-readable rendering of an attribute value (used in messages and diagnostics).
std::string attr_to_string(const AttrValue & value);

struct Source
{
  std::string robot;
  std::string node;
  std::string component;
  std::string host;

  /// The thing the event is about: the component if set, else the emitting node.
  const std::string & subject() const { return component.empty() ? node : component; }

  bool operator==(const Source &) const = default;
};

/// The one record type Jarvis moves around. See docs/design/03-data-model.md §1.
struct Event
{
  std::string id;
  std::uint64_t seq{0};
  Timestamp stamp{0};
  Timestamp recv_stamp{0};
  Source source;
  Kind kind{Kind::Unspecified};
  std::string code;
  Severity severity{Severity::Info};
  std::string message;
  Attributes attrs;
  std::vector<std::string> tags;
  std::string payload;
  std::string payload_schema;
  std::string correlation_id;
  std::string causation_id;

  bool operator==(const Event &) const = default;
};

/// Lookup helpers for attributes.
const std::string * attr_string(const Attributes & attrs, std::string_view key);
std::optional<std::int64_t> attr_int(const Attributes & attrs, std::string_view key);

}  // namespace jarvis_core
