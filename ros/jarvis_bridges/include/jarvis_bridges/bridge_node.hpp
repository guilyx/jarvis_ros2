// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Lifecycle plumbing shared by every bridge: the /jarvis/ingest publisher, autostart, and
// publishing only while active. Subclasses own their subscriptions and translator.

#pragma once

#include <jarvis_ros/common.hpp>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace jarvis_bridges
{

class BridgeNode : public rclcpp_lifecycle::LifecycleNode
{
public:
  using CallbackReturn = rclcpp_lifecycle::node_interfaces::LifecycleNodeInterface::CallbackReturn;

  BridgeNode(const std::string & name, const rclcpp::NodeOptions & options)
  : rclcpp_lifecycle::LifecycleNode(name, "jarvis/bridges", options)
  {
    jarvis_ros::declare<std::string>(
      *this, "ingest_topic", "/jarvis/ingest", "Where translated events are published");
    jarvis_ros::declare<int>(*this, "queue_depth", 1000, "History depth of the ingest publisher");
  }

  CallbackReturn on_configure(const rclcpp_lifecycle::State &) override
  {
    try {
      configure_bridge();
    } catch (const std::exception & e) {
      RCLCPP_ERROR(get_logger(), "invalid configuration: %s", e.what());
      return CallbackReturn::FAILURE;
    }
    const auto depth = static_cast<std::size_t>(get_parameter("queue_depth").as_int());
    publisher_ = jarvis_ros::create_event_publisher(
      *this, get_parameter("ingest_topic").as_string(), jarvis_ros::stream_qos(depth));
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_activate(const rclcpp_lifecycle::State &) override
  {
    active_ = true;
    activate_bridge();
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_deactivate(const rclcpp_lifecycle::State &) override
  {
    active_ = false;
    deactivate_bridge();
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_cleanup(const rclcpp_lifecycle::State &) override
  {
    publisher_.reset();
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_shutdown(const rclcpp_lifecycle::State & state) override
  {
    if (active_) {
      on_deactivate(state);
    }
    return on_cleanup(state);
  }

protected:
  /// Reads parameters and builds the translator. Throws on invalid parameters.
  virtual void configure_bridge() = 0;
  /// Creates the subscriptions.
  virtual void activate_bridge() = 0;
  /// Drops the subscriptions.
  virtual void deactivate_bridge() = 0;

  /// Call from the constructor, after the subclass declared its parameters.
  void maybe_autostart()
  {
    if (jarvis_ros::declare<bool>(*this, "autostart", false, "Configure and activate on startup")) {
      autostart_timer_ = create_wall_timer(std::chrono::milliseconds(1), [this]() {
        autostart_timer_->cancel();
        jarvis_ros::autostart(*this);
      });
    }
  }

  jarvis_core::Timestamp now_ns() { return jarvis_ros::now_ns(*this); }

  void publish(jarvis_core::Event && event)
  {
    if (!active_ || !publisher_) {
      return;
    }
    if (event.source.node.empty()) {
      event.source.node = get_fully_qualified_name();
    }
    publisher_->publish(event);
  }

  void publish(std::optional<jarvis_core::Event> && event)
  {
    if (event) {
      publish(std::move(*event));
    }
  }

  void publish(std::vector<jarvis_core::Event> && events)
  {
    for (auto & e : events) {
      publish(std::move(e));
    }
  }

  static std::vector<std::string> strings(const rclcpp::Parameter & p)
  {
    return p.get_type() == rclcpp::ParameterType::PARAMETER_NOT_SET ? std::vector<std::string>{}
                                                                    : p.as_string_array();
  }

  static jarvis_core::Severity severity(const std::string & name, const std::string & text)
  {
    const auto s = jarvis_core::parse_severity(text);
    if (!s) {
      throw std::invalid_argument(name + ": '" + text + "' is not a severity (DEBUG..CRITICAL)");
    }
    return *s;
  }

private:
  jarvis_ros::EventPublisher::SharedPtr publisher_;
  rclcpp::TimerBase::SharedPtr autostart_timer_;
  bool active_{false};
};

}  // namespace jarvis_bridges
