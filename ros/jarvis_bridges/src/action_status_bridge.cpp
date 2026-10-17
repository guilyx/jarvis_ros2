// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// /jarvis/bridges/action_status: `<action>/_action/status` goal transitions -> faults and states.
// Listens to the listed actions, or discovers every action server on the graph (but Jarvis' own:
// a recovery that fails is already on the stream, as a recovery).

#include <algorithm>
#include <jarvis_core/util.hpp>
#include <map>
#include <rclcpp_components/register_node_macro.hpp>
#include <string>
#include <vector>

#include "jarvis_bridges/bridge_node.hpp"
#include "jarvis_bridges/translators.hpp"

namespace jarvis_bridges
{

namespace
{
constexpr std::string_view kStatusSuffix = "/_action/status";
constexpr std::string_view kStatusType = "action_msgs/msg/GoalStatusArray";
}  // namespace

class ActionStatusBridge : public BridgeNode
{
public:
  explicit ActionStatusBridge(const rclcpp::NodeOptions & options)
  : BridgeNode("action_status", options)
  {
    using jarvis_ros::declare;
    declare<std::vector<std::string>>(
      *this, "actions", {}, "Action names to watch, e.g. /navigate_to_pose; empty discovers all");
    declare<double>(*this, "discovery_period_s", 2.0, "How often the graph is scanned for actions");
    declare<std::vector<std::string>>(
      *this, "ignore_actions", {"/jarvis/*"},
      "Globs of discovered actions to leave alone (Jarvis' own would loop)");
    maybe_autostart();
  }

protected:
  void configure_bridge() override
  {
    actions_ = strings(get_parameter("actions"));
    ignore_ = strings(get_parameter("ignore_actions"));
    discovery_period_ =
      std::chrono::duration<double>(get_parameter("discovery_period_s").as_double());
    if (actions_.empty() && discovery_period_.count() <= 0.0) {
      throw std::invalid_argument("discovery_period_s must be positive when no action is listed");
    }
    translator_ = std::make_unique<ActionStatusTranslator>();
  }

  void activate_bridge() override
  {
    for (const auto & action : actions_) {
      watch(action);
    }
    if (actions_.empty()) {
      discover();
      discovery_timer_ = create_wall_timer(
        std::chrono::duration_cast<std::chrono::nanoseconds>(discovery_period_),
        [this]() { on_graph([this] { discover(); }); });
    }
  }

  void deactivate_bridge() override
  {
    discovery_timer_.reset();
    subs_.clear();
  }

private:
  void discover()
  {
    for (const auto & [topic, types] : get_topic_names_and_types()) {
      if (
        topic.size() > kStatusSuffix.size() && topic.ends_with(kStatusSuffix) &&
        types.size() == 1 && types.front() == kStatusType) {
        const auto action = topic.substr(0, topic.size() - kStatusSuffix.size());
        const bool ignored = std::any_of(
          ignore_.begin(), ignore_.end(),
          [&action](const std::string & g) { return jarvis_core::glob_match(g, action); });
        if (!ignored) {
          watch(action);
        }
      }
    }
  }

  void watch(const std::string & action)
  {
    if (subs_.count(action) != 0) {
      return;
    }
    RCLCPP_INFO(get_logger(), "watching action %s", action.c_str());
    // Same QoS as rcl_action's status publisher: the latest array is latched.
    subs_[action] = create_subscription<action_msgs::msg::GoalStatusArray>(
      action + std::string(kStatusSuffix),
      rclcpp::QoS(rclcpp::KeepLast(1)).reliable().transient_local(),
      [this, action](const action_msgs::msg::GoalStatusArray & array) {
        publish(translator_->on_status(action, array, now_ns()));
      });
  }

  std::vector<std::string> actions_;
  std::vector<std::string> ignore_;
  std::chrono::duration<double> discovery_period_{2.0};
  std::unique_ptr<ActionStatusTranslator> translator_;
  std::map<std::string, rclcpp::Subscription<action_msgs::msg::GoalStatusArray>::SharedPtr> subs_;
  rclcpp::TimerBase::SharedPtr discovery_timer_;
};

}  // namespace jarvis_bridges

RCLCPP_COMPONENTS_REGISTER_NODE(jarvis_bridges::ActionStatusBridge)
