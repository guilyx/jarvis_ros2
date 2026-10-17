// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// /jarvis/bridges/lifecycle: transitions of managed nodes (`<node>/transition_event`) become STATE
// events (for guards and the dashboard) and faults when a node leaves `active` or fails. Watches the
// listed nodes, or discovers every managed node on the graph.

#include <algorithm>
#include <jarvis_core/util.hpp>
#include <lifecycle_msgs/msg/transition_event.hpp>
#include <lifecycle_msgs/srv/get_state.hpp>
#include <map>
#include <rclcpp_components/register_node_macro.hpp>
#include <string>
#include <vector>

#include "jarvis_bridges/bridge_node.hpp"
#include "jarvis_bridges/watchdogs.hpp"

namespace jarvis_bridges
{

namespace
{
constexpr std::string_view kSuffix = "/transition_event";
constexpr std::string_view kType = "lifecycle_msgs/msg/TransitionEvent";
}  // namespace

class LifecycleBridge : public BridgeNode
{
public:
  explicit LifecycleBridge(const rclcpp::NodeOptions & options) : BridgeNode("lifecycle", options)
  {
    using jarvis_ros::declare;
    declare<std::vector<std::string>>(
      *this, "nodes", {}, "Managed nodes to watch, e.g. /demo/scan_relay; empty discovers all");
    declare<std::vector<std::string>>(
      *this, "ignore_nodes", {}, "Globs of discovered nodes to leave alone");
    declare<double>(*this, "discovery_period_s", 2.0, "How often the graph is scanned");
    maybe_autostart();
  }

protected:
  void configure_bridge() override
  {
    nodes_ = strings(get_parameter("nodes"));
    ignore_ = strings(get_parameter("ignore_nodes"));
    ignore_.push_back(get_fully_qualified_name());  // our own transitions are not news
    if (nodes_.empty() && get_parameter("discovery_period_s").as_double() <= 0.0) {
      throw std::invalid_argument("discovery_period_s must be positive when no node is listed");
    }
    translator_ = std::make_unique<LifecycleTranslator>();
  }

  void activate_bridge() override
  {
    for (const auto & n : nodes_) {
      watch(n);
    }
    if (nodes_.empty()) {
      discover();
      discovery_timer_ = create_wall_timer(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::duration<double>(get_parameter("discovery_period_s").as_double())),
        [this]() { on_graph([this] { discover(); }); });
    }
  }

  void deactivate_bridge() override
  {
    discovery_timer_.reset();
    subs_.clear();
    clients_.clear();
  }

private:
  void discover()
  {
    for (const auto & [topic, types] : get_topic_names_and_types()) {
      if (
        topic.size() > kSuffix.size() && topic.ends_with(kSuffix) && types.size() == 1 &&
        types.front() == kType) {
        const auto node = topic.substr(0, topic.size() - kSuffix.size());
        const bool ignored = std::any_of(
          ignore_.begin(), ignore_.end(),
          [&node](const std::string & g) { return jarvis_core::glob_match(g, node); });
        if (!ignored) {
          watch(node);
        }
      }
    }
  }

  void watch(const std::string & node)
  {
    if (subs_.count(node) != 0) {
      return;
    }
    RCLCPP_INFO(get_logger(), "watching managed node %s", node.c_str());
    subs_[node] = create_subscription<lifecycle_msgs::msg::TransitionEvent>(
      node + std::string(kSuffix), rclcpp::QoS(rclcpp::KeepLast(10)).reliable(),
      [this, node](const lifecycle_msgs::msg::TransitionEvent & ev) {
        publish(translator_->on_transition(node, ev, now_ns()));
      });
    // A baseline, so guards know the state before the first transition.
    auto client = create_client<lifecycle_msgs::srv::GetState>(node + "/get_state");
    clients_[node] = client;
    if (client->service_is_ready()) {
      client->async_send_request(
        std::make_shared<lifecycle_msgs::srv::GetState::Request>(),
        [this, node](rclcpp::Client<lifecycle_msgs::srv::GetState>::SharedFuture f) {
          if (translator_) {
            const auto & s = f.get()->current_state;
            publish(translator_->on_state(node, s.id, s.label, now_ns()));
          }
        });
    }
  }

  std::vector<std::string> nodes_;
  std::vector<std::string> ignore_;
  std::unique_ptr<LifecycleTranslator> translator_;
  std::map<std::string, rclcpp::Subscription<lifecycle_msgs::msg::TransitionEvent>::SharedPtr>
    subs_;
  std::map<std::string, rclcpp::Client<lifecycle_msgs::srv::GetState>::SharedPtr> clients_;
  rclcpp::TimerBase::SharedPtr discovery_timer_;
};

}  // namespace jarvis_bridges

RCLCPP_COMPONENTS_REGISTER_NODE(jarvis_bridges::LifecycleBridge)
