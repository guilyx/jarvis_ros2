# Architecture Decision Records

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-hexagonal-ros-agnostic-core.md) | Hexagonal architecture, ROS 2 as an adapter layer | Proposed |
| [0002](0002-language-split.md) | C++20 runtime, Python agent & gateway, TypeScript dashboard | Proposed |
| [0003](0003-ros-native-persistence.md) | ROS-native persistence: rosbag2 (MCAP) record, Parquet + DuckDB history | Proposed (revised) |
| [0004](0004-json-schema-contracts.md) | JSON Schema canonical contract; ROS msgs as binding | Proposed |
| [0005](0005-declarative-rule-and-plan-dsl.md) | Declarative YAML DSL for rules (CEP) and plans | Proposed |
| [0006](0006-single-actuation-gate.md) | Recovery Orchestrator is the single actuation gate | Proposed |
| [0007](0007-agent-provider-port-and-autonomy.md) | Agent provider-agnostic via LiteLLM, offline fallback, "advise" default | Proposed (revised) |
| [0008](0008-web-dashboard-and-gateway.md) | Standalone web dashboard behind a gateway (LAN-only) | Proposed |
| [0009](0009-target-ros-distro.md) | Target ROS 2 Jazzy first | Proposed |
| [0010](0010-ros2-medkit-inspiration-not-dependency.md) | ros2_medkit: borrow ideas, stay interoperable, don't depend | Proposed |
| [0011](0011-events-vs-diagnostics.md) | Own Event type; `diagnostic_msgs` in and out | Proposed |

Format: Context → Decision → Alternatives → Consequences. Status moves `Proposed → Accepted` once validated.
