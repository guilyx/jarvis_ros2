// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// bt.run against real services, an action server and a stand-in /jarvis/faults: the tree runs, its
// named nodes' outcomes and the last failure's reason make the step's message, the plan's check
// refuses what cannot run, and a cancel halts the tree.

#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>
#include <jarvis_interfaces/action/analyze.hpp>
#include <jarvis_interfaces/msg/fault_array.hpp>
#include <jarvis_ros/actuator.hpp>
#include <memory>
#include <optional>
#include <pluginlib/class_loader.hpp>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp_action/rclcpp_action.hpp>
#include <rclcpp_lifecycle/lifecycle_node.hpp>
#include <std_srvs/srv/trigger.hpp>
#include <string>

#include "jarvis_bt/nodes.hpp"

namespace fs = std::filesystem;
using jarvis_interfaces::msg::Fault;
using jarvis_interfaces::msg::FaultArray;
using jarvis_ros::Attempt;
using Trigger = std_srvs::srv::Trigger;

namespace
{

const char * kFaults = "/t_bt/faults";

FaultArray snapshot(bool stale)
{
  FaultArray a;
  if (stale) {
    Fault f;
    f.code = "sensor.scan.stale";
    f.source.component = "lidar";
    f.state = Fault::STATE_ACTIVE;
    a.faults.push_back(f);
  }
  return a;
}

}  // namespace

class TreeActuatorTest : public ::testing::Test
{
protected:
  static void SetUpTestSuite() { rclcpp::init(0, nullptr); }
  static void TearDownTestSuite() { rclcpp::shutdown(); }

  void SetUp() override
  {
    dir_ = fs::temp_directory_path() / ("jarvis_bt_" + std::to_string(::getpid()));
    fs::create_directories(dir_);
    node_ = std::make_shared<rclcpp_lifecycle::LifecycleNode>("recovery", "t_bt");
    plain_ = std::make_shared<rclcpp::Node>("fixture", "t_bt");
    executor_.add_node(node_->get_node_base_interface());
    executor_.add_node(plain_);
    faults_ =
      plain_->create_publisher<FaultArray>(kFaults, rclcpp::QoS(1).reliable().transient_local());
    faults_->publish(snapshot(true));
    soft_ = plain_->create_service<Trigger>(
      "/t_bt/soft",
      [this](std::shared_ptr<Trigger::Request>, std::shared_ptr<Trigger::Response> r) {
        ++soft_calls_;
        r->success = false;
        r->message = "still wedged";
      });
    hard_ = plain_->create_service<Trigger>(
      "/t_bt/hard",
      [this](std::shared_ptr<Trigger::Request>, std::shared_ptr<Trigger::Response> r) {
        ++hard_calls_;
        r->success = true;
        r->message = "restarted";
        if (clear_on_hard_) {
          faults_->publish(snapshot(false));
        }
      });
  }

  void TearDown() override
  {
    actuator_.reset();
    executor_.remove_node(node_->get_node_base_interface());
    executor_.remove_node(plain_);
    fs::remove_all(dir_);
  }

  std::shared_ptr<jarvis_ros::Actuator> actuator(bool with_dir = true)
  {
    actuator_ = loader_.createSharedInstance("jarvis_bt::TreeActuator");
    actuator_->initialize(node_);
    if (with_dir) {
      node_->set_parameter(rclcpp::Parameter("bt.trees_dir", dir_.string()));
    }
    return actuator_;
  }

  void tree(const std::string & file, const std::string & xml)
  {
    std::ofstream(dir_ / file) << xml;
  }

  struct Outcome
  {
    bool ok;
    std::string message;
  };

  Attempt make(nlohmann::json args)
  {
    Attempt a;
    a.execution_id = "exec-" + std::to_string(++n_);
    a.step_id = "tree";
    a.action = "bt.run";
    a.args = std::move(args);
    return a;
  }

  std::optional<Outcome> run(nlohmann::json args, double timeout_s = 10.0)
  {
    std::optional<Outcome> out;
    actuator_->start(make(std::move(args)), [&out](bool ok, const std::string & message) {
      out = Outcome{ok, message};
    });
    spin_until([&out] { return out.has_value(); }, timeout_s);
    return out;
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

  fs::path dir_;
  rclcpp::executors::SingleThreadedExecutor executor_;
  rclcpp_lifecycle::LifecycleNode::SharedPtr node_;
  rclcpp::Node::SharedPtr plain_;
  rclcpp::Publisher<FaultArray>::SharedPtr faults_;
  rclcpp::Service<Trigger>::SharedPtr soft_;
  rclcpp::Service<Trigger>::SharedPtr hard_;
  int soft_calls_{0};
  int hard_calls_{0};
  bool clear_on_hard_{true};
  int n_{0};
  pluginlib::ClassLoader<jarvis_ros::Actuator> loader_{"jarvis_ros", "jarvis_ros::Actuator"};
  std::shared_ptr<jarvis_ros::Actuator> actuator_;
};

const char * kRecovery = R"(
<root BTCPP_format="4" main_tree_to_execute="Recover">
  <BehaviorTree ID="Recover">
    <Sequence>
      <JarvisFaultActive name="StillStale" code="sensor.scan.*" component="lidar" topic="/t_bt/faults"/>
      <Fallback>
        <Sequence name="SoftRestart">
          <JarvisCallService service="{soft}" type="std_srvs/srv/Trigger"/>
        </Sequence>
        <Sequence name="HardRestart">
          <JarvisCallService service="/t_bt/hard" type="std_srvs/srv/Trigger" request="{}"/>
          <JarvisWaitFaultCleared code="sensor.scan.stale" topic="/t_bt/faults" timeout="{wait}"/>
        </Sequence>
      </Fallback>
    </Sequence>
  </BehaviorTree>
</root>)";

TEST_F(TreeActuatorTest, RunsATreeAndSaysWhatItsNamedNodesDid)
{
  auto a = actuator();
  EXPECT_EQ(a->actions(), (std::vector<std::string>{"bt.run"}));
  tree("recovery.xml", kRecovery);
  const auto out =
    run({{"tree", "recovery.xml"}, {"blackboard", {{"soft", "/t_bt/soft"}, {"wait", 5}}}});
  ASSERT_TRUE(out.has_value());
  EXPECT_TRUE(out->ok) << out->message;
  EXPECT_EQ(soft_calls_, 1);
  EXPECT_EQ(hard_calls_, 1);
  EXPECT_NE(out->message.find("Recover succeeded in "), std::string::npos) << out->message;
  EXPECT_NE(
    out->message.find(": StillStale SUCCESS, SoftRestart FAILURE (/t_bt/soft: response.success "
                      "is false, expected true (still wedged)), HardRestart SUCCESS"),
    std::string::npos)
    << out->message;
}

TEST_F(TreeActuatorTest, FailsWithWhyTheLastNodeFailed)
{
  clear_on_hard_ = false;  // the hard restart does not help
  auto a = actuator();
  tree("recovery.xml", kRecovery);
  const auto out =
    run({{"tree", "recovery.xml"}, {"blackboard", {{"soft", "/t_bt/soft"}, {"wait", 0.5}}}});
  ASSERT_TRUE(out.has_value());
  EXPECT_FALSE(out->ok);
  EXPECT_NE(out->message.find("Recover failed in "), std::string::npos) << out->message;
  EXPECT_NE(
    out->message.find(
      ": sensor.scan.stale (lidar): ACTIVE, still, after 0.5 s; StillStale SUCCESS, "
      "SoftRestart FAILURE (/t_bt/soft: "),
    std::string::npos)
    << out->message;
  EXPECT_NE(out->message.find("HardRestart FAILURE"), std::string::npos) << out->message;
}

TEST_F(TreeActuatorTest, SendsGoalsAndWaitsForTheResult)
{
  using Analyze = jarvis_interfaces::action::Analyze;
  using Handle = rclcpp_action::ServerGoalHandle<Analyze>;
  auto server = rclcpp_action::create_server<Analyze>(
    plain_, "/t_bt/analyze",
    [](const rclcpp_action::GoalUUID &, std::shared_ptr<const Analyze::Goal>) {
      return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
    },
    [](std::shared_ptr<Handle>) { return rclcpp_action::CancelResponse::ACCEPT; },
    [](std::shared_ptr<Handle> handle) {
      auto result = std::make_shared<Analyze::Result>();
      result->success = handle->get_goal()->question != "fail";
      result->message = result->success ? "answered" : "no such incident";
      if (result->success) {
        handle->succeed(result);
      } else {
        handle->abort(result);
      }
    });
  auto a = actuator();
  // The goal: YAML in the XML, or the text of a blackboard entry.
  tree("ask.xml", R"(
<root BTCPP_format="4">
  <BehaviorTree ID="Ask">
    <Sequence>
      <JarvisSendGoal name="Literal" action="/t_bt/analyze" type="jarvis_interfaces/action/Analyze"
                      goal="{question: why, requester: tree}" result="{answer}"/>
      <JarvisSendGoal name="FromBlackboard" action="/t_bt/analyze"
                      type="jarvis_interfaces/action/Analyze" goal="{goal}"/>
    </Sequence>
  </BehaviorTree>
</root>)");
  auto out = run({{"tree", "ask.xml"}, {"blackboard", {{"goal", "{question: why}"}}}});
  ASSERT_TRUE(out.has_value());
  EXPECT_TRUE(out->ok) << out->message;
  EXPECT_EQ(out->message.rfind("Ask succeeded in ", 0), 0u) << out->message;
  EXPECT_NE(out->message.find("Literal SUCCESS, FromBlackboard SUCCESS"), std::string::npos)
    << out->message;
  out = run({{"tree", "ask.xml"}, {"blackboard", {{"goal", "{question: fail}"}}}});
  ASSERT_TRUE(out.has_value());
  EXPECT_FALSE(out->ok);
  EXPECT_NE(
    out->message.find("/t_bt/analyze: the goal was aborted: no such incident"), std::string::npos)
    << out->message;
}

TEST_F(TreeActuatorTest, ChecksTheTreeWhenThePlansLoad)
{
  {
    auto a = actuator(false);
    EXPECT_NE(
      a->check("bt.run", {{"tree", "recovery.xml"}}).value_or("").find("bt.trees_dir"),
      std::string::npos);
  }
  auto a = actuator();
  tree("recovery.xml", kRecovery);
  tree("two.xml", R"(<root BTCPP_format="4">
    <BehaviorTree ID="A"><AlwaysSuccess/></BehaviorTree>
    <BehaviorTree ID="B"><AlwaysSuccess/></BehaviorTree></root>)");
  tree("unknown.xml", R"(<root BTCPP_format="4">
    <BehaviorTree ID="A"><RestartTheWorld/></BehaviorTree></root>)");
  fs::create_directories(dir_ / "sub");
  tree(
    "sub/one.xml",
    R"(<root BTCPP_format="4"><BehaviorTree ID="A"><AlwaysSuccess/></BehaviorTree></root>)");
  std::ofstream(dir_.parent_path() / "outside.xml") << "<root/>";

  EXPECT_EQ(a->check("bt.run", {{"tree", "recovery.xml"}}), std::nullopt);
  EXPECT_EQ(a->check("bt.run", {{"tree", "recovery.xml"}, {"id", "Recover"}}), std::nullopt);
  EXPECT_EQ(a->check("bt.run", {{"tree", "sub/one.xml"}}), std::nullopt);
  EXPECT_EQ(a->check("bt.run", {{"tree", "{{ params.tree }}"}}), std::nullopt);  // when it runs
  EXPECT_EQ(a->check("bt.run", {{"tree", "two.xml"}, {"id", "B"}}), std::nullopt);
  EXPECT_EQ(
    a->check("bt.run", {}).value_or(""),
    "needs 'tree' (a non-empty string), a tree file in bt.trees_dir");
  EXPECT_NE(
    a->check("bt.run", {{"tree", "missing.xml"}}).value_or("").find("no tree file 'missing.xml'"),
    std::string::npos);
  EXPECT_NE(
    a->check("bt.run", {{"tree", "../outside.xml"}}).value_or("").find("is not in bt.trees_dir"),
    std::string::npos);
  EXPECT_NE(
    a->check("bt.run", {{"tree", "two.xml"}}).value_or("").find("several trees"),
    std::string::npos);
  EXPECT_NE(
    a->check("bt.run", {{"tree", "two.xml"}, {"id", "C"}})
      .value_or("")
      .find("has no tree 'C' (it has: A, B)"),
    std::string::npos);
  EXPECT_NE(
    a->check("bt.run", {{"tree", "unknown.xml"}}).value_or("").find("RestartTheWorld"),
    std::string::npos);
  EXPECT_NE(
    a->check("bt.run", {{"tree", "recovery.xml"}, {"blackboard", "x"}}).value_or("").find("a map"),
    std::string::npos);
  fs::remove(dir_.parent_path() / "outside.xml");
}

TEST_F(TreeActuatorTest, ACancelHaltsTheTree)
{
  clear_on_hard_ = false;
  auto a = actuator();
  tree("wait.xml", R"(<root BTCPP_format="4"><BehaviorTree ID="Wait">
    <JarvisWaitFaultCleared code="sensor.scan.stale" topic="/t_bt/faults" timeout="30"/>
  </BehaviorTree></root>)");
  std::optional<Outcome> out;
  const auto attempt = make({{"tree", "wait.xml"}});
  a->start(attempt, [&out](bool ok, const std::string & m) { out = Outcome{ok, m}; });
  spin_until([] { return false; }, 0.5);
  EXPECT_FALSE(out.has_value());
  a->cancel(attempt);
  spin_until([] { return false; }, 0.5);
  EXPECT_FALSE(out.has_value()) << "a cancelled step reports nothing";
}

TEST(JarvisNodes, NeedARosNodeOnTheBlackboard)
{
  BT::BehaviorTreeFactory factory;
  jarvis_bt::register_nodes(factory);
  auto tree = factory.createTreeFromText(R"(<root BTCPP_format="4"><BehaviorTree ID="T">
    <JarvisFaultActive code="x"/></BehaviorTree></root>)");
  EXPECT_EQ(tree.tickOnce(), BT::NodeStatus::FAILURE);
  const auto * node = dynamic_cast<const jarvis_bt::Explained *>(tree.rootNode());
  ASSERT_NE(node, nullptr);
  EXPECT_NE(node->why().find("no ROS node on the blackboard"), std::string::npos) << node->why();
}

TEST(JarvisNodes, ParseYaml)
{
  EXPECT_EQ(
    jarvis_bt::parse_yaml("{transition: {label: activate, id: 3}, on: true, x: 1.5, s: '2'}", "r"),
    (nlohmann::json{
      {"transition", {{"label", "activate"}, {"id", 3}}}, {"on", true}, {"x", 1.5}, {"s", "2"}}));
  EXPECT_EQ(jarvis_bt::parse_yaml("  ", "r"), nlohmann::json::object());
  EXPECT_EQ(
    jarvis_bt::parse_yaml(R"({"dock_id": "home", "ids": [1, 2]})", "r"),
    (nlohmann::json{{"dock_id", "home"}, {"ids", {1, 2}}}));
  EXPECT_THROW(jarvis_bt::parse_yaml("{a: [", "request"), std::runtime_error);
}

TEST(JarvisNodes, OpenFaults)
{
  FaultArray a = snapshot(true);
  Fault latched;
  latched.code = "safety.estop.engaged";
  latched.state = Fault::STATE_LATCHED;
  Fault pending;
  pending.code = "sensor.scan.slow";
  pending.state = Fault::STATE_PENDING;
  a.faults.push_back(latched);
  a.faults.push_back(pending);
  EXPECT_EQ(
    jarvis_bt::open_faults(a, "sensor.*", ""),
    (std::vector<std::string>{"sensor.scan.stale (lidar): ACTIVE"}));
  EXPECT_EQ(
    jarvis_bt::open_faults(a, "*", "lidar"),
    (std::vector<std::string>{"sensor.scan.stale (lidar): ACTIVE"}));
  EXPECT_EQ(
    jarvis_bt::open_faults(a, "safety.*", ""),
    (std::vector<std::string>{"safety.estop.engaged: LATCHED"}));
  EXPECT_TRUE(jarvis_bt::open_faults(a, "sensor.scan.slow", "").empty()) << "PENDING is not open";
}
