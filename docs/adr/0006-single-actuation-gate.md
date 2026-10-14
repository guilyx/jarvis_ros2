# ADR-0006 — The Recovery Orchestrator is the single actuation gate

- **Status**: Accepted (2026-10-14)
- **Date**: 2026-10-13

## Context
Three kinds of requesters can trigger recovery: rules, humans and the AI agent. Each could cause restart storms or conflicting actions, or act during unsafe conditions.

## Decision
- All requests go through `ExecuteRecovery` and the same ordered gates: **mode → plan/params → requester policy → preconditions → rate limit → locks → approval**.
- The agent has **no actuators**. It submits proposals under an autonomy level; `effective = min(deployment level, plan.agent_policy)`.
- Global kill switch (`DISABLED`), global `DRY_RUN`, per-rule shadow mode. No resume after a crash (`jarvis.recovery.interrupted`).
- Jarvis is **not** a safety function and never sits in the safety chain.

## Alternatives considered
- **Each requester actuates directly**: simpler, but no global view (locks, rates, audit) and the agent would hold dangerous capabilities.

## Consequences
- ➕ One audit trail, one place for safety policy, uniform UX (approvals, cancel, feedback).
- ➖ The orchestrator is a critical component and gets the deepest testing (gtest + backtest + e2e).
