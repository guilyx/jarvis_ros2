// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include <gtest/gtest.h>

#include <jarvis_bridges/watchdogs.hpp>
#include <lifecycle_msgs/msg/state.hpp>
#include <lifecycle_msgs/msg/transition.hpp>
#include <string>
#include <vector>

using namespace jarvis_bridges;
using jarvis_core::Kind;
using jarvis_core::kMillisecond;
using jarvis_core::kSecond;
using lifecycle_msgs::msg::State;

namespace
{

constexpr Timestamp kT0 = 100 * kSecond;

std::string attr(const Event & e, const std::string & key)
{
  const auto it = e.attrs.find(key);
  return it == e.attrs.end() ? "" : jarvis_core::attr_to_string(it->second);
}

std::vector<std::string> codes(const std::vector<Event> & events)
{
  std::vector<std::string> out;
  for (const auto & e : events) {
    out.push_back(std::string(jarvis_core::to_string(e.kind)) + " " + e.code);
  }
  return out;
}

lifecycle_msgs::msg::TransitionEvent transition(
  const std::string & label, std::uint8_t from, std::uint8_t to, const std::string & to_label)
{
  lifecycle_msgs::msg::TransitionEvent ev;
  ev.transition.label = label;
  ev.start_state.id = from;
  ev.goal_state.id = to;
  ev.goal_state.label = to_label;
  return ev;
}

using V = std::vector<std::string>;

}  // namespace

TEST(TopicWatch, SilenceRaisesStaleAndTheNextMessageClearsIt)
{
  TopicWatchConfig c;
  c.topic = "/scan";
  c.stale_after = 500 * kMillisecond;
  c.startup_grace = 2 * kSecond;
  TopicWatch w(c, kT0);
  EXPECT_TRUE(w.tick(kT0 + kSecond, 1).empty()) << "still within the startup grace";
  auto raised = w.tick(kT0 + 2 * kSecond, 0);
  ASSERT_EQ(codes(raised), V{"FAULT_RAISE ros.topic.stale"});
  EXPECT_EQ(raised[0].source.component, "scan");
  EXPECT_EQ(attr(raised[0], "reason"), "no_publisher");
  EXPECT_EQ(raised[0].message, "/scan: no publisher for 2.0 s");
  EXPECT_TRUE(w.tick(kT0 + 3 * kSecond, 0).empty()) << "raised once";

  EXPECT_EQ(codes(w.on_message(kT0 + 4 * kSecond)), V{"FAULT_CLEAR ros.topic.stale"});
  EXPECT_TRUE(w.on_message(kT0 + 4 * kSecond + 100 * kMillisecond).empty());
  EXPECT_TRUE(w.tick(kT0 + 4 * kSecond + 500 * kMillisecond, 1).empty()) << "400 ms of silence";
  raised = w.tick(kT0 + 4 * kSecond + 600 * kMillisecond, 1);
  ASSERT_EQ(codes(raised), V{"FAULT_RAISE ros.topic.stale"});
  EXPECT_EQ(attr(raised[0], "reason"), "silent");
  EXPECT_EQ(attr(raised[0], "silence_ms"), "500");
  EXPECT_EQ(raised[0].severity, Severity::Error);
}

TEST(TopicWatch, ALowRateRaisesSlowAndRecoveryClearsIt)
{
  TopicWatchConfig c;
  c.topic = "/odom";
  c.component = "base";
  c.stale_after = 0;  // only the rate is watched
  c.min_rate_hz = 10.0;
  c.rate_window = kSecond;
  c.startup_grace = 0;
  TopicWatch w(c, kT0);
  Timestamp t = kT0;
  std::vector<Event> out;
  auto feed = [&](int count, Duration period) {
    for (int i = 0; i < count; ++i) {
      t += period;
      w.on_message(t);
      for (auto & e : w.tick(t, 1)) {
        out.push_back(std::move(e));
      }
    }
  };
  feed(20, 50 * kMillisecond);  // 20 Hz
  EXPECT_TRUE(out.empty());
  feed(5, 200 * kMillisecond);  // 5 Hz
  ASSERT_EQ(codes(out), V{"FAULT_RAISE ros.topic.slow"});
  EXPECT_EQ(out[0].severity, Severity::Warn);
  EXPECT_EQ(out[0].source.component, "base");
  EXPECT_EQ(attr(out[0], "min_rate_hz"), "10");
  out.clear();
  feed(30, 50 * kMillisecond);
  ASSERT_EQ(codes(out), V{"FAULT_CLEAR ros.topic.slow"});
}

TEST(TopicWatch, SilenceIsStaleNotSlow)
{
  TopicWatchConfig c;
  c.topic = "/scan";
  c.stale_after = kSecond;
  c.min_rate_hz = 5.0;
  c.rate_window = kSecond;
  c.startup_grace = 0;
  TopicWatch w(c, kT0);
  w.on_message(kT0 + 10 * kMillisecond);
  EXPECT_EQ(codes(w.tick(kT0 + 2 * kSecond, 1)), V{"FAULT_RAISE ros.topic.stale"});
  EXPECT_TRUE(w.tick(kT0 + 3 * kSecond, 1).empty());
}

TEST(NodeWatchdog, MissingNodesAreFaultsAfterTheGrace)
{
  NodeWatchConfig c;
  c.nodes = {"/demo/scan_relay", "/controller_server"};
  c.grace = 2 * kSecond;
  NodeWatchdog w(c, kT0);
  EXPECT_TRUE(w.tick(kT0 + kSecond, {"/controller_server"}).empty());
  auto raised = w.tick(kT0 + 2 * kSecond, {"/controller_server"});
  ASSERT_EQ(codes(raised), V{"FAULT_RAISE ros.node.missing"});
  EXPECT_EQ(raised[0].source.component, "scan_relay");
  EXPECT_EQ(attr(raised[0], "node"), "/demo/scan_relay");
  EXPECT_TRUE(w.tick(kT0 + 3 * kSecond, {"/controller_server"}).empty()) << "raised once";

  auto cleared = w.tick(kT0 + 4 * kSecond, {"/controller_server", "/demo/scan_relay"});
  ASSERT_EQ(codes(cleared), V{"FAULT_CLEAR ros.node.missing"});
  EXPECT_EQ(cleared[0].message, "/demo/scan_relay is back");

  EXPECT_TRUE(w.tick(kT0 + 5 * kSecond, {"/demo/scan_relay"}).empty()) << "gone for 1 s only";
  raised = w.tick(kT0 + 6 * kSecond, {"/demo/scan_relay"});
  ASSERT_EQ(codes(raised), V{"FAULT_RAISE ros.node.missing"});
  EXPECT_EQ(raised[0].source.component, "controller_server");
}

TEST(LifecycleTranslator, StatesAndFaultsFollowTransitions)
{
  LifecycleTranslator t;
  const std::string node = "/demo/scan_relay";
  EXPECT_EQ(LifecycleTranslator::state_code(node), "ros.lifecycle.demo.scan_relay.state");

  auto out = t.on_state(node, State::PRIMARY_STATE_ACTIVE, "active", kT0);
  ASSERT_EQ(codes(out), V{"STATE ros.lifecycle.demo.scan_relay.state"});
  EXPECT_EQ(attr(out[0], "value"), "active");
  EXPECT_EQ(out[0].source.component, "scan_relay");

  // An intermediate state says nothing; landing in inactive is a state and a fault.
  EXPECT_TRUE(
    t.on_transition(
       node, transition("deactivate", 3, State::TRANSITION_STATE_DEACTIVATING, "deactivating"), kT0)
      .empty());
  out = t.on_transition(
    node, transition("deactivate", State::TRANSITION_STATE_DEACTIVATING, 2, "inactive"), kT0 + 1);
  ASSERT_EQ(
    codes(out),
    (V{"STATE ros.lifecycle.demo.scan_relay.state", "FAULT_RAISE ros.lifecycle.inactive"}));
  EXPECT_EQ(attr(out[1], "state"), "inactive");
  EXPECT_EQ(out[1].severity, Severity::Warn);

  out = t.on_transition(node, transition("cleanup", 2, 1, "unconfigured"), kT0 + 2);
  EXPECT_EQ(codes(out), V{"STATE ros.lifecycle.demo.scan_relay.state"}) << "raised once";
  out = t.on_transition(node, transition("configure", 1, 2, "inactive"), kT0 + 3);
  out = t.on_transition(node, transition("activate", 2, 3, "active"), kT0 + 4);
  ASSERT_EQ(
    codes(out),
    (V{"STATE ros.lifecycle.demo.scan_relay.state", "FAULT_CLEAR ros.lifecycle.inactive"}));

  // A failing transition: error processing raises `failed`; active again clears it.
  out = t.on_transition(
    node, transition("deactivate", 3, State::TRANSITION_STATE_ERRORPROCESSING, "errorprocessing"),
    kT0 + 5);
  ASSERT_EQ(codes(out), V{"FAULT_RAISE ros.lifecycle.failed"});
  EXPECT_EQ(out[0].severity, Severity::Error);
  out = t.on_transition(
    node, transition("on_error", State::TRANSITION_STATE_ERRORPROCESSING, 1, "unconfigured"),
    kT0 + 6);
  EXPECT_EQ(
    codes(out),
    (V{"STATE ros.lifecycle.demo.scan_relay.state", "FAULT_RAISE ros.lifecycle.inactive"}));
  t.on_transition(node, transition("configure", 1, 2, "inactive"), kT0 + 7);
  out = t.on_transition(node, transition("activate", 2, 3, "active"), kT0 + 8);
  EXPECT_EQ(
    codes(out), (
                  V{"STATE ros.lifecycle.demo.scan_relay.state",
                    "FAULT_CLEAR ros.lifecycle.inactive", "FAULT_CLEAR ros.lifecycle.failed"}));
}

TEST(LifecycleTranslator, TransitionsOlderThanTheBaselineAreSkipped)
{
  // The watch starts while the node configures and activates: GetState answers `active` before
  // the two transitions that got it there arrive.
  LifecycleTranslator t;
  const std::string node = "/demo/scan_relay";
  ASSERT_EQ(t.on_state(node, State::PRIMARY_STATE_ACTIVE, "active", kT0).size(), 1u);
  EXPECT_TRUE(
    t.on_transition(node, transition("", State::TRANSITION_STATE_CONFIGURING, 2, "inactive"), kT0)
      .empty())
    << "not the node leaving active";
  EXPECT_TRUE(
    t.on_transition(node, transition("", State::TRANSITION_STATE_ACTIVATING, 3, "active"), kT0)
      .empty());
  // A real deactivation still counts.
  auto out = t.on_transition(
    node, transition("", State::TRANSITION_STATE_DEACTIVATING, 2, "inactive"), kT0 + 1);
  EXPECT_EQ(
    codes(out),
    (V{"STATE ros.lifecycle.demo.scan_relay.state", "FAULT_RAISE ros.lifecycle.inactive"}));
}

TEST(LifecycleTranslator, ABaselineNeverOverridesATransition)
{
  LifecycleTranslator t;
  t.on_transition("/n", transition("activate", 2, 3, "active"), kT0);
  EXPECT_TRUE(t.on_state("/n", State::PRIMARY_STATE_INACTIVE, "inactive", kT0 + 1).empty())
    << "a GetState reply older than a transition";
  EXPECT_EQ(base_name("/a/b/c/"), "c");
  EXPECT_EQ(base_name("plain"), "plain");
}
