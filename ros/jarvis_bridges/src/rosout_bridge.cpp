// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// /jarvis/bridges/rosout: /rosout lines -> LOG events, or faults at and above `fault_level`.

#include <rclcpp_components/register_node_macro.hpp>

#include "jarvis_bridges/bridge_node.hpp"
#include "jarvis_bridges/translators.hpp"

namespace jarvis_bridges
{

class RosoutBridge : public BridgeNode
{
public:
  explicit RosoutBridge(const rclcpp::NodeOptions & options) : BridgeNode("rosout", options)
  {
    using jarvis_ros::declare;
    declare<std::string>(*this, "topic", "/rosout", "Log topic to read");
    declare<std::string>(*this, "min_level", "WARN", "Lines below this level are ignored");
    declare<std::string>(*this, "fault_level", "ERROR", "Lines at or above this level are faults");
    declare<std::vector<std::string>>(
      *this, "ignore_loggers", {"jarvis.*"},
      "Logger name globs to skip (Jarvis' own logs would loop)");
    declare<double>(
      *this, "fault_repeat_period_s", 1.0, "Repeat a logger's fault at most this often");
    maybe_autostart();
  }

protected:
  void configure_bridge() override
  {
    RosoutConfig config;
    config.min_level = severity("min_level", get_parameter("min_level").as_string());
    config.fault_level = severity("fault_level", get_parameter("fault_level").as_string());
    config.ignore_loggers = strings(get_parameter("ignore_loggers"));
    config.fault_repeat_period =
      static_cast<jarvis_core::Duration>(get_parameter("fault_repeat_period_s").as_double() * 1e9);
    translator_ = std::make_unique<RosoutTranslator>(std::move(config));
  }

  void activate_bridge() override
  {
    // /rosout is reliable + transient local; a volatile reader skips the backlog of old lines.
    sub_ = create_subscription<rcl_interfaces::msg::Log>(
      get_parameter("topic").as_string(), rclcpp::QoS(rclcpp::KeepLast(1000)).reliable(),
      [this](const rcl_interfaces::msg::Log & log) {
        publish(translator_->on_log(log, now_ns()));
      });
  }

  void deactivate_bridge() override { sub_.reset(); }

private:
  std::unique_ptr<RosoutTranslator> translator_;
  rclcpp::Subscription<rcl_interfaces::msg::Log>::SharedPtr sub_;
};

}  // namespace jarvis_bridges

RCLCPP_COMPONENTS_REGISTER_NODE(jarvis_bridges::RosoutBridge)
