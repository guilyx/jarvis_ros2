# ADR-0002 — Languages: C++20 runtime, Python agent & gateway, TypeScript dashboard

- **Status**: Accepted (2026-10-14)
- **Date**: 2026-10-13

## Context
The runtime (recorder, rules, recovery, bridges) runs on the robot next to perception and control. The agent needs the LLM ecosystem. The dashboard is a web app.

## Decision
| Part | Language | Reason |
|------|----------|--------|
| Runtime (`jarvis_core`, `jarvis_ros`, bridges) | **C++20** | Composable components, intra-process zero-copy, type adapters (C++ only), small footprint, the norm in commercial ROS stacks |
| Agent, gateway, lake (MCAP → Parquet), CLI, lint/backtest wrappers | **Python 3.12** | LLM SDKs, FastAPI, fast iteration; off the hot path |
| Dashboard | **TypeScript** (React) | Standard for web UIs; types generated from OpenAPI |

## Alternatives considered
- **All Python**: fastest to a first demo. But no composition or type adapters, GIL contention, and a bigger footprint on the robot. A reasonable choice for a prototype; not what I'd ship.
- **Rust core**: excellent safety, but ROS 2 Rust support (rclrs) is less mature and it's rarer in robotics teams.

## Consequences
- ➕ Production-grade runtime; Python stays where it helps most.
- ➖ Two toolchains in the core repo. Mitigated by one devcontainer and one CI pipeline.
