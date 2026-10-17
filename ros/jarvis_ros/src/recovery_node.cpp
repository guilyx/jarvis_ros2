// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// /jarvis/recovery: the one gate to recovery (docs/design/05-rules-and-recovery.md §8-11). It serves
// the ExecuteRecovery action, runs plans through jarvis_core::Orchestrator, performs actuator steps
// with pluginlib actuators, keeps the latched /jarvis/executions, and saves its state to disk.

#include <cmath>
#include <diagnostic_updater/diagnostic_updater.hpp>
#include <jarvis_core/orchestrator.hpp>
#include <jarvis_core/util.hpp>
#include <jarvis_interfaces/action/execute_recovery.hpp>
#include <jarvis_interfaces/msg/execution_array.hpp>
#include <jarvis_interfaces/msg/fault_array.hpp>
#include <jarvis_interfaces/srv/cancel_execution.hpp>
#include <jarvis_interfaces/srv/decide_approval.hpp>
#include <jarvis_interfaces/srv/list_plans.hpp>
#include <jarvis_interfaces/srv/reload_config.hpp>
#include <jarvis_interfaces/srv/set_recovery_mode.hpp>
#include <map>
#include <memory>
#include <nlohmann/json.hpp>
#include <pluginlib/class_loader.hpp>
#include <rclcpp/create_timer.hpp>
#include <rclcpp_action/rclcpp_action.hpp>
#include <rclcpp_components/register_node_macro.hpp>
#include <set>
#include <string>
#include <vector>

#include "jarvis_ros/actuator.hpp"
#include "jarvis_ros/common.hpp"
#include "jarvis_ros/config_dir.hpp"
#include "jarvis_ros/conversions.hpp"
#include "jarvis_ros/state_file.hpp"

namespace jarvis_ros
{

using CallbackReturn = rclcpp_lifecycle::node_interfaces::LifecycleNodeInterface::CallbackReturn;
using diagnostic_msgs::msg::DiagnosticStatus;
using jarvis_interfaces::action::ExecuteRecovery;
using jarvis_interfaces::msg::ExecutionArray;
using jarvis_interfaces::msg::FaultArray;
using jarvis_interfaces::srv::CancelExecution;
using jarvis_interfaces::srv::DecideApproval;
using jarvis_interfaces::srv::ListPlans;
using jarvis_interfaces::srv::ReloadConfig;
using jarvis_interfaces::srv::SetRecoveryMode;
using GoalHandle = rclcpp_action::ServerGoalHandle<ExecuteRecovery>;

const std::vector<std::string> kBuiltinActuators{
  "jarvis_ros::LifecycleActuator", "jarvis_ros::ServiceActuator", "jarvis_ros::ParamActuator",
  "jarvis_ros::BagActuator"};

class RecoveryNode : public rclcpp_lifecycle::LifecycleNode
{
public:
  explicit RecoveryNode(const rclcpp::NodeOptions & options)
  : rclcpp_lifecycle::LifecycleNode("recovery", "jarvis", options),
    loader_("jarvis_ros", "jarvis_ros::Actuator")
  {
    declare<std::string>(*this, "robot_id", "robot", "Robot id stamped on Jarvis events");
    declare<std::string>(*this, "plans_dir", "", "Directory of jarvis.plans/v1 YAML files");
    declare<std::string>(
      *this, "state_dir", "", "Where waiting executions are saved; empty disables persistence");
    declare<std::string>(
      *this, "mode", "ENABLED", "Initial mode (ENABLED, DRY_RUN, DISABLED); a saved mode wins");
    declare<int>(*this, "max_concurrent", 4, "Top-level executions running at once");
    declare<double>(*this, "approval_ttl_s", 300.0, "How long an approval request waits");
    declare<std::string>(
      *this, "agent_autonomy", "supervised",
      "Ceiling for agent requests: never, advise, supervised, autonomous");
    declare<double>(*this, "min_confidence", 0.8, "Autonomous agent runs need this confidence");
    declare<int>(*this, "max_depth", 3, "Nesting limit of `run` steps");
    declare<int>(*this, "recent_executions", 20, "Settled executions kept on /jarvis/executions");
    declare<std::vector<std::string>>(
      *this, "actuators", kBuiltinActuators, "Actuator plugins (pluginlib, jarvis_ros::Actuator)");
    declare<int>(*this, "queue_depth", 20000, "History depth of /jarvis/events and /jarvis/ingest");
    if (declare<bool>(*this, "autostart", false, "Configure and activate on startup")) {
      autostart_timer_ = create_wall_timer(std::chrono::milliseconds(1), [this]() {
        autostart_timer_->cancel();
        autostart(*this);
      });
    }
  }

  CallbackReturn on_configure(const rclcpp_lifecycle::State &) override
  {
    if (!load_actuators()) {
      return CallbackReturn::FAILURE;
    }
    std::vector<std::string> errors;
    std::vector<std::string> warnings;
    auto plans = load_plans(errors, warnings);
    for (const auto & w : warnings) {
      RCLCPP_WARN(get_logger(), "%s", w.c_str());
    }
    if (!errors.empty()) {
      for (const auto & e : errors) {
        RCLCPP_ERROR(get_logger(), "%s", e.c_str());
      }
      return CallbackReturn::FAILURE;
    }

    jarvis_core::OrchestratorConfig config;
    config.robot_id = get_parameter("robot_id").as_string();
    config.node_name = get_fully_qualified_name();
    const auto mode = jarvis_core::parse_recovery_mode(get_parameter("mode").as_string());
    const auto autonomy =
      jarvis_core::parse_agent_policy(get_parameter("agent_autonomy").as_string());
    if (!mode || !autonomy) {
      RCLCPP_ERROR(
        get_logger(),
        "mode must be ENABLED, DRY_RUN or DISABLED and agent_autonomy one of "
        "never, advise, supervised, autonomous");
      return CallbackReturn::FAILURE;
    }
    config.mode = *mode;
    config.agent_autonomy = *autonomy;
    config.max_concurrent = static_cast<std::size_t>(get_parameter("max_concurrent").as_int());
    config.approval_ttl =
      static_cast<jarvis_core::Duration>(get_parameter("approval_ttl_s").as_double() * 1e9);
    config.min_confidence = get_parameter("min_confidence").as_double();
    config.max_depth = static_cast<int>(get_parameter("max_depth").as_int());
    orch_ = std::make_unique<jarvis_core::Orchestrator>(config, std::move(plans));
    RCLCPP_INFO(
      get_logger(), "%zu plans, %zu actions, mode %s", orch_->plans().plans().size(),
      by_action_.size(), std::string(jarvis_core::to_string(orch_->mode())).c_str());

    state_ = std::make_unique<StateFile>(get_parameter("state_dir").as_string(), "recovery.json");
    try {
      if (const auto saved = state_->load()) {
        pending_ = orch_->restore(*saved, now_ns(*this));
        RCLCPP_INFO(
          get_logger(), "restored %zu waiting executions from %s", orch_->live().size(),
          state_->path().c_str());
      }
    } catch (const std::exception & e) {
      RCLCPP_ERROR(get_logger(), "ignoring saved state: %s", e.what());
    }

    depth_ = static_cast<std::size_t>(get_parameter("queue_depth").as_int());
    ingest_pub_ = create_event_publisher(*this, "ingest", stream_qos(depth_));
    executions_pub_ = create_publisher<ExecutionArray>("executions", latched_qos());

    mode_srv_ = create_service<SetRecoveryMode>(
      "~/set_mode", [this](
                      const std::shared_ptr<SetRecoveryMode::Request> req,
                      std::shared_ptr<SetRecoveryMode::Response> res) {
        const auto m = jarvis_core::parse_recovery_mode(req->mode);
        res->previous_mode = std::string(jarvis_core::to_string(orch_->mode()));
        if (!m) {
          res->message = "mode must be ENABLED, DRY_RUN or DISABLED";
          return;
        }
        auto r =
          orch_->set_mode(*m, req->operator_name, req->reason, req->cancel_running, now_ns(*this));
        res->success = r.success;
        res->message = r.message;
        apply(std::move(r.effects));
      });
    decide_srv_ = create_service<DecideApproval>(
      "~/decide", [this](
                    const std::shared_ptr<DecideApproval::Request> req,
                    std::shared_ptr<DecideApproval::Response> res) {
        auto r = orch_->decide(
          req->execution_id, req->approve, req->operator_name, req->note, now_ns(*this));
        res->success = r.success;
        res->message = r.message;
        apply(std::move(r.effects));
      });
    cancel_srv_ = create_service<CancelExecution>(
      "~/cancel", [this](
                    const std::shared_ptr<CancelExecution::Request> req,
                    std::shared_ptr<CancelExecution::Response> res) {
        auto r = orch_->cancel(req->execution_id, req->operator_name, req->reason, now_ns(*this));
        res->success = r.success;
        res->message = r.message;
        apply(std::move(r.effects));
      });
    reload_srv_ = create_service<ReloadConfig>(
      "~/reload", [this](
                    const std::shared_ptr<ReloadConfig::Request> req,
                    std::shared_ptr<ReloadConfig::Response> res) { on_reload(*req, *res); });
    list_plans_srv_ = create_service<ListPlans>(
      "~/list_plans", [this](
                        const std::shared_ptr<ListPlans::Request> req,
                        std::shared_ptr<ListPlans::Response> res) { on_list_plans(*req, *res); });

    self_diagnostics_ = std::make_unique<diagnostic_updater::Updater>(this);
    self_diagnostics_->setHardwareID("jarvis");
    self_diagnostics_->add("recovery", [this](diagnostic_updater::DiagnosticStatusWrapper & s) {
      if (!orch_) {
        s.summary(DiagnosticStatus::STALE, "not configured");
        return;
      }
      const bool disabled = orch_->mode() == jarvis_core::RecoveryMode::Disabled;
      s.summary(
        persist_failures_ > 0 || disabled ? DiagnosticStatus::WARN : DiagnosticStatus::OK,
        persist_failures_ > 0 ? "cannot save state"
                              : std::string(jarvis_core::to_string(orch_->mode())));
      s.add("plans", orch_->plans().plans().size());
      s.add("actions", by_action_.size());
      s.add("live_executions", orch_->live().size());
      s.add("attempts_in_flight", in_flight_.size());
      s.add("state_file", state_->enabled() ? state_->path() : std::string("disabled"));
      s.add("persist_failures", persist_failures_);
    });
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_activate(const rclcpp_lifecycle::State &) override
  {
    executions_pub_->on_activate();
    active_ = true;
    events_sub_ = create_subscription<AdaptedEvent>(
      "events", stream_qos(depth_),
      [this](const jarvis_core::Event & e) { apply(orch_->on_event(e, now_ns(*this))); });
    faults_sub_ =
      create_subscription<FaultArray>("faults", latched_qos(), [this](const FaultArray & msg) {
        std::map<std::string, jarvis_core::FaultView> faults;
        for (const auto & f : msg.faults) {
          faults.insert(to_view(f));
        }
        apply(orch_->sync_faults(std::move(faults), now_ns(*this)));
      });
    action_server_ = rclcpp_action::create_server<ExecuteRecovery>(
      this, std::string(get_fully_qualified_name()) + "/execute",
      [this](const rclcpp_action::GoalUUID &, std::shared_ptr<const ExecuteRecovery::Goal>) {
        return active_ ? rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE
                       : rclcpp_action::GoalResponse::REJECT;
      },
      [this](const std::shared_ptr<GoalHandle> gh) { return on_cancel_goal(gh); },
      [this](const std::shared_ptr<GoalHandle> gh) { on_goal(gh); });
    persist_timer_ =
      create_wall_timer(std::chrono::milliseconds(100), [this]() { persist_if_dirty(); });
    apply(std::exchange(pending_, {}));
    publish_executions();
    arm_deadline();
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_deactivate(const rclcpp_lifecycle::State &) override
  {
    active_ = false;
    for (auto & [id, gh] : std::exchange(goals_, {})) {
      auto result = std::make_shared<ExecuteRecovery::Result>();
      result->execution_id = id;
      result->outcome = ExecuteRecovery::Result::OUTCOME_ABORTED;
      result->reason = "the recovery component was deactivated";
      gh->abort(result);
    }
    goal_ids_.clear();
    action_server_.reset();
    events_sub_.reset();
    faults_sub_.reset();
    deadline_timer_.reset();
    persist_if_dirty();
    persist_timer_.reset();
    executions_pub_->on_deactivate();
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_cleanup(const rclcpp_lifecycle::State &) override
  {
    for (auto & [key, flight] : std::exchange(in_flight_, {})) {
      flight.actuator->cancel(flight.attempt);
    }
    self_diagnostics_.reset();
    mode_srv_.reset();
    decide_srv_.reset();
    cancel_srv_.reset();
    reload_srv_.reset();
    list_plans_srv_.reset();
    executions_pub_.reset();
    ingest_pub_.reset();
    orch_.reset();
    state_.reset();
    by_action_.clear();
    actuators_.clear();
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
  struct Flight
  {
    Attempt attempt;
    std::shared_ptr<Actuator> actuator;
  };

  bool load_actuators()
  {
    by_action_.clear();
    actuators_.clear();
    auto self = shared_from_this();
    const auto names =
      get_parameter("actuators").as_string_array();  // copy: the Parameter is a temporary
    for (const auto & name : names) {
      try {
        auto actuator = loader_.createSharedInstance(name);
        actuator->initialize(self);
        for (const auto & action : actuator->actions()) {
          if (jarvis_core::core_actions().count(action) || by_action_.count(action)) {
            RCLCPP_ERROR(
              get_logger(), "%s: action '%s' is already provided", name.c_str(), action.c_str());
            return false;
          }
          by_action_[action] = actuator;
        }
        actuators_.push_back(std::move(actuator));
      } catch (const pluginlib::PluginlibException & e) {
        RCLCPP_ERROR(get_logger(), "cannot load actuator %s: %s", name.c_str(), e.what());
        return false;
      }
    }
    return true;
  }

  jarvis_core::PlanSet load_plans(
    std::vector<std::string> & errors, std::vector<std::string> & warnings)
  {
    const auto dir = get_parameter("plans_dir").as_string();
    if (dir.empty()) {
      warnings.push_back("no plans_dir: nothing can be recovered");
      return {};
    }
    try {
      auto plans = jarvis_core::PlanSet::from_directory(dir);
      std::set<std::string> actions;
      for (const auto & [action, actuator] : by_action_) {
        actions.insert(action);
      }
      warnings = plans.validate(actions, static_cast<int>(get_parameter("max_depth").as_int()));
      for (const auto & p : plans.plans()) {
        for (const auto & s : p.steps) {
          if (const auto it = by_action_.find(s.action); it != by_action_.end()) {
            if (const auto err = it->second->check(s.action, s.with)) {
              errors.push_back(
                p.file + ": plan '" + p.id + "' step '" + s.id + "' (" + s.action + "): " + *err);
            }
          }
        }
      }
      return plans;
    } catch (const jarvis_core::PlanError & e) {
      errors.push_back(e.what());
      return {};
    }
  }

  void on_reload(const ReloadConfig::Request & req, ReloadConfig::Response & res)
  {
    auto plans = load_plans(res.errors, res.warnings);
    res.revision = directory_revision(get_parameter("plans_dir").as_string());
    if (!res.errors.empty()) {
      res.message = "plans not reloaded: " + std::to_string(res.errors.size()) + " error(s)";
      return;
    }
    res.success = true;
    if (req.validate_only) {
      res.message = std::to_string(plans.plans().size()) + " plans are valid";
      return;
    }
    res.message = "loaded " + std::to_string(plans.plans().size()) + " plans";
    apply(orch_->reload(std::move(plans), now_ns(*this), req.operator_name));
  }

  /// The loaded plans, with what a form needs to run one (parameters) and to explain it.
  void on_list_plans(const ListPlans::Request & req, ListPlans::Response & res)
  {
    using jarvis_core::ParamSpec;
    res.mode = std::string(jarvis_core::to_string(orch_->mode()));
    for (const auto & plan : orch_->plans().plans()) {
      if (!req.ids.empty() && std::none_of(req.ids.begin(), req.ids.end(), [&plan](const auto & g) {
            return jarvis_core::glob_match(g, plan.id);
          })) {
        continue;
      }
      jarvis_interfaces::msg::PlanInfo info;
      info.id = plan.id;
      info.description = plan.description;
      info.rev = plan.rev;
      info.file = plan.file;
      nlohmann::json params = nlohmann::json::object();
      for (const auto & [name, spec] : plan.params) {
        static const std::map<ParamSpec::Type, std::string> types{
          {ParamSpec::Type::String, "string"},
          {ParamSpec::Type::Int, "int"},
          {ParamSpec::Type::Number, "number"},
          {ParamSpec::Type::Bool, "bool"}};
        nlohmann::json p = {
          {"type", types.at(spec.type)},
          {"required", spec.required},
          {"description", spec.description}};
        if (spec.default_value) {
          p["default"] = *spec.default_value;
        }
        if (!spec.pattern.empty()) {
          p["pattern"] = spec.pattern;
        }
        if (!spec.one_of.empty()) {
          p["one_of"] = spec.one_of;
        }
        params[name] = std::move(p);
      }
      info.params_json = params.dump();
      for (auto r : plan.allowed_requesters) {
        info.allowed_requesters.emplace_back(jarvis_core::to_string(r));
      }
      for (auto r : plan.requires_approval) {
        info.requires_approval.emplace_back(jarvis_core::to_string(r));
      }
      info.agent_policy = std::string(jarvis_core::to_string(plan.agent_policy));
      for (const auto & step : plan.steps) {
        info.steps.push_back(step.id);
      }
      info.timeout_s = static_cast<double>(plan.timeout) / 1e9;
      res.plans.push_back(std::move(info));
    }
  }

  void apply(jarvis_core::Effects effects)
  {
    ++applying_;
    bool snapshot_changed = false;
    std::vector<std::string> changed;
    for (auto & fx : effects) {
      if (auto * emit = std::get_if<jarvis_core::EmitEvent>(&fx)) {
        if (active_) {
          ingest_pub_->publish(emit->event);
        } else {
          pending_.push_back(std::move(fx));
        }
      } else if (auto * start = std::get_if<jarvis_core::StartAction>(&fx)) {
        start_attempt(*start);
      } else if (auto * cancel = std::get_if<jarvis_core::CancelAction>(&fx)) {
        const auto key =
          cancel->execution_id + "/" + cancel->step_id + "#" + std::to_string(cancel->attempt);
        if (const auto it = in_flight_.find(key); it != in_flight_.end()) {
          auto flight = std::move(it->second);
          in_flight_.erase(it);
          flight.actuator->cancel(flight.attempt);
        }
      } else if (auto * ec = std::get_if<jarvis_core::ExecutionsChanged>(&fx)) {
        snapshot_changed = true;
        changed.insert(changed.end(), ec->execution_ids.begin(), ec->execution_ids.end());
      } else if (std::holds_alternative<jarvis_core::PersistState>(fx)) {
        dirty_ = true;
      }
    }
    if (snapshot_changed) {
      publish_executions();
      for (const auto & id : changed) {
        update_goal(id);
      }
    }
    --applying_;
    arm_deadline();
  }

  /// An actuator outcome. One that arrives while effects are being applied (an actuator that
  /// answers synchronously, a refusal) waits for the next turn, so events keep their order.
  void report(const Attempt & attempt, bool ok, const std::string & message)
  {
    auto job = [this, attempt, ok, message]() {
      apply(orch_->on_action_result(
        attempt.execution_id, attempt.step_id, attempt.attempt, ok, message, now_ns(*this)));
    };
    if (applying_ > 0) {
      deferred_.push_back(std::move(job));
      arm_deferred();
    } else {
      job();
    }
  }

  void start_attempt(const jarvis_core::StartAction & s)
  {
    Attempt attempt{s.execution_id, s.step_id, s.attempt,
                    s.action,       s.args,    std::chrono::nanoseconds(s.timeout)};
    const auto it = by_action_.find(s.action);
    std::string refusal;
    if (it == by_action_.end()) {
      refusal = "no actuator provides '" + s.action + "'";
    } else if (!active_) {
      refusal = "the recovery component is not active";
    }
    if (!refusal.empty()) {
      return report(attempt, false, refusal);
    }
    const auto key = attempt.key();
    in_flight_[key] = Flight{attempt, it->second};
    it->second->start(attempt, [this, key, attempt](bool ok, const std::string & message) {
      in_flight_.erase(key);
      report(attempt, ok, message);
    });
  }

  void arm_deferred()
  {
    if (deferred_timer_) {
      return;
    }
    deferred_timer_ = create_wall_timer(std::chrono::milliseconds(0), [this]() {
      deferred_timer_->cancel();
      auto self = std::exchange(deferred_timer_, nullptr);
      for (auto & job : std::exchange(deferred_, {})) {
        job();
      }
    });
  }

  rclcpp_action::CancelResponse on_cancel_goal(const std::shared_ptr<GoalHandle> & gh)
  {
    const auto it = goal_ids_.find(gh->get_goal_id());
    if (it == goal_ids_.end()) {
      return rclcpp_action::CancelResponse::REJECT;
    }
    // The goal turns CANCELING after this returns: cancel the execution on the next turn.
    const std::string id = it->second;
    deferred_.push_back([this, id]() {
      apply(orch_->cancel(id, "ExecuteRecovery client", "goal cancelled", now_ns(*this)).effects);
    });
    arm_deferred();
    return rclcpp_action::CancelResponse::ACCEPT;
  }

  void on_goal(const std::shared_ptr<GoalHandle> & gh)
  {
    const auto goal = gh->get_goal();
    const auto requester = requester_from_msg(goal->requester_type);
    if (!requester || !orch_) {
      auto result = std::make_shared<ExecuteRecovery::Result>();
      result->outcome = ExecuteRecovery::Result::OUTCOME_REJECTED;
      result->reason = requester ? "not configured" : "unknown requester_type";
      gh->abort(result);
      return;
    }
    jarvis_core::RecoveryRequest r;
    r.plan_id = goal->plan_id;
    r.args = args_from_msg(goal->args);
    r.requester = *requester;
    r.requester_id = goal->requester_id;
    r.correlation_id = goal->correlation_id;
    r.causation_id = goal->causation_id;
    r.rationale = goal->rationale;
    r.dry_run = goal->dry_run;
    if (std::isfinite(goal->confidence) && goal->confidence > 0.0) {
      r.confidence = goal->confidence;
    }
    auto submitted = orch_->submit(r, now_ns(*this));
    goals_[submitted.execution_id] = gh;
    goal_ids_[gh->get_goal_id()] = submitted.execution_id;
    apply(std::move(submitted.effects));
    update_goal(submitted.execution_id);
  }

  void update_goal(const std::string & id)
  {
    const auto it = goals_.find(id);
    if (it == goals_.end()) {
      return;
    }
    const auto gh = it->second;
    const jarvis_core::Execution * x = orch_->find(id);
    using jarvis_core::ExecState;
    if (!x || jarvis_core::is_terminal(x->state)) {
      auto result = std::make_shared<ExecuteRecovery::Result>();
      result->execution_id = id;
      if (!x) {
        result->outcome = ExecuteRecovery::Result::OUTCOME_ABORTED;
        result->reason = "execution no longer known";
      } else {
        result->reason = x->reason;
        result->failed_step = x->failed_step;
        switch (x->state) {
          case ExecState::Succeeded:
            result->outcome = ExecuteRecovery::Result::OUTCOME_SUCCEEDED;
            break;
          case ExecState::Failed:
            result->outcome = ExecuteRecovery::Result::OUTCOME_FAILED;
            break;
          case ExecState::Rejected:
            result->outcome = ExecuteRecovery::Result::OUTCOME_REJECTED;
            break;
          case ExecState::Expired:
            result->outcome = ExecuteRecovery::Result::OUTCOME_EXPIRED;
            break;
          default:
            result->outcome = ExecuteRecovery::Result::OUTCOME_ABORTED;
            break;
        }
      }
      if (x && x->state == ExecState::Succeeded) {
        gh->succeed(result);
      } else if (gh->is_canceling()) {
        gh->canceled(result);
      } else {
        gh->abort(result);
      }
      goal_ids_.erase(gh->get_goal_id());
      goals_.erase(it);
      return;
    }
    auto feedback = std::make_shared<ExecuteRecovery::Feedback>();
    feedback->execution_id = id;
    feedback->state = x->state == ExecState::PendingApproval
                        ? ExecuteRecovery::Feedback::STATE_PENDING_APPROVAL
                      : x->state == ExecState::Queued ? ExecuteRecovery::Feedback::STATE_QUEUED
                                                      : ExecuteRecovery::Feedback::STATE_RUNNING;
    feedback->step_count = static_cast<std::uint16_t>(x->steps.size());
    feedback->step_index = static_cast<std::uint16_t>(x->current_step);
    if (x->state == ExecState::Running && x->current_step < x->steps.size()) {
      feedback->step_id = x->steps[x->current_step].id;
      feedback->message = x->steps[x->current_step].message;
    } else {
      feedback->message = x->reason;
    }
    gh->publish_feedback(feedback);
  }

  void publish_executions()
  {
    if (!active_ || !orch_) {
      return;
    }
    ExecutionArray msg;
    msg.stamp = to_msg_time(now_ns(*this));
    msg.mode = std::string(jarvis_core::to_string(orch_->mode()));
    for (const auto & x : orch_->live()) {
      msg.executions.push_back(to_msg(x));
    }
    const auto & history = orch_->history();
    const auto keep = std::min(
      history.size(), static_cast<std::size_t>(get_parameter("recent_executions").as_int()));
    for (auto it = history.end() - static_cast<std::ptrdiff_t>(keep); it != history.end(); ++it) {
      msg.recent.push_back(to_msg(*it));
    }
    executions_pub_->publish(msg);
  }

  /// One timer, armed for the earliest deadline the core reports. Uses the node clock (sim time).
  void arm_deadline()
  {
    if (!active_ || !orch_) {
      return;
    }
    const auto next = orch_->next_deadline();
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
        apply(orch_->advance_to(now_ns(*this)));
      });
  }

  void persist_if_dirty()
  {
    if (!dirty_ || !orch_ || !state_) {
      return;
    }
    try {
      state_->save(orch_->snapshot());
      dirty_ = false;
    } catch (const std::exception & e) {
      ++persist_failures_;
      RCLCPP_ERROR_THROTTLE(get_logger(), *get_clock(), 10000, "saving state: %s", e.what());
    }
  }

  pluginlib::ClassLoader<Actuator> loader_;  // outlives the actuators it loaded
  std::vector<std::shared_ptr<Actuator>> actuators_;
  std::map<std::string, std::shared_ptr<Actuator>> by_action_;
  std::map<std::string, Flight> in_flight_;
  std::unique_ptr<jarvis_core::Orchestrator> orch_;
  std::unique_ptr<StateFile> state_;
  jarvis_core::Effects pending_;
  std::vector<std::function<void()>> deferred_;

  std::map<std::string, std::shared_ptr<GoalHandle>> goals_;  // execution id -> goal
  std::map<rclcpp_action::GoalUUID, std::string> goal_ids_;   // goal -> execution id

  EventPublisher::SharedPtr ingest_pub_;
  rclcpp_lifecycle::LifecyclePublisher<ExecutionArray>::SharedPtr executions_pub_;
  rclcpp::Subscription<AdaptedEvent>::SharedPtr events_sub_;
  rclcpp::Subscription<FaultArray>::SharedPtr faults_sub_;
  rclcpp_action::Server<ExecuteRecovery>::SharedPtr action_server_;
  rclcpp::Service<SetRecoveryMode>::SharedPtr mode_srv_;
  rclcpp::Service<DecideApproval>::SharedPtr decide_srv_;
  rclcpp::Service<CancelExecution>::SharedPtr cancel_srv_;
  rclcpp::Service<ReloadConfig>::SharedPtr reload_srv_;
  rclcpp::Service<ListPlans>::SharedPtr list_plans_srv_;
  rclcpp::TimerBase::SharedPtr deadline_timer_;
  rclcpp::TimerBase::SharedPtr deferred_timer_;
  rclcpp::TimerBase::SharedPtr persist_timer_;
  rclcpp::TimerBase::SharedPtr autostart_timer_;
  std::unique_ptr<diagnostic_updater::Updater> self_diagnostics_;

  std::size_t depth_{20000};
  jarvis_core::Timestamp armed_for_{0};
  int applying_{0};
  bool active_{false};
  bool dirty_{false};
  std::uint64_t persist_failures_{0};
};

}  // namespace jarvis_ros

RCLCPP_COMPONENTS_REGISTER_NODE(jarvis_ros::RecoveryNode)
