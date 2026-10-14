// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Actuators turn plan steps into ROS calls (docs/design/05-rules-and-recovery.md §8, action catalog).
// They are pluginlib plugins of base class `jarvis_ros::Actuator`, loaded by /jarvis/recovery: the
// built-in ones live in jarvis_ros, Nav2's in jarvis_nav2, and a vendor adds `<vendor>.<name>` actions
// with its own package.
//
// Contract: start() never blocks. It reports the outcome through `done`, exactly once, unless
// cancel() came first; after cancel(), a late response must not reach `done`. The orchestrator
// enforces the step timeout itself (and then cancels), so an actuator may simply wait.
// Everything runs in the recovery node's default (mutually exclusive) callback group.

#pragma once

#include <chrono>
#include <functional>
#include <map>
#include <memory>
#include <nlohmann/json.hpp>
#include <optional>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp_lifecycle/lifecycle_node.hpp>
#include <string>
#include <typeinfo>
#include <vector>

namespace jarvis_ros
{

/// One attempt of an actuator step, as the orchestrator asked for it (rendered arguments).
struct Attempt
{
  std::string execution_id;
  std::string step_id;
  int attempt{1};
  std::string action;
  nlohmann::json args = nlohmann::json::object();
  std::chrono::nanoseconds timeout{0};

  std::string key() const { return execution_id + "/" + step_id + "#" + std::to_string(attempt); }
};

class Actuator
{
public:
  using Done = std::function<void(bool success, const std::string & message)>;

  virtual ~Actuator() = default;

  /// Called once, right after loading, with the recovery node.
  virtual void initialize(const rclcpp_lifecycle::LifecycleNode::SharedPtr & node) { node_ = node; }

  /// The action names this actuator provides, e.g. {"lifecycle.restart", "lifecycle.transition"}.
  virtual std::vector<std::string> actions() const = 0;

  /// Load-time check of a step's `with` (not rendered: strings may still hold `{{ }}`). Returns
  /// the problem, or nothing.
  virtual std::optional<std::string> check(
    const std::string & action, const nlohmann::json & with) const
  {
    (void)action;
    (void)with;
    return std::nullopt;
  }

  virtual void start(const Attempt & attempt, Done done) = 0;

  /// Forget an attempt in flight; its result must not be reported.
  virtual void cancel(const Attempt & attempt)
  {
    if (const auto it = tokens_.find(attempt.key()); it != tokens_.end()) {
      *it->second = false;
      tokens_.erase(it);
    }
    waits_.erase(attempt.key());
  }

protected:
  /// True while the attempt is in flight (not reported, not cancelled).
  using Token = std::shared_ptr<bool>;

  rclcpp_lifecycle::LifecycleNode::SharedPtr node() const
  {
    auto n = node_.lock();
    if (!n) {
      throw std::runtime_error("actuator used after its node was destroyed");
    }
    return n;
  }

  /// Registers an attempt and wraps `done` so it fires once, and not after cancel().
  std::pair<Token, Done> track(const Attempt & attempt, Done done)
  {
    auto token = std::make_shared<bool>(true);
    tokens_[attempt.key()] = token;
    const std::string key = attempt.key();
    Done once = [this, token, key, done = std::move(done)](bool ok, const std::string & message) {
      if (!*token) {
        return;
      }
      *token = false;
      tokens_.erase(key);
      waits_.erase(key);
      done(ok, message);
    };
    return {token, std::move(once)};
  }

  /// A service client per name, created on first use.
  template <typename ServiceT>
  typename rclcpp::Client<ServiceT>::SharedPtr client(const std::string & service)
  {
    const std::string key = std::string(typeid(ServiceT).name()) + "@" + service;
    auto & slot = clients_[key];
    if (!slot) {
      slot = node()->template create_client<ServiceT>(service);
    }
    return std::static_pointer_cast<rclcpp::Client<ServiceT>>(slot);
  }

  /// Runs `send` once the service is available: right after a restart a node's services take a
  /// moment to appear. Polls every 100 ms while the attempt is in flight (the step timeout bounds it).
  template <typename ServiceT>
  void when_ready(
    const Attempt & attempt, const Token & token,
    const typename rclcpp::Client<ServiceT>::SharedPtr & c, std::function<void()> send)
  {
    if (c->service_is_ready()) {
      send();
      return;
    }
    const std::string key = attempt.key();
    waits_[key] = node()->create_wall_timer(
      std::chrono::milliseconds(100), [this, key, token, c, send = std::move(send)]() {
        if (!*token || c->service_is_ready()) {
          auto self = waits_[key];  // keep the timer alive until this callback returns
          self->cancel();
          waits_.erase(key);
          if (*token) {
            send();
          }
        }
      });
  }

  /// A string argument, or the reason it is missing.
  static std::optional<std::string> string_arg(
    const nlohmann::json & args, const std::string & name, std::string & out)
  {
    const auto it = args.find(name);
    if (it == args.end() || !it->is_string() || it->get<std::string>().empty()) {
      return "needs '" + name + "' (a non-empty string)";
    }
    out = it->get<std::string>();
    return std::nullopt;
  }

private:
  rclcpp_lifecycle::LifecycleNode::WeakPtr node_;
  std::map<std::string, Token> tokens_;
  std::map<std::string, rclcpp::TimerBase::SharedPtr> waits_;
  std::map<std::string, std::shared_ptr<rclcpp::ClientBase>> clients_;
};

}  // namespace jarvis_ros
