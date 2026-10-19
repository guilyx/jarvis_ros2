// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_bt/nodes.hpp"

#include <fnmatch.h>
#include <yaml-cpp/yaml.h>

#include <array>
#include <charconv>
#include <mutex>
#include <random>
#include <rclcpp/create_generic_client.hpp>
#include <rclcpp/node_interfaces/node_waitables_interface.hpp>  // create_generic_client.hpp needs it
#include <rclcpp_action/create_generic_client.hpp>

namespace jarvis_bt
{

using jarvis_interfaces::msg::Fault;
using jarvis_interfaces::msg::FaultArray;

// -- helpers ----------------------------------------------------------------------------------------

namespace
{

nlohmann::json to_json(const YAML::Node & n)
{
  switch (n.Type()) {
    case YAML::NodeType::Sequence: {
      auto out = nlohmann::json::array();
      for (const auto & item : n) {
        out.push_back(to_json(item));
      }
      return out;
    }
    case YAML::NodeType::Map: {
      auto out = nlohmann::json::object();
      for (const auto & kv : n) {
        out[kv.first.as<std::string>()] = to_json(kv.second);
      }
      return out;
    }
    case YAML::NodeType::Scalar: {
      const std::string & s = n.Scalar();
      if (n.Tag() == "!") {  // quoted: a string, whatever it looks like
        return s;
      }
      if (s == "true" || s == "True" || s == "TRUE") {
        return true;
      }
      if (s == "false" || s == "False" || s == "FALSE") {
        return false;
      }
      if (s == "null" || s == "~") {
        return nullptr;
      }
      std::int64_t i = 0;
      if (auto [p, ec] = std::from_chars(s.data(), s.data() + s.size(), i);
          ec == std::errc() && p == s.data() + s.size()) {
        return i;
      }
      char * end = nullptr;
      const double d = std::strtod(s.c_str(), &end);
      if (!s.empty() && end == s.c_str() + s.size()) {
        return d;
      }
      return s;
    }
    default:
      return nullptr;
  }
}

nlohmann::json random_uuid()
{
  static std::mt19937_64 rng{std::random_device{}()};
  std::array<std::uint8_t, 16> bytes{};
  for (auto & b : bytes) {
    b = static_cast<std::uint8_t>(rng());
  }
  return nlohmann::json(bytes);
}

const char * state_name(std::uint8_t state)
{
  switch (state) {
    case Fault::STATE_PENDING:
      return "PENDING";
    case Fault::STATE_ACTIVE:
      return "ACTIVE";
    case Fault::STATE_LATCHED:
      return "LATCHED";
    default:
      return "CLEARED";
  }
}

std::string seconds(double s)
{
  char buf[32];
  std::snprintf(buf, sizeof buf, "%g s", s);
  return buf;
}

/// What a response or result says, when it does not match: "relay: response.success is false,
/// expected true (still wedged)".
std::optional<std::string> verdict(
  const std::string & who, const std::string & what, const nlohmann::json & out,
  const nlohmann::json & expect)
{
  if (const auto miss = jarvis_ros::mismatch(expect, out, what)) {
    const auto detail = jarvis_ros::message_detail(out);
    return who + ": " + *miss + (detail.empty() ? "" : " (" + detail + ")");
  }
  return std::nullopt;
}

}  // namespace

nlohmann::json parse_yaml(const std::string & text, const std::string & what)
{
  if (text.find_first_not_of(" \t\r\n") == std::string::npos) {
    return nlohmann::json::object();
  }
  try {
    return to_json(YAML::Load(text));
  } catch (const YAML::Exception & e) {
    throw std::runtime_error("'" + what + "' is not YAML: " + e.msg);
  }
}

std::vector<std::string> open_faults(
  const FaultArray & snapshot, const std::string & code, const std::string & component)
{
  std::vector<std::string> out;
  for (const auto & f : snapshot.faults) {
    if (f.state != Fault::STATE_ACTIVE && f.state != Fault::STATE_LATCHED) {
      continue;
    }
    if (fnmatch(code.c_str(), f.code.c_str(), 0) != 0) {
      continue;
    }
    if (!component.empty() && fnmatch(component.c_str(), f.source.component.c_str(), 0) != 0) {
      continue;
    }
    out.push_back(
      f.code + (f.source.component.empty() ? "" : " (" + f.source.component + ")") + ": " +
      state_name(f.state));
  }
  return out;
}

// -- RosContext -------------------------------------------------------------------------------------

std::shared_ptr<RosContext> RosContext::of(const rclcpp::Node::SharedPtr & node)
{
  static std::mutex mutex;
  static std::map<const rclcpp::Node *, std::weak_ptr<RosContext>> contexts;
  std::lock_guard<std::mutex> lock(mutex);
  auto & slot = contexts[node.get()];
  auto context = slot.lock();
  if (!context || context->node_ != node) {
    context = std::make_shared<RosContext>(node);
    slot = context;
  }
  return context;
}

RosContext::RosContext(rclcpp::Node::SharedPtr node) : node_(std::move(node))
{
  group_ = node_->create_callback_group(rclcpp::CallbackGroupType::MutuallyExclusive, false);
  executor_.add_callback_group(group_, node_->get_node_base_interface());
}

void RosContext::spin() { executor_.spin_some(std::chrono::nanoseconds(0)); }

rclcpp::GenericClient::SharedPtr RosContext::service(
  const std::string & name, const std::string & type)
{
  auto & c = services_[type + "@" + name];
  if (!c) {
    c = rclcpp::create_generic_client(node_, name, type, rclcpp::ServicesQoS(), group_);
  }
  return c;
}

rclcpp_action::GenericClient::SharedPtr RosContext::action(
  const std::string & name, const std::string & type)
{
  auto & c = actions_[type + "@" + name];
  if (!c) {
    c = rclcpp_action::create_generic_client(node_, name, type, group_);
  }
  return c;
}

const FaultArray * RosContext::faults(const std::string & topic)
{
  if (!subs_.count(topic)) {
    rclcpp::SubscriptionOptions options;
    options.callback_group = group_;
    // /jarvis/faults is latched: the snapshot is there as soon as we subscribe.
    subs_[topic] = node_->create_subscription<FaultArray>(
      topic, rclcpp::QoS(1).reliable().transient_local(),
      [this, topic](FaultArray::SharedPtr msg) { snapshots_[topic] = std::move(msg); }, options);
  }
  spin();
  const auto it = snapshots_.find(topic);
  return it == snapshots_.end() ? nullptr : it->second.get();
}

// -- RosNode ----------------------------------------------------------------------------------------

RosNode::RosNode(const std::string & name, const BT::NodeConfig & config)
: BT::StatefulActionNode(name, config)
{
}

std::optional<std::string> RosNode::attach()
{
  rclcpp::Node::SharedPtr node;
  try {
    if (!config().blackboard->get("node", node) || !node) {
      return "no ROS node on the blackboard (the entry 'node', an rclcpp::Node::SharedPtr)";
    }
  } catch (const std::exception & e) {
    return std::string("the blackboard entry 'node' is not an rclcpp::Node::SharedPtr: ") +
           e.what();
  }
  ros_ = RosContext::of(node);
  return std::nullopt;
}

std::string RosNode::yaml_port(const std::string & port)
{
  const auto it = config().input_ports.find(port);
  if (it == config().input_ports.end()) {
    return getInput<std::string>(port).value_or("");
  }
  BT::StringView key;
  if (BT::TreeNode::isBlackboardPointer(it->second, &key)) {
    const auto entry = config().blackboard->getEntry(std::string(key));
    if (entry && !entry->value.empty()) {
      if (auto value = getInput<std::string>(port)) {
        return *value;
      }
    }
  }
  return it->second;
}

void RosNode::deadline_in(double seconds)
{
  deadline_ = seconds > 0 ? std::chrono::steady_clock::now() +
                              std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                                std::chrono::duration<double>(seconds))
                          : std::chrono::steady_clock::time_point::max();
}

// -- JarvisCallService ------------------------------------------------------------------------------

BT::PortsList CallService::providedPorts()
{
  return {
    BT::InputPort<std::string>("service", "the service, e.g. /relay/reset"),
    BT::InputPort<std::string>("type", "its type, e.g. std_srvs/srv/Trigger"),
    BT::InputPort<std::string>("request", "{}", "the request, YAML or JSON"),
    BT::InputPort<std::string>(
      "expect", "", "fields the response must have (default: success: true, if it has one)"),
    BT::InputPort<double>("server_timeout", 5.0, "seconds to wait for the service to appear"),
    BT::InputPort<double>("timeout", 30.0, "seconds to wait for the response (0: no limit)"),
    BT::OutputPort<std::string>("response", "the response, as JSON"),
  };
}

BT::NodeStatus CallService::onStart()
{
  reset();
  future_.reset();
  if (auto err = attach()) {
    return fail(*err);
  }
  std::string type;
  if (!getInput("service", name_) || name_.empty()) {
    return fail("needs 'service'");
  }
  if (!getInput("type", type) || type.empty()) {
    return fail("needs 'type', e.g. std_srvs/srv/Trigger");
  }
  try {
    std::tie(input_, output_) = jarvis_ros::MessageType::service(type);
    request_ = input_->create();
    const auto request = parse_yaml(yaml_port("request"), "request");
    if (auto err = input_->fill(request_.get(), request, "request")) {
      return fail(*err);
    }
    const auto expect = yaml_port("expect");
    expect_ = expect.empty() ? output_->default_expect() : parse_yaml(expect, "expect");
    client_ = ros_->service(name_, type);
  } catch (const std::exception & e) {
    return fail(e.what());
  }
  deadline_in(getInput<double>("server_timeout").value_or(5.0));
  return onRunning();
}

BT::NodeStatus CallService::onRunning()
{
  ros_->spin();
  if (!future_) {
    if (!client_->service_is_ready()) {
      return expired() ? fail(name_ + " is not available") : BT::NodeStatus::RUNNING;
    }
    auto sent = client_->async_send_request(request_.get());
    request_id_ = sent.request_id;
    future_ = sent.future.share();
    deadline_in(getInput<double>("timeout").value_or(30.0));
    return BT::NodeStatus::RUNNING;
  }
  if (future_->wait_for(std::chrono::seconds(0)) != std::future_status::ready) {
    if (expired()) {
      onHalted();
      return fail(name_ + ": no response");
    }
    return BT::NodeStatus::RUNNING;
  }
  return finish();
}

BT::NodeStatus CallService::finish()
{
  const auto out = output_->to_json(future_->get().get());
  future_.reset();
  setOutput("response", out.dump());
  if (auto err = verdict(name_, "response", out, expect_)) {
    return fail(*err);
  }
  return BT::NodeStatus::SUCCESS;
}

void CallService::onHalted()
{
  if (client_ && future_) {
    client_->remove_pending_request(request_id_);
  }
  future_.reset();
}

// -- JarvisSendGoal ---------------------------------------------------------------------------------

struct SendGoal::Outcome
{
  bool responded{false};
  bool halted{false};
  rclcpp_action::GenericClientGoalHandle::SharedPtr handle;
  bool done{false};
  rclcpp_action::GenericClientGoalHandle::ResultCode code{
    rclcpp_action::GenericClientGoalHandle::ResultCode::UNKNOWN};
  nlohmann::json result = nlohmann::json::object();
};

BT::PortsList SendGoal::providedPorts()
{
  return {
    BT::InputPort<std::string>("action", "the action, e.g. /dock_robot"),
    BT::InputPort<std::string>("type", "its type, e.g. nav2_msgs/action/DockRobot"),
    BT::InputPort<std::string>("goal", "{}", "the goal, YAML or JSON"),
    BT::InputPort<std::string>(
      "expect", "", "fields the result must have (default: success: true, if it has one)"),
    BT::InputPort<double>("server_timeout", 5.0, "seconds to wait for the action server"),
    BT::InputPort<double>("timeout", 0.0, "seconds to wait for the result (0: no limit)"),
    BT::OutputPort<std::string>("result", "the result, as JSON"),
  };
}

BT::NodeStatus SendGoal::onStart()
{
  reset();
  sent_ = false;
  outcome_ = std::make_shared<Outcome>();
  if (auto err = attach()) {
    return fail(*err);
  }
  std::string type;
  if (!getInput("action", name_) || name_.empty()) {
    return fail("needs 'action'");
  }
  if (!getInput("type", type) || type.find("/action/") == std::string::npos) {
    return fail("needs 'type', an action type, e.g. nav2_msgs/action/DockRobot");
  }
  try {
    const auto input = jarvis_ros::MessageType::message(type + "_SendGoal_Request");
    output_ = jarvis_ros::MessageType::message(type + "_Result");
    request_ = input->create();
    const auto goal = parse_yaml(yaml_port("goal"), "goal");
    if (
      auto err =
        input->fill(request_.get(), {{"goal", goal}, {"goal_id", {{"uuid", random_uuid()}}}}, "")) {
      return fail(*err);
    }
    const auto expect = yaml_port("expect");
    expect_ = expect.empty() ? output_->default_expect() : parse_yaml(expect, "expect");
    client_ = ros_->action(name_, type);
  } catch (const std::exception & e) {
    return fail(e.what());
  }
  deadline_in(getInput<double>("server_timeout").value_or(5.0));
  return onRunning();
}

BT::NodeStatus SendGoal::onRunning()
{
  ros_->spin();
  const auto o = outcome_;
  if (!sent_) {
    if (!client_->action_server_is_ready()) {
      return expired() ? fail(name_ + " is not available") : BT::NodeStatus::RUNNING;
    }
    rclcpp_action::GenericClient::SendGoalOptions options;
    std::weak_ptr<rclcpp_action::GenericClient> client = client_;
    options.goal_response_callback = [o, client](const auto & handle) {
      o->responded = true;
      o->handle = handle;
      if (handle && o->halted) {  // halted while the goal was on its way
        if (auto c = client.lock()) {
          c->async_cancel_goal(handle);
        }
      }
    };
    const auto output = output_;
    options.result_callback = [o, output](const auto & r) {
      o->done = true;
      o->code = r.code;
      if (r.result) {
        o->result = output->to_json(r.result);
      }
    };
    client_->async_send_goal(request_.get(), options);
    sent_ = true;
    deadline_in(getInput<double>("timeout").value_or(0.0));
    return BT::NodeStatus::RUNNING;
  }
  if (o->responded && !o->handle) {
    return fail(name_ + " rejected the goal");
  }
  if (!o->done) {
    if (expired()) {
      onHalted();
      return fail(name_ + ": no result");
    }
    return BT::NodeStatus::RUNNING;
  }
  setOutput("result", o->result.dump());
  const auto detail = jarvis_ros::message_detail(o->result);
  switch (o->code) {
    case rclcpp_action::GenericClientGoalHandle::ResultCode::SUCCEEDED:
      if (auto err = verdict(name_, "result", o->result, expect_)) {
        return fail(*err);
      }
      return BT::NodeStatus::SUCCESS;
    case rclcpp_action::GenericClientGoalHandle::ResultCode::CANCELED:
      return fail(name_ + ": the goal was canceled" + (detail.empty() ? "" : ": " + detail));
    case rclcpp_action::GenericClientGoalHandle::ResultCode::ABORTED:
      return fail(name_ + ": the goal was aborted" + (detail.empty() ? "" : ": " + detail));
    default:
      return fail(name_ + ": the goal ended in an unknown state");
  }
}

void SendGoal::onHalted()
{
  if (!outcome_ || outcome_->done) {
    return;
  }
  outcome_->halted = true;
  if (outcome_->handle) {
    client_->async_cancel_goal(outcome_->handle);
  }
}

// -- JarvisFaultActive, JarvisWaitFaultCleared ------------------------------------------------------

namespace
{

BT::PortsList fault_ports()
{
  return {
    BT::InputPort<std::string>("code", "the fault code (`*` globs), e.g. sensor.scan.stale"),
    BT::InputPort<std::string>("component", "", "only on this component (`*` globs)"),
    BT::InputPort<std::string>("topic", "/jarvis/faults", "Jarvis's open faults (latched)"),
  };
}

std::string what(const std::string & code, const std::string & component)
{
  return code + (component.empty() ? "" : " on " + component);
}

}  // namespace

BT::PortsList FaultActive::providedPorts()
{
  auto ports = fault_ports();
  ports.insert(
    BT::InputPort<double>("snapshot_timeout", 2.0, "seconds to wait for Jarvis's first snapshot"));
  return ports;
}

BT::NodeStatus FaultActive::onStart()
{
  reset();
  if (auto err = attach()) {
    return fail(*err);
  }
  if (getInput<std::string>("code").value_or("").empty()) {
    return fail("needs 'code'");
  }
  deadline_in(getInput<double>("snapshot_timeout").value_or(2.0));
  return onRunning();
}

BT::NodeStatus FaultActive::onRunning()
{
  const auto topic = getInput<std::string>("topic").value_or("/jarvis/faults");
  const auto code = getInput<std::string>("code").value_or("");
  const auto component = getInput<std::string>("component").value_or("");
  const auto * snapshot = ros_->faults(topic);
  if (!snapshot) {
    return expired() ? fail("nothing on " + topic + " (is Jarvis running?)")
                     : BT::NodeStatus::RUNNING;
  }
  if (open_faults(*snapshot, code, component).empty()) {
    return fail("no open " + what(code, component));
  }
  return BT::NodeStatus::SUCCESS;
}

BT::PortsList WaitFaultCleared::providedPorts()
{
  auto ports = fault_ports();
  ports.insert(BT::InputPort<double>("timeout", 10.0, "seconds to wait for it to clear"));
  return ports;
}

BT::NodeStatus WaitFaultCleared::onStart()
{
  reset();
  if (auto err = attach()) {
    return fail(*err);
  }
  if (getInput<std::string>("code").value_or("").empty()) {
    return fail("needs 'code'");
  }
  deadline_in(getInput<double>("timeout").value_or(10.0));
  return onRunning();
}

BT::NodeStatus WaitFaultCleared::onRunning()
{
  const auto topic = getInput<std::string>("topic").value_or("/jarvis/faults");
  const auto code = getInput<std::string>("code").value_or("");
  const auto component = getInput<std::string>("component").value_or("");
  const auto * snapshot = ros_->faults(topic);
  const auto open = snapshot ? open_faults(*snapshot, code, component) : std::vector<std::string>{};
  if (snapshot && open.empty()) {
    return BT::NodeStatus::SUCCESS;
  }
  if (!expired()) {
    return BT::NodeStatus::RUNNING;
  }
  const auto timeout = seconds(getInput<double>("timeout").value_or(10.0));
  return fail(
    snapshot ? open.front() + ", still, after " + timeout
             : "nothing on " + topic + " after " + timeout + " (is Jarvis running?)");
}

void register_nodes(BT::BehaviorTreeFactory & factory)
{
  factory.registerNodeType<CallService>("JarvisCallService");
  factory.registerNodeType<SendGoal>("JarvisSendGoal");
  factory.registerNodeType<FaultActive>("JarvisFaultActive");
  factory.registerNodeType<WaitFaultCleared>("JarvisWaitFaultCleared");
}

}  // namespace jarvis_bt
