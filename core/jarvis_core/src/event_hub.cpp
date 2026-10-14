// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_core/event_hub.hpp"

#include <algorithm>
#include <utility>

namespace jarvis_core
{

namespace
{

bool is_rate_limited_kind(Kind kind)
{
  return kind == Kind::Log || kind == Kind::State || kind == Kind::FaultRaise;
}

bool matches(const Event & e, const EventQuery & q)
{
  if (e.seq <= q.after_seq) {
    return false;
  }
  if (q.from != 0 && e.recv_stamp < q.from) {
    return false;
  }
  if (q.to != 0 && e.recv_stamp > q.to) {
    return false;
  }
  if (e.severity < q.min_severity) {
    return false;
  }
  if (!q.kinds.empty() && std::find(q.kinds.begin(), q.kinds.end(), e.kind) == q.kinds.end()) {
    return false;
  }
  if (
    !q.codes.empty() &&
    std::none_of(q.codes.begin(), q.codes.end(), [&e](const std::string & pattern) {
      return glob_match(pattern, e.code);
    })) {
    return false;
  }
  if (!q.component.empty() && e.source.subject() != q.component) {
    return false;
  }
  if (!q.correlation_id.empty() && e.correlation_id != q.correlation_id) {
    return false;
  }
  return true;
}

}  // namespace

EventHub::EventHub(EventHubConfig config, std::uint16_t boot_count, std::uint64_t id_seed)
: config_(std::move(config)), boot_count_(boot_count), ids_(id_seed)
{
}

std::uint64_t EventHub::make_seq(std::uint16_t boot_count, std::uint64_t counter)
{
  return (static_cast<std::uint64_t>(boot_count) << 48) | (counter & 0xFFFFFFFFFFFFULL);
}

std::string EventHub::validate(Event & e) const
{
  if (!is_valid(e.kind)) {
    return "kind is missing or unknown";
  }
  if (!is_valid(e.severity)) {
    return "severity is unknown";
  }
  if (e.code.size() > config_.max_code_length || !is_valid_code(e.code)) {
    return "code '" + e.code + "' is not a dotted lowercase code";
  }
  if (e.source.node.empty()) {
    return "source.node is empty";
  }
  if (e.id.size() > config_.max_id_length) {
    return "id is longer than " + std::to_string(config_.max_id_length) + " characters";
  }
  if (e.attrs.size() > config_.max_attrs) {
    return "more than " + std::to_string(config_.max_attrs) + " attributes";
  }
  for (const auto & [key, value] : e.attrs) {
    if (key.empty() || key.size() > config_.max_attr_key_length) {
      return "attribute key '" + key + "' is empty or too long";
    }
  }
  if (e.tags.size() > config_.max_tags) {
    return "more than " + std::to_string(config_.max_tags) + " tags";
  }
  if (e.payload.size() > config_.max_payload_bytes) {
    return "payload is larger than " + std::to_string(config_.max_payload_bytes) + " bytes";
  }
  if (e.message.size() > config_.max_message_bytes) {
    // Oversized messages are truncated, not rejected: losing the tail beats losing the event.
    e.message.resize(config_.max_message_bytes);
    e.attrs["jarvis.truncated"] = true;
  }
  return {};
}

bool EventHub::rate_limited(const Event & e, Timestamp now)
{
  if (!is_rate_limited_kind(e.kind) || config_.rate_limit_per_sec <= 0.0) {
    return false;
  }
  const std::string key = e.source.node + '\x1f' + e.code + '\x1f' + e.source.subject();
  auto [it, inserted] = buckets_.try_emplace(key);
  Bucket & b = it->second;
  if (inserted) {
    b.tokens = config_.rate_limit_burst;
    b.last = now;
    b.node = e.source.node;
    b.code = e.code;
    b.component = e.source.subject();
  }
  const double elapsed_s = static_cast<double>(std::max<Duration>(0, now - b.last)) / 1e9;
  b.tokens = std::min(config_.rate_limit_burst, b.tokens + elapsed_s * config_.rate_limit_per_sec);
  b.last = now;
  if (b.tokens >= 1.0) {
    b.tokens -= 1.0;
    return false;
  }
  if (b.suppressed == 0) {
    b.suppressed_since = now;
  }
  ++b.suppressed;
  ++stats_.suppressed;
  return true;
}

Event EventHub::make_system_event(
  std::string code, Severity severity, std::string message, Attributes attrs, Timestamp now,
  std::string causation_id)
{
  Event e;
  e.id = ids_.uuid7(now);
  e.stamp = now;
  e.source.robot = config_.robot_id;
  e.source.node = config_.node_name;
  e.kind = Kind::System;
  e.code = std::move(code);
  e.severity = severity;
  e.message = std::move(message);
  e.attrs = std::move(attrs);
  e.causation_id = std::move(causation_id);
  return e;
}

void EventHub::remember_id(const std::string & id, int depth)
{
  depth_by_id_[id] = depth;
  id_order_.push_back(id);
  while (id_order_.size() > config_.dedupe_capacity) {
    depth_by_id_.erase(id_order_.front());
    id_order_.pop_front();
  }
}

std::size_t EventHub::approx_size(const Event & e)
{
  std::size_t size = sizeof(Event) + e.id.size() + e.code.size() + e.message.size() +
                     e.payload.size() + e.payload_schema.size() + e.correlation_id.size() +
                     e.causation_id.size() + e.source.robot.size() + e.source.node.size() +
                     e.source.component.size() + e.source.host.size();
  for (const auto & [key, value] : e.attrs) {
    size += key.size() + sizeof(AttrValue) + 32;
    if (const auto * s = std::get_if<std::string>(&value)) {
      size += s->size();
    }
  }
  for (const auto & tag : e.tags) {
    size += tag.size() + sizeof(std::string);
  }
  return size;
}

void EventHub::store(const Event & e)
{
  const std::size_t size = approx_size(e);
  (e.severity >= Severity::Warn ? high_ : low_).push_back(e);
  buffered_bytes_ += size;
  while (buffered_bytes_ > config_.buffer_capacity_bytes && (!low_.empty() || !high_.empty())) {
    auto & victim_queue = !low_.empty() ? low_ : high_;
    const Event & victim = victim_queue.front();
    buffered_bytes_ -= std::min(buffered_bytes_, approx_size(victim));
    evicted_max_seq_ = std::max(evicted_max_seq_, victim.seq);
    evicted_max_recv_ = std::max(evicted_max_recv_, victim.recv_stamp);
    ++stats_.evicted;
    victim_queue.pop_front();
  }
  stats_.buffered_events = low_.size() + high_.size();
  stats_.buffered_bytes = buffered_bytes_;
}

void EventHub::sequence_and_store(Event & e, Timestamp now)
{
  if (e.id.empty()) {
    e.id = ids_.uuid7(now);
  }
  if (e.source.robot.empty()) {
    e.source.robot = config_.robot_id;
  }
  e.recv_stamp = now;
  if (e.stamp == 0) {
    e.stamp = now;
  }
  ++counter_;
  e.seq = make_seq(boot_count_, counter_);
  store(e);
}

IngestResult EventHub::ingest(Event event, Timestamp now)
{
  IngestResult result;

  if (!event.id.empty() && depth_by_id_.count(event.id) != 0) {
    ++stats_.duplicates;
    result.reject_reason = "duplicate id";
    return result;
  }

  if (std::string reason = validate(event); !reason.empty()) {
    ++stats_.rejected;
    result.reject_reason = reason;
    // Rejection notices are rate-limited per producer so a broken node can't flood the stream.
    Event probe;
    probe.kind = Kind::Log;
    probe.code = "jarvis.ingest.rejected";
    probe.source.node = event.source.node.empty() ? std::string("unknown") : event.source.node;
    if (!rate_limited(probe, now)) {
      Event notice = make_system_event(
        "jarvis.ingest.rejected", Severity::Warn, "Rejected an event: " + reason,
        {{"producer", probe.source.node}, {"code", event.code}, {"reason", reason}}, now);
      sequence_and_store(notice, now);
      result.publish.push_back(std::move(notice));
    }
    return result;
  }

  int depth = 0;
  if (!event.causation_id.empty()) {
    if (const auto it = depth_by_id_.find(event.causation_id); it != depth_by_id_.end()) {
      depth = it->second + 1;
    }
  }
  if (depth > config_.max_causal_depth) {
    ++stats_.rejected;
    result.reject_reason = "causal depth exceeded";
    Event notice = make_system_event(
      "jarvis.loop.detected", Severity::Error,
      "Causal chain deeper than " + std::to_string(config_.max_causal_depth) + " events; dropped " +
        event.code + " from " + event.source.node,
      {{"producer", event.source.node}, {"code", event.code}, {"depth", std::int64_t{depth}}}, now,
      event.causation_id);
    sequence_and_store(notice, now);
    result.publish.push_back(std::move(notice));
    return result;
  }

  if (rate_limited(event, now)) {
    result.reject_reason = "rate limited";
    return result;
  }

  sequence_and_store(event, now);
  remember_id(event.id, depth);
  ++stats_.accepted;
  result.accepted = true;
  result.publish.push_back(std::move(event));
  return result;
}

std::vector<Event> EventHub::advance_to(Timestamp now)
{
  std::vector<Event> out;
  for (auto it = buckets_.begin(); it != buckets_.end();) {
    Bucket & b = it->second;
    if (b.suppressed > 0 && now - b.suppressed_since >= config_.suppression_window) {
      const double window_s = static_cast<double>(now - b.suppressed_since) / 1e9;
      Event summary = make_system_event(
        "jarvis.ingest.suppressed", Severity::Warn,
        "Suppressed " + std::to_string(b.suppressed) + " x " + b.code + " from " + b.node,
        {{"producer", b.node},
         {"code", b.code},
         {"component", b.component},
         {"count", static_cast<std::int64_t>(b.suppressed)},
         {"window_s", window_s}},
        now);
      sequence_and_store(summary, now);
      out.push_back(std::move(summary));
      b.suppressed = 0;
    }
    // Forget idle buckets that have refilled, so the map doesn't grow without bound.
    const bool idle =
      b.suppressed == 0 && now - b.last > static_cast<Duration>(
                                            config_.rate_limit_burst /
                                            std::max(config_.rate_limit_per_sec, 1e-9) * 1e9) +
                                            kSecond;
    it = idle ? buckets_.erase(it) : std::next(it);
  }
  return out;
}

QueryResult EventHub::query(const EventQuery & q) const
{
  QueryResult result;
  const std::size_t limit = std::clamp<std::size_t>(q.limit, 1, 1000);
  auto lo = low_.begin();
  auto hi = high_.begin();
  // Both queues are in seq order; merge them.
  while (lo != low_.end() || hi != high_.end()) {
    const Event * next = nullptr;
    if (hi == high_.end() || (lo != low_.end() && lo->seq < hi->seq)) {
      next = &*lo++;
    } else {
      next = &*hi++;
    }
    if (!matches(*next, q)) {
      continue;
    }
    if (result.events.size() == limit) {
      result.next_seq = result.events.back().seq;
      break;
    }
    result.events.push_back(*next);
  }
  result.complete =
    !(evicted_max_seq_ > q.after_seq && (q.from == 0 || q.from <= evicted_max_recv_));
  return result;
}

}  // namespace jarvis_core
