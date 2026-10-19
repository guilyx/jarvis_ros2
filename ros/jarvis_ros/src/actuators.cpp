// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// The built-in actuators (docs/design/05-rules-and-recovery.md §8, action catalog):
//   lifecycle.transition, lifecycle.restart   lifecycle_msgs ChangeState / GetState
//   service.trigger, service.set_bool, service.empty   std_srvs
//   param.set                                  rcl_interfaces SetParameters
//   bag.snapshot                               rosbag2 snapshot service
// service.call and action.goal, for any type, are in generic_actuators.cpp.

#include <algorithm>
#include <lifecycle_msgs/msg/state.hpp>
#include <lifecycle_msgs/msg/transition.hpp>
#include <lifecycle_msgs/srv/change_state.hpp>
#include <lifecycle_msgs/srv/get_state.hpp>
#include <pluginlib/class_list_macros.hpp>
#include <rcl_interfaces/srv/set_parameters.hpp>
#include <rclcpp/parameter_value.hpp>
#include <rosbag2_interfaces/srv/snapshot.hpp>
#include <std_srvs/srv/empty.hpp>
#include <std_srvs/srv/set_bool.hpp>
#include <std_srvs/srv/trigger.hpp>

#include "jarvis_ros/actuator.hpp"

namespace jarvis_ros
{

namespace
{

bool is_template(const nlohmann::json & v)
{
  return v.is_string() && v.get<std::string>().find("{{") != std::string::npos;
}

std::optional<std::string> require_string(const nlohmann::json & with, const std::string & name)
{
  const auto it = with.find(name);
  if (it == with.end() || !it->is_string() || it->get<std::string>().empty()) {
    return "needs '" + name + "' (a non-empty string)";
  }
  return std::nullopt;
}

/// `node` names are absolute: services are `<node>/change_state`, `<node>/set_parameters`...
std::optional<std::string> require_node(const nlohmann::json & with)
{
  if (auto err = require_string(with, "node")) {
    return err;
  }
  const auto & node = with.at("node");
  if (!is_template(node) && node.get<std::string>().front() != '/') {
    return "'node' must be an absolute name (e.g. /demo/scan_relay)";
  }
  return std::nullopt;
}

}  // namespace

// -- lifecycle ------------------------------------------------------------------------------------

class LifecycleActuator : public Actuator
{
public:
  std::vector<std::string> actions() const override
  {
    return {"lifecycle.transition", "lifecycle.restart"};
  }

  std::optional<std::string> check(
    const std::string & action, const nlohmann::json & with) const override
  {
    if (auto err = require_node(with)) {
      return err;
    }
    if (action == "lifecycle.transition") {
      if (auto err = require_string(with, "transition")) {
        return err;
      }
      const auto & t = with.at("transition");
      if (!is_template(t) && !transition_id(t.get<std::string>())) {
        return "unknown transition '" + t.get<std::string>() +
               "' (configure, activate, deactivate, cleanup, shutdown)";
      }
    }
    return std::nullopt;
  }

  void start(const Attempt & attempt, Done done) override
  {
    auto tracked = track(attempt, std::move(done));
    const Token token = tracked.first;
    const Done once = tracked.second;
    std::string target;
    if (auto err = string_arg(attempt.args, "node", target)) {
      return once(false, *err);
    }
    if (attempt.action == "lifecycle.transition") {
      std::string label;
      if (auto err = string_arg(attempt.args, "transition", label)) {
        return once(false, *err);
      }
      if (!transition_id(label)) {
        return once(false, "unknown transition '" + label + "'");
      }
      return walk(attempt, token, target, {label}, 0, once);
    }
    // Restart: read the current state, then walk back to active from wherever it is.
    auto get = client<lifecycle_msgs::srv::GetState>(target + "/get_state");
    when_ready<lifecycle_msgs::srv::GetState>(attempt, token, get, [=, this]() {
      get->async_send_request(
        std::make_shared<lifecycle_msgs::srv::GetState::Request>(),
        [=, this](rclcpp::Client<lifecycle_msgs::srv::GetState>::SharedFuture f) {
          if (!*token) {
            return;
          }
          using lifecycle_msgs::msg::State;
          std::vector<std::string> path;
          switch (f.get()->current_state.id) {
            case State::PRIMARY_STATE_ACTIVE:
              path = {"deactivate", "cleanup", "configure", "activate"};
              break;
            case State::PRIMARY_STATE_INACTIVE:
              path = {"cleanup", "configure", "activate"};
              break;
            case State::PRIMARY_STATE_UNCONFIGURED:
              path = {"configure", "activate"};
              break;
            default:
              return once(
                false, target + " is " + f.get()->current_state.label + ": cannot restart it");
          }
          walk(attempt, token, target, path, 0, once);
        });
    });
  }

private:
  static std::optional<std::uint8_t> transition_id(const std::string & label)
  {
    using lifecycle_msgs::msg::Transition;
    static const std::map<std::string, std::uint8_t> ids{
      {"configure", Transition::TRANSITION_CONFIGURE},
      {"activate", Transition::TRANSITION_ACTIVATE},
      {"deactivate", Transition::TRANSITION_DEACTIVATE},
      {"cleanup", Transition::TRANSITION_CLEANUP},
      {"shutdown", 0},  // the id depends on the state: the label resolves it
    };
    const auto it = ids.find(label);
    return it == ids.end() ? std::nullopt : std::optional<std::uint8_t>(it->second);
  }

  void walk(
    const Attempt & attempt, const Token & token, const std::string & target,
    std::vector<std::string> path, std::size_t index, const Done & once)
  {
    if (index == path.size()) {
      std::string done_path;
      for (const auto & t : path) {
        done_path += (done_path.empty() ? "" : " -> ") + t;
      }
      return once(true, target + ": " + done_path);
    }
    auto change = client<lifecycle_msgs::srv::ChangeState>(target + "/change_state");
    when_ready<lifecycle_msgs::srv::ChangeState>(
      attempt, token, change, [=, this, path = std::move(path)]() {
        auto request = std::make_shared<lifecycle_msgs::srv::ChangeState::Request>();
        request->transition.id = *transition_id(path[index]);
        request->transition.label = path[index];  // the label takes precedence over the id
        change->async_send_request(
          request, [=, this](rclcpp::Client<lifecycle_msgs::srv::ChangeState>::SharedFuture f) {
            if (!*token) {
              return;
            }
            if (!f.get()->success) {
              return once(false, target + ": transition '" + path[index] + "' failed");
            }
            walk(attempt, token, target, path, index + 1, once);
          });
      });
  }
};

// -- std_srvs -------------------------------------------------------------------------------------

class ServiceActuator : public Actuator
{
public:
  std::vector<std::string> actions() const override
  {
    return {"service.trigger", "service.set_bool", "service.empty"};
  }

  std::optional<std::string> check(
    const std::string & action, const nlohmann::json & with) const override
  {
    if (auto err = require_string(with, "service")) {
      return err;
    }
    if (action == "service.set_bool") {
      const auto it = with.find("data");
      if (it == with.end() || !(it->is_boolean() || is_template(*it))) {
        return "needs 'data' (true or false)";
      }
    }
    return std::nullopt;
  }

  void start(const Attempt & attempt, Done done) override
  {
    auto tracked = track(attempt, std::move(done));
    const Token token = tracked.first;
    const Done once = tracked.second;
    std::string service;
    if (auto err = string_arg(attempt.args, "service", service)) {
      return once(false, *err);
    }
    if (attempt.action == "service.trigger") {
      using Srv = std_srvs::srv::Trigger;
      auto c = client<Srv>(service);
      when_ready<Srv>(attempt, token, c, [=]() {
        c->async_send_request(
          std::make_shared<Srv::Request>(), [=](rclcpp::Client<Srv>::SharedFuture f) {
            const auto & r = *f.get();
            once(r.success, r.message.empty() ? (r.success ? "ok" : "success=false") : r.message);
          });
      });
    } else if (attempt.action == "service.set_bool") {
      using Srv = std_srvs::srv::SetBool;
      const auto data = attempt.args.find("data");
      bool value = false;
      if (data != attempt.args.end() && data->is_boolean()) {
        value = data->get<bool>();
      } else if (
        data != attempt.args.end() && data->is_string() && (*data == "true" || *data == "false")) {
        value = *data == "true";
      } else {
        return once(false, "needs 'data' (true or false)");
      }
      auto c = client<Srv>(service);
      when_ready<Srv>(attempt, token, c, [=]() {
        auto request = std::make_shared<Srv::Request>();
        request->data = value;
        c->async_send_request(request, [=](rclcpp::Client<Srv>::SharedFuture f) {
          const auto & r = *f.get();
          once(r.success, r.message.empty() ? (r.success ? "ok" : "success=false") : r.message);
        });
      });
    } else {
      using Srv = std_srvs::srv::Empty;
      auto c = client<Srv>(service);
      when_ready<Srv>(attempt, token, c, [=]() {
        c->async_send_request(
          std::make_shared<Srv::Request>(),
          [=](rclcpp::Client<Srv>::SharedFuture) { once(true, "called " + service); });
      });
    }
  }
};

// -- parameters -----------------------------------------------------------------------------------

class ParamActuator : public Actuator
{
public:
  std::vector<std::string> actions() const override { return {"param.set"}; }

  std::optional<std::string> check(const std::string &, const nlohmann::json & with) const override
  {
    if (auto err = require_node(with)) {
      return err;
    }
    if (auto err = require_string(with, "name")) {
      return err;
    }
    if (!with.contains("value") || with.at("value").is_null() || with.at("value").is_object()) {
      return "needs 'value' (a scalar or a list of scalars)";
    }
    return std::nullopt;
  }

  void start(const Attempt & attempt, Done done) override
  {
    auto tracked = track(attempt, std::move(done));
    const Token token = tracked.first;
    const Done once = tracked.second;
    std::string target;
    std::string name;
    if (auto err = string_arg(attempt.args, "node", target)) {
      return once(false, *err);
    }
    if (auto err = string_arg(attempt.args, "name", name)) {
      return once(false, *err);
    }
    const auto value = to_value(attempt.args.value("value", nlohmann::json()));
    if (!value) {
      return once(false, "'value' must be a scalar or a list of scalars of one type");
    }
    using Srv = rcl_interfaces::srv::SetParameters;
    auto c = client<Srv>(target + "/set_parameters");
    when_ready<Srv>(attempt, token, c, [=]() {
      auto request = std::make_shared<Srv::Request>();
      request->parameters.push_back(rclcpp::Parameter(name, *value).to_parameter_msg());
      c->async_send_request(request, [=](rclcpp::Client<Srv>::SharedFuture f) {
        const auto & results = f.get()->results;
        if (results.empty() || !results[0].successful) {
          return once(
            false,
            target + ": cannot set " + name +
              (results.empty() || results[0].reason.empty() ? "" : ": " + results[0].reason));
        }
        once(true, target + ": " + name + " = " + attempt.args.at("value").dump());
      });
    });
  }

private:
  static std::optional<rclcpp::ParameterValue> to_value(const nlohmann::json & v)
  {
    if (v.is_boolean()) {
      return rclcpp::ParameterValue(v.get<bool>());
    }
    if (v.is_number_integer()) {
      return rclcpp::ParameterValue(v.get<std::int64_t>());
    }
    if (v.is_number()) {
      return rclcpp::ParameterValue(v.get<double>());
    }
    if (v.is_string()) {
      return rclcpp::ParameterValue(v.get<std::string>());
    }
    if (!v.is_array() || v.empty()) {
      return std::nullopt;
    }
    auto all = [&v](auto pred) { return std::all_of(v.begin(), v.end(), pred); };
    if (all([](const auto & x) { return x.is_boolean(); })) {
      return rclcpp::ParameterValue(v.get<std::vector<bool>>());
    }
    if (all([](const auto & x) { return x.is_number_integer(); })) {
      return rclcpp::ParameterValue(v.get<std::vector<std::int64_t>>());
    }
    if (all([](const auto & x) { return x.is_number(); })) {
      return rclcpp::ParameterValue(v.get<std::vector<double>>());
    }
    if (all([](const auto & x) { return x.is_string(); })) {
      return rclcpp::ParameterValue(v.get<std::vector<std::string>>());
    }
    return std::nullopt;
  }
};

// -- recording ------------------------------------------------------------------------------------

class BagActuator : public Actuator
{
public:
  std::vector<std::string> actions() const override { return {"bag.snapshot"}; }

  void start(const Attempt & attempt, Done done) override
  {
    auto tracked = track(attempt, std::move(done));
    const Token token = tracked.first;
    const Done once = tracked.second;
    const std::string service = attempt.args.value("service", "/jarvis/blackbox/snapshot");
    using Srv = rosbag2_interfaces::srv::Snapshot;
    auto c = client<Srv>(service);
    when_ready<Srv>(attempt, token, c, [=]() {
      c->async_send_request(
        std::make_shared<Srv::Request>(), [=](rclcpp::Client<Srv>::SharedFuture f) {
          once(f.get()->success, f.get()->success ? "snapshot saved" : "snapshot failed");
        });
    });
  }
};

}  // namespace jarvis_ros

PLUGINLIB_EXPORT_CLASS(jarvis_ros::LifecycleActuator, jarvis_ros::Actuator)
PLUGINLIB_EXPORT_CLASS(jarvis_ros::ServiceActuator, jarvis_ros::Actuator)
PLUGINLIB_EXPORT_CLASS(jarvis_ros::ParamActuator, jarvis_ros::Actuator)
PLUGINLIB_EXPORT_CLASS(jarvis_ros::BagActuator, jarvis_ros::Actuator)
