// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// The Jarvis nodes as a BehaviorTree.CPP plugin library, for a robot's own trees: load
// `jarvis_bt_nodes` (e.g. in Nav2's bt_navigator `plugin_lib_names`).

#include "jarvis_bt/nodes.hpp"

BT_REGISTER_NODES(factory) { jarvis_bt::register_nodes(factory); }
