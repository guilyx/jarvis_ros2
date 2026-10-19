// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// /jarvis/bridges/qos_watchdog: publishers and subscriptions whose QoS cannot match (a best-effort
// publisher and a reliable subscription, ...) never exchange a message, and nothing in ROS says so.
// It reads the QoS of every endpoint on the graph and raises `ros.topic.qos_incompatible` per
// topic until they match again.

#include <jarvis_core/util.hpp>
#include <map>
#include <rclcpp/create_timer.hpp>
#include <rclcpp_components/register_node_macro.hpp>
#include <string>
#include <vector>

#include "jarvis_bridges/bridge_node.hpp"
#include "jarvis_bridges/watchdogs.hpp"

namespace jarvis_bridges
{

class QosWatchdogBridge : public BridgeNode
{
public:
  explicit QosWatchdogBridge(const rclcpp::NodeOptions & options)
  : BridgeNode("qos_watchdog", options)
  {
    using jarvis_ros::declare;
    declare<double>(*this, "period_s", 2.0, "How often the graph's QoS is checked");
    declare<std::vector<std::string>>(
      *this, "ignore_topics", {}, "Topics left out (globs, e.g. /debug/*)");
    declare<std::vector<std::string>>(
      *this, "ignore_nodes", {}, "Nodes whose endpoints are left out (globs on the full name)");
    declare<std::string>(*this, "severity", "ERROR", "Severity of ros.topic.qos_incompatible");
    maybe_autostart();
  }

protected:
  void configure_bridge() override
  {
    if (get_parameter("period_s").as_double() <= 0.0) {
      throw std::invalid_argument("period_s must be positive");
    }
    ignore_topics_ = strings(get_parameter("ignore_topics"));
    ignore_nodes_ = strings(get_parameter("ignore_nodes"));
    severity_ = severity("severity", get_parameter("severity").as_string());
  }

  void activate_bridge() override
  {
    watchdog_ = std::make_unique<QosWatchdog>(severity_);
    timer_ = rclcpp::create_timer(
      this, get_clock(), rclcpp::Duration::from_seconds(get_parameter("period_s").as_double()),
      [this]() { on_graph([this] { publish(watchdog_->tick(now_ns(), read_graph())); }); });
  }

  void deactivate_bridge() override
  {
    timer_.reset();
    watchdog_.reset();
  }

private:
  static bool matches(const std::vector<std::string> & globs, const std::string & name)
  {
    for (const auto & g : globs) {
      if (jarvis_core::glob_match(g, name)) {
        return true;
      }
    }
    return false;
  }

  std::vector<QosEndpoint> endpoints(const std::vector<rclcpp::TopicEndpointInfo> & infos) const
  {
    std::vector<QosEndpoint> out;
    for (const auto & info : infos) {
      const std::string & ns = info.node_namespace();
      std::string node = ns + (!ns.empty() && ns.back() == '/' ? "" : "/") + info.node_name();
      if (!matches(ignore_nodes_, node)) {
        out.push_back({std::move(node), info.qos_profile().get_rmw_qos_profile()});
      }
    }
    return out;
  }

  std::map<std::string, TopicEndpoints> read_graph()
  {
    std::map<std::string, TopicEndpoints> graph;
    for (const auto & [topic, types] : get_topic_names_and_types()) {
      if (matches(ignore_topics_, topic)) {
        continue;
      }
      TopicEndpoints e{
        endpoints(get_publishers_info_by_topic(topic)),
        endpoints(get_subscriptions_info_by_topic(topic))};
      if (!e.publishers.empty() && !e.subscriptions.empty()) {
        graph.emplace(topic, std::move(e));
      }
    }
    return graph;
  }

  std::vector<std::string> ignore_topics_;
  std::vector<std::string> ignore_nodes_;
  Severity severity_{Severity::Error};
  std::unique_ptr<QosWatchdog> watchdog_;
  rclcpp::TimerBase::SharedPtr timer_;
};

}  // namespace jarvis_bridges

RCLCPP_COMPONENTS_REGISTER_NODE(jarvis_bridges::QosWatchdogBridge)
