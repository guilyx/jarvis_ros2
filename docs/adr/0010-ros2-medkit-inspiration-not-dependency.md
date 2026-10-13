# ADR-0010 — ros2_medkit: borrow ideas, stay interoperable, don't depend on it

- **Status**: Proposed
- **Date**: 2026-10-13

## Context
[ros2_medkit](https://github.com/selfpatch/ros2_medkit) (Apache-2.0, Jazzy/Humble/Lyrical) is an SOVD-inspired diagnostics layer for ROS 2. It has a fault manager (AUTOSAR DEM-style debounce, SQLite/Postgres persistence, correlation), bridges (diagnostics, rosout, action status), freeze frames and per-fault rosbag capture, a REST gateway with an entity tree, a web UI and an MCP server. Its scope is **diagnostics**. It does not do rule-based recovery orchestration, CEP over event sequences, or an agent that proposes gated actions, which are Jarvis's core.

## Decision
- **Borrow** proven ideas:
  - DEM counter debounce (`counter` mode)
  - root-cause/symptom correlation with muting and clear-with-root
  - occurrence counting on edges and `reporting_sources`
  - the **action status bridge** (`*/_action/status` → faults, e.g. Nav2 aborts)
  - per-fault freeze-frame topic sampling
  - an MCP server idea for later
- **Don't depend** on it for v1. Jarvis needs one event model across faults, rules, recoveries and insights, a deterministic core for replay, and ROS-native recording. Medkit's model is fault-centric, with UPPER_SNAKE codes, service-based reporting and a DB-backed store.
- **Stay interoperable**: a small optional `jarvis_medkit_bridge` (later) can turn medkit `FaultEvent`s into Jarvis events, so a team running medkit gets Jarvis rules, recovery and agent on top.

## Alternatives considered
- **Build on medkit** (use its fault manager and gateway; Jarvis = rules + recovery + agent): less to build, but it couples our core model and release cadence to an evolving project (its interfaces changed type hashes between minor versions).
- **Ignore it**: we'd reinvent solved details (debounce edge cases, action status bridging).

## Consequences
- ➕ We keep a single coherent model and a deterministic core, plus the best of medkit's field experience.
- ➖ Some overlap in fault management. Revisit after M2 if medkit's fault manager could serve as a drop-in backend.
