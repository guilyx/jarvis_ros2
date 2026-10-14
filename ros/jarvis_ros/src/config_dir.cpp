// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_ros/config_dir.hpp"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <vector>

namespace jarvis_ros
{

std::string directory_revision(const std::string & dir)
{
  std::vector<std::filesystem::path> files;
  std::error_code ec;
  for (const auto & entry : std::filesystem::directory_iterator(dir, ec)) {
    const auto ext = entry.path().extension();
    if (entry.is_regular_file() && (ext == ".yaml" || ext == ".yml")) {
      files.push_back(entry.path());
    }
  }
  if (files.empty()) {
    return "empty";
  }
  std::sort(files.begin(), files.end());
  std::uint64_t h = 1469598103934665603ULL;
  auto mix = [&h](unsigned char c) {
    h ^= c;
    h *= 1099511628211ULL;
  };
  for (const auto & f : files) {
    for (char c : f.filename().string()) {
      mix(static_cast<unsigned char>(c));
    }
    mix(0);
    std::ifstream in(f, std::ios::binary);
    for (auto it = std::istreambuf_iterator<char>(in); it != std::istreambuf_iterator<char>();
         ++it) {
      mix(static_cast<unsigned char>(*it));
    }
    mix(0);
  }
  char buf[17];
  std::snprintf(buf, sizeof(buf), "%016llx", static_cast<unsigned long long>(h));
  return std::string(buf, 12);
}

}  // namespace jarvis_ros
