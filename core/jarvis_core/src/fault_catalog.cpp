// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_core/fault_catalog.hpp"

#include <yaml-cpp/yaml.h>

#include <algorithm>
#include <fstream>
#include <initializer_list>
#include <sstream>

#include "jarvis_core/util.hpp"

namespace jarvis_core
{

namespace
{

[[noreturn]] void fail(const std::string & path, const std::string & what)
{
  throw CatalogError(path + ": " + what);
}

void check_keys(
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

std::string as_string(const YAML::Node & node, const std::string & path)
{
  if (!node.IsScalar()) {
    fail(path, "expected a string");
  }
  return node.as<std::string>();
}

bool as_bool(const YAML::Node & node, const std::string & path)
{
  try {
    return node.as<bool>();
  } catch (const YAML::Exception &) {
    fail(path, "expected true or false");
  }
}

int as_int(const YAML::Node & node, const std::string & path)
{
  try {
    return node.as<int>();
  } catch (const YAML::Exception &) {
    fail(path, "expected an integer");
  }
}

Duration as_duration(const YAML::Node & node, const std::string & path)
{
  const auto text = as_string(node, path);
  const auto d = parse_duration(text);
  if (!d || *d < 0) {
    fail(path, "invalid duration '" + text + "' (use e.g. 500ms, 1s, 2m, 1h)");
  }
  return *d;
}

Severity as_severity(const YAML::Node & node, const std::string & path)
{
  const auto text = as_string(node, path);
  const auto s = parse_severity(text);
  if (!s) {
    fail(path, "invalid severity '" + text + "' (DEBUG, INFO, WARN, ERROR, CRITICAL)");
  }
  return *s;
}

std::vector<std::string> as_string_list(const YAML::Node & node, const std::string & path)
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

DebouncePolicy parse_debounce(const YAML::Node & node, const std::string & path)
{
  DebouncePolicy d;
  if (node.IsScalar()) {
    if (node.as<std::string>() != "immediate") {
      fail(path, "expected 'immediate' or a mapping with count, persist or counter");
    }
    return d;
  }
  check_keys(node, path, {"count", "within", "persist", "counter"});
  if (node["count"]) {
    d.mode = DebouncePolicy::Mode::Count;
    d.count = as_int(node["count"], path + ".count");
    if (d.count < 1) {
      fail(path + ".count", "must be >= 1");
    }
    if (!node["within"]) {
      fail(path, "'count' needs 'within'");
    }
    d.within = as_duration(node["within"], path + ".within");
  } else if (node["persist"]) {
    d.mode = DebouncePolicy::Mode::Persist;
    d.persist = as_duration(node["persist"], path + ".persist");
  } else if (node["counter"]) {
    d.mode = DebouncePolicy::Mode::Counter;
    const auto & c = node["counter"];
    const std::string cpath = path + ".counter";
    check_keys(c, cpath, {"confirm_at", "heal_at"});
    if (c["confirm_at"]) {
      d.confirm_at = as_int(c["confirm_at"], cpath + ".confirm_at");
    }
    if (c["heal_at"]) {
      d.heal_at = as_int(c["heal_at"], cpath + ".heal_at");
    }
    if (d.confirm_at >= 0 || d.heal_at <= 0) {
      fail(cpath, "confirm_at must be negative and heal_at positive");
    }
  } else {
    fail(path, "expected one of count, persist or counter");
  }
  return d;
}

HealPolicy parse_heal(const YAML::Node & node, const std::string & path, HealPolicy base)
{
  check_keys(node, path, {"on_clear", "after"});
  if (node["on_clear"]) {
    base.on_clear = as_bool(node["on_clear"], path + ".on_clear");
  }
  if (node["after"]) {
    base.after = as_duration(node["after"], path + ".after");
  }
  return base;
}

IncidentPolicy parse_incident(const YAML::Node & node, const std::string & path)
{
  const auto text = as_string(node, path);
  if (text == "auto") {
    return IncidentPolicy::Auto;
  }
  if (text == "always") {
    return IncidentPolicy::Always;
  }
  if (text == "never") {
    return IncidentPolicy::Never;
  }
  fail(path, "expected auto, always or never");
}

FaultPolicy parse_policy(const YAML::Node & node, const std::string & path, FaultPolicy base)
{
  if (node["severity"]) {
    base.severity = as_severity(node["severity"], path + ".severity");
  }
  if (node["debounce"]) {
    base.debounce = parse_debounce(node["debounce"], path + ".debounce");
  }
  if (node["heal"]) {
    base.heal = parse_heal(node["heal"], path + ".heal", base.heal);
  }
  if (node["latch"]) {
    base.latch = as_bool(node["latch"], path + ".latch");
  }
  if (node["incident"]) {
    base.incident = parse_incident(node["incident"], path + ".incident");
  }
  return base;
}

}  // namespace

bool CorrelationRule::is_root(std::string_view code) const
{
  return std::any_of(
    root.begin(), root.end(), [code](const std::string & p) { return glob_match(p, code); });
}

bool CorrelationRule::is_symptom(std::string_view code) const
{
  return std::any_of(symptoms.begin(), symptoms.end(), [code](const std::string & p) {
    return glob_match(p, code);
  });
}

void FaultCatalog::add(FaultPolicy policy)
{
  policy.cataloged = true;
  faults_[policy.code] = std::move(policy);
}

FaultPolicy FaultCatalog::policy_for(const std::string & code) const
{
  if (const auto it = faults_.find(code); it != faults_.end()) {
    return it->second;
  }
  FaultPolicy p = defaults_;
  p.code = code;
  p.cataloged = false;
  return p;
}

FaultCatalog FaultCatalog::from_yaml(const std::string & text)
{
  YAML::Node root;
  try {
    root = YAML::Load(text);
  } catch (const YAML::Exception & e) {
    throw CatalogError(std::string("invalid YAML: ") + e.what());
  }
  if (!root || root.IsNull()) {
    return FaultCatalog{};
  }
  check_keys(root, "catalog", {"schema", "defaults", "faults", "correlation"});
  if (root["schema"] && root["schema"].as<std::string>() != "jarvis.faults/v1") {
    fail("catalog.schema", "expected 'jarvis.faults/v1'");
  }

  FaultCatalog catalog;
  FaultPolicy defaults;
  if (root["defaults"]) {
    check_keys(root["defaults"], "defaults", {"severity", "debounce", "heal", "latch", "incident"});
    defaults = parse_policy(root["defaults"], "defaults", defaults);
  }
  catalog.set_defaults(defaults);

  if (const auto faults = root["faults"]) {
    if (!faults.IsSequence()) {
      fail("faults", "expected a list");
    }
    for (std::size_t i = 0; i < faults.size(); ++i) {
      const std::string path = "faults[" + std::to_string(i) + "]";
      const auto & node = faults[i];
      check_keys(
        node, path,
        {"code", "description", "severity", "debounce", "heal", "latch", "capture", "runbook",
         "incident"});
      if (!node["code"]) {
        fail(path, "missing 'code'");
      }
      FaultPolicy p = parse_policy(node, path, defaults);
      p.code = as_string(node["code"], path + ".code");
      if (!is_valid_code(p.code)) {
        fail(path + ".code", "'" + p.code + "' is not a dotted lowercase code");
      }
      if (catalog.contains(p.code)) {
        fail(path + ".code", "duplicate code '" + p.code + "'");
      }
      if (node["description"]) {
        p.description = as_string(node["description"], path + ".description");
      }
      if (node["runbook"]) {
        p.runbook = as_string(node["runbook"], path + ".runbook");
      }
      if (const auto capture = node["capture"]) {
        check_keys(capture, path + ".capture", {"freeze_frame", "blackbox"});
        if (capture["freeze_frame"]) {
          p.capture.freeze_frame_topics =
            as_string_list(capture["freeze_frame"], path + ".capture.freeze_frame");
        }
        if (capture["blackbox"]) {
          p.capture.blackbox = as_bool(capture["blackbox"], path + ".capture.blackbox");
        }
      }
      catalog.add(std::move(p));
    }
  }

  if (const auto rules = root["correlation"]) {
    if (!rules.IsSequence()) {
      fail("correlation", "expected a list");
    }
    for (std::size_t i = 0; i < rules.size(); ++i) {
      const std::string path = "correlation[" + std::to_string(i) + "]";
      const auto & node = rules[i];
      check_keys(
        node, path, {"id", "root", "symptoms", "window", "mute_symptoms", "clear_with_root"});
      CorrelationRule r;
      if (!node["id"] || !node["root"] || !node["symptoms"]) {
        fail(path, "needs 'id', 'root' and 'symptoms'");
      }
      r.id = as_string(node["id"], path + ".id");
      r.root = as_string_list(node["root"], path + ".root");
      r.symptoms = as_string_list(node["symptoms"], path + ".symptoms");
      if (node["window"]) {
        r.window = as_duration(node["window"], path + ".window");
      }
      if (node["mute_symptoms"]) {
        r.mute_symptoms = as_bool(node["mute_symptoms"], path + ".mute_symptoms");
      }
      if (node["clear_with_root"]) {
        r.clear_with_root = as_bool(node["clear_with_root"], path + ".clear_with_root");
      }
      catalog.add_correlation(std::move(r));
    }
  }
  return catalog;
}

FaultCatalog FaultCatalog::from_file(const std::string & path)
{
  std::ifstream in(path);
  if (!in) {
    throw CatalogError("cannot open fault catalog '" + path + "'");
  }
  std::stringstream buffer;
  buffer << in.rdbuf();
  try {
    return from_yaml(buffer.str());
  } catch (const CatalogError & e) {
    throw CatalogError(path + ": " + e.what());
  }
}

}  // namespace jarvis_core
