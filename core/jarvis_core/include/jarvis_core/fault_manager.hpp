// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Fault lifecycle (PENDING -> ACTIVE -> LATCHED -> CLEARED), correlation and incidents.
// Deterministic: time only comes in through `now`; timers are exposed as `next_deadline()` and
// processed by `advance_to()`. See docs/design/03-data-model.md §5-6.

#pragma once

#include <cstdint>
#include <deque>
#include <map>
#include <nlohmann/json.hpp>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

#include "jarvis_core/effects.hpp"
#include "jarvis_core/fault_catalog.hpp"
#include "jarvis_core/types.hpp"
#include "jarvis_core/util.hpp"

namespace jarvis_core
{

enum class FaultState : std::uint8_t {
  Pending = 1,
  Active = 2,
  Latched = 3,
  Cleared = 4,
};

enum class IncidentState : std::uint8_t {
  Open = 1,
  Recovering = 2,
  Escalated = 3,
  Resolved = 4,
  Closed = 5,
};

std::string_view to_string(FaultState state);
std::string_view to_string(IncidentState state);
std::optional<FaultState> parse_fault_state(std::string_view text);
std::optional<IncidentState> parse_incident_state(std::string_view text);

struct FaultEpisode
{
  std::string fault_id;
  std::string code;
  Source source;
  std::vector<std::string> reporting_sources;
  FaultState state{FaultState::Pending};
  Severity severity{Severity::Error};
  std::uint32_t occurrences{0};
  Timestamp first_seen{0};
  Timestamp last_seen{0};
  Timestamp confirmed_at{0};
  Timestamp cleared_at{0};
  bool acknowledged{false};
  std::string acknowledged_by;
  Timestamp acknowledged_at{0};
  bool latch{false};
  bool cataloged{false};
  std::string muted_by;
  std::string muted_rule;
  std::string incident_id;
  std::string snapshot_ref;
  std::string description;
  std::string message;
  Attributes report_attrs;
  Attributes freeze_frame;

  // Debounce bookkeeping, persisted so a restart resumes exactly where it left off.
  int counter{0};
  std::vector<Timestamp> raise_times;
  Timestamp pending_deadline{0};
  Timestamp heal_deadline{0};

  bool is_open() const { return state != FaultState::Cleared; }
};

struct Incident
{
  std::string incident_id;
  IncidentState state{IncidentState::Open};
  Severity severity{Severity::Error};
  std::string title;
  std::string component;
  std::string root_fault_id;
  std::vector<std::string> fault_ids;
  Timestamp opened_at{0};
  Timestamp last_activity{0};
  Timestamp resolved_at{0};
  Timestamp closed_at{0};

  bool is_live() const
  {
    return state == IncidentState::Open || state == IncidentState::Recovering ||
           state == IncidentState::Escalated;
  }
};

void to_json(nlohmann::json & j, const FaultEpisode & f);
void from_json(const nlohmann::json & j, FaultEpisode & f);
void to_json(nlohmann::json & j, const Incident & i);
void from_json(const nlohmann::json & j, Incident & i);

struct FaultManagerConfig
{
  std::string robot_id{"robot"};
  std::string node_name{"/jarvis/fault_manager"};
  Severity incident_min_severity{Severity::Error};
  Duration incident_merge_window{120 * kSecond};
  Duration incident_auto_close{24 * kHour};
  /// Resolved incidents kept until they auto-close; beyond this, the oldest close early (a
  /// flapping fault resolves one incident and opens the next many times an hour).
  std::size_t max_resolved_incidents{500};
  std::size_t history_capacity{500};
  std::size_t state_values_capacity{256};
};

struct OperatorResult
{
  bool success{false};
  std::string message;
  std::vector<std::string> cleared_symptoms;
  Effects effects;
};

class FaultManager
{
public:
  FaultManager(FaultCatalog catalog, FaultManagerConfig config, std::uint64_t id_seed = 0);

  /// Feed every event from the canonical stream. FAULT_RAISE, FAULT_CLEAR, STATE, RECOVERY and
  /// `jarvis.incident.escalate` matter; everything else is ignored.
  Effects on_event(const Event & event, Timestamp now);

  /// Processes every deadline <= now, in time order.
  Effects advance_to(Timestamp now);
  std::optional<Timestamp> next_deadline() const;

  OperatorResult acknowledge(
    const std::string & fault_id, const std::string & user, const std::string & note,
    Timestamp now);
  OperatorResult clear(
    const std::string & fault_id, const std::string & user, const std::string & reason,
    bool skip_symptoms, Timestamp now);
  OperatorResult close_incident(
    const std::string & incident_id, const std::string & user, Timestamp now);

  void set_catalog(FaultCatalog catalog) { catalog_ = std::move(catalog); }
  const FaultCatalog & catalog() const { return catalog_; }

  /// Open episodes ordered by first_seen, then (optionally) cleared ones, newest first.
  std::vector<FaultEpisode> faults(bool include_cleared = false) const;
  std::vector<Incident> incidents(bool include_closed = false) const;
  const FaultEpisode * find_fault(const std::string & fault_id) const;
  const Incident * find_incident(const std::string & incident_id) const;

  /// Everything needed to resume after a restart. Small: only open faults and live incidents.
  nlohmann::json snapshot() const;
  Effects restore(const nlohmann::json & state, Timestamp now);

private:
  using Attrs = Attributes;

  Effects on_raise(const Event & e, Timestamp now);
  Effects on_clear(const Event & e, Timestamp now);
  Effects on_recovery(const Event & e, Timestamp now);
  void record_state(const Event & e);

  void confirm(
    FaultEpisode & ep, Timestamp at, const std::string & causation, Effects & fx,
    bool fresh = false);
  void heal(FaultEpisode & ep, Timestamp at, const std::string & causation, Effects & fx);
  void close(
    std::string fault_id, Timestamp at, const std::string & causation, Attrs extra,
    bool clear_symptoms, Effects & fx, std::vector<std::string> * cleared = nullptr);
  void emit_transition(
    const FaultEpisode & ep, std::optional<FaultState> prev, Timestamp at,
    const std::string & causation, Attrs extra, Effects & fx);

  void open_or_join_incident(FaultEpisode & ep, Timestamp at, Effects & fx);
  void transition_incident(
    Incident & inc, IncidentState next, Timestamp at, const std::string & causation,
    const std::string & reason, Effects & fx);
  void check_incident_resolved(const std::string & incident_id, Timestamp at, Effects & fx);
  void close_oldest_resolved(Timestamp at, Effects & fx);

  Event make_event(
    Kind kind, std::string code, Severity severity, std::string message, Attrs attrs,
    const std::string & component, const std::string & correlation_id,
    const std::string & causation_id, Timestamp at);
  std::string key_for(const Event & e) const;

  FaultCatalog catalog_;
  FaultManagerConfig config_;
  IdGenerator ids_;

  std::map<std::string, FaultEpisode> open_;                  // by fault_id
  std::unordered_map<std::string, std::string> open_by_key_;  // robot|code|subject -> fault_id
  std::deque<FaultEpisode> history_;                          // cleared, newest at the back
  std::map<std::string, Incident> incidents_;                 // live and recently closed
  std::map<std::string, AttrValue> state_values_;             // latest STATE value per code
  std::deque<std::string> state_order_;
};

}  // namespace jarvis_core
