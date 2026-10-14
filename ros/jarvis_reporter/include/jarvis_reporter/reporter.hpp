// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// One-line fault and event reporting for any ROS 2 node:
//
//   jarvis_reporter::Reporter jarvis(node, "lidar");
//   jarvis.fault("sensor.lidar.stale", Severity::Error, "no scan for 1.2 s", {{"silence_ms", 1200}});
//   jarvis.clear("sensor.lidar.stale");
//   jarvis.state("robot.mode", "AUTONOMOUS");
//
// Depends only on rclcpp and jarvis_interfaces. Reports are debounced on the client: a fault is
// sent when it starts, then at most once per `repeat_period` while it lasts; a clear is sent only
// if the fault was raised; a state only when its value changes.

#pragma once

#include <chrono>
#include <cstdint>
#include <jarvis_interfaces/msg/event.hpp>
#include <map>
#include <mutex>
#include <rclcpp/rclcpp.hpp>
#include <string>
#include <type_traits>
#include <unordered_map>
#include <utility>
#include <variant>

namespace jarvis_reporter
{

enum class Severity : uint8_t {
  Debug = jarvis_interfaces::msg::Event::SEVERITY_DEBUG,
  Info = jarvis_interfaces::msg::Event::SEVERITY_INFO,
  Warn = jarvis_interfaces::msg::Event::SEVERITY_WARN,
  Error = jarvis_interfaces::msg::Event::SEVERITY_ERROR,
  Critical = jarvis_interfaces::msg::Event::SEVERITY_CRITICAL,
};

/// Attribute values keep their type so rules can compare numbers. Constructors are implicit on
/// purpose, so `{{"silence_ms", 1200}, {"topic", "/scan"}}` just works.
class Value
{
public:
  using Variant = std::variant<std::string, int64_t, double, bool>;

  Value(const char * v) : v_(std::string(v)) {}
  Value(std::string v) : v_(std::move(v)) {}
  Value(bool v) : v_(v) {}
  Value(double v) : v_(v) {}
  Value(float v) : v_(static_cast<double>(v)) {}
  template <
    typename T, typename = std::enable_if_t<std::is_integral_v<T> && !std::is_same_v<T, bool>>>
  Value(T v) : v_(static_cast<int64_t>(v))
  {
  }

  const Variant & get() const { return v_; }

private:
  Variant v_;
};

using Attributes = std::map<std::string, Value>;

struct Options
{
  std::string topic{"/jarvis/ingest"};
  /// While a fault lasts, repeat it at most this often (keeps heal timers and counters fed).
  std::chrono::milliseconds repeat_period{1000};
  std::size_t queue_depth{1000};
};

class Reporter
{
public:
  using Event = jarvis_interfaces::msg::Event;

  /// `node` is any node pointer: rclcpp::Node::SharedPtr, rclcpp_lifecycle::LifecycleNode::SharedPtr...
  template <typename NodeT>
  explicit Reporter(NodeT && node, std::string component = {}, Options options = {})
  : clock_(node->get_clock()),
    node_name_(node->get_fully_qualified_name()),
    component_(std::move(component)),
    options_(std::move(options))
  {
    publisher_ = rclcpp::create_publisher<Event>(
      node, options_.topic, rclcpp::QoS(rclcpp::KeepLast(options_.queue_depth)).reliable());
  }

  /// Reports a fault condition (FAULT_RAISE). Returns true if a message was published.
  bool fault(
    const std::string & code, Severity severity = Severity::Error, const std::string & message = {},
    const Attributes & attrs = {})
  {
    // Publishing under the lock keeps a raise and a clear from two threads in order.
    std::lock_guard<std::mutex> lock(mutex_);
    const auto now = clock_->now();
    auto & slot = faults_[code];
    if (slot.raised) {
      const auto elapsed = now - slot.last_sent;
      // A negative gap means sim time went backwards (bag loop): treat it as a fresh period.
      if (elapsed.nanoseconds() >= 0 && elapsed < rclcpp::Duration(options_.repeat_period)) {
        return false;
      }
    }
    slot.raised = true;
    slot.last_sent = now;
    publish(Event::KIND_FAULT_RAISE, code, severity, message, attrs, now);
    return true;
  }

  /// Reports that a fault condition is gone (FAULT_CLEAR). Only sent if the fault was raised.
  bool clear(
    const std::string & code, const std::string & message = {}, const Attributes & attrs = {})
  {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = faults_.find(code);
    if (it == faults_.end() || !it->second.raised) {
      return false;
    }
    faults_.erase(it);
    publish(Event::KIND_FAULT_CLEAR, code, Severity::Info, message, attrs, clock_->now());
    return true;
  }

  /// Latest-value-wins state (STATE), e.g. state("robot.mode", "AUTONOMOUS"). Sent on change.
  bool state(const std::string & code, const Value & value, const std::string & message = {})
  {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = states_.find(code);
    if (it != states_.end() && it->second == value.get()) {
      return false;
    }
    states_[code] = value.get();
    publish(Event::KIND_STATE, code, Severity::Info, message, {{"value", value}}, clock_->now());
    return true;
  }

  /// Something noteworthy happened (LOG). Never debounced: the event hub rate-limits floods.
  void log(
    const std::string & code, Severity severity, const std::string & message,
    const Attributes & attrs = {})
  {
    publish(Event::KIND_LOG, code, severity, message, attrs, clock_->now());
  }

  bool is_raised(const std::string & code) const
  {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = faults_.find(code);
    return it != faults_.end() && it->second.raised;
  }

private:
  struct FaultSlot
  {
    bool raised{false};
    rclcpp::Time last_sent;
  };

  void publish(
    uint8_t kind, const std::string & code, Severity severity, const std::string & message,
    const Attributes & attrs, const rclcpp::Time & now)
  {
    Event e;
    e.stamp = now;
    e.source.node = node_name_;
    e.source.component = component_;
    e.kind = kind;
    e.code = code;
    e.severity = static_cast<uint8_t>(severity);
    e.message = message;
    e.attrs.reserve(attrs.size());
    for (const auto & [key, value] : attrs) {
      jarvis_interfaces::msg::Attribute a;
      a.key = key;
      std::visit(
        [&a](const auto & v) {
          using T = std::decay_t<decltype(v)>;
          if constexpr (std::is_same_v<T, std::string>) {
            a.type = a.TYPE_STRING;
            a.string_value = v;
          } else if constexpr (std::is_same_v<T, int64_t>) {
            a.type = a.TYPE_INT;
            a.int_value = v;
          } else if constexpr (std::is_same_v<T, double>) {
            a.type = a.TYPE_DOUBLE;
            a.double_value = v;
          } else {
            a.type = a.TYPE_BOOL;
            a.bool_value = v;
          }
        },
        value.get());
      e.attrs.push_back(std::move(a));
    }
    publisher_->publish(e);
  }

  rclcpp::Clock::SharedPtr clock_;
  std::string node_name_;
  std::string component_;
  Options options_;
  rclcpp::Publisher<Event>::SharedPtr publisher_;
  mutable std::mutex mutex_;
  std::unordered_map<std::string, FaultSlot> faults_;
  std::unordered_map<std::string, Value::Variant> states_;
};

}  // namespace jarvis_reporter
