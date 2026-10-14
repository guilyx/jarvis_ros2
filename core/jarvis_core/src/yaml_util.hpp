// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Strict YAML readers shared by the config parsers (fault catalog, rules, plans). Every error names
// the path of the offending value, e.g. `rules[2].when.count.threshold: must be >= 1`.

#pragma once

#include <yaml-cpp/yaml.h>

#include <algorithm>
#include <initializer_list>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "jarvis_core/types.hpp"
#include "jarvis_core/util.hpp"

namespace jarvis_core::detail
{

/// Thrown by the readers below; each parser rethrows it as its own error type.
class YamlError : public std::runtime_error
{
public:
  using std::runtime_error::runtime_error;
};

[[noreturn]] inline void fail(const std::string & path, const std::string & what)
{
  throw YamlError(path + ": " + what);
}

inline void check_keys(
  const YAML::Node & node, const std::string & path,
  std::initializer_list<std::string_view> allowed)
{
  if (!node.IsMap()) {
    fail(path, "expected a mapping");
  }
  for (const auto & item : node) {
    const auto key = item.first.as<std::string>();
    if (std::find(allowed.begin(), allowed.end(), key) == allowed.end()) {
      fail(path + "." + key, "unknown key");
    }
  }
}

inline std::string as_string(const YAML::Node & node, const std::string & path)
{
  if (!node.IsScalar()) {
    fail(path, "expected a string");
  }
  return node.as<std::string>();
}

inline bool as_bool(const YAML::Node & node, const std::string & path)
{
  try {
    return node.as<bool>();
  } catch (const YAML::Exception &) {
    fail(path, "expected true or false");
  }
}

inline int as_int(const YAML::Node & node, const std::string & path)
{
  try {
    return node.as<int>();
  } catch (const YAML::Exception &) {
    fail(path, "expected an integer");
  }
}

inline double as_double(const YAML::Node & node, const std::string & path)
{
  try {
    return node.as<double>();
  } catch (const YAML::Exception &) {
    fail(path, "expected a number");
  }
}

inline Duration as_duration(const YAML::Node & node, const std::string & path)
{
  const auto text = as_string(node, path);
  const auto d = parse_duration(text);
  if (!d || *d < 0) {
    fail(path, "invalid duration '" + text + "' (use e.g. 500ms, 1s, 2m, 1h)");
  }
  return *d;
}

inline Severity as_severity(const YAML::Node & node, const std::string & path)
{
  const auto text = as_string(node, path);
  const auto s = parse_severity(text);
  if (!s) {
    fail(path, "invalid severity '" + text + "' (DEBUG, INFO, WARN, ERROR, CRITICAL)");
  }
  return *s;
}

inline std::vector<std::string> as_string_list(const YAML::Node & node, const std::string & path)
{
  if (node.IsScalar()) {
    return {node.as<std::string>()};
  }
  if (!node.IsSequence()) {
    fail(path, "expected a list of strings");
  }
  std::vector<std::string> out;
  for (std::size_t i = 0; i < node.size(); ++i) {
    out.push_back(as_string(node[i], path + "[" + std::to_string(i) + "]"));
  }
  return out;
}

/// A scalar as a typed value: quoted scalars stay strings, plain ones become bool, int or double
/// when they look like one (so `threshold: 5` and `value: "5"` differ, as in YAML itself).
inline AttrValue as_value(const YAML::Node & node, const std::string & path)
{
  if (!node.IsScalar()) {
    fail(path, "expected a scalar (string, number or boolean)");
  }
  const std::string text = node.as<std::string>();
  if (node.Tag() == "!") {
    return text;
  }
  if (text == "true" || text == "false") {
    return text == "true";
  }
  try {
    std::size_t used = 0;
    const long long i = std::stoll(text, &used);
    if (used == text.size()) {
      return static_cast<std::int64_t>(i);
    }
    const double d = std::stod(text, &used);
    if (used == text.size()) {
      return d;
    }
  } catch (const std::exception &) {
  }
  return text;
}

inline YAML::Node load(const std::string & text)
{
  try {
    return YAML::Load(text);
  } catch (const YAML::Exception & e) {
    throw YamlError(std::string("invalid YAML: ") + e.what());
  }
}

}  // namespace jarvis_core::detail
