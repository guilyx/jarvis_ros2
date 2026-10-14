// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// /jarvis/rule_engine: complex event processing over /jarvis/events (docs/design/05-rules-and-recovery.md
// §1-7). It runs jarvis_core::RuleEngine, publishes matches and rule actions to /jarvis/ingest, sends
// `recover` actions to /jarvis/recovery as ExecuteRecovery goals, and saves fire history to disk so
// a restart cannot cause a recovery storm.

#include <algorithm>
#include <diagnostic_updater/diagnostic_updater.hpp>
#include <jarvis_core/rule_engine.hpp>
#include <jarvis_core/util.hpp>
#include <jarvis_interfaces/action/execute_recovery.hpp>
#include <jarvis_interfaces/msg/fault_array.hpp>
#include <jarvis_interfaces/srv/list_rules.hpp>
#include <jarvis_interfaces/srv/reload_config.hpp>
#include <jarvis_interfaces/srv/set_rule_state.hpp>
#include <memory>
#include <rclcpp/create_timer.hpp>
#include <rclcpp_action/rclcpp_action.hpp>
#include <rclcpp_components/register_node_macro.hpp>
#include <string>
#include <vector>

#include "jarvis_ros/common.hpp"
#include "jarvis_ros/config_dir.hpp"
#include "jarvis_ros/conversions.hpp"
#include "jarvis_ros/state_file.hpp"

namespace jarvis_ros
{

using CallbackReturn = rclcpp_lifecycle::node_interfaces::LifecycleNodeInterface::CallbackReturn;
using diagnostic_msgs::msg::DiagnosticStatus;
using jarvis_interfaces::action::ExecuteRecovery;
using jarvis_interfaces::msg::FaultArray;
using jarvis_interfaces::srv::ListRules;
using jarvis_interfaces::srv::ReloadConfig;
using jarvis_interfaces::srv::SetRuleState;

class RuleEngineNode : public rclcpp_lifecycle::LifecycleNode
{
public:
  explicit RuleEngineNode(const rclcpp::NodeOptions & options)
  : rclcpp_lifecycle::LifecycleNode("rule_engine", "jarvis", options)
  {
    declare<std::string>(*this, "robot_id", "robot", "Robot id stamped on Jarvis events");
    declare<std::string>(*this, "rules_dir", "", "Directory of jarvis.rules/v1 YAML files");
    declare<std::string>(
      *this, "state_dir", "", "Where fire history is saved; empty disables persistence");
    declare<std::string>(
      *this, "recovery_action", "/jarvis/recovery/execute", "ExecuteRecovery action server");
    declare<double>(
      *this, "recovery_wait_s", 5.0,
      "How long a recover action waits for the recovery server (e.g. at startup) before giving up");
    declare<int>(*this, "max_partitions", 1000, "Partitions kept per rule");
    declare<int>(*this, "queue_depth", 1000, "History depth of /jarvis/events and /jarvis/ingest");
    if (declare<bool>(*this, "autostart", false, "Configure and activate on startup")) {
      autostart_timer_ = create_wall_timer(std::chrono::milliseconds(1), [this]() {
        autostart_timer_->cancel();
        autostart(*this);
      });
    }
  }

  CallbackReturn on_configure(const rclcpp_lifecycle::State &) override
  {
    std::string error;
    auto rules = load_rules(error);
    if (!error.empty()) {
      RCLCPP_ERROR(get_logger(), "%s", error.c_str());
      return CallbackReturn::FAILURE;
    }
    jarvis_core::RuleEngineConfig config;
    config.robot_id = get_parameter("robot_id").as_string();
    config.node_name = get_fully_qualified_name();
    config.max_partitions = static_cast<std::size_t>(get_parameter("max_partitions").as_int());
    engine_ = std::make_unique<jarvis_core::RuleEngine>(config, std::move(rules));
    RCLCPP_INFO(get_logger(), "%zu rules", engine_->rules().rules().size());

    state_ =
      std::make_unique<StateFile>(get_parameter("state_dir").as_string(), "rule_engine.json");
    try {
      if (const auto saved = state_->load()) {
        engine_->restore(*saved);
        RCLCPP_INFO(get_logger(), "restored fire history from %s", state_->path().c_str());
      }
    } catch (const std::exception & e) {
      RCLCPP_ERROR(get_logger(), "ignoring saved state: %s", e.what());
    }

    depth_ = static_cast<std::size_t>(get_parameter("queue_depth").as_int());
    ingest_pub_ = create_event_publisher(*this, "ingest", stream_qos(depth_));
    recovery_ = rclcpp_action::create_client<ExecuteRecovery>(
      this, get_parameter("recovery_action").as_string());

    reload_srv_ = create_service<ReloadConfig>(
      "~/reload", [this](
                    const std::shared_ptr<ReloadConfig::Request> req,
                    std::shared_ptr<ReloadConfig::Response> res) { on_reload(*req, *res); });
    state_srv_ = create_service<SetRuleState>(
      "~/set_rule_state", [this](
                            const std::shared_ptr<SetRuleState::Request> req,
                            std::shared_ptr<SetRuleState::Response> res) {
        auto pick = [](std::uint8_t v) -> std::optional<bool> {
          if (v == SetRuleState::Request::SET_TRUE) {
            return true;
          }
          if (v == SetRuleState::Request::SET_FALSE) {
            return false;
          }
          return std::nullopt;
        };
        res->success =
          engine_->set_rule_state(req->rule_id, pick(req->enabled), pick(req->dry_run));
        res->message = res->success ? "updated" : "unknown rule '" + req->rule_id + "'";
        if (res->success) {
          RCLCPP_INFO(
            get_logger(), "rule %s: %s", req->rule_id.c_str(), state_text(req->rule_id).c_str());
        }
      });
    list_srv_ = create_service<ListRules>(
      "~/list_rules",
      [this](
        const std::shared_ptr<ListRules::Request> req, std::shared_ptr<ListRules::Response> res) {
        for (const auto & s : engine_->stats()) {
          if (
            !req->ids.empty() &&
            std::none_of(req->ids.begin(), req->ids.end(), [&s](const auto & g) {
              return jarvis_core::glob_match(g, s.id);
            })) {
            continue;
          }
          res->rules.push_back(to_msg(s, engine_->rules().find(s.id)));
        }
      });

    self_diagnostics_ = std::make_unique<diagnostic_updater::Updater>(this);
    self_diagnostics_->setHardwareID("jarvis");
    self_diagnostics_->add("rule engine", [this](diagnostic_updater::DiagnosticStatusWrapper & s) {
      if (!engine_) {
        s.summary(DiagnosticStatus::STALE, "not configured");
        return;
      }
      s.summary(
        persist_failures_ > 0 || unavailable_ > 0 ? DiagnosticStatus::WARN : DiagnosticStatus::OK,
        persist_failures_ > 0 ? "cannot save state"
        : unavailable_ > 0    ? "recovery server unreachable"
                              : (active_ ? "active" : "inactive"));
      s.add("rules", engine_->rules().rules().size());
      std::uint64_t fires = 0;
      for (const auto & r : engine_->stats()) {
        fires += r.fires;
      }
      s.add("fires", fires);
      s.add("recoveries_requested", requested_);
      s.add("recoveries_unavailable", unavailable_);
      s.add("state_file", state_->enabled() ? state_->path() : std::string("disabled"));
      s.add("persist_failures", persist_failures_);
    });
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_activate(const rclcpp_lifecycle::State &) override
  {
    active_ = true;
    events_sub_ = create_subscription<AdaptedEvent>(
      "events", stream_qos(depth_),
      [this](const jarvis_core::Event & e) { apply(engine_->on_event(e, now_ns(*this))); });
    faults_sub_ =
      create_subscription<FaultArray>("faults", latched_qos(), [this](const FaultArray & msg) {
        std::map<std::string, std::string> active;
        for (const auto & f : msg.faults) {
          const auto [id, view] = to_view(f);
          if (jarvis_core::is_active_fault_state(view.state)) {
            active[id] = view.code;
          }
        }
        engine_->sync_active_faults(std::move(active));
      });
    persist_timer_ =
      create_wall_timer(std::chrono::milliseconds(100), [this]() { persist_if_dirty(); });
    apply(std::exchange(pending_, {}));
    arm_deadline();
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_deactivate(const rclcpp_lifecycle::State &) override
  {
    active_ = false;
    events_sub_.reset();
    faults_sub_.reset();
    deadline_timer_.reset();
    waiting_.clear();
    retry_timer_.reset();
    persist_if_dirty();
    persist_timer_.reset();
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_cleanup(const rclcpp_lifecycle::State &) override
  {
    self_diagnostics_.reset();
    reload_srv_.reset();
    state_srv_.reset();
    list_srv_.reset();
    recovery_.reset();
    ingest_pub_.reset();
    engine_.reset();
    state_.reset();
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_shutdown(const rclcpp_lifecycle::State & state) override
  {
    if (active_) {
      on_deactivate(state);
    }
    return on_cleanup(state);
  }

private:
  struct Waiting
  {
    jarvis_core::RequestRecovery request;
    std::chrono::steady_clock::time_point deadline;
  };

  jarvis_core::RuleSet load_rules(std::string & error)
  {
    const auto dir = get_parameter("rules_dir").as_string();
    if (dir.empty()) {
      RCLCPP_WARN(get_logger(), "no rules_dir: no rules are loaded");
      return {};
    }
    try {
      return jarvis_core::RuleSet::from_directory(dir);
    } catch (const jarvis_core::RuleError & e) {
      error = e.what();
      return {};
    }
  }

  std::string state_text(const std::string & id) const
  {
    for (const auto & s : engine_->stats()) {
      if (s.id == id) {
        return std::string(s.enabled ? "enabled" : "disabled") + (s.dry_run ? ", dry run" : "");
      }
    }
    return "unknown";
  }

  void on_reload(const ReloadConfig::Request & req, ReloadConfig::Response & res)
  {
    std::string error;
    auto rules = load_rules(error);
    res.revision = directory_revision(get_parameter("rules_dir").as_string());
    if (!error.empty()) {
      res.errors.push_back(error);
      res.message = "rules not reloaded";
      return;
    }
    res.success = true;
    const auto count = rules.rules().size();
    if (req.validate_only) {
      res.message = std::to_string(count) + " rules are valid";
      return;
    }
    res.message = "loaded " + std::to_string(count) + " rules";
    apply(engine_->reload(std::move(rules), now_ns(*this)));
  }

  void apply(jarvis_core::Effects effects)
  {
    for (auto & fx : effects) {
      if (auto * emit = std::get_if<jarvis_core::EmitEvent>(&fx)) {
        if (active_) {
          ingest_pub_->publish(emit->event);
        } else {
          pending_.push_back(std::move(fx));
        }
      } else if (auto * recover = std::get_if<jarvis_core::RequestRecovery>(&fx)) {
        request_recovery(*recover);
      } else if (std::holds_alternative<jarvis_core::PersistState>(fx)) {
        dirty_ = true;
      }
    }
    arm_deadline();
  }

  /// Recover actions wait a little for the recovery server: right after startup the client may not
  /// have discovered it yet.
  void request_recovery(const jarvis_core::RequestRecovery & r)
  {
    ++requested_;
    if (recovery_->action_server_is_ready()) {
      send_goal(r);
      return;
    }
    const auto wait = std::chrono::duration<double>(get_parameter("recovery_wait_s").as_double());
    waiting_.push_back(
      {r, std::chrono::steady_clock::now() +
            std::chrono::duration_cast<std::chrono::steady_clock::duration>(wait)});
    if (!retry_timer_) {
      retry_timer_ = create_wall_timer(std::chrono::milliseconds(100), [this]() {
        const bool ready = recovery_->action_server_is_ready();
        const auto now = std::chrono::steady_clock::now();
        for (auto & w : std::exchange(waiting_, {})) {
          if (ready) {
            send_goal(w.request);
          } else if (now >= w.deadline) {
            unavailable(
              w.request,
              "recovery server not reachable at " + get_parameter("recovery_action").as_string());
          } else {
            waiting_.push_back(std::move(w));
          }
        }
        if (waiting_.empty()) {
          auto self = std::exchange(retry_timer_, nullptr);
          self->cancel();
        }
      });
    }
  }

  void send_goal(const jarvis_core::RequestRecovery & r)
  {
    ExecuteRecovery::Goal goal;
    goal.plan_id = r.plan;
    for (const auto & [k, v] : r.args) {
      jarvis_interfaces::msg::Attribute a;
      a.key = k;
      a.type = jarvis_interfaces::msg::Attribute::TYPE_STRING;
      a.string_value = v;
      goal.args.push_back(std::move(a));
    }
    goal.requester_type = ExecuteRecovery::Goal::REQUESTER_RULE;
    goal.requester_id = r.rule_id;
    goal.correlation_id = r.correlation_id;
    goal.causation_id = r.match_id;
    goal.rationale = r.rationale;
    rclcpp_action::Client<ExecuteRecovery>::SendGoalOptions options;
    options.goal_response_callback =
      [this, r](const rclcpp_action::ClientGoalHandle<ExecuteRecovery>::SharedPtr & handle) {
        if (!handle) {
          unavailable(r, "the recovery server refused the goal (not active)");
        }
      };
    // The orchestrator reports every step on the stream; the result only goes to the log.
    options.result_callback =
      [this, r](const rclcpp_action::ClientGoalHandle<ExecuteRecovery>::WrappedResult & result) {
        RCLCPP_INFO(
          get_logger(), "rule %s -> %s: %s", r.rule_id.c_str(), r.plan.c_str(),
          result.result ? result.result->reason.c_str() : "no result");
      };
    recovery_->async_send_goal(goal, options);
  }

  void unavailable(const jarvis_core::RequestRecovery & r, const std::string & why)
  {
    ++unavailable_;
    RCLCPP_WARN(
      get_logger(), "rule %s could not request %s: %s", r.rule_id.c_str(), r.plan.c_str(),
      why.c_str());
    if (!active_) {
      return;
    }
    jarvis_core::Event e;
    e.id = ids_.uuid7(now_ns(*this));
    e.stamp = now_ns(*this);
    e.source.robot = get_parameter("robot_id").as_string();
    e.source.node = get_fully_qualified_name();
    e.kind = jarvis_core::Kind::System;
    e.code = "jarvis.rule.recovery_unavailable";
    e.severity = jarvis_core::Severity::Warn;
    e.message = "rule " + r.rule_id + " could not request " + r.plan + ": " + why;
    e.attrs = {{"rule", r.rule_id}, {"plan", r.plan}};
    e.correlation_id = r.correlation_id;
    e.causation_id = r.match_id;
    ingest_pub_->publish(e);
  }

  void arm_deadline()
  {
    if (!active_ || !engine_) {
      return;
    }
    const auto next = engine_->next_deadline();
    if (!next) {
      deadline_timer_.reset();
      armed_for_ = 0;
      return;
    }
    if (deadline_timer_ && armed_for_ == *next) {
      return;
    }
    armed_for_ = *next;
    const auto delay = std::max<jarvis_core::Duration>(*next - now_ns(*this), 1'000'000);
    deadline_timer_ =
      rclcpp::create_timer(this, get_clock(), rclcpp::Duration::from_nanoseconds(delay), [this]() {
        deadline_timer_->cancel();
        auto self = std::exchange(deadline_timer_, nullptr);
        armed_for_ = 0;
        apply(engine_->advance_to(now_ns(*this)));
      });
  }

  void persist_if_dirty()
  {
    if (!dirty_ || !engine_ || !state_) {
      return;
    }
    try {
      state_->save(engine_->snapshot());
      dirty_ = false;
    } catch (const std::exception & e) {
      ++persist_failures_;
      RCLCPP_ERROR_THROTTLE(get_logger(), *get_clock(), 10000, "saving state: %s", e.what());
    }
  }

  std::unique_ptr<jarvis_core::RuleEngine> engine_;
  std::unique_ptr<StateFile> state_;
  jarvis_core::Effects pending_;
  jarvis_core::IdGenerator ids_;
  std::vector<Waiting> waiting_;

  EventPublisher::SharedPtr ingest_pub_;
  rclcpp::Subscription<AdaptedEvent>::SharedPtr events_sub_;
  rclcpp::Subscription<FaultArray>::SharedPtr faults_sub_;
  rclcpp_action::Client<ExecuteRecovery>::SharedPtr recovery_;
  rclcpp::Service<ReloadConfig>::SharedPtr reload_srv_;
  rclcpp::Service<SetRuleState>::SharedPtr state_srv_;
  rclcpp::Service<ListRules>::SharedPtr list_srv_;
  rclcpp::TimerBase::SharedPtr deadline_timer_;
  rclcpp::TimerBase::SharedPtr retry_timer_;
  rclcpp::TimerBase::SharedPtr persist_timer_;
  rclcpp::TimerBase::SharedPtr autostart_timer_;
  std::unique_ptr<diagnostic_updater::Updater> self_diagnostics_;

  std::size_t depth_{1000};
  jarvis_core::Timestamp armed_for_{0};
  std::uint64_t requested_{0};
  std::uint64_t unavailable_{0};
  bool active_{false};
  bool dirty_{false};
  std::uint64_t persist_failures_{0};
};

}  // namespace jarvis_ros

RCLCPP_COMPONENTS_REGISTER_NODE(jarvis_ros::RuleEngineNode)
