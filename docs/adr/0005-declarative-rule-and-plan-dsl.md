# ADR-0005 — Declarative YAML DSL for rules (CEP) and recovery plans

- **Status**: Accepted (2026-10-14)
- **Date**: 2026-10-13

## Context
Recovery must be driven by rules over **series of events** (counts in windows, ordered sequences, absence), authored by robotics engineers, reviewable in PRs, safe to hot-reload, and replayable.

## Decision
- **Rules**: YAML with `when` (patterns: `event`, `count`, `sequence` with `absent` steps, `all`, `any`), `guard`, `then`, `policy`. CEP semantics: per-partition automata, skip-till-next-match, bounded state, injected clock.
- **Plans**: YAML ordered steps with timeout/retry, `wait_for` verification, `on_failure` escalation. A closed set of registered actions, plus pluginlib for custom ones.
- Both are validated by JSON Schema plus semantic lint, support dry-run/shadow mode, and can be backtested.

## Alternatives considered
- **BehaviorTree.CPP for plans**: powerful and familiar from Nav2, but heavier to author and review for linear recovery ladders, and it would put BT into the core. It stays available later as a `bt.run` action.
- **Embedded scripting (Python/Lua)**: maximum flexibility, but not statically checkable, hard to sandbox, and replay is non-deterministic.
- **CEL expressions for predicates**: elegant, but a heavy C++ dependency. It can be added later as a matcher operator if needed.

## Consequences
- ➕ Reviewable diffs, static validation, deterministic replay, safe hot reload.
- ➖ Expressiveness is bounded by design. Complex logic goes into custom actuators/bridges (plugins).
