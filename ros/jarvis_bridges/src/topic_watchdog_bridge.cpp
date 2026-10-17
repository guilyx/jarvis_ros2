// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// /jarvis/bridges/topic_watchdog: watched topics that go silent or slow become faults
// (`ros.topic.stale`, `ros.topic.slow`). Subscriptions are generic (serialized), so any message type
// works without its type support; a topic's type is read from the graph unless given.
//
//   topics: [scan]                 # names; each has its own parameters:
//   scan.topic: /scan              # default "/<name>"
//   scan.component: lidar          # default: the topic's last segment
//   scan.stale_after_s: 1.0        # 0 disables
//   scan.min_rate_hz: 5.0          # 0 disables
//   scan.rate_window_s: 2.0
//   scan.startup_grace_s: 5.0      # how long the first message may take
//   scan.severity: ERROR
//   scan.type: ""                  # e.g. sensor_msgs/msg/LaserScan; empty: discovered

#include <map>
#include <rclcpp/create_timer.hpp>
#include <rclcpp_components/register_node_macro.hpp>
#include <string>
#include <vector>

#include "jarvis_bridges/bridge_node.hpp"
#include "jarvis_bridges/watchdogs.hpp"

namespace jarvis_bridges
{

class TopicWatchdogBridge : public BridgeNode
{
public:
  explicit TopicWatchdogBridge(const rclcpp::NodeOptions & options)
  : BridgeNode("topic_watchdog", options)
  {
    using jarvis_ros::declare;
    declare<std::vector<std::string>>(*this, "topics", {}, "Names of the watches (see <name>.*)");
    declare<double>(*this, "tick_period_s", 0.1, "How often silence and rates are checked");
    declare<double>(
      *this, "discovery_period_s", 1.0, "How often unknown topic types are looked up");
    maybe_autostart();
  }

protected:
  void configure_bridge() override
  {
    configs_.clear();
    types_.clear();
    for (const auto & name : strings(get_parameter("topics"))) {
      auto param = [&](const std::string & key, auto fallback) {
        const std::string full = name + "." + key;
        if (!has_parameter(full)) {
          declare_parameter(full, fallback);
        }
        return get_parameter(full).get_value<decltype(fallback)>();
      };
      TopicWatchConfig c;
      c.topic = param("topic", std::string("/") + name);
      c.component = param("component", std::string());
      c.stale_after = seconds(param("stale_after_s", 1.0));
      c.min_rate_hz = param("min_rate_hz", 0.0);
      c.rate_window = seconds(param("rate_window_s", 2.0));
      c.startup_grace = seconds(param("startup_grace_s", 5.0));
      c.severity = severity(name + ".severity", param("severity", std::string("ERROR")));
      if (c.rate_window <= 0 || c.topic.empty() || c.topic.front() != '/') {
        throw std::invalid_argument(
          name + ": needs an absolute topic and a positive rate_window_s");
      }
      types_.push_back(param("type", std::string()));
      configs_.push_back(std::move(c));
    }
    if (configs_.empty()) {
      RCLCPP_INFO(get_logger(), "no topics to watch");
    }
  }

  void activate_bridge() override
  {
    const auto now = now_ns();
    watches_.clear();
    subs_.assign(configs_.size(), nullptr);
    for (const auto & c : configs_) {
      watches_.emplace_back(c, now);
    }
    discover();
    tick_timer_ = rclcpp::create_timer(
      this, get_clock(), rclcpp::Duration::from_seconds(get_parameter("tick_period_s").as_double()),
      [this]() { on_graph([this] { tick(); }); });
    discovery_timer_ = create_wall_timer(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::duration<double>(get_parameter("discovery_period_s").as_double())),
      [this]() { on_graph([this] { discover(); }); });
  }

  void deactivate_bridge() override
  {
    tick_timer_.reset();
    discovery_timer_.reset();
    subs_.clear();
    watches_.clear();
  }

private:
  static Duration seconds(double s) { return static_cast<Duration>(s * 1e9); }

  void tick()
  {
    const auto now = now_ns();
    for (auto & w : watches_) {
      publish(w.tick(now, count_publishers(w.config().topic)));
    }
  }

  void discover()
  {
    std::map<std::string, std::vector<std::string>> graph;
    for (std::size_t i = 0; i < watches_.size(); ++i) {
      if (subs_[i]) {
        continue;
      }
      std::string type = types_[i];
      if (type.empty()) {
        if (graph.empty()) {
          graph = get_topic_names_and_types();
        }
        const auto it = graph.find(watches_[i].config().topic);
        if (it == graph.end() || it->second.empty()) {
          continue;  // nobody has advertised it yet
        }
        type = it->second.front();
      }
      RCLCPP_INFO(
        get_logger(), "watching %s (%s)", watches_[i].config().topic.c_str(), type.c_str());
      // Best effort and volatile: compatible with every publisher, reliable or not.
      subs_[i] = create_generic_subscription(
        watches_[i].config().topic, type, rclcpp::SensorDataQoS(),
        [this, i](std::shared_ptr<const rclcpp::SerializedMessage>) {
          publish(watches_[i].on_message(now_ns()));
        });
    }
  }

  std::vector<TopicWatchConfig> configs_;
  std::vector<std::string> types_;
  std::vector<TopicWatch> watches_;
  std::vector<rclcpp::GenericSubscription::SharedPtr> subs_;
  rclcpp::TimerBase::SharedPtr tick_timer_;
  rclcpp::TimerBase::SharedPtr discovery_timer_;
};

}  // namespace jarvis_bridges

RCLCPP_COMPONENTS_REGISTER_NODE(jarvis_bridges::TopicWatchdogBridge)
