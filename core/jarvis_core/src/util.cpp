// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_core/util.hpp"

#include <array>
#include <cctype>
#include <charconv>
#include <cmath>
#include <cstdio>

namespace jarvis_core
{

IdGenerator::IdGenerator(std::uint64_t seed)
: rng_(
    seed == 0 ? std::random_device{}() ^ (static_cast<std::uint64_t>(std::random_device{}()) << 32)
              : seed)
{
}

std::string IdGenerator::uuid7(Timestamp now)
{
  const std::uint64_t unix_ms = now > 0 ? static_cast<std::uint64_t>(now / kMillisecond) : 0;
  const std::uint64_t rand_a = rng_() & 0x0FFFULL;
  const std::uint64_t rand_b = rng_() & 0x3FFFFFFFFFFFFFFFULL;

  std::array<std::uint8_t, 16> b{};
  for (int i = 0; i < 6; ++i) {
    b[i] = static_cast<std::uint8_t>((unix_ms >> (40 - 8 * i)) & 0xFF);
  }
  b[6] = static_cast<std::uint8_t>(0x70 | ((rand_a >> 8) & 0x0F));
  b[7] = static_cast<std::uint8_t>(rand_a & 0xFF);
  b[8] = static_cast<std::uint8_t>(0x80 | ((rand_b >> 56) & 0x3F));
  for (int i = 9; i < 16; ++i) {
    b[i] = static_cast<std::uint8_t>((rand_b >> (8 * (15 - i))) & 0xFF);
  }

  static constexpr char kHex[] = "0123456789abcdef";
  std::string out;
  out.reserve(36);
  for (int i = 0; i < 16; ++i) {
    if (i == 4 || i == 6 || i == 8 || i == 10) {
      out.push_back('-');
    }
    out.push_back(kHex[b[i] >> 4]);
    out.push_back(kHex[b[i] & 0x0F]);
  }
  return out;
}

bool glob_match(std::string_view pattern, std::string_view text)
{
  std::size_t p = 0;
  std::size_t t = 0;
  std::size_t star = std::string_view::npos;
  std::size_t mark = 0;
  while (t < text.size()) {
    if (p < pattern.size() && (pattern[p] == '?' || pattern[p] == text[t])) {
      ++p;
      ++t;
    } else if (p < pattern.size() && pattern[p] == '*') {
      star = p++;
      mark = t;
    } else if (star != std::string_view::npos) {
      p = star + 1;
      t = ++mark;
    } else {
      return false;
    }
  }
  while (p < pattern.size() && pattern[p] == '*') {
    ++p;
  }
  return p == pattern.size();
}

bool is_valid_code(std::string_view code)
{
  if (code.empty()) {
    return false;
  }
  bool segment_empty = true;
  for (const char c : code) {
    if (c == '.') {
      if (segment_empty) {
        return false;
      }
      segment_empty = true;
    } else if ((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_') {
      segment_empty = false;
    } else {
      return false;
    }
  }
  return !segment_empty;
}

std::optional<Duration> parse_duration(std::string_view text)
{
  while (!text.empty() && std::isspace(static_cast<unsigned char>(text.front()))) {
    text.remove_prefix(1);
  }
  while (!text.empty() && std::isspace(static_cast<unsigned char>(text.back()))) {
    text.remove_suffix(1);
  }
  if (text.empty()) {
    return std::nullopt;
  }
  std::size_t split = 0;
  while (split < text.size() &&
         (std::isdigit(static_cast<unsigned char>(text[split])) || text[split] == '.')) {
    ++split;
  }
  if (split == 0) {
    return std::nullopt;
  }
  double value = 0.0;
  const auto number = std::string(text.substr(0, split));
  try {
    std::size_t used = 0;
    value = std::stod(number, &used);
    if (used != number.size()) {
      return std::nullopt;
    }
  } catch (...) {
    return std::nullopt;
  }
  const std::string_view unit = text.substr(split);
  double scale = 0.0;
  if (unit.empty() || unit == "s") {
    scale = static_cast<double>(kSecond);
  } else if (unit == "ms") {
    scale = static_cast<double>(kMillisecond);
  } else if (unit == "us") {
    scale = 1e3;
  } else if (unit == "ns") {
    scale = 1.0;
  } else if (unit == "m" || unit == "min") {
    scale = static_cast<double>(kMinute);
  } else if (unit == "h") {
    scale = static_cast<double>(kHour);
  } else {
    return std::nullopt;
  }
  return static_cast<Duration>(std::llround(value * scale));
}

namespace
{

// Howard Hinnant's civil-date algorithms (public domain).
std::int64_t days_from_civil(std::int64_t y, unsigned m, unsigned d)
{
  y -= m <= 2;
  const std::int64_t era = (y >= 0 ? y : y - 399) / 400;
  const auto yoe = static_cast<unsigned>(y - era * 400);
  const unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
  const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  return era * 146097 + static_cast<std::int64_t>(doe) - 719468;
}

void civil_from_days(std::int64_t z, std::int64_t & y, unsigned & m, unsigned & d)
{
  z += 719468;
  const std::int64_t era = (z >= 0 ? z : z - 146096) / 146097;
  const auto doe = static_cast<unsigned>(z - era * 146097);
  const unsigned yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
  y = static_cast<std::int64_t>(yoe) + era * 400;
  const unsigned doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
  const unsigned mp = (5 * doy + 2) / 153;
  d = doy - (153 * mp + 2) / 5 + 1;
  m = mp + (mp < 10 ? 3 : -9);
  y += m <= 2;
}

bool parse_uint(std::string_view s, std::int64_t & out)
{
  if (s.empty()) {
    return false;
  }
  const auto res = std::from_chars(s.data(), s.data() + s.size(), out);
  return res.ec == std::errc{} && res.ptr == s.data() + s.size();
}

}  // namespace

std::string format_iso8601(Timestamp t)
{
  std::int64_t secs = t / kSecond;
  std::int64_t nanos = t % kSecond;
  if (nanos < 0) {
    nanos += kSecond;
    secs -= 1;
  }
  std::int64_t days = secs / 86400;
  std::int64_t rem = secs % 86400;
  if (rem < 0) {
    rem += 86400;
    days -= 1;
  }
  std::int64_t y = 0;
  unsigned m = 0;
  unsigned d = 0;
  civil_from_days(days, y, m, d);
  char buf[96];
  std::snprintf(
    buf, sizeof(buf), "%04lld-%02u-%02uT%02lld:%02lld:%02lld.%09lldZ", static_cast<long long>(y), m,
    d, static_cast<long long>(rem / 3600), static_cast<long long>((rem / 60) % 60),
    static_cast<long long>(rem % 60), static_cast<long long>(nanos));
  return buf;
}

std::optional<Timestamp> parse_iso8601(std::string_view s)
{
  // YYYY-MM-DDTHH:MM:SS[.fffffffff]Z
  if (
    s.size() < 20 || s.back() != 'Z' || s[4] != '-' || s[7] != '-' ||
    (s[10] != 'T' && s[10] != 't') || s[13] != ':' || s[16] != ':') {
    return std::nullopt;
  }
  std::int64_t y = 0;
  std::int64_t mo = 0;
  std::int64_t d = 0;
  std::int64_t h = 0;
  std::int64_t mi = 0;
  std::int64_t se = 0;
  if (
    !parse_uint(s.substr(0, 4), y) || !parse_uint(s.substr(5, 2), mo) ||
    !parse_uint(s.substr(8, 2), d) || !parse_uint(s.substr(11, 2), h) ||
    !parse_uint(s.substr(14, 2), mi) || !parse_uint(s.substr(17, 2), se)) {
    return std::nullopt;
  }
  if (mo < 1 || mo > 12 || d < 1 || d > 31 || h > 23 || mi > 59 || se > 60) {
    return std::nullopt;
  }
  std::int64_t nanos = 0;
  std::string_view rest = s.substr(19, s.size() - 20);
  if (!rest.empty()) {
    if (rest.front() != '.' || rest.size() < 2 || rest.size() > 10) {
      return std::nullopt;
    }
    std::string digits(rest.substr(1));
    digits.resize(9, '0');
    if (!parse_uint(digits, nanos)) {
      return std::nullopt;
    }
  }
  const std::int64_t days = days_from_civil(y, static_cast<unsigned>(mo), static_cast<unsigned>(d));
  return ((days * 86400 + h * 3600 + mi * 60 + se) * kSecond) + nanos;
}

}  // namespace jarvis_core
