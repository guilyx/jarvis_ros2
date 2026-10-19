// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Pure watchdogs (docs/design/04-data-flow.md §2, 08-ros2-practices.md §6): they turn what the
// bridges observe (message arrivals, the node graph, lifecycle transitions) into Jarvis events.
// No node, no clock, no I/O, like the translators.

#pragma once

#include <rmw/types.h>

#include <deque>
#include <jarvis_core/types.hpp>
#include <lifecycle_msgs/msg/transition_event.hpp>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <string_view>
#include <vector>

namespace jarvis_bridges
{

using jarvis_core::Duration;
using jarvis_core::Event;
using jarvis_core::Severity;
using jarvis_core::Timestamp;

/// The last segment of a node or topic name: `/demo/scan_relay` gives `scan_relay`.
std::string base_name(std::string_view name);

// -- topic watchdog -------------------------------------------------------------------------------

struct TopicWatchConfig
{
  std::string topic;
  std::string component;  ///< empty: the topic's last segment
  /// No message for this long raises `ros.topic.stale`; 0 disables the check.
  Duration stale_after{jarvis_core::kSecond};
  /// A rate below this over `rate_window` raises `ros.topic.slow`; 0 disables the check.
  double min_rate_hz{0.0};
  Duration rate_window{2 * jarvis_core::kSecond};
  /// How long the first message may take after the watch starts.
  Duration startup_grace{5 * jarvis_core::kSecond};
  Severity severity{Severity::Error};
};

/// One watched topic. `ros.topic.stale` (silence, or no publisher at all) clears on the next
/// message; `ros.topic.slow` (WARN) clears once the rate is back above the minimum.
class TopicWatch
{
public:
  TopicWatch(TopicWatchConfig config, Timestamp started);

  std::vector<Event> on_message(Timestamp now);
  /// Periodic check. `publishers` is the publisher count seen on the graph.
  std::vector<Event> tick(Timestamp now, std::size_t publishers);

  double rate_hz(Timestamp now);
  const TopicWatchConfig & config() const { return config_; }

private:
  Event make(
    jarvis_core::Kind kind, const std::string & code, Severity severity, Timestamp at) const;

  TopicWatchConfig config_;
  Timestamp started_;
  std::optional<Timestamp> last_;
  std::deque<Timestamp> arrivals_;
  bool stale_{false};
  bool slow_{false};
};

// -- node watchdog --------------------------------------------------------------------------------

struct NodeWatchConfig
{
  std::vector<std::string> nodes;  ///< fully qualified names, e.g. /demo/scan_relay
  /// A node missing from the graph for this long raises `ros.node.missing`.
  Duration grace{5 * jarvis_core::kSecond};
  Severity severity{Severity::Error};
};

class NodeWatchdog
{
public:
  NodeWatchdog(NodeWatchConfig config, Timestamp started);

  /// `present`: fully qualified names of the nodes on the graph now.
  std::vector<Event> tick(Timestamp now, const std::set<std::string> & present);

private:
  struct Track
  {
    Timestamp last_seen{0};
    bool raised{false};
  };

  NodeWatchConfig config_;
  std::map<std::string, Track> nodes_;
};

// -- QoS watchdog ---------------------------------------------------------------------------------

/// A publisher or a subscription of a topic, as the graph reports it.
struct QosEndpoint
{
  std::string node;  ///< fully qualified
  rmw_qos_profile_t qos;
};

struct TopicEndpoints
{
  std::vector<QosEndpoint> publishers;
  std::vector<QosEndpoint> subscriptions;
};

/// A publisher and a subscription whose QoS cannot match (rmw's own check: a best-effort publisher
/// and a reliable subscription, a volatile publisher and a transient-local subscription, ...) never
/// exchange a message, and nothing in ROS says so. Per topic, `ros.topic.qos_incompatible` while
/// any of its pairs cannot match, on the topic (`/front/scan`: `front_scan`); it clears once none is left (the QoS
/// fixed, or an endpoint gone). Pairs that only may not match (a policy left to the system) are not
/// faults.
class QosWatchdog
{
public:
  explicit QosWatchdog(Severity severity = Severity::Error) : severity_(severity) {}

  /// `graph`: the endpoints of every topic checked, by name.
  std::vector<Event> tick(Timestamp now, const std::map<std::string, TopicEndpoints> & graph);

  /// Why a publisher and a subscription cannot match, empty if they can.
  static std::string incompatibility(
    const rmw_qos_profile_t & publisher, const rmw_qos_profile_t & subscription);

private:
  Severity severity_;
  std::map<std::string, std::string> raised_;  ///< topic -> the mismatches raised
};

// -- lifecycle ------------------------------------------------------------------------------------

/// Transitions of managed nodes become events:
///  - every primary state reached sets STATE `ros.lifecycle.<node>.state` (value: the state label);
///  - leaving `active` raises `ros.lifecycle.inactive` (WARN), going through error processing raises
///    `ros.lifecycle.failed` (ERROR); reaching `active` again clears both.
/// The component is the node's last segment. A catalog debounce keeps deliberate restarts quiet.
class LifecycleTranslator
{
public:
  std::vector<Event> on_transition(
    const std::string & node, const lifecycle_msgs::msg::TransitionEvent & event, Timestamp now);
  /// The state read with GetState when the watch starts (a baseline: nothing is raised).
  std::vector<Event> on_state(
    const std::string & node, std::uint8_t id, const std::string & label, Timestamp now);

  static std::string state_code(const std::string & node);

private:
  struct Track
  {
    std::uint8_t primary{0};
    bool inactive{false};
    bool failed{false};
  };

  Event make(
    const std::string & node, jarvis_core::Kind kind, std::string code, Severity severity,
    Timestamp at) const;

  std::map<std::string, Track> nodes_;
};

}  // namespace jarvis_bridges
