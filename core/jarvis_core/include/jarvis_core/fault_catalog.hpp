// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// The fault catalog: per-code policies (severity, debounce, heal, latch, capture, incident)
// plus correlation rules. Loaded from YAML (docs/design/03-data-model.md §5).

#pragma once

#include <map>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "jarvis_core/types.hpp"

namespace jarvis_core
{

struct DebouncePolicy
{
  enum class Mode {
    Immediate,  ///< confirm on the first RAISE
    Count,      ///< confirm on `count` RAISEs within `within`
    Persist,    ///< confirm if no CLEAR arrives within `persist`
    Counter,  ///< AUTOSAR DEM style: RAISE -1, CLEAR +1; confirm at `confirm_at`, heal at `heal_at`
  };
  Mode mode{Mode::Immediate};
  int count{1};
  Duration within{0};
  Duration persist{0};
  int confirm_at{-1};
  int heal_at{1};

  bool operator==(const DebouncePolicy &) const = default;
};

struct HealPolicy
{
  bool on_clear{true};
  Duration after{0};  ///< 0 = never auto-heal on silence

  bool operator==(const HealPolicy &) const = default;
};

struct CapturePolicy
{
  std::vector<std::string> freeze_frame_topics;
  bool blackbox{false};

  bool operator==(const CapturePolicy &) const = default;
};

enum class IncidentPolicy {
  Auto,  ///< open an incident when severity >= the manager's threshold
  Always,
  Never,
};

struct FaultPolicy
{
  std::string code;
  std::string description;
  Severity severity{Severity::Error};
  DebouncePolicy debounce;
  HealPolicy heal;
  bool latch{false};
  CapturePolicy capture;
  std::string runbook;
  IncidentPolicy incident{IncidentPolicy::Auto};
  bool cataloged{false};
};

struct CorrelationRule
{
  std::string id;
  std::vector<std::string> root;      ///< code globs
  std::vector<std::string> symptoms;  ///< code globs
  Duration window{kSecond};
  bool mute_symptoms{true};
  bool clear_with_root{true};

  bool is_root(std::string_view code) const;
  bool is_symptom(std::string_view code) const;
};

class CatalogError : public std::runtime_error
{
public:
  using std::runtime_error::runtime_error;
};

class FaultCatalog
{
public:
  FaultCatalog() = default;

  /// Parses a `jarvis.faults/v1` document. Throws CatalogError with the offending path.
  static FaultCatalog from_yaml(const std::string & text);
  static FaultCatalog from_file(const std::string & path);

  /// The policy for a code: its catalog entry, or the defaults (marked `cataloged = false`).
  FaultPolicy policy_for(const std::string & code) const;
  bool contains(const std::string & code) const { return faults_.count(code) != 0; }
  const std::vector<CorrelationRule> & correlation() const { return correlation_; }
  const FaultPolicy & defaults() const { return defaults_; }
  std::size_t size() const { return faults_.size(); }

  void add(FaultPolicy policy);
  void add_correlation(CorrelationRule rule) { correlation_.push_back(std::move(rule)); }
  void set_defaults(FaultPolicy defaults) { defaults_ = std::move(defaults); }

private:
  FaultPolicy defaults_;
  std::map<std::string, FaultPolicy> faults_;
  std::vector<CorrelationRule> correlation_;
};

}  // namespace jarvis_core
