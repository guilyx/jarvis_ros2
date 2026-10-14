# ADR-0007 — Agent: provider-agnostic via LiteLLM, offline fallback, "advise" by default

- **Status**: Accepted (2026-10-14)
- **Date**: 2026-10-13

## Context
We want an agent that reads windows of events and explains or acts. The harness must allow cloud LLMs and must not lock us to one vendor (review: "something like LiteLLM / OpenRouter"). Robots can be offline, LLM calls cost money, and LLM output must never directly move hardware.

## Decision
- `LLMClient` port with three adapters:
  - **LiteLLM** (default; Python SDK in-process). One interface for **OpenRouter**, direct providers and local models (Ollama/vLLM), chosen by a config string. A LiteLLM proxy on the LAN is optional for central keys and budgets.
  - **Heuristic analyst** for offline use, budget exhaustion or conclusive triage.
  - **Fake** for tests.
- Our own small tool loop. Only portable features are required (messages, tools, JSON). Provider extras (caching hints, reasoning effort, strict schemas) are used when available.
- The final answer goes through a `submit_insight` tool validated by pydantic, with one repair turn.
- Deterministic triage before any LLM call; budgets from LiteLLM cost tracking; trigger coalescing.
- Read-only tools plus a gated `propose_recovery`. The harness validates evidence ids and plans.
- Default autonomy: **advise**. `supervised` and `autonomous` are opt-in per deployment and per plan.
- Redaction before data leaves the robot. API keys only from secrets. `litellm` pinned with hashes (it had a PyPI supply-chain compromise in March 2026).

## Alternatives considered
- **Single vendor SDK**: the richest features, but vendor lock-in.
- **Raw OpenRouter only (OpenAI-compatible API)**: simple, but every model goes through one gateway, and local models need a second path. LiteLLM covers OpenRouter *and* the rest.
- **Agent frameworks (LangChain etc.)**: more abstraction than we need; harder to audit the gate.
- **No LLM, rules only**: predictable, but can't explain novel multi-signal incidents.

## Consequences
- ➕ Switch models and providers by config; cloud now, local later.
- ➖ Lowest-common-denominator features; quality varies by model, so the eval suite is how we pick a default model.
- ➖ One more supply-chain-sensitive dependency (mitigated by pinning and isolation in the agent process).
