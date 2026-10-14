// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Nav2 actuators for recovery plans (docs/design/05-rules-and-recovery.md §8, action catalog):
//   nav2.manage_lifecycle    nav2_msgs/srv/ManageLifecycleNodes on <manager>/manage_nodes
//   nav2.clear_costmaps      nav2_msgs/srv/ClearEntireCostmap (local, global or both)
//   nav2.cancel_navigation   cancels every NavigateToPose / NavigateThroughPoses goal
//   nav2.navigate_to_pose    sends a goal and waits for its result (e.g. go to the dock)

#include <cmath>
#include <jarvis_ros/actuator.hpp>
#include <map>
#include <memory>
#include <nav2_msgs/action/navigate_through_poses.hpp>
#include <nav2_msgs/action/navigate_to_pose.hpp>
#include <nav2_msgs/srv/clear_entire_costmap.hpp>
#include <nav2_msgs/srv/manage_lifecycle_nodes.hpp>
#include <pluginlib/class_list_macros.hpp>
#include <rclcpp_action/rclcpp_action.hpp>
#include <string>
#include <vector>

namespace jarvis_nav2
{

using jarvis_ros::Attempt;
using ManageNodes = nav2_msgs::srv::ManageLifecycleNodes;
using ClearCostmap = nav2_msgs::srv::ClearEntireCostmap;
using NavigateToPose = nav2_msgs::action::NavigateToPose;
using NavigateThroughPoses = nav2_msgs::action::NavigateThroughPoses;

namespace
{

bool is_template(const nlohmann::json & v)
{
  return v.is_string() && v.get<std::string>().find("{{") != std::string::npos;
}

const std::map<std::string, std::uint8_t> & commands()
{
  static const std::map<std::string, std::uint8_t> c{
    {"STARTUP", ManageNodes::Request::STARTUP},   {"PAUSE", ManageNodes::Request::PAUSE},
    {"RESUME", ManageNodes::Request::RESUME},     {"RESET", ManageNodes::Request::RESET},
    {"SHUTDOWN", ManageNodes::Request::SHUTDOWN}, {"CONFIGURE", ManageNodes::Request::CONFIGURE},
    {"CLEANUP", ManageNodes::Request::CLEANUP},
  };
  return c;
}

const std::vector<std::string> kNavigationActions{"/navigate_to_pose", "/navigate_through_poses"};

std::optional<double> number(const nlohmann::json & obj, const std::string & key)
{
  const auto it = obj.find(key);
  if (it == obj.end()) {
    return std::nullopt;
  }
  if (it->is_number()) {
    return it->get<double>();
  }
  if (it->is_string()) {
    try {
      std::size_t used = 0;
      const auto text = it->get<std::string>();
      const double d = std::stod(text, &used);
      return used == text.size() ? std::optional<double>(d) : std::nullopt;
    } catch (const std::exception &) {
    }
  }
  return std::nullopt;
}

}  // namespace

class Nav2Actuator : public jarvis_ros::Actuator
{
public:
  std::vector<std::string> actions() const override
  {
    return {
      "nav2.manage_lifecycle", "nav2.clear_costmaps", "nav2.cancel_navigation",
      "nav2.navigate_to_pose"};
  }

  std::optional<std::string> check(
    const std::string & action, const nlohmann::json & with) const override
  {
    if (action == "nav2.manage_lifecycle") {
      const auto m = with.find("manager");
      if (m == with.end() || !m->is_string() || m->get<std::string>().empty()) {
        return "needs 'manager' (e.g. /lifecycle_manager_navigation)";
      }
      const auto c = with.find("command");
      if (c == with.end() || !c->is_string()) {
        return "needs 'command' (STARTUP, PAUSE, RESUME, RESET, SHUTDOWN, CONFIGURE, CLEANUP)";
      }
      if (!is_template(*c) && !commands().count(c->get<std::string>())) {
        return "unknown command '" + c->get<std::string>() + "'";
      }
    } else if (action == "nav2.clear_costmaps") {
      const auto w = with.find("which");
      if (w != with.end() && !is_template(*w) && *w != "local" && *w != "global" && *w != "both") {
        return "'which' is local, global or both";
      }
    } else if (action == "nav2.navigate_to_pose") {
      const auto p = with.find("pose");
      if (p == with.end() || !p->is_object() || !p->contains("x") || !p->contains("y")) {
        return "needs 'pose' with x and y (and optionally yaw, in radians)";
      }
    }
    return std::nullopt;
  }

  void start(const Attempt & attempt, Done done) override
  {
    auto tracked = track(attempt, std::move(done));
    const Token token = tracked.first;
    const Done once = tracked.second;
    if (attempt.action == "nav2.manage_lifecycle") {
      manage(attempt, token, once);
    } else if (attempt.action == "nav2.clear_costmaps") {
      const std::string which = attempt.args.value("which", "both");
      std::vector<std::string> services;
      if (which == "local" || which == "both") {
        services.push_back(
          attempt.args.value("local_service", "/local_costmap/clear_entirely_local_costmap"));
      }
      if (which == "global" || which == "both") {
        services.push_back(
          attempt.args.value("global_service", "/global_costmap/clear_entirely_global_costmap"));
      }
      if (services.empty()) {
        return once(false, "'which' is local, global or both");
      }
      clear(attempt, token, once, services, 0);
    } else if (attempt.action == "nav2.cancel_navigation") {
      cancel_all(attempt, once);
    } else {
      navigate(attempt, token, once);
    }
  }

  void cancel(const Attempt & attempt) override
  {
    if (const auto it = goals_.find(attempt.key()); it != goals_.end()) {
      navigate_client(attempt.args.value("action", "/navigate_to_pose"))
        ->async_cancel_goal(it->second);
      goals_.erase(it);
    }
    Actuator::cancel(attempt);
  }

private:
  void manage(const Attempt & attempt, const Token & token, const Done & once)
  {
    std::string manager;
    std::string command;
    if (auto err = string_arg(attempt.args, "manager", manager)) {
      return once(false, *err);
    }
    if (auto err = string_arg(attempt.args, "command", command)) {
      return once(false, *err);
    }
    const auto it = commands().find(command);
    if (it == commands().end()) {
      return once(false, "unknown command '" + command + "'");
    }
    auto c = client<ManageNodes>(manager + "/manage_nodes");
    const auto code = it->second;
    when_ready<ManageNodes>(attempt, token, c, [=]() {
      auto request = std::make_shared<ManageNodes::Request>();
      request->command = code;
      c->async_send_request(request, [=](rclcpp::Client<ManageNodes>::SharedFuture f) {
        once(f.get()->success, manager + ": " + command + (f.get()->success ? " done" : " failed"));
      });
    });
  }

  void clear(
    const Attempt & attempt, const Token & token, const Done & once,
    std::vector<std::string> services, std::size_t index)
  {
    if (index == services.size()) {
      return once(true, "cleared " + std::to_string(services.size()) + " costmap(s)");
    }
    auto c = client<ClearCostmap>(services[index]);
    when_ready<ClearCostmap>(attempt, token, c, [=, this]() {
      c->async_send_request(
        std::make_shared<ClearCostmap::Request>(),
        [=, this](rclcpp::Client<ClearCostmap>::SharedFuture) {
          if (*token) {
            clear(attempt, token, once, services, index + 1);
          }
        });
    });
  }

  /// Cancels every goal of each navigation server that is up. A server that is not up has no goal.
  void cancel_all(const Attempt & attempt, const Done & once)
  {
    std::vector<std::string> names = kNavigationActions;
    if (const auto it = attempt.args.find("actions"); it != attempt.args.end() && it->is_array()) {
      names = it->get<std::vector<std::string>>();
    }
    auto pending = std::make_shared<std::size_t>(0);
    auto cancelled = std::make_shared<std::size_t>(0);
    std::vector<std::function<void()>> sends;
    for (const auto & name : names) {
      auto send = cancel_one(name, pending, cancelled, once);
      if (send) {
        ++*pending;
        sends.push_back(std::move(send));
      }
    }
    if (*pending == 0) {
      return once(true, "no navigation server is up: nothing to cancel");
    }
    for (auto & send : sends) {
      send();
    }
  }

  std::function<void()> cancel_one(
    const std::string & name, const std::shared_ptr<std::size_t> & pending,
    const std::shared_ptr<std::size_t> & cancelled, const Done & once)
  {
    auto finish = [pending, cancelled, once](std::size_t n) {
      *cancelled += n;
      if (--*pending == 0) {
        once(true, "cancelled " + std::to_string(*cancelled) + " navigation goal(s)");
      }
    };
    if (name.find("through_poses") != std::string::npos) {
      auto c = action_client<NavigateThroughPoses>(name);
      if (!c->action_server_is_ready()) {
        return {};
      }
      return [c, finish]() {
        c->async_cancel_all_goals(
          [finish](auto response) { finish(response ? response->goals_canceling.size() : 0); });
      };
    }
    auto c = navigate_client(name);
    if (!c->action_server_is_ready()) {
      return {};
    }
    return [c, finish]() {
      c->async_cancel_all_goals(
        [finish](auto response) { finish(response ? response->goals_canceling.size() : 0); });
    };
  }

  void navigate(const Attempt & attempt, const Token & token, const Done & once)
  {
    const auto pose = attempt.args.value("pose", nlohmann::json::object());
    const auto x = number(pose, "x");
    const auto y = number(pose, "y");
    if (!x || !y) {
      return once(false, "needs 'pose' with numeric x and y");
    }
    const double yaw = number(pose, "yaw").value_or(0.0);
    NavigateToPose::Goal goal;
    goal.pose.header.frame_id = attempt.args.value("frame", "map");
    goal.pose.header.stamp = node()->now();
    goal.pose.pose.position.x = *x;
    goal.pose.pose.position.y = *y;
    goal.pose.pose.orientation.z = std::sin(yaw / 2.0);
    goal.pose.pose.orientation.w = std::cos(yaw / 2.0);
    goal.behavior_tree = attempt.args.value("behavior_tree", "");
    const std::string name = attempt.args.value("action", "/navigate_to_pose");
    auto c = navigate_client(name);
    const std::string key = attempt.key();
    when(
      attempt, token, [c]() { return c->action_server_is_ready(); },
      [=, this]() {
        rclcpp_action::Client<NavigateToPose>::SendGoalOptions options;
        options.goal_response_callback = [=, this](const auto & handle) {
          if (!handle) {
            return once(false, name + " rejected the goal");
          }
          if (*token) {
            goals_[key] = handle;
          }
        };
        options.result_callback = [=, this](const auto & result) {
          goals_.erase(key);
          switch (result.code) {
            case rclcpp_action::ResultCode::SUCCEEDED:
              return once(true, "reached the goal");
            case rclcpp_action::ResultCode::CANCELED:
              return once(false, "navigation was cancelled");
            default:
              return once(
                false, "navigation failed" + (result.result && !result.result->error_msg.empty()
                                                ? ": " + result.result->error_msg
                                                : std::string()));
          }
        };
        c->async_send_goal(goal, options);
      });
  }

  template <typename ActionT>
  typename rclcpp_action::Client<ActionT>::SharedPtr action_client(const std::string & name)
  {
    const std::string key = std::string(typeid(ActionT).name()) + "@" + name;
    auto & slot = actions_[key];
    if (!slot) {
      slot = rclcpp_action::create_client<ActionT>(node(), name);
    }
    return std::static_pointer_cast<rclcpp_action::Client<ActionT>>(slot);
  }

  rclcpp_action::Client<NavigateToPose>::SharedPtr navigate_client(const std::string & name)
  {
    return action_client<NavigateToPose>(name);
  }

  std::map<std::string, std::shared_ptr<rclcpp_action::ClientBase>> actions_;
  std::map<std::string, rclcpp_action::ClientGoalHandle<NavigateToPose>::SharedPtr> goals_;
};

}  // namespace jarvis_nav2

PLUGINLIB_EXPORT_CLASS(jarvis_nav2::Nav2Actuator, jarvis_ros::Actuator)
