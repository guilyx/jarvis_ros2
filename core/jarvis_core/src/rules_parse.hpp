// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Parsers for the pieces rules and plans share: tests, matchers, guards and templates.
// Internal to jarvis_core (yaml-cpp stays a private dependency). They throw detail::YamlError.

#pragma once

#include <yaml-cpp/yaml.h>

#include <string>

#include "jarvis_core/rules.hpp"

namespace jarvis_core::detail
{

/// `field` tells how to read scalars: severities and kinds are validated, `tags` takes a list.
Test parse_test(const YAML::Node & node, const std::string & path, const std::string & field);
Matcher parse_matcher(const YAML::Node & node, const std::string & path);
Guard parse_guard(const YAML::Node & node, const std::string & path);
Template parse_template(const YAML::Node & node, const std::string & path);

}  // namespace jarvis_core::detail
