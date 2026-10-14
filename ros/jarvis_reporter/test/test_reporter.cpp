// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include <gtest/gtest.h>

#include <algorithm>
#include <chrono>
#include <jarvis_reporter/reporter.hpp>
#include <memory>
#include <string>
#include <thread>
#include <vector>

using jarvis_interfaces::msg::Attribute;
using jarvis_interfaces::msg::Event;
using jarvis_reporter::Options;
using jarvis_reporter::Reporter;
using jarvis_reporter::Severity;
using namespace std::chrono_literals;

class ReporterTest : public ::testing::Test
{
protected:
  static void SetUpTestSuite() { rclcpp::init(0, nullptr); }
  static void TearDownTestSuite() { rclcpp::shutdown(); }

  void SetUp() override
  {
    static int counter = 0;
    options_.topic = "/test_reporter_cpp_" + std::to_string(counter++);
    options_.repeat_period = 200ms;
    node_ = std::make_shared<rclcpp::Node>("app", "test");
    sub_ = node_->create_subscription<Event>(
      options_.topic, rclcpp::QoS(1000).reliable(),
      [this](Event::ConstSharedPtr msg) { received_.push_back(*msg); });
    executor_.add_node(node_);
  }

  /// Creates the reporter and waits until its publisher is matched with our subscription.
  std::unique_ptr<Reporter> make_reporter()
  {
    auto reporter = std::make_unique<Reporter>(node_, "lidar", options_);
    const auto deadline = std::chrono::steady_clock::now() + 5s;
    while (sub_->get_publisher_count() == 0 && std::chrono::steady_clock::now() < deadline) {
      executor_.spin_some(10ms);
    }
    EXPECT_GT(sub_->get_publisher_count(), 0u);
    return reporter;
  }

  /// Spins until `n` messages arrived, then a little longer to catch extra ones.
  const std::vector<Event> & receive(std::size_t n)
  {
    const auto deadline = std::chrono::steady_clock::now() + 5s;
    while (received_.size() < n && std::chrono::steady_clock::now() < deadline) {
      executor_.spin_some(10ms);
    }
    const auto settle = std::chrono::steady_clock::now() + 100ms;
    while (std::chrono::steady_clock::now() < settle) {
      executor_.spin_some(10ms);
    }
    return received_;
  }

  Options options_;
  rclcpp::Node::SharedPtr node_;
  rclcpp::Subscription<Event>::SharedPtr sub_;
  rclcpp::executors::SingleThreadedExecutor executor_;
  std::vector<Event> received_;
};

TEST_F(ReporterTest, FaultIsSentOnEdgeThenRepeatedAtMostOncePerPeriod)
{
  auto jarvis = make_reporter();
  EXPECT_TRUE(jarvis->fault("sensor.lidar.stale", Severity::Error, "no scan"));
  EXPECT_FALSE(jarvis->fault("sensor.lidar.stale", Severity::Error, "no scan"));
  EXPECT_TRUE(jarvis->is_raised("sensor.lidar.stale"));
  std::this_thread::sleep_for(250ms);
  EXPECT_TRUE(jarvis->fault("sensor.lidar.stale", Severity::Error, "still no scan"));

  const auto & msgs = receive(2);
  ASSERT_EQ(msgs.size(), 2u);
  EXPECT_EQ(msgs[0].kind, Event::KIND_FAULT_RAISE);
  EXPECT_EQ(msgs[0].code, "sensor.lidar.stale");
  EXPECT_EQ(msgs[0].severity, Event::SEVERITY_ERROR);
  EXPECT_EQ(msgs[0].message, "no scan");
  EXPECT_EQ(msgs[0].source.node, "/test/app");
  EXPECT_EQ(msgs[0].source.component, "lidar");
  EXPECT_TRUE(msgs[0].id.empty()) << "the event hub assigns ids";
  EXPECT_GT(rclcpp::Time(msgs[0].stamp).nanoseconds(), 0);
  EXPECT_EQ(msgs[1].message, "still no scan");
}

TEST_F(ReporterTest, ClearIsSentOnlyForARaisedFault)
{
  auto jarvis = make_reporter();
  EXPECT_FALSE(jarvis->clear("sensor.lidar.stale"));
  EXPECT_TRUE(jarvis->fault("sensor.lidar.stale"));
  EXPECT_TRUE(jarvis->clear("sensor.lidar.stale", "scans are back"));
  EXPECT_FALSE(jarvis->clear("sensor.lidar.stale"));
  EXPECT_FALSE(jarvis->is_raised("sensor.lidar.stale"));
  // A new episode is reported at once, whatever the repeat period.
  EXPECT_TRUE(jarvis->fault("sensor.lidar.stale"));

  const auto & msgs = receive(3);
  ASSERT_EQ(msgs.size(), 3u);
  EXPECT_EQ(msgs[0].kind, Event::KIND_FAULT_RAISE);
  EXPECT_EQ(msgs[1].kind, Event::KIND_FAULT_CLEAR);
  EXPECT_EQ(msgs[1].severity, Event::SEVERITY_INFO);
  EXPECT_EQ(msgs[1].message, "scans are back");
  EXPECT_EQ(msgs[2].kind, Event::KIND_FAULT_RAISE);
}

TEST_F(ReporterTest, StateIsSentWhenTheValueOrItsTypeChanges)
{
  auto jarvis = make_reporter();
  EXPECT_TRUE(jarvis->state("robot.mode", "IDLE"));
  EXPECT_FALSE(jarvis->state("robot.mode", "IDLE"));
  EXPECT_TRUE(jarvis->state("robot.mode", "AUTONOMOUS"));
  EXPECT_TRUE(jarvis->state("robot.docked", 1));
  EXPECT_TRUE(jarvis->state("robot.docked", true));

  const auto & msgs = receive(4);
  ASSERT_EQ(msgs.size(), 4u);
  EXPECT_EQ(msgs[0].kind, Event::KIND_STATE);
  ASSERT_EQ(msgs[1].attrs.size(), 1u);
  EXPECT_EQ(msgs[1].attrs[0].key, "value");
  EXPECT_EQ(msgs[1].attrs[0].string_value, "AUTONOMOUS");
  EXPECT_EQ(msgs[2].attrs[0].type, Attribute::TYPE_INT);
  EXPECT_EQ(msgs[3].attrs[0].type, Attribute::TYPE_BOOL);
}

TEST_F(ReporterTest, LogIsNeverDebouncedAndAttributesKeepTheirType)
{
  auto jarvis = make_reporter();
  const jarvis_reporter::Attributes attrs{
    {"topic", "/scan"}, {"silence_ms", 1200}, {"rate_hz", 9.5}, {"degraded", true}};
  jarvis->log("sensor.lidar.slow", Severity::Warn, "scan rate low", attrs);
  jarvis->log("sensor.lidar.slow", Severity::Warn, "scan rate low", attrs);

  const auto & msgs = receive(2);
  ASSERT_EQ(msgs.size(), 2u);
  EXPECT_EQ(msgs[0].kind, Event::KIND_LOG);
  EXPECT_EQ(msgs[0].severity, Event::SEVERITY_WARN);
  ASSERT_EQ(msgs[0].attrs.size(), 4u);  // sorted by key
  EXPECT_EQ(msgs[0].attrs[0].key, "degraded");
  EXPECT_EQ(msgs[0].attrs[0].type, Attribute::TYPE_BOOL);
  EXPECT_TRUE(msgs[0].attrs[0].bool_value);
  EXPECT_EQ(msgs[0].attrs[1].key, "rate_hz");
  EXPECT_EQ(msgs[0].attrs[1].type, Attribute::TYPE_DOUBLE);
  EXPECT_DOUBLE_EQ(msgs[0].attrs[1].double_value, 9.5);
  EXPECT_EQ(msgs[0].attrs[2].key, "silence_ms");
  EXPECT_EQ(msgs[0].attrs[2].type, Attribute::TYPE_INT);
  EXPECT_EQ(msgs[0].attrs[2].int_value, 1200);
  EXPECT_EQ(msgs[0].attrs[3].key, "topic");
  EXPECT_EQ(msgs[0].attrs[3].type, Attribute::TYPE_STRING);
  EXPECT_EQ(msgs[0].attrs[3].string_value, "/scan");
}

TEST_F(ReporterTest, IsThreadSafe)
{
  auto jarvis = make_reporter();
  // At most 4 x 50 x 3 = 600 messages: fits the 1000-deep queues, so none is overwritten.
  std::vector<std::thread> threads;
  for (int t = 0; t < 4; ++t) {
    threads.emplace_back([&jarvis, t]() {
      for (int i = 0; i < 50; ++i) {
        jarvis->fault("app.worker.busy", Severity::Warn);
        jarvis->state("app.worker.last", t * 1000 + i);
        jarvis->clear("app.worker.busy");
      }
    });
  }
  for (auto & th : threads) {
    th.join();
  }
  // Every state value is distinct, so all 200 are sent: wait for them, then the stream is complete.
  const auto deadline = std::chrono::steady_clock::now() + 5s;
  auto states = [this]() {
    return std::count_if(received_.begin(), received_.end(), [](const Event & m) {
      return m.kind == Event::KIND_STATE;
    });
  };
  while (states() < 200 && std::chrono::steady_clock::now() < deadline) {
    executor_.spin_some(10ms);
  }
  receive(0);
  ASSERT_EQ(states(), 200);

  // Every raise is followed by its clear: the stream alternates and ends cleared.
  std::vector<uint8_t> faults;
  for (const auto & m : received_) {
    if (m.code == "app.worker.busy") {
      faults.push_back(m.kind);
    }
  }
  ASSERT_FALSE(faults.empty());
  ASSERT_EQ(faults.size() % 2, 0u);
  for (std::size_t i = 0; i < faults.size(); ++i) {
    EXPECT_EQ(faults[i], i % 2 == 0 ? Event::KIND_FAULT_RAISE : Event::KIND_FAULT_CLEAR) << i;
  }
  EXPECT_FALSE(jarvis->is_raised("app.worker.busy"));
}

TEST_F(ReporterTest, WaitsForTheHubAndFlushes)
{
  auto jarvis = make_reporter();
  EXPECT_TRUE(jarvis->wait_for_hub(1s));
  EXPECT_TRUE(jarvis->fault("app.cli.report"));
  EXPECT_TRUE(jarvis->flush(2s));
  EXPECT_EQ(receive(1).size(), 1u);

  Options lonely = options_;
  lonely.topic = "/test_reporter_cpp_nobody";
  Reporter alone(node_, "cli", lonely);
  EXPECT_FALSE(alone.wait_for_hub(50ms)) << "no reader on that topic";
}
