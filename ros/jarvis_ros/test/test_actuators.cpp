// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// The built-in actuators, loaded through pluginlib like /jarvis/recovery does, against real ROS
// endpoints in the same process.

#include <gtest/gtest.h>

#include <lifecycle_msgs/msg/state.hpp>
#include <memory>
#include <optional>
#include <pluginlib/class_loader.hpp>
#include <rclcpp/rclcpp.hpp>
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
