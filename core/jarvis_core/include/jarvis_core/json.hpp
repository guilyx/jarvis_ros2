// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// JSON codec matching schemas/*.schema.json (ADR-0004).
// Timestamps are ISO 8601 strings with nanoseconds; seq is a decimal string (uint64 does not fit
// in a JavaScript number).

#pragma once

#include <nlohmann/json.hpp>

#include "jarvis_core/types.hpp"

namespace jarvis_core
{

void to_json(nlohmann::json & j, const Source & s);
void from_json(const nlohmann::json & j, Source & s);

void to_json(nlohmann::json & j, const Event & e);
/// Throws nlohmann::json::exception or std::invalid_argument on malformed input.
void from_json(const nlohmann::json & j, Event & e);

nlohmann::json attrs_to_json(const Attributes & attrs);
Attributes attrs_from_json(const nlohmann::json & j);

}  // namespace jarvis_core
