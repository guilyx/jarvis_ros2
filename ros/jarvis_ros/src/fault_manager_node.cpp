// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// /jarvis/fault_manager: fault lifecycle, correlation and incidents (docs/design/04-data-flow.md §2).
// It reads /jarvis/events, publishes its decisions to /jarvis/ingest, keeps the latched
// /jarvis/faults snapshot, exports active faults on /diagnostics and saves its state to disk.

#include <algorithm>
#include <diagnostic_msgs/msg/diagnostic_array.hpp>
#include <diagnostic_updater/diagnostic_updater.hpp>
#include <jarvis_core/fault_manager.hpp>
#include <jarvis_core/util.hpp>
#include <jarvis_interfaces/msg/fault_array.hpp>
#include <jarvis_interfaces/srv/acknowledge_fault.hpp>
#include <jarvis_interfaces/srv/clear_fault.hpp>
#include <jarvis_interfaces/srv/close_incident.hpp>
#include <jarvis_interfaces/srv/get_faults.hpp>
#include <map>
#include <memory>
#include <rclcpp/create_timer.hpp>
#include <rclcpp_components/register_node_macro.hpp>
#include <rosbag2_interfaces/srv/snapshot.hpp>
#include <string>

#include "jarvis_ros/common.hpp"
#include "jarvis_ros/conversions.hpp"
#include "jarvis_ros/state_file.hpp"

namespace jarvis_ros
{

using CallbackReturn = rclcpp_lifecycle::node_interfaces::LifecycleNodeInterface::CallbackReturn;
using diagnostic_msgs::msg::DiagnosticArray;
using diagnostic_msgs::msg::DiagnosticStatus;
using jarvis_interfaces::msg::FaultArray;
using jarvis_interfaces::srv::AcknowledgeFault;
using jarvis_interfaces::srv::ClearFault;
using jarvis_interfaces::srv::CloseIncident;
using jarvis_interfaces::srv::GetFaults;
using Snapshot = rosbag2_interfaces::srv::Snapshot;

class FaultManagerNode : public rclcpp_lifecycle::LifecycleNode
{
public:
  explicit FaultManagerNode(const rclcpp::NodeOptions & options)
  : rclcpp_lifecycle::LifecycleNode("fault_manager", "jarvis", options)
  {
    declare<std::string>(*this, "robot_id", "robot", "Robot id stamped on Jarvis events");
    declare<std::string>(*this, "catalog_path", "", "Fault catalog (jarvis.faults/v1 YAML)");
    declare<std::string>(
      *this, "state_dir", "", "Where open faults are saved; empty disables persistence");
    declare<std::string>(
      *this, "incident_min_severity", "ERROR", "Lowest severity that opens an incident");
    declare<double>(*this, "incident_merge_window_s", 120.0, "Same-component faults join within");
    declare<double>(*this, "incident_auto_close_h", 24.0, "Resolved incidents close after");
    declare<int>(
      *this, "max_resolved_incidents", 500, "Resolved incidents kept; the oldest close early");
    declare<bool>(*this, "export_diagnostics", true, "Publish active faults on /diagnostics");
    declare<std::string>(
      *this, "blackbox_service", "/jarvis/blackbox/snapshot",
      "rosbag2 snapshot service called for faults with capture.blackbox");
    declare<double>(
      *this, "blackbox_wait_s", 2.0,
      "How long a capture waits for the snapshot service (e.g. at startup) before giving up");
    declare<double>(
      *this, "blackbox_timeout_s", 10.0,
      "How long a capture waits for the snapshot service's answer once the request is sent");
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
    jarvis_core::FaultCatalog catalog;
    const auto catalog_path = get_parameter("catalog_path").as_string();
    if (!catalog_path.empty()) {
      try {
        catalog = jarvis_core::FaultCatalog::from_file(catalog_path);
      } catch (const std::exception & e) {
        RCLCPP_ERROR(get_logger(), "%s", e.what());
        return CallbackReturn::FAILURE;
      }
    } else {
      RCLCPP_WARN(get_logger(), "no catalog_path: every fault uses the default policy");
    }

    jarvis_core::FaultManagerConfig config;
    config.robot_id = get_parameter("robot_id").as_string();
    config.node_name = get_fully_qualified_name();
    const auto min_sev =
      jarvis_core::parse_severity(get_parameter("incident_min_severity").as_string());
    if (!min_sev) {
      RCLCPP_ERROR(
        get_logger(), "incident_min_severity must be DEBUG, INFO, WARN, ERROR or CRITICAL");
      return CallbackReturn::FAILURE;
    }
    config.incident_min_severity = *min_sev;
    config.incident_merge_window = static_cast<jarvis_core::Duration>(
      get_parameter("incident_merge_window_s").as_double() * 1e9);
    config.incident_auto_close = static_cast<jarvis_core::Duration>(
      get_parameter("incident_auto_close_h").as_double() * 3600e9);
    config.max_resolved_incidents = static_cast<std::size_t>(
      std::max<int64_t>(1, get_parameter("max_resolved_incidents").as_int()));
    manager_ = std::make_unique<jarvis_core::FaultManager>(std::move(catalog), config);

    state_ =
      std::make_unique<StateFile>(get_parameter("state_dir").as_string(), "fault_manager.json");
    try {
      if (const auto saved = state_->load()) {
        pending_ = manager_->restore(*saved, now_ns(*this));
        RCLCPP_INFO(
          get_logger(), "restored %zu open faults from %s", manager_->faults().size(),
          state_->path().c_str());
      }
    } catch (const std::exception & e) {
      // A corrupt state file must not keep the robot from starting: start empty, say so loudly.
      RCLCPP_ERROR(get_logger(), "ignoring saved state: %s", e.what());
    }

    const auto depth = static_cast<std::size_t>(get_parameter("queue_depth").as_int());
    depth_ = depth;
    ingest_pub_ = create_event_publisher(*this, "ingest", stream_qos(depth));
    faults_pub_ = create_publisher<FaultArray>("faults", latched_qos());
    if (get_parameter("export_diagnostics").as_bool()) {
      diagnostics_pub_ =
        create_publisher<DiagnosticArray>("/diagnostics", rclcpp::SystemDefaultsQoS());
    }
    blackbox_client_ = create_client<Snapshot>(get_parameter("blackbox_service").as_string());

    get_faults_srv_ = create_service<GetFaults>(
      "~/get_faults", [this](
                        const std::shared_ptr<GetFaults::Request> req,
                        std::shared_ptr<GetFaults::Response> res) { on_get_faults(*req, *res); });
    ack_srv_ = create_service<AcknowledgeFault>(
      "~/acknowledge_fault", [this](
                               const std::shared_ptr<AcknowledgeFault::Request> req,
                               std::shared_ptr<AcknowledgeFault::Response> res) {
        auto r = manager_->acknowledge(req->fault_id, req->operator_name, req->note, now_ns(*this));
        res->success = r.success;
        res->message = r.message;
        apply(std::move(r.effects));
      });
    close_srv_ = create_service<CloseIncident>(
      "~/close_incident", [this](
                            const std::shared_ptr<CloseIncident::Request> req,
                            std::shared_ptr<CloseIncident::Response> res) {
        auto r =
          manager_->close_incident(req->incident_id, req->operator_name, req->note, now_ns(*this));
        res->success = r.success;
        res->message = r.message;
        apply(std::move(r.effects));
      });
    clear_srv_ = create_service<ClearFault>(
      "~/clear_fault",
      [this](
        const std::shared_ptr<ClearFault::Request> req, std::shared_ptr<ClearFault::Response> res) {
        auto r = manager_->clear(
          req->fault_id, req->operator_name, req->reason, req->skip_symptoms, now_ns(*this));
        res->success = r.success;
        res->message = r.message;
        res->cleared_symptoms = r.cleared_symptoms;
        apply(std::move(r.effects));
      });

    self_diagnostics_ = std::make_unique<diagnostic_updater::Updater>(this);
    self_diagnostics_->setHardwareID("jarvis");
    self_diagnostics_->add(
      "fault manager", [this](diagnostic_updater::DiagnosticStatusWrapper & s) {
        if (!manager_) {
          s.summary(DiagnosticStatus::STALE, "not configured");
          return;
        }
        s.summary(
          persist_failures_ > 0 ? DiagnosticStatus::WARN : DiagnosticStatus::OK,
          persist_failures_ > 0 ? "cannot save state" : (active_ ? "active" : "inactive"));
        s.add("open_faults", manager_->faults().size());
        s.add("live_incidents", manager_->incidents().size());
        s.add("catalog_size", manager_->catalog().size());
        s.add("state_file", state_->enabled() ? state_->path() : std::string("disabled"));
        s.add("persist_failures", persist_failures_);
      });
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_activate(const rclcpp_lifecycle::State &) override
  {
    faults_pub_->on_activate();
    if (diagnostics_pub_) {
      diagnostics_pub_->on_activate();
    }
    active_ = true;
    events_sub_ = create_subscription<AdaptedEvent>(
      "events", stream_qos(depth_),
      [this](const jarvis_core::Event & e) { apply(manager_->on_event(e, now_ns(*this))); });
    // State writes are coalesced so a burst of transitions costs one fsync.
    persist_timer_ =
      create_wall_timer(std::chrono::milliseconds(100), [this]() { persist_if_dirty(); });
    diagnostics_timer_ =
      create_wall_timer(std::chrono::seconds(1), [this]() { export_diagnostics(); });
    apply(std::exchange(pending_, {}));
    publish_faults();
    arm_deadline();
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_deactivate(const rclcpp_lifecycle::State &) override
  {
    active_ = false;
    events_sub_.reset();
    deadline_timer_.reset();
    blackbox_retry_timer_.reset();
    waiting_captures_.clear();
    for (const auto & [id, sent] : sent_captures_) {
      if (blackbox_client_) {
        blackbox_client_->remove_pending_request(id);
      }
    }
    sent_captures_.clear();
    diagnostics_timer_.reset();
    persist_if_dirty();
    persist_timer_.reset();
    faults_pub_->on_deactivate();
    if (diagnostics_pub_) {
      diagnostics_pub_->on_deactivate();
    }
    return CallbackReturn::SUCCESS;
  }

  CallbackReturn on_cleanup(const rclcpp_lifecycle::State &) override
  {
    self_diagnostics_.reset();
    get_faults_srv_.reset();
    ack_srv_.reset();
    clear_srv_.reset();
    close_srv_.reset();
    blackbox_client_.reset();
    diagnostics_pub_.reset();
    faults_pub_.reset();
    ingest_pub_.reset();
    manager_.reset();
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
  void apply(jarvis_core::Effects effects)
  {
    for (auto & fx : effects) {
      if (auto * emit = std::get_if<jarvis_core::EmitEvent>(&fx)) {
        if (active_) {
          ingest_pub_->publish(emit->event);
        } else {
          pending_.push_back(std::move(fx));
        }
      } else if (std::holds_alternative<jarvis_core::FaultsChanged>(fx)) {
        if (active_) {
          publish_faults();
          export_diagnostics();
        }
      } else if (std::holds_alternative<jarvis_core::PersistState>(fx)) {
        dirty_ = true;
      } else if (auto * capture = std::get_if<jarvis_core::CaptureBlackbox>(&fx)) {
        request_blackbox(*capture);
      }
    }
    arm_deadline();
  }

  /// One timer, armed for the earliest deadline the core reports (debounce, heal, auto-close).
  /// Uses the node clock, so it follows sim time.
  void arm_deadline()
  {
    if (!active_ || !manager_) {
      return;
    }
    const auto next = manager_->next_deadline();
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
        deadline_timer_.reset();
        armed_for_ = 0;
        apply(manager_->advance_to(now_ns(*this)));
      });
  }

  void persist_if_dirty()
  {
    if (!dirty_ || !manager_ || !state_) {
      return;
    }
    try {
      state_->save(manager_->snapshot());
      dirty_ = false;
    } catch (const std::exception & e) {
      ++persist_failures_;
      RCLCPP_ERROR_THROTTLE(get_logger(), *get_clock(), 10000, "saving state: %s", e.what());
    }
  }

  void publish_faults()
  {
    FaultArray msg;
    msg.stamp = to_msg_time(now_ns(*this));
    for (const auto & f : manager_->faults()) {
      msg.faults.push_back(to_msg(f));
    }
    faults_pub_->publish(msg);
  }

  void export_diagnostics()
  {
    if (!diagnostics_pub_ || !active_) {
      return;
    }
    DiagnosticArray array;
    array.header.stamp = now();
    for (const auto & f : manager_->faults()) {
      if (f.state == jarvis_core::FaultState::Pending || !f.muted_by.empty()) {
        continue;
      }
      DiagnosticStatus s;
      s.hardware_id = "jarvis";
      s.name = "jarvis: " + f.code + " (" + f.source.component + ")";
      s.level = f.severity >= jarvis_core::Severity::Error ? DiagnosticStatus::ERROR
                                                           : DiagnosticStatus::WARN;
      s.message = std::string(jarvis_core::to_string(f.state)) +
                  (f.message.empty() ? std::string() : ": " + f.message);
      auto kv = [&s](std::string key, std::string value) {
        diagnostic_msgs::msg::KeyValue v;
        v.key = std::move(key);
        v.value = std::move(value);
        s.values.push_back(std::move(v));
      };
      kv("fault_id", f.fault_id);
      kv("severity", std::string(jarvis_core::to_string(f.severity)));
      kv("occurrences", std::to_string(f.occurrences));
      kv("incident_id", f.incident_id);
      kv("acknowledged", f.acknowledged ? "true" : "false");
      array.status.push_back(std::move(s));
    }
    diagnostics_pub_->publish(array);
  }

  void on_get_faults(const GetFaults::Request & req, GetFaults::Response & res)
  {
    if (!manager_) {
      return;
    }
    for (const auto & f : manager_->faults(req.include_cleared)) {
      if (!req.include_muted && !f.muted_by.empty()) {
        continue;
      }
      if (!req.component.empty() && f.source.component != req.component) {
        continue;
      }
      if (
        !req.codes.empty() &&
        std::none_of(req.codes.begin(), req.codes.end(), [&f](const std::string & p) {
          return jarvis_core::glob_match(p, f.code);
        })) {
        continue;
      }
      res.faults.push_back(to_msg(f));
      if (req.limit != 0 && res.faults.size() >= req.limit) {
        break;
      }
    }
  }

  /// Captures wait a little for the snapshot service: right after startup (or a recorder restart)
  /// the client may not have discovered it yet, and a request sent then would be lost.
  void request_blackbox(const jarvis_core::CaptureBlackbox & capture)
  {
    if (blackbox_client_->service_is_ready()) {
      send_blackbox(capture);
      return;
    }
    const auto wait = std::chrono::duration<double>(get_parameter("blackbox_wait_s").as_double());
    waiting_captures_.push_back(
      {capture, std::chrono::steady_clock::now() +
                  std::chrono::duration_cast<std::chrono::steady_clock::duration>(wait)});
    watch_blackbox();
  }

  void watch_blackbox()
  {
    if (!blackbox_retry_timer_) {
      blackbox_retry_timer_ =
        create_wall_timer(std::chrono::milliseconds(100), [this]() { retry_blackbox(); });
    }
  }

  void retry_blackbox()
  {
    const bool ready = blackbox_client_->service_is_ready();
    const auto now = std::chrono::steady_clock::now();
    auto waiting = std::exchange(waiting_captures_, {});
    for (auto & [capture, deadline] : waiting) {
      if (ready) {
        send_blackbox(capture);
      } else if (now >= deadline) {
        blackbox_notice(
          capture, "jarvis.blackbox.unavailable", jarvis_core::Severity::Warn,
          "Black-box recorder not reachable at " +
            std::string(blackbox_client_->get_service_name()));
      } else {
        waiting_captures_.push_back({std::move(capture), deadline});
      }
    }
    // A request sent to a recorder that went away (or that the graph still listed after it did)
    // is never answered: give it up after blackbox_timeout_s, and say so.
    for (auto it = sent_captures_.begin(); it != sent_captures_.end();) {
      if (now < it->second.deadline) {
        ++it;
        continue;
      }
      blackbox_client_->remove_pending_request(it->first);
      blackbox_notice(
        it->second.capture, "jarvis.blackbox.unavailable", jarvis_core::Severity::Warn,
        "No answer from the black-box recorder at " +
          std::string(blackbox_client_->get_service_name()));
      it = sent_captures_.erase(it);
    }
    if (waiting_captures_.empty() && sent_captures_.empty()) {
      blackbox_retry_timer_.reset();
    }
  }

  void send_blackbox(const jarvis_core::CaptureBlackbox & capture)
  {
    // The answer and the timeout run in this node's (mutually exclusive) callback group: the
    // request id is stored before either can look for it.
    auto id = std::make_shared<int64_t>(-1);
    auto sent = blackbox_client_->async_send_request(
      std::make_shared<Snapshot::Request>(),
      [this, capture, id](rclcpp::Client<Snapshot>::SharedFuture future) {
        sent_captures_.erase(*id);
        const bool ok = future.get()->success;
        blackbox_notice(
          capture, ok ? "jarvis.blackbox.captured" : "jarvis.blackbox.failed",
          ok ? jarvis_core::Severity::Info : jarvis_core::Severity::Warn,
          ok ? "Black-box snapshot saved" : "Black-box snapshot failed");
      });
    *id = sent.request_id;
    const auto timeout =
      std::chrono::duration<double>(get_parameter("blackbox_timeout_s").as_double());
    sent_captures_[sent.request_id] = {
      capture, std::chrono::steady_clock::now() +
                 std::chrono::duration_cast<std::chrono::steady_clock::duration>(timeout)};
    watch_blackbox();
  }

  void blackbox_notice(
    const jarvis_core::CaptureBlackbox & capture, std::string code, jarvis_core::Severity sev,
    std::string text)
  {
    // The reply can come back after a deactivation; there's nowhere to publish it then.
    if (!active_) {
      return;
    }
    jarvis_core::Event e;
    e.id = ids_.uuid7(now_ns(*this));
    e.stamp = now_ns(*this);
    e.source.robot = get_parameter("robot_id").as_string();
    e.source.node = get_fully_qualified_name();
    e.kind = jarvis_core::Kind::System;
    e.code = std::move(code);
    e.severity = sev;
    e.message = std::move(text);
    e.attrs = {{"fault_id", capture.fault_id}, {"fault_code", capture.code}};
    e.correlation_id = capture.incident_id;
    apply({jarvis_core::EmitEvent{std::move(e)}});
  }

  struct WaitingCapture
  {
    jarvis_core::CaptureBlackbox capture;
    std::chrono::steady_clock::time_point deadline;
  };

  std::unique_ptr<jarvis_core::FaultManager> manager_;
  std::unique_ptr<StateFile> state_;
  jarvis_core::Effects pending_;
  jarvis_core::IdGenerator ids_;

  EventPublisher::SharedPtr ingest_pub_;
  rclcpp_lifecycle::LifecyclePublisher<FaultArray>::SharedPtr faults_pub_;
  rclcpp_lifecycle::LifecyclePublisher<DiagnosticArray>::SharedPtr diagnostics_pub_;
  rclcpp::Subscription<AdaptedEvent>::SharedPtr events_sub_;
  rclcpp::Service<GetFaults>::SharedPtr get_faults_srv_;
  rclcpp::Service<AcknowledgeFault>::SharedPtr ack_srv_;
  rclcpp::Service<ClearFault>::SharedPtr clear_srv_;
  rclcpp::Service<CloseIncident>::SharedPtr close_srv_;
  rclcpp::Client<Snapshot>::SharedPtr blackbox_client_;
  rclcpp::TimerBase::SharedPtr deadline_timer_;
  rclcpp::TimerBase::SharedPtr blackbox_retry_timer_;
  std::vector<WaitingCapture> waiting_captures_;
  std::map<int64_t, WaitingCapture> sent_captures_;  ///< request id -> capture, until answered
  rclcpp::TimerBase::SharedPtr persist_timer_;
  rclcpp::TimerBase::SharedPtr diagnostics_timer_;
  rclcpp::TimerBase::SharedPtr autostart_timer_;
  std::unique_ptr<diagnostic_updater::Updater> self_diagnostics_;

  std::size_t depth_{1000};
  jarvis_core::Timestamp armed_for_{0};
  bool active_{false};
  bool dirty_{false};
  std::uint64_t persist_failures_{0};
};

}  // namespace jarvis_ros

RCLCPP_COMPONENTS_REGISTER_NODE(jarvis_ros::FaultManagerNode)
