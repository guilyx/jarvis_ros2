# ADR-0008 — Standalone web dashboard behind a gateway

- **Status**: Proposed
- **Date**: 2026-10-13

## Context
Operators and engineers need a live, linkable, role-aware UI, without installing ROS tooling on their machines.

## Decision
- `jarvis_gateway` (FastAPI): REST + WS (gap-free resume), auth/roles, notifier, Prometheus. It reads SQLite read-only and commands through ROS services/actions.
- Dashboard: React + TypeScript + Vite, TanStack (Query/Table/Router), Tailwind + shadcn/ui, ECharts. Shipped as a static bundle served by the gateway.

## Alternatives considered
- **Foxglove extension panels**: great for engineers already in Foxglove, but weaker for operators (auth, roles, approvals) and tied to that tool. A panel can come later on top of the same API.
- **rosbridge + browser direct to ROS**: no auth model and it exposes the whole graph. Rejected.
- **RViz/rqt plugins**: engineer-only, desktop-only.

## Consequences
- ➕ Works from any browser; clean API for future integrations (fleet, Foxglove, mobile).
- ➖ One more process on the robot (small; it can also run off-board).
