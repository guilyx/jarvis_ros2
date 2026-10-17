// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// /jarvis/event_hub: the single publisher of /jarvis/events (docs/design/04-data-flow.md §1).

#include <chrono>
#include <diagnostic_updater/diagnostic_updater.hpp>
#include <jarvis_core/event_hub.hpp>
#include <jarvis_core/util.hpp>
#include <jarvis_interfaces/srv/query_events.hpp>
#include <memory>
#include <rclcpp_components/register_node_macro.hpp>
#include <string>
#include <vector>

#include "jarvis_ros/common.hpp"
#include "jarvis_ros/conversions.hpp"
#include "jarvis_ros/state_file.hpp"

namespace jarvis_ros
{

using CallbackReturn = rclcpp_lifecycle::node_interfaces::LifecycleNodeInterface::CallbackReturn;
using QueryEvents = jarvis_interfaces::srv::QueryEvents;

class EventHubNode : public rclcpp_lifecycle::LifecycleNode
{
public:
  explicit EventHubNode(const rclcpp::NodeOptions & options)
  : rclcpp_lifecycle::LifecycleNode("event_hub", "jarvis", options)
  {
    declare<std::string>(*this, "robot_id", "robot", "Robot id stamped on events that lack one");
    declare<std::string>(
      *this, "state_dir", "", "Where the boot counter is kept; empty disables persistence");
    declare<double>(*this, "rate_limit_per_sec", 50.0, "Token refill per (node, code)", false);
    declare<double>(*this, "rate_limit_burst", 100.0, "Token bucket size per (node, code)", false);
    declare<double>(*this, "suppression_window_s", 10.0, "Period of suppression summaries", false);
    declare<double>(
      *this, "shed_after_s", 1.0, "Behind by more than this, logs below shed_below are dropped",
      false);
    declare<std::string>(*this, "shed_below", "WARN", "Logs below this severity can be shed");
    declare<int>(*this, "buffer_capacity_mb", 128, "Memory budget of the recent-events buffer");
    declare<int>(*this, "max_causal_depth", 16, "Longest allowed chain of caused events");
    // A burst waits in the ingest queue (NFR-1: 20 000 events) while the hub catches up. The
    // stream's writer stays shallow: a deep reliable writer slows down for its slowest reader;
    // readers that may lag (the recorder, the gateway) are deep instead.
    declare<int>(*this, "queue_depth", 1000, "History depth of /jarvis/events (the writer)");
    declare<int>(*this, "ingest_queue_depth", 20000, "History depth of /jarvis/ingest");
    if (declare<bool>(*this, "autostart", false, "Configure and activate on startup")) {
      autostart_timer_ = create_wall_timer(std::chrono::milliseconds(1), [this]() {
        autostart_timer_->cancel();
        autostart(*this);
      });
    }
  }

  CallbackReturn on_configure(const rclcpp_lifecycle::State &) override
  {
    jarvis_core::EventHubConfig config;
    config.robot_id = get_parameter("robot_id").as_string();
    config.node_name = get_fully_qualified_name();
    config.rate_limit_per_sec = get_parameter("rate_limit_per_sec").as_double();
    config.rate_limit_burst = get_parameter("rate_limit_burst").as_double();
    config.suppression_window =
      static_cast<jarvis_core::Duration>(get_parameter("suppression_window_s").as_double() * 1e9);
    config.shed_after =
      static_cast<jarvis_core::Duration>(get_parameter("shed_after_s").as_double() * 1e9);
    const auto shed_below = jarvis_core::parse_severity(get_parameter("shed_below").as_string());
    if (!shed_below) {
      RCLCPP_ERROR(get_logger(), "shed_below: not a severity");
      return CallbackReturn::FAILURE;
    }
    config.shed_below = *shed_below;
    config.buffer_capacity_bytes =
      static_cast<std::size_t>(get_parameter("buffer_capacity_mb").as_int()) << 20;
    config.max_causal_depth = static_cast<int>(get_parameter("max_causal_depth").as_int());
    depth_ = static_cast<std::size_t>(get_parameter("queue_depth").as_int());
    ingest_depth_ = static_cast<std::size_t>(get_parameter("ingest_queue_depth").as_int());

    StateFile state(get_parameter("state_dir").as_string(), "event_hub.json");
    try {
      const auto previous = state.load();
      boot_count_ = static_cast<uint16_t>(previous ? previous->value("boot_count", 0) + 1 : 1);
      state.save({{"schema", "jarvis.event_hub.state/v1"}, {"boot_count", boot_count_}});
    } catch (const std::exception & e) {
      RCLCPP_ERROR(get_logger(), "boot counter: %s", e.what());
      return CallbackReturn::FAILURE;
    }

    hub_ = std::make_unique<jarvis_core::EventHub>(config, boot_count_);
    events_pub_ = create_event_publisher(*this, "events", stream_qos(depth_));
    query_srv_ = create_service<QueryEvents>(
      "~/query_events", [this](
                          const std::shared_ptr<QueryEvents::Request> req,
                          std::shared_ptr<QueryEvents::Response> res) { on_query(*req, *res); });

    diagnostics_ = std::make_unique<diagnostic_updater::Updater>(this);
    diagnostics_->setHardwareID("jarvis");
    diagnostics_->add("event hub", [this](diagnostic_updater::DiagnosticStatusWrapper & s) {
      if (!hub_) {
        s.summary(diagnostic_msgs::msg::DiagnosticStatus::STALE, "not configured");
        return;
      }
      const auto & st = hub_->stats();
      s.summary(
        st.rejected > 0 && st.accepted == 0 ? diagnostic_msgs::msg::DiagnosticStatus::WARN
                                            : diagnostic_msgs::msg::DiagnosticStatus::OK,
        is_active_ ? "active" : "inactive");
      s.add("boot_count", boot_count_);
      s.add("accepted", st.accepted);
      s.add("rejected", st.rejected);
      s.add("duplicates", st.duplicates);
      s.add("suppressed", st.suppressed);
      s.add("shed", st.shed);
      s.add("evicted", st.evicted);
      s.add("buffered_events", st.buffered_events);
      s.add("buffered_bytes", st.buffered_bytes);
    });

    RCLCPP_INFO(get_logger(), "configured (boot %u)", static_cast<unsigned>(boot_count_));
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_activate(const rclcpp_lifecycle::State &) override
  {
    ingest_sub_ = create_subscription<AdaptedEvent>(
      "ingest", stream_qos(ingest_depth_),
      [this](const jarvis_core::Event & event, const rclcpp::MessageInfo & info) {
        on_ingest(event, info);
      });
    tick_timer_ = create_wall_timer(
      std::chrono::seconds(1), [this]() { publish_all(hub_->advance_to(now_ns(*this))); });
    is_active_ = true;
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_deactivate(const rclcpp_lifecycle::State &) override
  {
    is_active_ = false;
    ingest_sub_.reset();
    tick_timer_.reset();
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_cleanup(const rclcpp_lifecycle::State &) override
  {
    diagnostics_.reset();
    query_srv_.reset();
    events_pub_.reset();
    hub_.reset();
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_shutdown(const rclcpp_lifecycle::State & state) override
  {
    if (is_active_) {
      on_deactivate(state);
    }
    return on_cleanup(state);
  }

private:
  void on_ingest(const jarvis_core::Event & event, const rclcpp::MessageInfo & info)
  {
    // How long it waited in the queue, on this host's clock: the hub sheds routine logs when it is
    // behind. Unknown (0) for intra-process deliveries, whose message info carries no timestamps
    // (Jarvis's own components, in this container), and for an implausible one.
    const auto & rmw = info.get_rmw_message_info();
    jarvis_core::Duration queued = 0;
    if (!rmw.from_intra_process && rmw.received_timestamp > 0) {
      const auto wall = std::chrono::duration_cast<std::chrono::nanoseconds>(
                          std::chrono::system_clock::now().time_since_epoch())
                          .count();
      const auto waited = wall - rmw.received_timestamp;
      if (waited > 0 && waited < 3600 * jarvis_core::kSecond) {
        queued = waited;
      }
    }
    auto result = hub_->ingest(event, now_ns(*this), queued);
    if (
      !result.accepted && result.reject_reason != "rate limited" &&
      result.reject_reason != "duplicate id" && result.reject_reason != "shed") {
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 5000, "rejected %s from %s: %s", event.code.c_str(),
        event.source.node.c_str(), result.reject_reason.c_str());
    }
    publish_all(std::move(result.publish));
  }

  void publish_all(std::vector<jarvis_core::Event> && events)
  {
    if (!is_active_) {
      return;
    }
    for (const auto & e : events) {
      events_pub_->publish(e);
    }
  }

  void on_query(const QueryEvents::Request & req, QueryEvents::Response & res)
  {
    if (!hub_) {
      res.complete = false;
      return;
    }
    if (is_active_) {
      publish_all(hub_->advance_to(now_ns(*this)));
    }
    jarvis_core::EventQuery q;
    q.after_seq = req.after_seq;
    q.from = from_msg_time(req.since);
    q.to = from_msg_time(req.until);
    for (const auto kind : req.kinds) {
      q.kinds.push_back(static_cast<jarvis_core::Kind>(kind));
    }
    q.codes = req.codes;
    q.min_severity = static_cast<jarvis_core::Severity>(req.min_severity);
    q.component = req.component;
    q.correlation_id = req.correlation_id;
    q.limit = req.limit == 0 ? 1000 : req.limit;
    const auto result = hub_->query(q);
    res.events.reserve(result.events.size());
    for (const auto & e : result.events) {
      jarvis_interfaces::msg::Event msg;
      to_msg(e, msg);
      res.events.push_back(std::move(msg));
    }
    res.next_seq = result.next_seq;
    res.complete = result.complete;
  }

  std::unique_ptr<jarvis_core::EventHub> hub_;
  EventPublisher::SharedPtr events_pub_;
  rclcpp::Subscription<AdaptedEvent>::SharedPtr ingest_sub_;
  rclcpp::Service<QueryEvents>::SharedPtr query_srv_;
  rclcpp::TimerBase::SharedPtr tick_timer_;
  rclcpp::TimerBase::SharedPtr autostart_timer_;
  std::unique_ptr<diagnostic_updater::Updater> diagnostics_;
  std::size_t depth_{1000};
  std::size_t ingest_depth_{20000};
  uint16_t boot_count_{0};
  bool is_active_{false};
};

}  // namespace jarvis_ros

RCLCPP_COMPONENTS_REGISTER_NODE(jarvis_ros::EventHubNode)
