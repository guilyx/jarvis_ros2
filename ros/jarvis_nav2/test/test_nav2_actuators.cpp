// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// The Nav2 actuator, loaded through pluginlib like /jarvis/recovery does, against fake Nav2 servers.

#include <gtest/gtest.h>

#include <jarvis_ros/actuator.hpp>
#include <memory>
#include <nav2_msgs/action/navigate_to_pose.hpp>
#include <nav2_msgs/srv/clear_entire_costmap.hpp>
#include <nav2_msgs/srv/manage_lifecycle_nodes.hpp>
#include <optional>
#include <pluginlib/class_loader.hpp>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp_action/rclcpp_action.hpp>
#include <rclcpp_lifecycle/lifecycle_node.hpp>
#include <string>
#include <thread>

using jarvis_ros::Attempt;
using ManageNodes = nav2_msgs::srv::ManageLifecycleNodes;
using ClearCostmap = nav2_msgs::srv::ClearEntireCostmap;
using NavigateToPose = nav2_msgs::action::NavigateToPose;

class Nav2ActuatorTest : public ::testing::Test
{
protected:
  static void SetUpTestSuite() { rclcpp::init(0, nullptr); }
  static void TearDownTestSuite() { rclcpp::shutdown(); }

  void SetUp() override
  {
    node_ = std::make_shared<rclcpp_lifecycle::LifecycleNode>("recovery", "jarvis_test");
    nav2_ = std::make_shared<rclcpp::Node>("fake_nav2");
    executor_.add_node(node_->get_node_base_interface());
    executor_.add_node(nav2_);
    actuator_ = loader_.createSharedInstance("jarvis_nav2::Nav2Actuator");
    actuator_->initialize(node_);
  }

  void TearDown() override
  {
    actuator_.reset();
    executor_.remove_node(nav2_);
    executor_.remove_node(node_->get_node_base_interface());
  }

  struct Outcome
  {
    bool ok;
    std::string message;
  };

  std::optional<Outcome> run(
    const std::string & action, nlohmann::json args, double timeout_s = 5.0)
  {
    Attempt a;
    a.execution_id = "exec-" + std::to_string(++n_);
    a.step_id = "step";
    a.action = action;
    a.args = std::move(args);
    std::optional<Outcome> out;
    actuator_->start(
      a, [&out](bool ok, const std::string & message) { out = Outcome{ok, message}; });
    const auto deadline =
      std::chrono::steady_clock::now() + std::chrono::duration<double>(timeout_s);
    while (!out && std::chrono::steady_clock::now() < deadline) {
      executor_.spin_some(std::chrono::milliseconds(20));
    }
    return out;
  }

  pluginlib::ClassLoader<jarvis_ros::Actuator> loader_{"jarvis_ros", "jarvis_ros::Actuator"};
  std::shared_ptr<jarvis_ros::Actuator> actuator_;
  rclcpp_lifecycle::LifecycleNode::SharedPtr node_;
  rclcpp::Node::SharedPtr nav2_;
  rclcpp::executors::SingleThreadedExecutor executor_;
  int n_{0};
};

TEST_F(Nav2ActuatorTest, ProvidesTheNav2ActionsAndChecksArguments)
{
  const auto actions = actuator_->actions();
  EXPECT_EQ(
    actions, (std::vector<std::string>{
               "nav2.manage_lifecycle", "nav2.clear_costmaps", "nav2.cancel_navigation",
               "nav2.navigate_to_pose"}));
  EXPECT_EQ(
    actuator_->check("nav2.manage_lifecycle", {{"manager", "/lm"}, {"command", "RESET"}}),
    std::nullopt);
  EXPECT_NE(
    actuator_->check("nav2.manage_lifecycle", {{"manager", "/lm"}, {"command", "REBOOT"}}),
    std::nullopt);
  EXPECT_NE(actuator_->check("nav2.manage_lifecycle", {{"command", "RESET"}}), std::nullopt);
  EXPECT_EQ(
    actuator_->check(
      "nav2.manage_lifecycle", {{"manager", "/lm"}, {"command", "{{ params.command }}"}}),
    std::nullopt)
    << "templates are checked when rendered";
  EXPECT_NE(actuator_->check("nav2.clear_costmaps", {{"which", "middle"}}), std::nullopt);
  EXPECT_NE(actuator_->check("nav2.navigate_to_pose", {{"pose", {{"x", 1.0}}}}), std::nullopt);
  EXPECT_EQ(
    actuator_->check("nav2.navigate_to_pose", {{"pose", {{"x", 1.0}, {"y", 2.0}}}}), std::nullopt);
}

TEST_F(Nav2ActuatorTest, ManagesTheLifecycleManager)
{
  std::optional<std::uint8_t> received;
  auto srv = nav2_->create_service<ManageNodes>(
    "/lifecycle_manager_navigation/manage_nodes",
    [&received](
      const std::shared_ptr<ManageNodes::Request> req, std::shared_ptr<ManageNodes::Response> res) {
      received = req->command;
      res->success = req->command != ManageNodes::Request::SHUTDOWN;
    });
  auto ok = run(
    "nav2.manage_lifecycle", {{"manager", "/lifecycle_manager_navigation"}, {"command", "RESET"}});
  ASSERT_TRUE(ok);
  EXPECT_TRUE(ok->ok) << ok->message;
  EXPECT_EQ(received, ManageNodes::Request::RESET);
  auto refused = run(
    "nav2.manage_lifecycle",
    {{"manager", "/lifecycle_manager_navigation"}, {"command", "SHUTDOWN"}});
  ASSERT_TRUE(refused);
  EXPECT_FALSE(refused->ok);
  EXPECT_EQ(refused->message, "/lifecycle_manager_navigation: SHUTDOWN failed");
}

TEST_F(Nav2ActuatorTest, ClearsBothCostmaps)
{
  int local = 0;
  int global = 0;
  auto l = nav2_->create_service<ClearCostmap>(
    "/local_costmap/clear_entirely_local_costmap",
    [&local](
      const std::shared_ptr<ClearCostmap::Request>, std::shared_ptr<ClearCostmap::Response>) {
      ++local;
    });
  auto g = nav2_->create_service<ClearCostmap>(
    "/global_costmap/clear_entirely_global_costmap",
    [&global](
      const std::shared_ptr<ClearCostmap::Request>, std::shared_ptr<ClearCostmap::Response>) {
      ++global;
    });
  auto both = run("nav2.clear_costmaps", nlohmann::json::object());
  ASSERT_TRUE(both);
  EXPECT_TRUE(both->ok);
  EXPECT_EQ(both->message, "cleared 2 costmap(s)");
  auto only_local = run("nav2.clear_costmaps", {{"which", "local"}});
  ASSERT_TRUE(only_local);
  EXPECT_EQ(local, 2);
  EXPECT_EQ(global, 1);
}

TEST_F(Nav2ActuatorTest, CancellingWithoutNavigationIsANoOp)
{
  auto out = run("nav2.cancel_navigation", nlohmann::json::object());
  ASSERT_TRUE(out);
  EXPECT_TRUE(out->ok);
  EXPECT_EQ(out->message, "no navigation server is up: nothing to cancel");
}

TEST_F(Nav2ActuatorTest, NavigatesAndCancelsGoals)
{
  using GoalHandle = rclcpp_action::ServerGoalHandle<NavigateToPose>;
  std::shared_ptr<GoalHandle> active;
  NavigateToPose::Goal seen;
  bool hold = false;
  auto server = rclcpp_action::create_server<NavigateToPose>(
    nav2_, "/navigate_to_pose",
    [&seen](const rclcpp_action::GoalUUID &, std::shared_ptr<const NavigateToPose::Goal> goal) {
      seen = *goal;
      return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
    },
    [](const std::shared_ptr<GoalHandle>) { return rclcpp_action::CancelResponse::ACCEPT; },
    [&active, &hold](const std::shared_ptr<GoalHandle> gh) {
      active = gh;
      if (!hold) {
        gh->succeed(std::make_shared<NavigateToPose::Result>());
      }
    });
  auto reached = run(
    "nav2.navigate_to_pose",
    {{"pose", {{"x", 1.5}, {"y", -2.0}, {"yaw", M_PI}}}, {"frame", "odom"}});
  ASSERT_TRUE(reached);
  EXPECT_TRUE(reached->ok) << reached->message;
  EXPECT_DOUBLE_EQ(seen.pose.pose.position.x, 1.5);
  EXPECT_DOUBLE_EQ(seen.pose.pose.position.y, -2.0);
  EXPECT_NEAR(seen.pose.pose.orientation.z, 1.0, 1e-9);
  EXPECT_EQ(seen.pose.header.frame_id, "odom");

  // A goal still running is cancelled by cancel_navigation.
  hold = true;
  Attempt a;
  a.execution_id = "long";
  a.step_id = "go";
  a.action = "nav2.navigate_to_pose";
  a.args = {{"pose", {{"x", 0}, {"y", 0}}}};
  std::optional<bool> result;
  actuator_->start(a, [&result](bool ok, const std::string &) { result = ok; });
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
  while (!(active && active->is_executing() && active->get_goal()->pose.pose.position.x == 0.0) &&
         std::chrono::steady_clock::now() < deadline) {
    executor_.spin_some(std::chrono::milliseconds(20));
  }
  auto cancelled = run("nav2.cancel_navigation", {{"actions", {"/navigate_to_pose"}}});
  ASSERT_TRUE(cancelled);
  EXPECT_TRUE(cancelled->ok);
  EXPECT_EQ(cancelled->message, "cancelled 1 navigation goal(s)");
  actuator_->cancel(a);  // the orchestrator's timeout: no result is reported after this
  active->canceled(std::make_shared<NavigateToPose::Result>());
  for (int i = 0; i < 20; ++i) {
    executor_.spin_some(std::chrono::milliseconds(20));
  }
  EXPECT_FALSE(result.has_value());
}
