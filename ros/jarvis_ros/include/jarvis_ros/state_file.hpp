// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Small state that must survive restarts and power cuts (ADR-0003): written atomically
// (temp file, fsync, rename, fsync directory) so a reader never sees a half-written file.

#pragma once

#include <nlohmann/json.hpp>
#include <optional>
#include <string>

namespace jarvis_ros
{

class StateFile
{
public:
  /// An empty directory disables persistence (every call becomes a no-op).
  StateFile(std::string directory, std::string name);

  bool enabled() const { return !directory_.empty(); }
  const std::string & path() const { return path_; }

  /// Returns nullopt when the file is missing. Throws std::runtime_error when it is unreadable.
  std::optional<nlohmann::json> load() const;
  /// Throws std::runtime_error with the reason on failure.
  void save(const nlohmann::json & state) const;

private:
  std::string directory_;
  std::string path_;
};

}  // namespace jarvis_ros
