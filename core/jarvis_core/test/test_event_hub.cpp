// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include <gtest/gtest.h>

#include "jarvis_core/event_hub.hpp"

using namespace jarvis_core;

namespace
{

constexpr Timestamp kT0 = 1'000 * kSecond;

Event report(
  std::string code, Kind kind = Kind::FaultRaise, Severity sev = Severity::Error,
  std::string node = "/demo/node")
{
  Event e;
  e.kind = kind;
  e.code = std::move(code);
  e.severity = sev;
  e.source.node = std::move(node);
  return e;
}

EventHubConfig small_config()
{
  EventHubConfig c;
  c.robot_id = "tb4-sim";
  c.rate_limit_per_sec = 10.0;
  c.rate_limit_burst = 5.0;
  c.suppression_window = 2 * kSecond;
  return c;
}

}  // namespace

TEST(EventHub, AcceptsSequencesAndFillsDefaults)
{
  EventHub hub(small_config(), 3, 1);
  const auto r = hub.ingest(report("ros.topic.stale"), kT0);
  ASSERT_TRUE(r.accepted) << r.reject_reason;
  ASSERT_EQ(r.publish.size(), 1u);
  const Event & e = r.publish.front();
  EXPECT_EQ(e.seq, EventHub::make_seq(3, 1));
  EXPECT_EQ(e.seq >> 48, 3u);
  EXPECT_EQ(e.recv_stamp, kT0);
  EXPECT_EQ(e.stamp, kT0);
  EXPECT_EQ(e.source.robot, "tb4-sim");
  EXPECT_EQ(e.id.size(), 36u);

  const auto r2 = hub.ingest(report("ros.topic.ok", Kind::Log), kT0 + 1);
  EXPECT_EQ(r2.publish.front().seq, EventHub::make_seq(3, 2));
  EXPECT_GT(r2.publish.front().seq, e.seq);
}

TEST(EventHub, SeqStaysMonotonicAcrossBoots)
{
  EventHub before(small_config(), 7, 1);
  Event last;
  for (int i = 0; i < 3; ++i) {
    last = before.ingest(report("a.b", Kind::Log), kT0 + i).publish.front();
  }
  EventHub after(small_config(), 8, 2);
  EXPECT_GT(after.ingest(report("a.b", Kind::Log), kT0).publish.front().seq, last.seq);
}

TEST(EventHub, RejectsInvalidEventsWithANotice)
{
  EventHub hub(small_config(), 1, 1);
  struct Case
  {
    Event event;
    std::string reason_fragment;
  };
  std::vector<Case> cases;
  cases.push_back({report("Bad.Code"), "dotted lowercase"});
  cases.push_back({report("ok.code", Kind::Unspecified), "kind"});
  cases.push_back({report("ok.code", Kind::Log, Severity::Error, ""), "source.node"});
  Event big_payload = report("ok.code");
  big_payload.payload = std::string(70 * 1024, 'x');
  cases.push_back({big_payload, "payload"});
  Event many_attrs = report("ok.code");
  for (int i = 0; i < 40; ++i) {
    many_attrs.attrs["k" + std::to_string(i)] = std::int64_t{i};
  }
  cases.push_back({many_attrs, "attributes"});

  Timestamp t = kT0;
  for (auto & c : cases) {
    const auto r = hub.ingest(c.event, t += kSecond);
    EXPECT_FALSE(r.accepted);
    EXPECT_NE(r.reject_reason.find(c.reason_fragment), std::string::npos) << r.reject_reason;
    ASSERT_EQ(r.publish.size(), 1u);
    EXPECT_EQ(r.publish.front().code, "jarvis.ingest.rejected");
    EXPECT_EQ(r.publish.front().kind, Kind::System);
  }
  EXPECT_EQ(hub.stats().rejected, cases.size());
}

TEST(EventHub, TruncatesLongMessagesInsteadOfDropping)
{
  EventHub hub(small_config(), 1, 1);
  Event e = report("ok.code", Kind::Log);
  e.message = std::string(5000, 'm');
  const auto r = hub.ingest(e, kT0);
  ASSERT_TRUE(r.accepted);
  EXPECT_EQ(r.publish.front().message.size(), 1024u);
  EXPECT_EQ(std::get<bool>(r.publish.front().attrs.at("jarvis.truncated")), true);
}

TEST(EventHub, DropsDuplicateIds)
{
  EventHub hub(small_config(), 1, 1);
  Event e = report("ok.code", Kind::Log);
  e.id = "fixed-id";
  EXPECT_TRUE(hub.ingest(e, kT0).accepted);
  const auto again = hub.ingest(e, kT0 + 1);
  EXPECT_FALSE(again.accepted);
  EXPECT_TRUE(again.publish.empty());
  EXPECT_EQ(hub.stats().duplicates, 1u);
}

TEST(EventHub, RateLimitsFloodsAndSummarizes)
{
  EventHub hub(small_config(), 1, 1);
  int accepted = 0;
  for (int i = 0; i < 50; ++i) {  // 50 reports in 50 ms; burst is 5
    accepted += hub.ingest(report("ros.log.error", Kind::Log), kT0 + i * kMillisecond).accepted;
  }
  EXPECT_EQ(accepted, 5);
  EXPECT_EQ(hub.stats().suppressed, 45u);

  EXPECT_TRUE(hub.advance_to(kT0 + kSecond).empty());  // window not over yet
  const auto summaries = hub.advance_to(kT0 + 3 * kSecond);
  ASSERT_EQ(summaries.size(), 1u);
  EXPECT_EQ(summaries.front().code, "jarvis.ingest.suppressed");
  EXPECT_EQ(std::get<std::int64_t>(summaries.front().attrs.at("count")), 45);
  EXPECT_EQ(std::get<std::string>(summaries.front().attrs.at("code")), "ros.log.error");
}

TEST(EventHub, NeverRateLimitsClearsOrJarvisDecisions)
{
  EventHub hub(small_config(), 1, 1);
  for (int i = 0; i < 50; ++i) {
    EXPECT_TRUE(hub.ingest(report("ros.topic.stale", Kind::FaultClear), kT0 + i).accepted);
    EXPECT_TRUE(hub.ingest(report("ros.topic.stale", Kind::FaultState), kT0 + i).accepted);
  }
}

TEST(EventHub, StopsCausalLoops)
{
  EventHubConfig config = small_config();
  config.max_causal_depth = 3;
  config.rate_limit_per_sec = 0;  // disabled
  EventHub hub(config, 1, 1);
  std::string parent;
  for (int depth = 0; depth <= 3; ++depth) {
    Event e = report("rule.loop", Kind::RuleMatch);
    e.causation_id = parent;
    const auto r = hub.ingest(e, kT0 + depth);
    ASSERT_TRUE(r.accepted) << depth;
    parent = r.publish.front().id;
  }
  Event too_deep = report("rule.loop", Kind::RuleMatch);
  too_deep.causation_id = parent;
  const auto r = hub.ingest(too_deep, kT0 + 10);
  EXPECT_FALSE(r.accepted);
  ASSERT_EQ(r.publish.size(), 1u);
  EXPECT_EQ(r.publish.front().code, "jarvis.loop.detected");
  EXPECT_EQ(r.publish.front().severity, Severity::Error);
}

TEST(EventHub, QueriesFilterAndPage)
{
  EventHubConfig config = small_config();
  config.rate_limit_per_sec = 0;
  EventHub hub(config, 1, 1);
  for (int i = 0; i < 20; ++i) {
    Event e = report(
      i % 2 == 0 ? "ros.topic.stale" : "nav.goal.aborted", Kind::FaultRaise,
      i % 4 == 0 ? Severity::Error : Severity::Warn);
    e.source.component = i < 10 ? "lidar" : "nav";
    if (i == 7) {
      e.correlation_id = "inc-1";
    }
    hub.ingest(e, kT0 + i * kSecond);
  }

  EventQuery q;
  q.codes = {"ros.topic.*"};
  EXPECT_EQ(hub.query(q).events.size(), 10u);

  q = {};
  q.min_severity = Severity::Error;
  EXPECT_EQ(hub.query(q).events.size(), 5u);

  q = {};
  q.component = "nav";
  q.from = kT0 + 15 * kSecond;
  EXPECT_EQ(hub.query(q).events.size(), 5u);

  q = {};
  q.correlation_id = "inc-1";
  ASSERT_EQ(hub.query(q).events.size(), 1u);

  q = {};
  q.limit = 8;
  const auto page1 = hub.query(q);
  ASSERT_EQ(page1.events.size(), 8u);
  EXPECT_NE(page1.next_seq, 0u);
  q.after_seq = page1.next_seq;
  const auto page2 = hub.query(q);
  ASSERT_EQ(page2.events.size(), 8u);
  EXPECT_GT(page2.events.front().seq, page1.events.back().seq);
  q.after_seq = page2.next_seq;
  const auto page3 = hub.query(q);
  EXPECT_EQ(page3.events.size(), 4u);
  EXPECT_EQ(page3.next_seq, 0u);
  EXPECT_TRUE(page3.complete);
}

TEST(EventHub, EvictsLowSeverityFirstAndReportsIncompleteness)
{
  EventHubConfig config = small_config();
  config.rate_limit_per_sec = 0;
  config.buffer_capacity_bytes = 20 * 1024;
  EventHub hub(config, 1, 1);
  hub.ingest(report("important.fault", Kind::FaultRaise, Severity::Error), kT0);
  for (int i = 0; i < 200; ++i) {
    Event e = report("chatty.log", Kind::Log, Severity::Info);
    e.message = std::string(200, 'x');
    hub.ingest(e, kT0 + (i + 1) * kMillisecond);
  }
  EXPECT_GT(hub.stats().evicted, 0u);
  EXPECT_LE(hub.stats().buffered_bytes, config.buffer_capacity_bytes);

  EventQuery q;
  q.codes = {"important.*"};
  const auto r = hub.query(q);
  ASSERT_EQ(r.events.size(), 1u) << "WARN+ events survive while INFO is evicted";
  EXPECT_FALSE(r.complete) << "a query from the beginning may have missed evicted events";

  q.after_seq = hub.last_seq();
  EXPECT_TRUE(hub.query(q).complete);
}

TEST(EventHub, ShedsRoutineLogsWhenItIsBehind)
{
  EventHub hub(small_config(), 1);  // shed_after 1 s, below WARN (the defaults)
  const Duration behind = 2 * kSecond;
  EXPECT_EQ(
    hub.ingest(report("app.chatty", Kind::Log, Severity::Info), kT0, behind).reject_reason, "shed");
  EXPECT_EQ(
    hub.ingest(report("app.chatty", Kind::Log, Severity::Debug), kT0, behind).reject_reason,
    "shed");
  EXPECT_TRUE(hub.ingest(report("app.retry", Kind::Log, Severity::Warn), kT0, behind).accepted);
  EXPECT_TRUE(hub.ingest(report("app.mode", Kind::State, Severity::Info), kT0, behind).accepted);
  EXPECT_TRUE(hub.ingest(report("app.broke"), kT0, behind).accepted) << "faults are never shed";
  EXPECT_TRUE(
    hub.ingest(report("app.chatty", Kind::Log, Severity::Info), kT0, kSecond / 2).accepted)
    << "not behind enough";
  EXPECT_TRUE(hub.ingest(report("app.chatty2", Kind::Log, Severity::Info), kT0).accepted)
    << "queued unknown";
  EXPECT_EQ(hub.stats().shed, 2u);

  EXPECT_TRUE(hub.advance_to(kT0 + kSecond).empty()) << "summaries come once a window";
  const auto out = hub.advance_to(kT0 + 2 * kSecond);
  ASSERT_EQ(out.size(), 1u);
  EXPECT_EQ(out[0].code, "jarvis.ingest.shed");
  EXPECT_EQ(attr_int(out[0].attrs, "count"), 2);
  EXPECT_EQ(*attr_string(out[0].attrs, "below"), "WARN");
  EXPECT_TRUE(hub.advance_to(kT0 + 10 * kSecond).empty()) << "nothing shed since";

  EventHubConfig off = small_config();
  off.shed_after = 0;
  EventHub never(off, 1);
  EXPECT_TRUE(
    never.ingest(report("app.chatty", Kind::Log, Severity::Info), kT0, 60 * kSecond).accepted);
}
