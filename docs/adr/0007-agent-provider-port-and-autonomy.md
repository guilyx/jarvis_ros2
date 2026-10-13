# ADR-0007 — Agent: provider port, Claude by default, offline fallback, "advise" by default

- **Status**: Proposed
- **Date**: 2026-10-13

## Context
We want an agent that reads windows of events and explains or acts. Robots can be offline, LLM calls cost money, and LLM output must never directly move hardware.

## Decision
- `LLMClient` port with a **Claude adapter** (official `anthropic` SDK, default model `claude-opus-5-5`, adaptive thinking, prompt caching, strict `submit_insight` output), a **heuristic analyst** (offline/budget fallback) and a **fake** (tests).
- Deterministic triage before any LLM call; budgets; trigger coalescing.
- Read-only tools only, plus a gated `propose_recovery`. The harness validates evidence ids and plans.
- Default autonomy: **advise** (humans click "run"). `supervised` and `autonomous` are opt-in per deployment and per plan.
- Redaction of configured attributes before data leaves the robot.
- Quality tracked by an eval corpus built from recorded incidents (replay adapter).

## Alternatives considered
- **No LLM, rules only**: predictable, but can't explain novel multi-signal incidents.
- **Agent with broad tools (shell, arbitrary services)**: more capable, but unacceptable risk on a robot.
- **Local/on-device LLM only**: private, but much weaker today. The port allows adding one later.

## Consequences
- ➕ Useful explanations with bounded risk and cost; works offline in degraded form.
- ➖ Needs connectivity for full quality; eval maintenance is ongoing work.
