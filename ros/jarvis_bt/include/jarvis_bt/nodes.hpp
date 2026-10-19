// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// BehaviorTree.CPP nodes that talk to ROS and to Jarvis, for the trees `bt.run` runs and for a
// robot's own trees (the `jarvis_bt_nodes` plugin library, e.g. in Nav2's `plugin_lib_names`):
//
//   <JarvisCallService service="/relay/reset" type="std_srvs/srv/Trigger"/>
//   <JarvisSendGoal action="/dock_robot" type="nav2_msgs/action/DockRobot" goal="{dock_id: home}"/>
//   <JarvisSendGoal action="/dock_robot" type="nav2_msgs/action/DockRobot" goal="{dock_goal}"/>
//   <JarvisFaultActive code="sensor.scan.stale" component="lidar"/>
//   <JarvisWaitFaultCleared code="sensor.scan.stale" timeout="5"/>
//
// Requests, goals and expectations are YAML (or JSON) of a type named at run time, as in the
// `service.call` and `action.goal` steps of a plan. In braces they are a map, unless they name a
// blackboard entry that is set (`{dock_goal}`): then they are that entry's text. The nodes take their ROS node from the
// blackboard entry `node` (Nav2's convention) and spin a callback group of their own while they
// wait, so they work in any tree whoever ticks it.

#pragma once

// First: BehaviorTree.CPP bundles an older nlohmann/json under the same include guard, and Jarvis's
// libraries are built against the system one.
#include <nlohmann/json.hpp>
// clang-format off
#include <behaviortree_cpp/action_node.h>
#include <behaviortree_cpp/bt_factory.h>

#include <chrono>
#include <jarvis_interfaces/msg/fault_array.hpp>
#include <jarvis_ros/message.hpp>
#include <map>
#include <memory>
#include <optional>
#include <rclcpp/generic_client.hpp>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp_action/generic_client.hpp>
#include <string>
// clang-format on

namespace jarvis_bt
{

/// The ROS side of the nodes on one rclcpp node: a callback group spun by whoever waits, the
/// clients (by type and name) and the latest /jarvis/faults snapshot. One per node.
class RosContext
{
public:
  static std::shared_ptr<RosContext> of(const rclcpp::Node::SharedPtr & node);

  explicit RosContext(rclcpp::Node::SharedPtr node);

  /// Handles what arrived (responses, results, snapshots), without blocking.
  void spin();

  rclcpp::GenericClient::SharedPtr service(const std::string & name, const std::string & type);
  rclcpp_action::GenericClient::SharedPtr action(
    const std::string & name, const std::string & type);

  /// The open faults, or nothing before the first snapshot. Subscribes on first use.
  const jarvis_interfaces::msg::FaultArray * faults(const std::string & topic);

  rclcpp::Node::SharedPtr node() const { return node_; }

private:
  rclcpp::Node::SharedPtr node_;
  rclcpp::CallbackGroup::SharedPtr group_;
  rclcpp::executors::SingleThreadedExecutor executor_;
  std::map<std::string, rclcpp::GenericClient::SharedPtr> services_;
  std::map<std::string, rclcpp_action::GenericClient::SharedPtr> actions_;
  std::map<std::string, rclcpp::Subscription<jarvis_interfaces::msg::FaultArray>::SharedPtr> subs_;
  std::map<std::string, std::shared_ptr<jarvis_interfaces::msg::FaultArray>> snapshots_;
};

/// A node that says why it failed: `bt.run` puts it in the step's message.
class Explained
{
public:
  virtual ~Explained() = default;
  const std::string & why() const { return why_; }

protected:
  BT::NodeStatus fail(std::string why)
  {
    why_ = std::move(why);
    return BT::NodeStatus::FAILURE;
  }
  void reset() { why_.clear(); }

private:
  std::string why_;
};

/// The base of the Jarvis nodes: the ROS context from the blackboard, a deadline.
class RosNode : public BT::StatefulActionNode, public Explained
{
public:
  RosNode(const std::string & name, const BT::NodeConfig & config);

protected:
  /// The context, or why there is none (no `node` on the blackboard).
  std::optional<std::string> attach();
  /// A YAML port (request, goal, expect): the blackboard entry it names, when it names one that
  /// is set ("{goal}"), else its text, braces included ("{dock_id: home}" is a map, not an entry).
  std::string yaml_port(const std::string & port);
  bool expired() const { return std::chrono::steady_clock::now() >= deadline_; }
  void deadline_in(double seconds);

  std::shared_ptr<RosContext> ros_;

private:
  std::chrono::steady_clock::time_point deadline_;
};

/// Calls a service of a type named at run time; SUCCESS when the response matches `expect`.
class CallService : public RosNode
{
public:
  using RosNode::RosNode;
  static BT::PortsList providedPorts();
  BT::NodeStatus onStart() override;
  BT::NodeStatus onRunning() override;
  void onHalted() override;

private:
  BT::NodeStatus finish();

  std::string name_;
  jarvis_ros::MessageType::Ptr input_;
  jarvis_ros::MessageType::Ptr output_;
  nlohmann::json expect_;
  std::shared_ptr<void> request_;
  rclcpp::GenericClient::SharedPtr client_;
  std::optional<rclcpp::GenericClient::SharedFuture> future_;
  std::int64_t request_id_{0};
};

/// Sends a goal to an action of a type named at run time; SUCCESS when it succeeds and its result
/// matches `expect`. Halting cancels the goal.
class SendGoal : public RosNode
{
public:
  using RosNode::RosNode;
  static BT::PortsList providedPorts();
  BT::NodeStatus onStart() override;
  BT::NodeStatus onRunning() override;
  void onHalted() override;

private:
  std::string name_;
  jarvis_ros::MessageType::Ptr output_;
  nlohmann::json expect_;
  std::shared_ptr<void> request_;
  rclcpp_action::GenericClient::SharedPtr client_;
  bool sent_{false};
  struct Outcome;
  std::shared_ptr<Outcome> outcome_;  // filled by the client's callbacks
};

/// SUCCESS if Jarvis has an open fault (ACTIVE or LATCHED) of that code (and component), else
/// FAILURE. Waits for the first snapshot of /jarvis/faults, at most `snapshot_timeout`.
class FaultActive : public RosNode
{
public:
  using RosNode::RosNode;
  static BT::PortsList providedPorts();
  BT::NodeStatus onStart() override;
  BT::NodeStatus onRunning() override;
  void onHalted() override {}
};

/// SUCCESS once no such fault is open (it cleared, or healed); FAILURE after `timeout`.
class WaitFaultCleared : public RosNode
{
public:
  using RosNode::RosNode;
  static BT::PortsList providedPorts();
  BT::NodeStatus onStart() override;
  BT::NodeStatus onRunning() override;
  void onHalted() override {}
};

/// Registers JarvisCallService, JarvisSendGoal, JarvisFaultActive and JarvisWaitFaultCleared.
void register_nodes(BT::BehaviorTreeFactory & factory);

/// YAML (or JSON) text as JSON: `{transition: {label: activate}}`. Throws std::runtime_error.
nlohmann::json parse_yaml(const std::string & text, const std::string & what);

/// The faults of a snapshot that are open (ACTIVE or LATCHED) and match `code` (`*` globs) and,
/// if given, `component`; for the messages, as "code (component): STATE".
std::vector<std::string> open_faults(
  const jarvis_interfaces::msg::FaultArray & snapshot, const std::string & code,
  const std::string & component);

}  // namespace jarvis_bt
