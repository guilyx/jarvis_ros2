// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// /jarvis/bridges/diagnostics: /diagnostics level changes -> FAULT_RAISE / FAULT_CLEAR (ADR-0011).

#include <jarvis_core/util.hpp>
#include <rclcpp_components/register_node_macro.hpp>

#include "jarvis_bridges/bridge_node.hpp"
#include "jarvis_bridges/translators.hpp"

namespace jarvis_bridges
{

class DiagnosticsBridge : public BridgeNode
{
public:
  explicit DiagnosticsBridge(const rclcpp::NodeOptions & options)
  : BridgeNode("diagnostics", options)
  {
    using jarvis_ros::declare;
    declare<std::string>(*this, "topic", "/diagnostics", "DiagnosticArray topic to read");
    declare<std::string>(*this, "code_prefix", "diag", "Prefix of the event codes");
    declare<std::vector<std::string>>(
      *this, "ignore_hardware_ids", {"jarvis"}, "Hardware id globs to skip (jarvis is our export)");
    declare<std::vector<std::string>>(*this, "ignore_names", {}, "Status name globs to skip");
    declare<double>(
      *this, "repeat_period_s", 0.0,
      "Repeat a steady non-OK level this often; 0 sends changes only");
    maybe_autostart();
  }

protected:
  void configure_bridge() override
  {
    DiagnosticsConfig config;
    config.code_prefix = get_parameter("code_prefix").as_string();
    if (!jarvis_core::is_valid_code(config.code_prefix)) {
      throw std::invalid_argument("code_prefix '" + config.code_prefix + "' is not a valid code");
    }
    config.ignore_hardware_ids = strings(get_parameter("ignore_hardware_ids"));
    config.ignore_names = strings(get_parameter("ignore_names"));
    config.repeat_period =
      static_cast<jarvis_core::Duration>(get_parameter("repeat_period_s").as_double() * 1e9);
    translator_ = std::make_unique<DiagnosticsTranslator>(std::move(config));
  }

  void activate_bridge() override
  {
    // Best effort matches every publisher; a lost array is made up by the next periodic one.
    sub_ = create_subscription<diagnostic_msgs::msg::DiagnosticArray>(
      get_parameter("topic").as_string(), rclcpp::QoS(rclcpp::KeepLast(100)).best_effort(),
      [this](const diagnostic_msgs::msg::DiagnosticArray & array) {
        publish(translator_->on_array(array, now_ns()));
      });
  }

  void deactivate_bridge() override { sub_.reset(); }

private:
  std::unique_ptr<DiagnosticsTranslator> translator_;
  rclcpp::Subscription<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr sub_;
};

}  // namespace jarvis_bridges

RCLCPP_COMPONENTS_REGISTER_NODE(jarvis_bridges::DiagnosticsBridge)
