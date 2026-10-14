# ADR-0004 — JSON Schema as the canonical contract; ROS messages are a binding

- **Status**: Accepted (2026-10-14)
- **Date**: 2026-10-13

## Context
The same data crosses C++, Python, TypeScript, ROS messages, MCAP/Parquet, REST and YAML configs. Drift between these is a classic source of bugs.

## Decision
- `schemas/*.schema.json` is canonical for Event, Fault, Incident, Execution, Insight, Rule, Plan and FaultCatalog.
- Generated: Python pydantic models and TypeScript types.
- Hand-written but **contract-tested**: C++ structs + JSON codec; `.msg` files (field-parity test).
- C++ ↔ ROS conversion through `rclcpp::TypeAdapter` (REP-2007).
- Rules, plans and catalogs are validated against the same schemas in CI (`jarvis lint`), at load time and in the gateway.

## Alternatives considered
- **ROS `.msg` as source of truth**: couples every consumer (dashboard, agent) to ROS tooling.
- **Protobuf**: strong codegen and evolution rules, but it adds a heavy dependency to the core, and JSON is needed anyway for REST, DuckDB/Parquet JSON columns and human-edited YAML.

## Consequences
- ➕ One contract, many bindings; configs and APIs validated identically everywhere.
- ➖ C++ structs and `.msg` are maintained by hand (small, stable surface; guarded by tests).
