// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// /jarvis/bridges/node_watchdog: expected nodes missing from the ROS graph become `ros.node.missing`
// faults (a crashed process, a node that never started); they clear when the node is back.

#include <rclcpp/create_timer.hpp>
#include <rclcpp_components/register_node_macro.hpp>
#include <set>
#include <string>
#include <vector>

#include "jarvis_bridges/bridge_node.hpp"
#include "jarvis_bridges/watchdogs.hpp"

namespace jarvis_bridges
{

class NodeWatchdogBridge : public BridgeNode
{
public:
  explicit NodeWatchdogBridge(const rclcpp::NodeOptions & options)
  : BridgeNode("node_watchdog", options)
  {
    using jarvis_ros::declare;
    declare<std::vector<std::string>>(
      *this, "nodes", {},
      "Fully qualified names of the nodes that must be up, e.g. /demo/scan_relay");
    declare<double>(*this, "grace_s", 5.0, "How long a node may be missing before it is a fault");
    declare<double>(*this, "period_s", 1.0, "How often the graph is checked");
    declare<std::string>(*this, "severity", "ERROR", "Severity of ros.node.missing");
    maybe_autostart();
  }

protected:
  void configure_bridge() override
  {
    config_ = NodeWatchConfig{};
    for (const auto & n : strings(get_parameter("nodes"))) {
      if (n.empty() || n.front() != '/') {
        throw std::invalid_argument("nodes: '" + n + "' is not a fully qualified name");
      }
      config_.nodes.push_back(n);
    }
    config_.grace = static_cast<Duration>(get_parameter("grace_s").as_double() * 1e9);
    config_.severity = severity("severity", get_parameter("severity").as_string());
    if (get_parameter("period_s").as_double() <= 0.0) {
      throw std::invalid_argument("period_s must be positive");
    }
    if (config_.nodes.empty()) {
      RCLCPP_INFO(get_logger(), "no nodes to watch");
    }
  }

  void activate_bridge() override
  {
    watchdog_ = std::make_unique<NodeWatchdog>(config_, now_ns());
    timer_ = rclcpp::create_timer(
      this, get_clock(), rclcpp::Duration::from_seconds(get_parameter("period_s").as_double()),
      [this]() {
        const auto names = get_node_graph_interface()->get_node_names();
        publish(watchdog_->tick(now_ns(), std::set<std::string>(names.begin(), names.end())));
      });
  }

  void deactivate_bridge() override
  {
    timer_.reset();
    watchdog_.reset();
  }

private:
  NodeWatchConfig config_;
  std::unique_ptr<NodeWatchdog> watchdog_;
  rclcpp::TimerBase::SharedPtr timer_;
};

}  // namespace jarvis_bridges

RCLCPP_COMPONENTS_REGISTER_NODE(jarvis_bridges::NodeWatchdogBridge)
