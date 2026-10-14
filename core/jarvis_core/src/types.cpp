// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_core/types.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <sstream>
#include <utility>

namespace jarvis_core
{

namespace
{

constexpr std::array<std::pair<Kind, std::string_view>, 11> kKindNames{{
  {Kind::Unspecified, "UNSPECIFIED"},
  {Kind::Log, "LOG"},
  {Kind::State, "STATE"},
  {Kind::FaultRaise, "FAULT_RAISE"},
  {Kind::FaultClear, "FAULT_CLEAR"},
  {Kind::FaultState, "FAULT_STATE"},
  {Kind::RuleMatch, "RULE_MATCH"},
  {Kind::Recovery, "RECOVERY"},
  {Kind::Insight, "INSIGHT"},
  {Kind::Operator, "OPERATOR"},
  {Kind::System, "SYSTEM"},
}};

constexpr std::array<std::pair<Severity, std::string_view>, 5> kSeverityNames{{
  {Severity::Debug, "DEBUG"},
  {Severity::Info, "INFO"},
  {Severity::Warn, "WARN"},
  {Severity::Error, "ERROR"},
  {Severity::Critical, "CRITICAL"},
}};

std::string upper(std::string_view text)
{
  std::string out(text);
  std::transform(out.begin(), out.end(), out.begin(), [](unsigned char c) {
    return static_cast<char>(std::toupper(c));
  });
  return out;
}

}  // namespace

std::string_view to_string(Kind kind)
{
  for (const auto & [k, name] : kKindNames) {
    if (k == kind) {
      return name;
    }
  }
  return "UNSPECIFIED";
}

std::string_view to_string(Severity severity)
{
  for (const auto & [s, name] : kSeverityNames) {
    if (s == severity) {
      return name;
    }
  }
  return "INFO";
}

std::optional<Kind> parse_kind(std::string_view text)
{
  const std::string key = upper(text);
  for (const auto & [k, name] : kKindNames) {
    if (name == key) {
      return k;
    }
  }
  return std::nullopt;
}

std::optional<Severity> parse_severity(std::string_view text)
{
  const std::string key = upper(text);
  if (key == "WARNING") {
    return Severity::Warn;
  }
  if (key == "FATAL") {
    return Severity::Critical;
  }
  for (const auto & [s, name] : kSeverityNames) {
    if (name == key) {
      return s;
    }
  }
  return std::nullopt;
}

bool is_valid(Kind kind)
{
  const auto v = static_cast<std::uint8_t>(kind);
  return v >= 1 && v <= 10;
}

bool is_valid(Severity severity)
{
  for (const auto & [s, name] : kSeverityNames) {
    if (s == severity) {
      return true;
    }
  }
  return false;
}

std::string attr_to_string(const AttrValue & value)
{
  return std::visit(
    [](const auto & v) -> std::string {
      using T = std::decay_t<decltype(v)>;
      if constexpr (std::is_same_v<T, std::string>) {
        return v;
      } else if constexpr (std::is_same_v<T, bool>) {
        return v ? "true" : "false";
      } else if constexpr (std::is_same_v<T, double>) {
        std::ostringstream os;
        os << v;
        return os.str();
      } else {
        return std::to_string(v);
      }
    },
    value);
}

const std::string * attr_string(const Attributes & attrs, std::string_view key)
{
  const auto it = attrs.find(key);
  if (it == attrs.end()) {
    return nullptr;
  }
  return std::get_if<std::string>(&it->second);
}

std::optional<std::int64_t> attr_int(const Attributes & attrs, std::string_view key)
{
  const auto it = attrs.find(key);
  if (it == attrs.end()) {
    return std::nullopt;
  }
  if (const auto * i = std::get_if<std::int64_t>(&it->second)) {
    return *i;
  }
  return std::nullopt;
}

}  // namespace jarvis_core
