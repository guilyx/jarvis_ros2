// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Functional core, imperative shell: core services return effects as plain data and the adapter
// (ROS node, replay tool, test) performs them. See docs/design/02-architecture.md §3.

#pragma once

#include <map>
#include <nlohmann/json.hpp>
#include <string>
#include <variant>
#include <vector>

#include "jarvis_core/types.hpp"

namespace jarvis_core
{

/// Publish this event on the ingest topic (it comes back sequenced on the canonical stream).
struct EmitEvent
{
  Event event;
};

/// The set of open faults or incidents changed: republish the latched snapshot.
struct FaultsChanged
{
};

/// State that must survive a restart changed: write the snapshot file.
struct PersistState
{
};

/// Ask the black-box recorder for a snapshot around this fault.
struct CaptureBlackbox
{
  std::string fault_id;
  std::string code;
  std::string incident_id;
};

/// A rule's `recover` action: an ExecuteRecovery request with requester RULE.
struct RequestRecovery
{
  std::string rule_id;
  std::string plan;
  std::map<std::string, std::string> args;
  std::string rationale;
  std::string match_id;        ///< the RULE_MATCH event, causation of the recovery
  std::string correlation_id;  ///< incident, when there is one
};

/// Run one attempt of an actuator step, then report with Orchestrator::on_action_result.
struct StartAction
{
  std::string execution_id;
  std::string step_id;
  int attempt{1};
  std::string action;
  nlohmann::json args;
  Duration timeout{0};  ///< the orchestrator enforces it too; past it the result is ignored
};

/// Stop an attempt still in flight (timeout, abort, kill switch). No result is expected.
struct CancelAction
{
  std::string execution_id;
  std::string step_id;
  int attempt{1};
};

/// The executions snapshot changed (these executions, or the mode when the list is empty):
/// republish /jarvis/executions and update the ExecuteRecovery goals of these executions.
struct ExecutionsChanged
{
  std::vector<std::string> execution_ids;
};

using Effect = std::variant<
  EmitEvent, FaultsChanged, PersistState, CaptureBlackbox, RequestRecovery, StartAction,
  CancelAction, ExecutionsChanged>;
using Effects = std::vector<Effect>;

/// Appends `more` to `into`, keeping a single FaultsChanged, ExecutionsChanged (with every id) and
/// PersistState at the end.
void merge_effects(Effects & into, Effects more);

}  // namespace jarvis_core
