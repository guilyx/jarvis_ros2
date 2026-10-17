// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// The event hub is the single publisher of the canonical stream. It validates, de-duplicates,
// rate-limits and sequences events, and keeps a bounded buffer of recent events for queries.
// Pure: no I/O, no clocks. The caller passes `now` (docs/design/04-data-flow.md §1).

#pragma once

#include <cstddef>
#include <cstdint>
#include <deque>
#include <string>
#include <unordered_map>
#include <vector>

#include "jarvis_core/types.hpp"
#include "jarvis_core/util.hpp"

namespace jarvis_core
{

struct EventHubConfig
{
  std::string robot_id{"robot"};
  std::string node_name{"/jarvis/event_hub"};

  std::size_t max_id_length{64};
  std::size_t max_code_length{128};
  std::size_t max_message_bytes{1024};
  std::size_t max_attrs{32};
  std::size_t max_attr_key_length{64};
  std::size_t max_tags{16};
  std::size_t max_payload_bytes{64 * 1024};

  /// Token bucket per (node, code). Applies to LOG, STATE and FAULT_RAISE only: clears and
  /// Jarvis's own decisions are never rate-limited, so fault state can't be corrupted by a flood.
  double rate_limit_per_sec{50.0};
  double rate_limit_burst{100.0};
  /// How long suppressed events are counted before one `jarvis.ingest.suppressed` summary.
  Duration suppression_window{10 * kSecond};

  /// Load shedding: when an event waited longer than this in the ingest queue (the hub is behind),
  /// a LOG below `shed_below` is dropped, so that faults, states and warnings get through and the
  /// hub catches up. 0 disables it. Counted, and summarized as `jarvis.ingest.shed` every
  /// `suppression_window`.
  Duration shed_after{1 * kSecond};
  Severity shed_below{Severity::Warn};

  std::size_t dedupe_capacity{8192};
  int max_causal_depth{16};

  /// Approximate memory budget of the recent-events buffer. DEBUG/INFO are evicted first.
  std::size_t buffer_capacity_bytes{128ull << 20};
};

struct IngestResult
{
  bool accepted{false};
  std::string reject_reason;
  /// Everything to publish on the canonical stream, already sequenced, in order: the accepted
  /// event and/or Jarvis's own SYSTEM events (rejections, loop detection).
  std::vector<Event> publish;
};

struct EventQuery
{
  std::uint64_t after_seq{0};
  Timestamp from{0};
  Timestamp to{0};  ///< 0 = open-ended
  std::vector<Kind> kinds;
  std::vector<std::string> codes;  ///< globs
  Severity min_severity{Severity::Debug};
  std::string component;
  std::string correlation_id;
  std::size_t limit{1000};
};

struct QueryResult
{
  std::vector<Event> events;
  /// Pass as `after_seq` to continue; 0 when there is nothing more.
  std::uint64_t next_seq{0};
  /// False when evicted events could have matched (the caller should also ask the lake).
  bool complete{true};
};

struct HubStats
{
  std::uint64_t accepted{0};
  std::uint64_t rejected{0};
  std::uint64_t duplicates{0};
  std::uint64_t suppressed{0};
  std::uint64_t shed{0};
  std::uint64_t evicted{0};
  std::size_t buffered_events{0};
  std::size_t buffered_bytes{0};
};

class EventHub
{
public:
  /// `boot_count` goes into the high 16 bits of every seq so seq stays monotonic across restarts.
  EventHub(EventHubConfig config, std::uint16_t boot_count, std::uint64_t id_seed = 0);

  /// `queued`: how long the event waited before the hub got it (0: not known), for load shedding.
  IngestResult ingest(Event event, Timestamp now, Duration queued = 0);

  /// Emits suppression summaries that are due. Call periodically (e.g. 1 Hz) and before queries.
  std::vector<Event> advance_to(Timestamp now);

  QueryResult query(const EventQuery & query) const;

  const HubStats & stats() const { return stats_; }
  std::uint64_t last_seq() const { return make_seq(boot_count_, counter_); }
  static std::uint64_t make_seq(std::uint16_t boot_count, std::uint64_t counter);

private:
  struct Bucket
  {
    double tokens{0.0};
    Timestamp last{0};
    std::uint64_t suppressed{0};
    Timestamp suppressed_since{0};
    std::string node;
    std::string code;
    std::string component;
  };

  std::string validate(Event & event) const;
  bool rate_limited(const Event & event, Timestamp now);
  Event make_system_event(
    std::string code, Severity severity, std::string message, Attributes attrs, Timestamp now,
    std::string causation_id = {});
  void sequence_and_store(Event & event, Timestamp now);
  void remember_id(const std::string & id, int depth);
  void store(const Event & event);
  static std::size_t approx_size(const Event & event);

  EventHubConfig config_;
  std::uint16_t boot_count_;
  std::uint64_t counter_{0};
  IdGenerator ids_;
  HubStats stats_;

  std::unordered_map<std::string, Bucket> buckets_;

  std::unordered_map<std::string, int> depth_by_id_;
  std::deque<std::string> id_order_;

  std::deque<Event> low_;   // DEBUG and INFO
  std::deque<Event> high_;  // WARN and above
  std::size_t buffered_bytes_{0};
  std::uint64_t shed_pending_{0};
  Timestamp shed_since_{0};
  std::uint64_t evicted_max_seq_{0};
  Timestamp evicted_max_recv_{0};
};

}  // namespace jarvis_core
