// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// The built-in actuators, loaded through pluginlib like /jarvis/recovery does, against real ROS
// endpoints in the same process.

#include <gtest/gtest.h>

#include <jarvis_interfaces/action/analyze.hpp>
#include <lifecycle_msgs/msg/state.hpp>
#include <lifecycle_msgs/srv/change_state.hpp>
#include <memory>
#include <optional>
#include <pluginlib/class_loader.hpp>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp_action/rclcpp_action.hpp>
#include <rclcpp_lifecycle/lifecycle_node.hpp>
#include <std_srvs/srv/set_bool.hpp>
#include <string>

#include "jarvis_ros/actuator.hpp"

using jarvis_ros::Attempt;
using lifecycle_msgs::msg::State;

class ActuatorsTest : public ::testing::Test
{
protected:
  static void SetUpTestSuite() { rclcpp::init(0, nullptr); }
  static void TearDownTestSuite() { rclcpp::shutdown(); }

  void SetUp() override
  {
    node_ = std::make_shared<rclcpp_lifecycle::LifecycleNode>("recovery", "jarvis_test");
    target_ = std::make_shared<rclcpp_lifecycle::LifecycleNode>("managed", "test");
    plain_ = std::make_shared<rclcpp::Node>("plain", "test");
    for (auto base :
         {node_->get_node_base_interface(), target_->get_node_base_interface(),
          plain_->get_node_base_interface()}) {
      executor_.add_node(base);
    }
  }

  void TearDown() override
  {
    actuators_.clear();
    for (auto base :
         {node_->get_node_base_interface(), target_->get_node_base_interface(),
          plain_->get_node_base_interface()}) {
      executor_.remove_node(base);
    }
  }

  std::shared_ptr<jarvis_ros::Actuator> actuator(const std::string & type)
  {
    auto a = loader_.createSharedInstance(type);
    a->initialize(node_);
    actuators_.push_back(a);
    return a;
  }

  struct Outcome
  {
    bool ok;
    std::string message;
  };

  std::optional<Outcome> run(
    const std::shared_ptr<jarvis_ros::Actuator> & a, const std::string & action,
    nlohmann::json args, double timeout_s = 5.0)
  {
    const auto attempt = make(action, std::move(args));
    std::optional<Outcome> out;
    a->start(attempt, [&out](bool ok, const std::string & message) { out = Outcome{ok, message}; });
    spin_until([&out] { return out.has_value(); }, timeout_s);
    return out;
  }

  Attempt make(const std::string & action, nlohmann::json args)
  {
    Attempt a;
    a.execution_id = "exec-" + std::to_string(++n_);
    a.step_id = "step";
    a.action = action;
    a.args = std::move(args);
    return a;
  }

  template <typename Pred>
  void spin_until(Pred done, double timeout_s)
  {
    const auto deadline =
      std::chrono::steady_clock::now() + std::chrono::duration<double>(timeout_s);
    while (!done() && std::chrono::steady_clock::now() < deadline) {
      executor_.spin_some(std::chrono::milliseconds(20));
    }
  }

  pluginlib::ClassLoader<jarvis_ros::Actuator> loader_{"jarvis_ros", "jarvis_ros::Actuator"};
  std::vector<std::shared_ptr<jarvis_ros::Actuator>> actuators_;
  rclcpp_lifecycle::LifecycleNode::SharedPtr node_;
  rclcpp_lifecycle::LifecycleNode::SharedPtr target_;
  rclcpp::Node::SharedPtr plain_;
  rclcpp::executors::SingleThreadedExecutor executor_;
  int n_{0};
};

TEST_F(ActuatorsTest, LifecycleTransitionsAndRestartsFromAnyState)
{
  auto a = actuator("jarvis_ros::LifecycleActuator");
  EXPECT_EQ(a->actions(), (std::vector<std::string>{"lifecycle.transition", "lifecycle.restart"}));

  auto configured =
    run(a, "lifecycle.transition", {{"node", "/test/managed"}, {"transition", "configure"}});
  ASSERT_TRUE(configured);
  EXPECT_TRUE(configured->ok) << configured->message;
  EXPECT_EQ(target_->get_current_state().id(), State::PRIMARY_STATE_INACTIVE);

  auto restarted = run(a, "lifecycle.restart", {{"node", "/test/managed"}});
  ASSERT_TRUE(restarted);
  EXPECT_TRUE(restarted->ok) << restarted->message;
  EXPECT_EQ(restarted->message, "/test/managed: cleanup -> configure -> activate")
    << "from INACTIVE";
  EXPECT_EQ(target_->get_current_state().id(), State::PRIMARY_STATE_ACTIVE);

  restarted = run(a, "lifecycle.restart", {{"node", "/test/managed"}});
  ASSERT_TRUE(restarted);
  EXPECT_EQ(restarted->message, "/test/managed: deactivate -> cleanup -> configure -> activate");

  auto refused =
    run(a, "lifecycle.transition", {{"node", "/test/managed"}, {"transition", "configure"}});
  ASSERT_TRUE(refused);
  EXPECT_FALSE(refused->ok) << "configure is not valid from ACTIVE";
  EXPECT_EQ(refused->message, "/test/managed: transition 'configure' failed");

  EXPECT_NE(a->check("lifecycle.restart", {{"node", "relative"}}), std::nullopt);
  EXPECT_NE(
    a->check("lifecycle.transition", {{"node", "/a"}, {"transition", "reboot"}}), std::nullopt);
  EXPECT_EQ(a->check("lifecycle.restart", {{"node", "{{ params.node }}"}}), std::nullopt);
}

TEST_F(ActuatorsTest, SetBoolReportsTheServiceAnswer)
{
  std::optional<bool> received;
  auto srv = plain_->create_service<std_srvs::srv::SetBool>(
    "/test/motors", [&received](
                      const std::shared_ptr<std_srvs::srv::SetBool::Request> req,
                      std::shared_ptr<std_srvs::srv::SetBool::Response> res) {
      received = req->data;
      res->success = req->data;
      res->message = req->data ? "motors on" : "refused";
    });
  auto a = actuator("jarvis_ros::ServiceActuator");
  auto on = run(a, "service.set_bool", {{"service", "/test/motors"}, {"data", true}});
  ASSERT_TRUE(on);
  EXPECT_TRUE(on->ok);
  EXPECT_EQ(on->message, "motors on");
  auto off = run(a, "service.set_bool", {{"service", "/test/motors"}, {"data", "false"}});
  ASSERT_TRUE(off);
  EXPECT_FALSE(off->ok) << "a rendered template gives the string \"false\"";
  EXPECT_EQ(received, false);
  EXPECT_NE(a->check("service.set_bool", {{"service", "/x"}}), std::nullopt);
}

TEST_F(ActuatorsTest, ParamSetChangesALiveParameter)
{
  plain_->declare_parameter("max_speed", 1.0);
  auto a = actuator("jarvis_ros::ParamActuator");
  auto set = run(a, "param.set", {{"node", "/test/plain"}, {"name", "max_speed"}, {"value", 0.25}});
  ASSERT_TRUE(set);
  EXPECT_TRUE(set->ok) << set->message;
  EXPECT_DOUBLE_EQ(plain_->get_parameter("max_speed").as_double(), 0.25);
  auto unknown = run(a, "param.set", {{"node", "/test/plain"}, {"name", "nope"}, {"value", 1}});
  ASSERT_TRUE(unknown);
  EXPECT_FALSE(unknown->ok);
  EXPECT_NE(a->check("param.set", {{"node", "/test/plain"}, {"name", "x"}}), std::nullopt);
}

TEST_F(ActuatorsTest, ACancelledAttemptNeverReports)
{
  auto a = actuator("jarvis_ros::ServiceActuator");
  const auto attempt = make("service.trigger", {{"service", "/test/not_there_yet"}});
  bool reported = false;
  a->start(attempt, [&reported](bool, const std::string &) { reported = true; });
  spin_until([] { return false; }, 0.3);
  a->cancel(attempt);
  spin_until([] { return false; }, 0.3);
  EXPECT_FALSE(reported);
}

TEST_F(ActuatorsTest, ServiceCallSendsAnyTypedRequest)
{
  using Srv = lifecycle_msgs::srv::ChangeState;
  std::optional<Srv::Request> received;
  auto srv = plain_->create_service<Srv>(
    "/test/typed",
    [&received](const std::shared_ptr<Srv::Request> req, std::shared_ptr<Srv::Response> res) {
      received = *req;
      res->success = req->transition.label == "activate";
    });
  auto a = actuator("jarvis_ros::GenericActuator");
  EXPECT_EQ(a->actions(), (std::vector<std::string>{"service.call", "action.goal"}));
  const nlohmann::json base{{"service", "/test/typed"}, {"type", "lifecycle_msgs/srv/ChangeState"}};

  auto args = base;
  args["request"] = {{"transition", {{"id", 3}, {"label", "activate"}}}};
  auto ok = run(a, "service.call", args);
  ASSERT_TRUE(ok);
  EXPECT_TRUE(ok->ok) << ok->message;
  EXPECT_EQ(ok->message, R"(/test/typed: {"success":true})");
  ASSERT_TRUE(received);
  EXPECT_EQ(received->transition.id, 3);

  // A response with a boolean `success` must say true, unless the plan expects otherwise.
  args["request"] = {{"transition", {{"label", "shutdown"}}}};
  auto refused = run(a, "service.call", args);
  ASSERT_TRUE(refused);
  EXPECT_FALSE(refused->ok);
  EXPECT_EQ(refused->message, "/test/typed: response.success is false, expected true");
  args["expect"] = nlohmann::json::object();
  auto anything = run(a, "service.call", args);
  ASSERT_TRUE(anything);
  EXPECT_TRUE(anything->ok) << anything->message;

  // Rendered arguments that don't fit the type fail the step, before anything is sent.
  args["request"] = {{"transition", {{"id", "three"}}}};
  auto bad = run(a, "service.call", args);
  ASSERT_TRUE(bad);
  EXPECT_FALSE(bad->ok);
  EXPECT_EQ(bad->message, "request.transition.id: expects an integer, not \"three\"");
}

TEST_F(ActuatorsTest, GenericStepsAreCheckedWhenThePlansLoad)
{
  auto a = actuator("jarvis_ros::GenericActuator");
  const nlohmann::json srv{{"service", "/x"}, {"type", "lifecycle_msgs/srv/ChangeState"}};
  auto with = [](nlohmann::json w, const std::string & key, nlohmann::json v) {
    w[key] = std::move(v);
    return w;
  };
  EXPECT_EQ(a->check("service.call", srv), std::nullopt);
  EXPECT_EQ(
    a->check("service.call", with(srv, "request", {{"transition", {{"id", "{{ params.id }}"}}}})),
    std::nullopt);
  EXPECT_EQ(
    a->check("service.call", with(srv, "request", {{"transitionn", {}}})).value_or("ok"),
    "request: lifecycle_msgs/srv/ChangeState_Request has no field 'transitionn' (it has: "
    "transition)");
  EXPECT_EQ(
    a->check("service.call", with(srv, "expect", {{"succes", true}})).value_or("ok"),
    "expect: lifecycle_msgs/srv/ChangeState_Response has no field 'succes' (it has: success)");
  EXPECT_NE(a->check("service.call", with(srv, "type", "lifecycle_msgs/srv/Nope")), std::nullopt);
  EXPECT_NE(a->check("service.call", with(srv, "type", "{{ params.type }}")), std::nullopt);
  EXPECT_NE(a->check("service.call", {{"service", "/x"}}), std::nullopt) << "no type";

  const nlohmann::json goal{{"action", "/x"}, {"type", "jarvis_interfaces/action/Analyze"}};
  EXPECT_EQ(a->check("action.goal", with(goal, "goal", {{"question", "why?"}})), std::nullopt);
  EXPECT_EQ(
    a->check("action.goal", with(goal, "goal", {{"questionn", "why?"}})).value_or("ok"),
    "goal: jarvis_interfaces/action/Analyze_Goal has no field 'questionn' (it has: incident_id, "
    "question, requester)");
  EXPECT_EQ(a->check("action.goal", with(goal, "expect", {{"success", true}})), std::nullopt);
  EXPECT_NE(
    a->check("action.goal", with(goal, "type", "lifecycle_msgs/srv/ChangeState")), std::nullopt);
}

TEST_F(ActuatorsTest, ActionGoalWaitsForTheResult)
{
  using Analyze = jarvis_interfaces::action::Analyze;
  using Handle = rclcpp_action::ServerGoalHandle<Analyze>;
  std::shared_ptr<Handle> pending;
  auto server = rclcpp_action::create_server<Analyze>(
    plain_, "/test/analyze",
    [](const rclcpp_action::GoalUUID &, std::shared_ptr<const Analyze::Goal> goal) {
      return goal->question == "reject" ? rclcpp_action::GoalResponse::REJECT
                                        : rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
    },
    [](std::shared_ptr<Handle>) { return rclcpp_action::CancelResponse::ACCEPT; },
    [&pending](std::shared_ptr<Handle> handle) {
      auto result = std::make_shared<Analyze::Result>();
      const auto & question = handle->get_goal()->question;
      if (question == "slow") {
        pending = handle;  // finished by the test
        return;
      }
      result->success = question != "fail";
      result->message = result->success ? "answered: " + question : "no such incident";
      result->insight_json = "{}";
      if (result->success) {
        handle->succeed(result);
      } else {
        handle->abort(result);
      }
    });
  auto a = actuator("jarvis_ros::GenericActuator");
  auto goal = [](const std::string & question) {
    return nlohmann::json{
      {"action", "/test/analyze"},
      {"type", "jarvis_interfaces/action/Analyze"},
      {"goal", {{"question", question}}}};
  };

  auto answered = run(a, "action.goal", goal("why?"));
  ASSERT_TRUE(answered);
  EXPECT_TRUE(answered->ok) << answered->message;
  EXPECT_EQ(answered->message, "/test/analyze: answered: why?");

  auto aborted = run(a, "action.goal", goal("fail"));
  ASSERT_TRUE(aborted);
  EXPECT_FALSE(aborted->ok);
  EXPECT_EQ(aborted->message, "/test/analyze: the goal was aborted: no such incident");

  auto rejected = run(a, "action.goal", goal("reject"));
  ASSERT_TRUE(rejected);
  EXPECT_FALSE(rejected->ok);
  EXPECT_EQ(rejected->message, "/test/analyze rejected the goal");

  auto args = goal("why?");
  args["expect"] = {{"insight_json", "x"}};
  auto unexpected = run(a, "action.goal", args);
  ASSERT_TRUE(unexpected);
  EXPECT_FALSE(unexpected->ok);
  EXPECT_EQ(
    unexpected->message,
    R"(/test/analyze: result.insight_json is "{}", expected "x" (answered: why?))");

  // A step timeout cancels the goal on the server, and nothing is reported.
  const auto attempt = make("action.goal", goal("slow"));
  bool reported = false;
  a->start(attempt, [&reported](bool, const std::string &) { reported = true; });
  spin_until([&pending] { return pending != nullptr; }, 5.0);
  ASSERT_TRUE(pending);
  spin_until([] { return false; }, 0.2);  // the goal response reaches the actuator
  a->cancel(attempt);
  spin_until([&pending] { return pending->is_canceling(); }, 5.0);
  EXPECT_TRUE(pending->is_canceling());
  pending->canceled(std::make_shared<Analyze::Result>());
  spin_until([] { return false; }, 0.3);
  EXPECT_FALSE(reported);
}
