// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Functional core, imperative shell: core services return effects as plain data and the adapter
// (ROS node, replay tool, test) performs them. See docs/design/02-architecture.md §3.

#pragma once

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

using Effect = std::variant<EmitEvent, FaultsChanged, PersistState, CaptureBlackbox>;
using Effects = std::vector<Effect>;

/// Appends `more` to `into`, keeping a single FaultsChanged and a single PersistState at the end.
void merge_effects(Effects & into, Effects more);

}  // namespace jarvis_core
