// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Pure translators from standard ROS 2 messages to Jarvis events (docs/design/03-data-model.md §10).
// No node, no clock, no I/O: the bridge components feed them messages and `now`, and publish what
// comes back. That keeps every mapping rule unit-testable.

#pragma once

#include <action_msgs/msg/goal_status_array.hpp>
#include <array>
#include <cstdint>
#include <diagnostic_msgs/msg/diagnostic_array.hpp>
#include <jarvis_core/types.hpp>
#include <optional>
#include <rcl_interfaces/msg/log.hpp>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace jarvis_bridges
{

using jarvis_core::Duration;
using jarvis_core::Event;
using jarvis_core::Severity;
using jarvis_core::Timestamp;

/// Turns a free-form name into dotted code segments: `"Controller Server: /cmd_vel"` gives
/// `"controller_server.cmd_vel"`. `:` `/` `.` separate segments, other symbols become `_`.
/// Returns an empty string if nothing usable is left.
std::string to_code(std::string_view text);

/// Like to_code, but a single segment: separators become `_` too.
std::string to_segment(std::string_view text);

/// Joins a prefix and a suffix into a valid code no longer than `max_length`.
std::string make_code(
  std::string_view prefix, std::string_view suffix, std::size_t max_length = 128);

/// Diagnostic values are strings; give numbers and booleans back their type so rules can compare.
jarvis_core::AttrValue parse_value(const std::string & text);

/// 16-byte goal UUID as the usual 8-4-4-4-12 hex string.
std::string uuid_to_string(const std::array<uint8_t, 16> & uuid);

// -- diagnostics ----------------------------------------------------------------------------------

struct DiagnosticsConfig
{
  std::string code_prefix{"diag"};
  /// Statuses whose hardware_id matches one of these globs are skipped. `jarvis` is our own export.
  std::vector<std::string> ignore_hardware_ids{"jarvis"};
  /// Statuses whose name matches one of these globs are skipped.
  std::vector<std::string> ignore_names;
  /// 0 sends level changes only. Otherwise a non-OK level is repeated this often, for catalog
  /// entries that heal after silence.
  Duration repeat_period{0};
};

/// Level changes become FAULT_RAISE / FAULT_CLEAR. Steady states are not re-sent.
class DiagnosticsTranslator
{
public:
  explicit DiagnosticsTranslator(DiagnosticsConfig config) : config_(std::move(config)) {}

  std::vector<Event> on_array(const diagnostic_msgs::msg::DiagnosticArray & array, Timestamp now);

private:
  struct Seen
  {
    uint8_t level{0};
    Timestamp last_sent{0};
  };

  std::optional<Event> on_status(
    const diagnostic_msgs::msg::DiagnosticStatus & status, Timestamp stamp, Timestamp now);

  DiagnosticsConfig config_;
  std::unordered_map<std::string, Seen> seen_;
};

// -- rosout ---------------------------------------------------------------------------------------

struct RosoutConfig
{
  /// Lines below this level are ignored.
  Severity min_level{Severity::Warn};
  /// Lines at or above this level are faults (FAULT_RAISE), lower ones are LOG events.
  Severity fault_level{Severity::Error};
  /// Loggers matching one of these globs are skipped. Jarvis' own logs would loop otherwise.
  std::vector<std::string> ignore_loggers{"jarvis.*"};
  /// A logger's fault is repeated at most this often, so an error loop can't flood the bus.
  Duration fault_repeat_period{jarvis_core::kSecond};
};

/// `/rosout` lines become `ros.log.<level>` events about the logger (the component).
class RosoutTranslator
{
public:
  explicit RosoutTranslator(RosoutConfig config) : config_(std::move(config)) {}

  std::optional<Event> on_log(const rcl_interfaces::msg::Log & log, Timestamp now);

private:
  RosoutConfig config_;
  std::unordered_map<std::string, Timestamp> last_fault_;
};

// -- action status --------------------------------------------------------------------------------

/// Goal transitions of one action server become events:
///  - ABORTED raises `ros.action.<name>.aborted`; the next SUCCEEDED clears it.
///  - every transition sets the state `ros.action.<name>.status` (ACCEPTED, EXECUTING, ...).
/// The first status array of an action is a baseline: goals that ended before we listened are
/// not reported.
class ActionStatusTranslator
{
public:
  std::vector<Event> on_status(
    const std::string & action, const action_msgs::msg::GoalStatusArray & array, Timestamp now);

private:
  struct Track
  {
    std::unordered_map<std::string, int8_t> goals;
    bool raised{false};
    bool synced{false};  // a clear went out since we started, so restored faults can heal
  };

  std::unordered_map<std::string, Track> actions_;
};

std::string_view goal_status_name(int8_t status);

}  // namespace jarvis_bridges
