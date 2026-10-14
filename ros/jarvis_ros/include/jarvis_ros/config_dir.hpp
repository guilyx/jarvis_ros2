// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Rules and plans live in directories of YAML files (one concern per file, reviewed like code).

#pragma once

#include <string>

namespace jarvis_ros
{

/// A short content hash of the directory's *.yaml / *.yml files (names and bytes, in name order):
/// the `revision` that ~/reload reports, so an operator can tell which configuration is live.
/// An empty or missing directory hashes to "empty".
std::string directory_revision(const std::string & dir);

}  // namespace jarvis_ros
