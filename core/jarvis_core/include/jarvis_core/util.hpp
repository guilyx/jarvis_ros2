// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Small, dependency-free helpers: ids, globs, code validation, durations and timestamps.

#pragma once

#include <cstdint>
#include <optional>
#include <random>
#include <string>
#include <string_view>

#include "jarvis_core/types.hpp"

namespace jarvis_core
{

/// Generates RFC 9562 UUIDv7 strings. Time-ordered, so ids sort roughly by creation time.
/// Seed 0 draws a seed from std::random_device; a fixed seed makes ids reproducible in tests.
class IdGenerator
{
public:
  explicit IdGenerator(std::uint64_t seed = 0);
  std::string uuid7(Timestamp now);

private:
  std::mt19937_64 rng_;
};

/// Shell-style glob: `*` matches any run of characters (dots included), `?` matches one character.
bool glob_match(std::string_view pattern, std::string_view text);

/// Event codes are dotted, lowercase: `^[a-z0-9_]+(\.[a-z0-9_]+)*$`.
bool is_valid_code(std::string_view code);

/// The inverse of parse_duration, in the largest exact unit: `1h`, `90s`, `250ms`, `0s`.
std::string format_duration(Duration d);

/// Parses "250ms", "1.5s", "2m", "1h", "500us", "10ns". A bare number means seconds.
std::optional<Duration> parse_duration(std::string_view text);

/// "2026-10-13T09:14:03.120000000Z" (UTC, always nine fractional digits).
std::string format_iso8601(Timestamp t);
/// Accepts UTC timestamps with 0 to 9 fractional digits and a trailing Z.
std::optional<Timestamp> parse_iso8601(std::string_view text);

}  // namespace jarvis_core
