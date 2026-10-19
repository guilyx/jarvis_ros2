// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// `bt.run`: a recovery step that runs a behavior tree (BehaviorTree.CPP 4), so a team's existing
// recovery trees run behind Jarvis's gates, locks, approvals and audit trail
// (docs/guides/behavior-trees.md):
//
//   - id: recover
//     action: bt.run
//     with:
//       tree: lidar_recovery.xml        # in the recovery node's bt.trees_dir
//       id: RecoverLidar                # the tree to run (default: the file's main tree)
//       blackboard:                     # entries the tree reads as {node_name}
//         relay: "{{ params.node }}"
//
// The tree gets the Jarvis nodes (jarvis_bt/nodes.hpp), the nodes of the BT plugin libraries in
// bt.plugins (Nav2's, a vendor's), and on its blackboard what Nav2's nodes expect: `node` (an
// rclcpp node of its own), `server_timeout`, `bt_loop_duration`, `wait_for_service_timeout`. The
// recovery node ticks it at bt.tick_hz; the step's timeout halts it. SUCCESS succeeds the step,
// FAILURE fails it, with what the named nodes did and why the last one failed: "RecoverLidar
// succeeded: SoftRestart FAILURE (sensor.scan.stale (lidar): ACTIVE, still, after 3 s),
// HardRestart SUCCESS".

#include <behaviortree_cpp/bt_factory.h>
#include <behaviortree_cpp/loggers/abstract_logger.h>
#include <behaviortree_cpp/utils/shared_library.h>

#include <filesystem>
#include <fstream>
#include <jarvis_ros/actuator.hpp>
#include <pluginlib/class_list_macros.hpp>
#include <sstream>

#include "jarvis_bt/nodes.hpp"

namespace jarvis_bt
{

namespace fs = std::filesystem;
using jarvis_ros::Attempt;

namespace
{

bool is_template(const std::string & s) { return s.find("{{") != std::string::npos; }

std::string format_seconds(double s)
{
  char buf[32];
  std::snprintf(buf, sizeof buf, "%.1f s", s);
  return buf;
}

/// What the named nodes of a tree did (nodes given a `name` in the XML, e.g. <Sequence
/// name="SoftRestart">), in the order they finished, and why the last failure failed.
class Trace : public BT::StatusChangeLogger
{
public:
  struct Step
  {
    std::string name;
    BT::NodeStatus status;
    std::string why;
  };

  Trace(BT::TreeNode * root, std::string tree, rclcpp::Logger logger)
  : BT::StatusChangeLogger(root), tree_(std::move(tree)), logger_(std::move(logger))
  {
  }

  void callback(
    BT::Duration, const BT::TreeNode & node, BT::NodeStatus, BT::NodeStatus status) override
  {
    if (status != BT::NodeStatus::SUCCESS && status != BT::NodeStatus::FAILURE) {
      return;
    }
    const bool leaf = node.type() == BT::NodeType::ACTION || node.type() == BT::NodeType::CONDITION;
    if (status == BT::NodeStatus::FAILURE && leaf) {
      const auto * explained = dynamic_cast<const Explained *>(&node);
      last_failure_ = explained && !explained->why().empty()
                        ? explained->why()
                        : node.name() + " (" + node.registrationName() + ") failed";
    }
    if (node.name().empty() || node.name() == node.registrationName()) {
      return;
    }
    const std::string why = status == BT::NodeStatus::FAILURE ? last_failure_ : "";
    RCLCPP_INFO(
      logger_, "bt %s: %s %s%s", tree_.c_str(), node.name().c_str(), BT::toStr(status).c_str(),
      why.empty() ? "" : (": " + why).c_str());
    steps_.push_back({node.name(), status, why});
  }

  void flush() override {}

  const std::vector<Step> & steps() const { return steps_; }
  const std::string & last_failure() const { return last_failure_; }

private:
  std::string tree_;
  rclcpp::Logger logger_;
  std::vector<Step> steps_;
  std::string last_failure_;
};

/// One tree being run: its factory (the tree refers to it), the tree, its trace, its ticker.
struct Run
{
  std::unique_ptr<BT::BehaviorTreeFactory> factory;
  BT::Tree tree;
  std::unique_ptr<Trace> trace;
  rclcpp::TimerBase::SharedPtr timer;
  std::string id;
  std::chrono::steady_clock::time_point started;
  std::size_t ticks{0};
};

}  // namespace

class TreeActuator : public jarvis_ros::Actuator
{
public:
  void initialize(const rclcpp_lifecycle::LifecycleNode::SharedPtr & node) override
  {
    Actuator::initialize(node);
    declare(
      node, "bt.trees_dir", std::string(), "Where bt.run finds trees: nothing outside it runs");
    declare(
      node, "bt.plugins", std::vector<std::string>{},
      "BehaviorTree.CPP plugin libraries whose nodes the trees may use (names or paths)");
    declare(node, "bt.tick_hz", 20.0, "How often a running tree is ticked");
    // The trees' own node, Nav2's convention: their clients are spun by the nodes themselves.
    auto options = rclcpp::NodeOptions()
                     .context(node->get_node_base_interface()->get_context())
                     .use_global_arguments(false)
                     .start_parameter_services(false)
                     .start_parameter_event_publisher(false);
    tree_node_ = rclcpp::Node::make_shared(
      std::string(node->get_name()) + "_bt", node->get_namespace(), options);
    ros_ = RosContext::of(tree_node_);
    ros_->faults("/jarvis/faults");  // subscribed now: the snapshot is there when a tree asks
  }

  std::vector<std::string> actions() const override { return {"bt.run"}; }

  std::optional<std::string> check(const std::string &, const nlohmann::json & with) const override
  {
    std::string tree;
    if (auto err = string_arg(with, "tree", tree)) {
      return *err + ", a tree file in bt.trees_dir";
    }
    if (const auto it = with.find("blackboard"); it != with.end() && !it->is_object()) {
      return "'blackboard' must be a map (entry: value)";
    }
    const std::string id = with.value("id", "");
    if (is_template(tree) || is_template(id)) {
      return std::nullopt;  // checked when it runs
    }
    try {
      auto factory = make_factory();
      const auto [path, err] = resolve(tree);
      if (err) {
        return err;
      }
      return pick(*factory, path, id).second;
    } catch (const std::exception & e) {
      return e.what();
    }
  }

  void start(const Attempt & attempt, Done done) override
  {
    auto [token, once] = track(attempt, std::move(done));
    std::string tree;
    if (auto err = string_arg(attempt.args, "tree", tree)) {
      return once(false, *err);
    }
    auto run = std::make_shared<Run>();
    try {
      run->factory = make_factory();
      const auto [path, err] = resolve(tree);
      if (err) {
        return once(false, *err);
      }
      const auto [id, problem] = pick(*run->factory, path, attempt.args.value("id", ""));
      if (problem) {
        return once(false, *problem);
      }
      run->id = id;
      run->tree = run->factory->createTree(id, blackboard(attempt.args));
    } catch (const std::exception & e) {
      return once(false, tree + ": " + e.what());
    }
    run->trace = std::make_unique<Trace>(run->tree.rootNode(), run->id, node()->get_logger());
    run->started = std::chrono::steady_clock::now();
    const std::string key = attempt.key();
    runs_[key] = run;
    const double hz = std::max(1.0, node()->get_parameter("bt.tick_hz").as_double());
    run->timer = node()->create_wall_timer(
      std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::duration<double>(1.0 / hz)),
      [this, key, token, once]() { tick(key, token, once); });
    tick(key, token, once);
  }

  void cancel(const Attempt & attempt) override
  {
    if (const auto it = runs_.find(attempt.key()); it != runs_.end()) {
      it->second->timer->cancel();
      it->second->tree.haltTree();
      runs_.erase(it);
    }
    Actuator::cancel(attempt);
  }

private:
  template <typename T>
  static void declare(
    const rclcpp_lifecycle::LifecycleNode::SharedPtr & node, const std::string & name, T value,
    const std::string & description)
  {
    if (!node->has_parameter(name)) {
      rcl_interfaces::msg::ParameterDescriptor d;
      d.description = description;
      node->declare_parameter(name, value, d);
    }
  }

  std::unique_ptr<BT::BehaviorTreeFactory> make_factory() const
  {
    auto factory = std::make_unique<BT::BehaviorTreeFactory>();
    register_nodes(*factory);
    const auto plugins = node()->get_parameter("bt.plugins").as_string_array();  // a copy
    for (const auto & plugin : plugins) {
      const auto path =
        plugin.find('/') == std::string::npos ? BT::SharedLibrary::getOSName(plugin) : plugin;
      try {
        factory->registerFromPlugin(path);
      } catch (const std::exception & e) {
        throw std::runtime_error("bt.plugins: cannot load '" + plugin + "': " + e.what());
      }
    }
    return factory;
  }

  /// The tree file, inside bt.trees_dir, or why not.
  std::pair<fs::path, std::optional<std::string>> resolve(const std::string & tree) const
  {
    const auto dir = node()->get_parameter("bt.trees_dir").as_string();
    if (dir.empty()) {
      return {{}, "bt.trees_dir (a parameter of the recovery node) is not set: no tree may run"};
    }
    std::error_code ec;
    const auto root = fs::weakly_canonical(dir, ec);
    const auto path =
      fs::weakly_canonical(fs::path(tree).is_absolute() ? fs::path(tree) : root / tree, ec);
    const auto rel = path.lexically_relative(root);
    if (ec || rel.empty() || *rel.begin() == "..") {
      return {{}, "'" + tree + "' is not in bt.trees_dir (" + dir + ")"};
    }
    if (!fs::is_regular_file(path)) {
      return {{}, "no tree file '" + tree + "' in bt.trees_dir (" + dir + ")"};
    }
    return {path, std::nullopt};
  }

  /// Loads the file and picks the tree: `id`, the file's main tree, or its only one.
  static std::pair<std::string, std::optional<std::string>> pick(
    BT::BehaviorTreeFactory & factory, const fs::path & path, const std::string & id)
  {
    try {
      factory.registerBehaviorTreeFromFile(path);
    } catch (const std::exception & e) {
      return {"", path.filename().string() + ": " + e.what()};
    }
    const auto trees = factory.registeredBehaviorTrees();
    if (!id.empty()) {
      if (std::find(trees.begin(), trees.end(), id) == trees.end()) {
        std::string known;
        for (const auto & t : trees) {
          known += (known.empty() ? "" : ", ") + t;
        }
        return {"", path.filename().string() + " has no tree '" + id + "' (it has: " + known + ")"};
      }
      return {id, std::nullopt};
    }
    std::ifstream in(path);
    std::stringstream text;
    text << in.rdbuf();
    // <root main_tree_to_execute="X">: BT.CPP keeps it out of the factory, so read it.
    const std::string attr = "main_tree_to_execute=\"";
    if (const auto at = text.str().find(attr); at != std::string::npos) {
      const auto from = at + attr.size();
      return {text.str().substr(from, text.str().find('"', from) - from), std::nullopt};
    }
    if (trees.size() == 1) {
      return {trees.front(), std::nullopt};
    }
    return {"", path.filename().string() + " has several trees: say which with 'id'"};
  }

  BT::Blackboard::Ptr blackboard(const nlohmann::json & args) const
  {
    auto bb = BT::Blackboard::create();
    bb->set("node", tree_node_);
    // What Nav2's BT nodes read from the blackboard.
    bb->set("server_timeout", std::chrono::milliseconds(1000));
    bb->set("bt_loop_duration", std::chrono::milliseconds(10));
    bb->set("wait_for_service_timeout", std::chrono::milliseconds(1000));
    const auto entries = args.value("blackboard", nlohmann::json::object());
    for (const auto & [key, value] : entries.items()) {
      bb->set(key, value.is_string() ? value.get<std::string>() : value.dump());
    }
    return bb;
  }

  void tick(const std::string & key, const Token & token, const Done & once)
  {
    const auto it = runs_.find(key);
    if (it == runs_.end() || !*token) {
      return;
    }
    const auto run = it->second;  // keeps it alive through the tick
    BT::NodeStatus status;
    try {
      status = run->tree.tickOnce();
      ++run->ticks;
    } catch (const std::exception & e) {
      run->tree.haltTree();
      finish(key);
      return once(false, run->id + ": " + e.what());
    }
    if (status != BT::NodeStatus::SUCCESS && status != BT::NodeStatus::FAILURE) {
      return;
    }
    finish(key);
    const double took =
      std::chrono::duration<double>(std::chrono::steady_clock::now() - run->started).count();
    const bool ok = status == BT::NodeStatus::SUCCESS;
    std::string message = run->id + (ok ? " succeeded" : " failed") + " in " + format_seconds(took);
    if (!ok && !run->trace->last_failure().empty()) {
      message += ": " + run->trace->last_failure();
    }
    std::string steps;
    for (const auto & s : run->trace->steps()) {
      steps +=
        (steps.empty() ? "" : ", ") + s.name + " " + BT::toStr(s.status) +
        (s.why.empty() || (!ok && s.why == run->trace->last_failure()) ? "" : " (" + s.why + ")");
    }
    if (!steps.empty()) {
      message += (ok ? ": " : "; ") + steps;
    }
    once(ok, message);
  }

  void finish(const std::string & key)
  {
    if (const auto it = runs_.find(key); it != runs_.end()) {
      it->second->timer->cancel();
      runs_.erase(it);
    }
  }

  rclcpp::Node::SharedPtr tree_node_;
  std::shared_ptr<RosContext> ros_;
  std::map<std::string, std::shared_ptr<Run>> runs_;
};

}  // namespace jarvis_bt

PLUGINLIB_EXPORT_CLASS(jarvis_bt::TreeActuator, jarvis_ros::Actuator)
