# ADR-0001 — Hexagonal architecture, ROS 2 as an adapter layer

- **Status**: Accepted (2026-10-14)
- **Date**: 2026-10-13

## Context
Jarvis must be "ROS 2 oriented but ROS 2 agnostic". We want to test decision logic without a ROS graph, replay recorded incidents, and possibly add other transports later (Zenoh, MQTT, gRPC).

## Decision
- Ports & adapters. `core/` (C++ `jarvis_core`; Python `jarvis_agent.core`, `jarvis_lake`) has **zero** ROS dependencies.
- Core services follow **functional core / imperative shell**: deterministic `input → effects`. Adapters do I/O, timers and actuation.
- ROS 2 lives in `ros/` packages: interfaces, type adapters, lifecycle components, actuators and bridges.
- CI enforces the boundary (grep for `rclcpp`/`rclpy` in `core/`, plus a ROS-free build job).

## Alternatives considered
- **ROS-native everywhere** (logic inside nodes): fastest start, but untestable without ROS, no replay, and locked to ROS.
- **Separate standalone service + ROS bridge**: agnostic, but adds an IPC hop and a second process on the hot path.

## Consequences
- ➕ Unit tests run in milliseconds. Backtesting and simulation come for free. Transports are swappable.
- ➕ The ROS layer is thin and idiomatic (type adapters remove conversion boilerplate).
- ➖ Some duplication of type definitions (C++ structs vs. `.msg`), mitigated by parity tests (ADR-0004).
