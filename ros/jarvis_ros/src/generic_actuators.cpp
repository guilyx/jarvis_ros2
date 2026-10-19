// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Generic actuators: any service, any action, of a type named in the plan, without writing an
// actuator (docs/design/05-rules-and-recovery.md §8):
//
//   - id: reset
//     action: service.call
//     with:
//       service: /lidar/reset
//       type: vendor_msgs/srv/ResetDriver
//       request: { mode: 2 }
//       expect: { success: true }        # the default when the response has a boolean `success`
//
//   - id: dock
//     action: action.goal
//     with:
//       action: /dock_robot
//       type: nav2_msgs/action/DockRobot
//       goal: { dock_id: home }
//       expect: { success: true }        # on the result, which must also be SUCCEEDED
//
// Types are loaded by name (their C++ introspection type support), so the recovery node checks
// requests, goals and expectations when it loads the plans: an unknown type or field fails the
// load, before anything runs. A step timeout cancels a goal in flight.

#include <array>
#include <pluginlib/class_list_macros.hpp>
#include <random>
#include <rclcpp/create_generic_client.hpp>
#include <rclcpp/generic_client.hpp>
#include <rclcpp/node_interfaces/node_waitables_interface.hpp>  // create_generic_client.hpp needs it
#include <rclcpp_action/create_generic_client.hpp>
#include <rclcpp_action/generic_client.hpp>

#include "jarvis_ros/actuator.hpp"
#include "jarvis_ros/message.hpp"

namespace jarvis_ros
{

namespace
{

/// A service's or an action's types: what we send, what comes back.
struct Interface
{
  MessageType::Ptr input;   // a service's request; an action's SendGoal request (goal_id, goal)
  MessageType::Ptr output;  // a service's response; an action's result
};

nlohmann::json random_uuid()
{
  static std::mt19937_64 rng{std::random_device{}()};
  std::array<std::uint8_t, 16> bytes{};
  for (auto & b : bytes) {
    b = static_cast<std::uint8_t>(rng());
  }
  return nlohmann::json(bytes);
}

/// The outcome of a response or result against its expectation.
std::pair<bool, std::string> verdict(
  const std::string & who, const std::string & what, const nlohmann::json & out,
  const nlohmann::json & expect)
{
  const auto detail = message_detail(out);
  if (const auto miss = mismatch(expect, out, what)) {
    return {false, who + ": " + *miss + (detail.empty() ? "" : " (" + detail + ")")};
  }
  if (!detail.empty()) {
    return {true, who + ": " + detail};
  }
  return {true, who + (out.empty() ? ": done" : ": " + message_summary(out))};
}

}  // namespace

class GenericActuator : public Actuator
{
public:
  std::vector<std::string> actions() const override { return {"service.call", "action.goal"}; }

  std::optional<std::string> check(
    const std::string & action, const nlohmann::json & with) const override
  {
    const bool service = action == "service.call";
    std::string target;
    std::string type;
    if (auto err = string_arg(with, service ? "service" : "action", target)) {
      return err;
    }
    if (auto err = string_arg(with, "type", type)) {
      return *err + (service ? ", e.g. std_srvs/srv/Trigger" : ", e.g. nav2_msgs/action/DockRobot");
    }
    if (type.find("{{") != std::string::npos) {
      return "'type' cannot be a template: the plan is checked against it";
    }
    try {
      const auto & t = types(service, type);
      const auto input = with.value(service ? "request" : "goal", nlohmann::json::object());
      if (
        auto err = t.input->fill(
          t.input->create().get(), service ? input : nlohmann::json{{"goal", input}},
          service ? "request" : "", true)) {
        return err;
      }
      if (const auto it = with.find("expect"); it != with.end()) {
        if (auto err = t.output->fill(t.output->create().get(), *it, "expect", true)) {
          return err;
        }
      }
    } catch (const std::exception & e) {
      return e.what();
    }
    return std::nullopt;
  }

  void start(const Attempt & attempt, Done done) override
  {
    auto tracked = track(attempt, std::move(done));
    const Token token = tracked.first;
    const Done once = tracked.second;
    const bool service = attempt.action == "service.call";
    std::string target;
    std::string type;
    if (auto err = string_arg(attempt.args, service ? "service" : "action", target)) {
      return once(false, *err);
    }
    if (auto err = string_arg(attempt.args, "type", type)) {
      return once(false, *err);
    }
    try {
      const Interface t = types(service, type);
      const auto expect =
        attempt.args.contains("expect") ? attempt.args.at("expect") : t.output->default_expect();
      auto input = t.input->create();
      const auto args = attempt.args.value(service ? "request" : "goal", nlohmann::json::object());
      if (
        auto err = t.input->fill(
          input.get(),
          service ? args : nlohmann::json{{"goal", args}, {"goal_id", {{"uuid", random_uuid()}}}},
          service ? "request" : "")) {
        return once(false, *err);
      }
      if (service) {
        call(attempt, token, once, target, type, t, input, expect);
      } else {
        send_goal(attempt, token, once, target, type, t, input, expect);
      }
    } catch (const std::exception & e) {
      once(false, e.what());
    }
  }

  void cancel(const Attempt & attempt) override
  {
    if (const auto it = calls_.find(attempt.key()); it != calls_.end()) {
      it->second.first->remove_pending_request(it->second.second);
      calls_.erase(it);
    }
    if (const auto it = goals_.find(attempt.key()); it != goals_.end()) {
      it->second.first->async_cancel_goal(it->second.second);
      goals_.erase(it);
    }
    Actuator::cancel(attempt);
  }

private:
  const Interface & types(bool service, const std::string & type) const
  {
    auto & slot = types_[type];
    if (!slot.input) {
      if (service) {
        auto [request, response] = MessageType::service(type);
        slot = {request, response};
      } else {
        // An action's parts are types of their own: <type>_SendGoal_Request, <type>_Result.
        if (type.find("/action/") == std::string::npos) {
          throw std::runtime_error("'" + type + "' is not an action type (package/action/Name)");
        }
        try {
          slot = {
            MessageType::message(type + "_SendGoal_Request"),
            MessageType::message(type + "_Result")};
        } catch (const std::runtime_error &) {
          throw std::runtime_error(
            "unknown type '" + type +
            "': is its package installed and sourced, and the name right?");
        }
      }
    }
    return slot;
  }

  void call(
    const Attempt & attempt, const Token & token, const Done & once, const std::string & name,
    const std::string & type, const Interface & t, const std::shared_ptr<void> & request,
    const nlohmann::json & expect)
  {
    auto & c = services_[type + "@" + name];
    if (!c) {
      c = rclcpp::create_generic_client(node(), name, type);
    }
    const auto client = c;
    const std::string key = attempt.key();
    when(
      attempt, token, [client]() { return client->service_is_ready(); },
      [=, this]() {
        auto sent = client->async_send_request(request.get());
        calls_[key] = {client, sent.request_id};
        auto future = sent.future.share();
        // GenericClient answers through a future only: look at it until it is set.
        when(
          attempt, token,
          [future]() {
            return future.wait_for(std::chrono::seconds(0)) == std::future_status::ready;
          },
          [=, this]() {
            calls_.erase(key);
            const auto out = t.output->to_json(future.get().get());
            const auto [ok, message] = verdict(name, "response", out, expect);
            once(ok, message);
          });
      });
  }

  void send_goal(
    const Attempt & attempt, const Token & token, const Done & once, const std::string & name,
    const std::string & type, const Interface & t, const std::shared_ptr<void> & request,
    const nlohmann::json & expect)
  {
    auto & c = actions_[type + "@" + name];
    if (!c) {
      c = rclcpp_action::create_generic_client(node(), name, type);
    }
    const auto client = c;
    const std::string key = attempt.key();
    when(
      attempt, token, [client]() { return client->action_server_is_ready(); },
      [=, this]() {
        rclcpp_action::GenericClient::SendGoalOptions options;
        options.goal_response_callback = [=, this](const auto & handle) {
          if (!handle) {
            return once(false, name + " rejected the goal");
          }
          if (*token) {
            goals_[key] = {client, handle};
          } else {
            client->async_cancel_goal(handle);  // the step ended while the goal was on its way
          }
        };
        options.result_callback = [=, this](const auto & result) {
          goals_.erase(key);
          const auto out =
            result.result ? t.output->to_json(result.result) : nlohmann::json::object();
          const auto detail = message_detail(out);
          using Code = rclcpp_action::GenericClientGoalHandle::ResultCode;
          switch (result.code) {
            case Code::SUCCEEDED: {
              const auto [ok, message] = verdict(name, "result", out, expect);
              return once(ok, message);
            }
            case Code::CANCELED:
              return once(
                false, name + ": the goal was canceled" + (detail.empty() ? "" : ": " + detail));
            case Code::ABORTED:
              return once(
                false, name + ": the goal was aborted" + (detail.empty() ? "" : ": " + detail));
            default:
              return once(false, name + ": the goal ended in an unknown state");
          }
        };
        client->async_send_goal(request.get(), options);
      });
  }

  mutable std::map<std::string, Interface> types_;
  std::map<std::string, rclcpp::GenericClient::SharedPtr> services_;
  std::map<std::string, rclcpp_action::GenericClient::SharedPtr> actions_;
  std::map<std::string, std::pair<rclcpp::GenericClient::SharedPtr, std::int64_t>> calls_;
  std::map<
    std::string,
    std::pair<
      rclcpp_action::GenericClient::SharedPtr, rclcpp_action::GenericClientGoalHandle::SharedPtr>>
    goals_;
};

}  // namespace jarvis_ros

PLUGINLIB_EXPORT_CLASS(jarvis_ros::GenericActuator, jarvis_ros::Actuator)
