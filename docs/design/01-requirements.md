# 01 — Requirements & Scope

> **TL;DR** — Jarvis records every event and fault on a robot, reacts automatically using
> declarative rules, explains what happened with an AI agent, and shows it all live on a
> dashboard. The core is ROS-agnostic; ROS 2 is the transport. It is **not** a safety system.

---

## 1. Goals

| ID | Goal |
|----|------|
| G1 | **Record** every relevant event and fault with enough context to explain it later (who, what, when, why). |
| G2 | **Manage faults** with a proper lifecycle: debounce, confirm, heal, latch, acknowledge, clear. |
| G3 | **Recover automatically** from rules built from events, sequences of events, counts in time windows and missing events. |
| G4 | **Explain and suggest** using an agent that reads windows of events and produces insights and recovery proposals. |
| G5 | **Visualize** live state, history, incidents, recoveries and insights in a web dashboard. |
| G6 | **Stay ROS-agnostic**: the core builds and runs without ROS. ROS 2 is one adapter among possible others (in-memory, Zenoh, MQTT, …). |
| G7 | **Ship with commercial-grade ROS 2 practice**: lifecycle nodes, components, explicit QoS, typed params, tests, CI. |

## 2. Non-goals (v1)

- **Not a functional-safety system.** Jarvis is not part of the safety chain (no ISO 13849 / IEC 61508 claims). E-stops, safety PLCs and speed-and-separation monitoring stay independent. Jarvis must respect their state and never override them.
- Not a general log aggregator (not a replacement for Loki/ELK). It records *events*, not every log line.
- Not a time-series database for high-rate signals (use rosbag2/MCAP; Jarvis links to snapshots).
- No fleet backend in v1. The schema is fleet-ready (`robot` id on every event), but v1 targets **one robot**.
- No real hardware in v1: the robot is **simulated** (Gazebo + Nav2 TurtleBot 4). Real-robot bring-up comes later.
- No visual rule editor in v1 (read-only YAML view plus validation API; editor in v2).

## 3. Personas

| Persona | Needs |
|---------|-------|
| **Robot software engineer** | Report faults from code in one line, write rules and recovery plans, test them offline. |
| **Operator / field support** | See what's wrong *now*, acknowledge, approve or trigger recoveries, read a plain-language explanation. |
| **Reliability engineer** | Analyze incidents, tune rules, backtest rules on recorded incidents, track MTTR and auto-recovery rate. |

## 4. Functional requirements

### Recording
- **FR-R1** Ingest events from: reporter libraries (C++/Python), `/diagnostics`, `/rosout`, lifecycle transitions, action goal status (e.g. Nav2 aborts), topic/node watchdogs.
- **FR-R2** Validate and normalize every event (schema, size limits, code format) and assign a monotonic sequence number.
- **FR-R3** Publish every canonical event on the ROS graph and record it with rosbag2 (MCAP). State that must survive restarts (open faults, running executions) is kept in fsynced snapshots.
- **FR-R4** Flood control: per-(source, code) rate limiting with summary events, and severity-aware load shedding (never drop ≥ `ERROR`).
- **FR-R5** Query by time range, sequence cursor, kind, code glob, severity, component and correlation id: recent events from the event hub, history from Parquet via DuckDB.
- **FR-R6** Black-box capture: on configured faults, trigger a rosbag2 snapshot and link it to the fault.
- **FR-R7** Retention for bags, Parquet and black-box snapshots by age and size. Bags are deleted only after conversion.

### Faults
- **FR-F1** Fault catalog (YAML) defines known codes: severity, debounce, heal, latch, capture, runbook.
- **FR-F2** Fault lifecycle state machine: `PENDING → ACTIVE → (LATCHED) → CLEARED`, with occurrence counting and freeze-frame.
- **FR-F3** Operator acknowledge and clear, both audited.
- **FR-F4** Latched snapshot of active faults for late joiners, also exported on `/diagnostics` for standard tools.
- **FR-F6** Correlation: root-cause/symptom muting and clear-with-root (catalog-driven).
- **FR-F5** Incidents group correlated events (fault, rule matches, recoveries, insights) into one timeline.

### Rules & recovery
- **FR-X1** Declarative rules with patterns: single event, count-in-window, ordered sequence, all-of (any order), any-of, absence (“A not followed by B within T”).
- **FR-X2** Guards on current state (active faults, latest `STATE` values).
- **FR-X3** Actions: request recovery, emit derived event, open/escalate incident, notify, ask the agent.
- **FR-X4** Policies: cooldown, max fires per period, partitioning (per component, per node, …), dry-run (shadow) mode.
- **FR-X5** Recovery plans: ordered steps with timeout, retry/backoff, verification (`wait_for`), escalation chain.
- **FR-X6** One actuation gate for every requester (rule, operator, agent): mode/kill switch, param validation, requester policy, preconditions, rate limits, resource locks, approvals.
- **FR-X7** Hot reload of rules and plans with validation (invalid config is rejected; the previous config stays live).
- **FR-X8** Deterministic replay: run the decision layer over a recorded event log (backtesting).

### Agent
- **FR-A1** Trigger analysis on: incident opened, recovery failed/escalated, rule `ask_agent`, periodic digest, on-demand question.
- **FR-A2** Build bounded windows of events with context (active faults, state, plan catalog, runbooks).
- **FR-A3** Investigate with **read-only** tools; produce a structured `Insight` with evidence that links to real event ids.
- **FR-A4** Propose recoveries only from the plan catalog; proposals go through the same actuation gate, under an autonomy policy.
- **FR-A5** Cloud LLMs through LiteLLM (OpenRouter, direct providers, local models), selected by config. Work offline (heuristic analyst) when no LLM is reachable. Cost and rate budgets.

### Dashboard & API
- **FR-D1** REST + WebSocket API (OpenAPI documented). Live stream with gap-free resume.
- **FR-D2** Views: Overview, Live events, Faults, Incidents (timeline), Recovery (incl. approvals), Rules, Agent, System health.
- **FR-D3** Operator actions: ack/clear fault, trigger/cancel/approve/reject recovery, toggle rules, switch recovery mode.
- **FR-D4** LAN-only access with simple roles (viewer / operator / admin). Every write is audited as an `OPERATOR` event.

## 5. Non-functional requirements

| ID | Area | Target (v1) |
|----|------|-------------|
| NFR-1 | Ingest throughput | ≥ 10 000 events/s sustained on an x86 dev machine (simulation); bursts of 20 000 buffered. ARM64 targets are set at real-robot bring-up. |
| NFR-2 | Ingest → publish latency | p99 ≤ 50 ms (group commit ≤ 20 ms) |
| NFR-3 | Rule evaluation | p99 ≤ 5 ms per event with 200 rules |
| NFR-4 | Match → first actuation | p99 ≤ 100 ms (excluding approval wait) |
| NFR-5 | Footprint | Runtime container ≤ 100 MB RSS; ≤ 5 % of one core at 500 events/s |
| NFR-6 | Durability | Open faults/executions survive crashes and power loss (fsynced snapshots). Recorded events: at most the last unflushed MCAP chunk (≤ ~1 s) lost on a hard crash. |
| NFR-7 | Storage | Bounded: bags 10 GiB / 7 days, Parquet 5 GiB / 180 days, black box 2 GiB (defaults) |
| NFR-8 | Dashboard freshness | Live event visible in UI ≤ 1 s after ingest |
| NFR-9 | Agent latency | Incident insight p90 ≤ 60 s when online |
| NFR-10 | Portability | Core builds with plain CMake/pip on Linux x86_64/ARM64 without ROS |
| NFR-11 | Middleware | Works on Fast DDS, Cyclone DDS and rmw_zenoh (no vendor-specific code) |
| NFR-12 | Sim time | Everything follows the node clock (`use_sim_time` supported) |
| NFR-13 | Security | LAN-only dashboard with roles; LLM API keys from secrets only; data redaction before LLM calls; hash-pinned Python deps; SROS2 compatible |
| NFR-14 | Operability | Jarvis reports its own health on `/diagnostics` and Prometheus metrics |

### Measured (5.1)

`jarvis-bench` times the core (no ROS); `jarvis_load` puts the stack under load end to end
(`ros2 run jarvis_ros jarvis_load`, nightly in `.github/workflows/soak.yml`). On an x86 dev VM
(4 vCPU Xeon, 16 GB), ROS 2 Jazzy, Fast DDS 2.14, the bringup as shipped (agent off):

| Target | Measured | |
|--------|----------|---|
| NFR-1 throughput | Core: 110–150 k events/s through the event hub. Stack: 1 000/s plus a 20 000-event burst, nothing lost (the burst's routine logs shed once the hub is 1 s behind); 5 000/s, nothing lost, a third shed; 10 000/s, most lost | bursts ✔, 10 000/s ✘ (5.2) |
| NFR-2 latency | p99 12–21 ms at 500/s, 20 ms at 1 000/s with a burst (publish → `/jarvis/events` → a reader); about 1 s when shedding | ✔ below ~4 000/s |
| NFR-3 rules | p99 37 µs per event with 200 rules (event, count and sequence patterns) | ✔ |
| NFR-5 footprint | Container: 80–90 MB RSS with nothing buffered, growing to the buffers' budgets (event buffer 128 MB, black box 256 MB by default); 45–48 % of one core at 500/s with the recorder and the black box | ✘ (5.2) |

What it took:
- **Bounded incidents.** A fault that flaps opened a new incident each time, and every resolved one
  stayed in memory for `incident_auto_close` (24 h). The fault manager handled 4 000 events/s
  this way and grew without bound. Now at most `max_resolved_incidents` (500) are kept: the
  oldest close early. It handles 94 000 events/s.
- **An isolated container.** In `component_container_mt`, the executor threads share one wait set
  of every component's entities, and each event wakes them all. `component_container_isolated`
  gives each component its own thread. At 1 000 events/s that halves the CPU (96 % → 47 %) and the
  p99 (49 → 13 ms), and a slow component can't stall the others.
- **A burst-deep ingest queue.** `/jarvis/ingest` holds 20 000 events (`ingest_queue_depth`), as do
  the stream's readers that may lag: the recorder and the gateway. Before, a burst lost all but the
  first 1 000. Fast DDS also caps any history at 5 000 samples, whatever the depth asked for: the
  bringup applies `config/fastdds.xml` to its processes to lift that cap. The stream's writer stays
  1 000 deep: a deep reliable writer slows down for its slowest reader, and at 5 000 events/s that
  lost half the stream.
- **Load shedding.** When an event waited more than `shed_after_s` (1 s) in the ingest queue, the
  hub drops it if it is a LOG below `shed_below` (WARN), and reports the count as a
  `jarvis.ingest.shed` event every 10 s. Faults, states, warnings and Jarvis's own events are never
  shed. The wait is the middleware's receive time on the hub's host, so a producer's clock can't
  trigger it. Without shedding, 5 000 events/s lost a quarter of everything, faults included.

The rest is the cost of one ROS message per event. In the profiles, the executor and Fast DDS
spend more than Jarvis does: wait-set rebuilds, waking readers, locks. Past about twice the hub's
rate, even taking a message to shed it costs too much: the ingest queue fills and most events are
lost. 5.2 batches the stream (one message per group of events, the "group commit" of NFR-2) to
reach NFR-1 and NFR-5.

## 6. Glossary

| Term | Meaning |
|------|---------|
| **Event** | Immutable record that something happened. The only thing stored in the log. |
| **Report** | Event emitted by a producer (`LOG`, `STATE`, `FAULT_RAISE`, `FAULT_CLEAR`). |
| **Fault** | Condition tracked by the Fault Manager, keyed by `(robot, code, component)`. |
| **Fault episode** | One lifecycle of a fault, from first raise to cleared. Each episode has a `fault_id`. |
| **Freeze frame** | Snapshot of attributes captured when a fault is confirmed. |
| **Incident** | Group of causally related events (fault + rule matches + recoveries + insights). |
| **Rule** | Pattern over events plus guard, actions and policy. |
| **Plan** | Named recovery procedure: ordered steps with verification and escalation. |
| **Actuator** | Adapter that performs a step action (lifecycle transition, service call, …). |
| **Gate** | Admission check before a plan runs (mode, preconditions, locks, approvals, …). |
| **Insight** | Structured agent output: summary, hypotheses with evidence, recommendations. |
| **Proposal** | Agent-suggested recovery, which must still pass the gates. |
| **Window** | Bounded set of events the agent analyzes (sliding, incident-scoped or digest). |
