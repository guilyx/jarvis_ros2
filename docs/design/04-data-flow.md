# 04 — Data Flow

> **TL;DR** — Producers publish to `/jarvis/ingest`. The recorder validates, persists and then
> republishes on `/jarvis/events`. Every decision Jarvis makes (fault transitions, rule matches,
> recovery steps, insights) loops back through the recorder as a new event, with `causation_id`
> pointing at its cause. The dashboard and the agent read the same log.

---

## 0. Big picture

```mermaid
flowchart LR
  P["Producers<br/>reporter libs · bridges"] -->|"/jarvis/ingest"| ING

  subgraph REC["/jarvis/recorder — single writer"]
    ING["IngestPipeline<br/>validate · normalize · shed · seq"]
    FM["FaultManager"]
    IT["IncidentTracker"]
    ING --> FM --> IT
    FM -->|"FAULT_STATE"| ING
  end

  ING -->|"append batch"| S[("EventStore")]
  ING -->|"/jarvis/events"| EV{{"canonical stream"}}
  FM -->|"/jarvis/faults"| FS{{"latched snapshot"}}

  EV --> RE["/jarvis/rule_engine"]
  FS --> RE
  RE -->|"RULE_MATCH"| P2["→ /jarvis/ingest"]
  RE -->|"ExecuteRecovery"| RO["/jarvis/recovery"]
  RO -->|"actuate"| ACT["Actuators → robot"]
  RO -->|"RECOVERY"| P3["→ /jarvis/ingest"]

  EV --> AG["/jarvis/agent"]
  AG -->|"INSIGHT"| P4["→ /jarvis/ingest"]
  AG -->|"proposal: ExecuteRecovery, requester=AGENT"| RO

  EV --> GW["/jarvis/gateway"]
  S --> GW
  FS --> GW
  GW <-->|"REST · WS"| UI["Dashboard"]
  GW -->|"ack · clear · trigger · approve"| REC
  GW -->|"trigger · approve · cancel · mode"| RO
```

## 1. Ingest

```mermaid
sequenceDiagram
  autonumber
  participant App as App (reporter lib)
  participant In as /jarvis/ingest
  participant P as IngestPipeline
  participant W as Store writer thread
  participant DB as SQLite (WAL)
  participant Out as /jarvis/events

  App->>In: Event (id=UUIDv7, seq=0)
  In->>P: callback (core input group)
  P->>P: validate schema, sizes, code format
  P->>P: dedupe by id, rate-limit per (source, code)
  P->>P: fill recv_stamp, defaults from catalog, seq = last + 1
  P->>W: enqueue (bounded, severity-aware)
  W->>DB: group commit (≤ 20 ms or 256 events)
  DB-->>W: committed up to seq N
  W-->>P: ack N
  P->>Out: publish events up to seq N (persist-then-publish)
```

Details:
- **Rejections** (bad schema, oversize, depth > 16) produce a rate-limited `jarvis.ingest.rejected` SYSTEM event that carries the reason.
- **Suppression**: after the token bucket empties, the pipeline counts instead of storing, then emits one `jarvis.ingest.suppressed` event (`attrs.code`, `attrs.count`, `attrs.window_s`).
- **Load shedding**: when the writer queue is above the high-water mark, DEBUG and then INFO are dropped and counted. ≥ ERROR always enqueues; if needed the producer callback blocks briefly.

## 2. Fault confirmation, incident & black box

```mermaid
sequenceDiagram
  autonumber
  participant B as Bridge or app
  participant P as IngestPipeline
  participant F as FaultManager
  participant I as IncidentTracker
  participant K as rosbag2 recorder (snapshot mode)
  participant FS as /jarvis/faults

  B->>P: FAULT_RAISE ros.topic.stale (component=lidar_front)
  P->>F: on_report
  F->>F: no open episode, create PENDING (debounce persist 1 s)
  F->>P: emit FAULT_STATE PENDING
  Note over F: timer fires at +1 s, no CLEAR received
  F->>F: confirm, state ACTIVE, capture freeze frame
  F->>I: fault confirmed (severity ERROR)
  I->>I: open incident (or join one on the same component within 120 s)
  F->>P: emit FAULT_STATE ACTIVE (correlation_id = incident)
  F->>K: catalog capture=true, call ~/snapshot
  K-->>F: bag path
  F->>P: emit SYSTEM jarvis.blackbox.captured (attrs.path)
  F->>FS: publish snapshot (TRANSIENT_LOCAL)
```

## 3. Rule match → recovery → verify → escalate

```mermaid
sequenceDiagram
  autonumber
  participant EV as /jarvis/events
  participant RE as RuleEngine
  participant RO as Orchestrator
  participant A as Actuator (lifecycle)
  participant N as lidar driver (managed node)
  participant IN as /jarvis/ingest

  EV->>RE: FAULT_STATE ACTIVE ros.topic.stale (seq 1042)
  RE->>RE: rule lidar-stale-restart matches, guard ok, cooldown ok
  RE->>IN: RULE_MATCH (causation = event 1042)
  RE->>RO: ExecuteRecovery goal (plan restart-lifecycle-node, requester RULE)
  RO->>RO: gates: mode, params, requester, preconditions, rate, locks
  RO->>IN: RECOVERY STARTED
  RO->>A: step restart, lifecycle.restart
  A->>N: ChangeState deactivate, cleanup, configure, activate
  N-->>A: ok
  A-->>RO: step result ok
  RO->>IN: RECOVERY STEP_SUCCEEDED restart
  Note over RO: step verify, wait_for fault CLEARED (timeout 15 s)
  EV->>RO: FAULT_STATE CLEARED ros.topic.stale
  RO->>IN: RECOVERY SUCCEEDED
  RO-->>RE: result SUCCEEDED
```

When verification times out, the escalation ladder runs:

```mermaid
flowchart LR
  L1["L1 · targeted fix<br/>restart node"] -->|"failed"| L2["L2 · broader fix<br/>restart subsystem"]
  L2 -->|"failed"| L3["L3 · safe state<br/>safe-stop plan"]
  L3 -->|"always"| L4["L4 · human<br/>incident ESCALATED + notify"]
```

## 4. Agent analysis

```mermaid
sequenceDiagram
  autonumber
  participant EV as /jarvis/events
  participant T as TriggerPolicy
  participant W as WindowManager
  participant G as Triage (deterministic)
  participant L as Analyst (LLM + tools)
  participant Q as Query tools (read-only)
  participant H as Harness checks
  participant IN as /jarvis/ingest

  EV->>T: FAULT_STATE ACTIVE opens incident inc-42
  T->>T: coalesce triggers for inc-42 (30 s), check budget
  T->>W: build window(inc-42, plus or minus 5 min context)
  W->>G: 214 events
  G->>G: collapse repeats, rank, stats, redact attrs
  G->>L: compact context (cached prefix + window)
  loop investigation (max 12 tool calls)
    L->>Q: query_events / get_fault_history / get_runbook / ...
    Q-->>L: results
  end
  L->>H: submit_insight (strict schema) and maybe propose_recovery
  H->>H: validate evidence ids, plan exists, autonomy policy
  H->>IN: INSIGHT event (payload = Insight, correlation = inc-42)
```

## 5. Agent proposal with approval (supervised autonomy)

```mermaid
sequenceDiagram
  autonumber
  participant AG as Agent
  participant RO as Orchestrator
  participant IN as /jarvis/ingest
  participant GW as Gateway
  participant OP as Operator (dashboard)

  AG->>RO: ExecuteRecovery (requester AGENT, rationale, insight id)
  RO->>RO: gates pass, plan agent_policy = supervised, approval required
  RO->>IN: RECOVERY PENDING_APPROVAL (TTL 5 min)
  RO-->>AG: feedback state PENDING_APPROVAL
  IN-->>GW: event via /jarvis/events
  GW-->>OP: WS push, approval card with rationale and evidence
  OP->>GW: POST /recoveries/exec-77:approve
  GW->>RO: DecideApproval (approve, user)
  RO->>IN: OPERATOR approve + RECOVERY APPROVED, STARTED
  RO-->>AG: result (SUCCEEDED or FAILED)
```

If nobody decides before the TTL, the execution ends as `EXPIRED`. Nothing runs.

## 6. Dashboard live stream with gap-free resume

```mermaid
sequenceDiagram
  autonumber
  participant UI as Dashboard
  participant GW as Gateway
  participant DB as SQLite (read-only)
  participant EV as /jarvis/events

  UI->>GW: WS subscribe (since_seq 9000, filter min_severity WARN)
  GW->>GW: start buffering live events from EV
  GW->>DB: SELECT where seq > 9000 (paged)
  DB-->>GW: rows up to seq 9120
  GW-->>UI: backfill events up to 9120
  GW-->>UI: buffered live events with seq > 9120 (dedupe by seq)
  GW-->>UI: backfill_done (seq 9131)
  EV-->>GW: live events
  GW-->>UI: live events and fault snapshots
  Note over UI: on reconnect, resubscribe with the last seq seen
```

## 7. Offline replay & backtesting

```mermaid
flowchart LR
  DB[("incident.db<br/>or events.jsonl")] --> RR["Replay reader<br/>(ordered by seq)"]
  RR --> CORE["jarvis_core<br/>FaultManager · RuleEngine · Orchestrator"]
  CK["FakeClock<br/>driven by recv_stamp"] --> CORE
  CORE --> FX["Effects log"]
  FX --> DIFF["Report: rules fired · plans requested<br/>diff vs. what really happened"]
```

- `jarvis backtest --rules ./rules --plans ./plans --events incident.db`
- Actuators are replaced by a **simulated actuator** that returns the outcomes recorded in the log (or `success`, configurable).
- The same harness is used for **regression tests**: every incident fixed in the field becomes a test case.

## 8. Delivery guarantees

| Path | Guarantee | Mechanism |
|------|-----------|-----------|
| Producer → recorder | At-least-once while the recorder is up | RELIABLE QoS, KEEP_LAST 1000, de-duplication by `id` |
| Recorder → store | Exactly-once per `id` | UNIQUE(`id`), transaction per batch |
| Store → `/jarvis/events` | In-order, persisted first | Publish after commit, monotonic `seq` |
| `/jarvis/events` → consumers | Gap-detectable | Consumers track `seq` and backfill via `query_events` |
| Orchestrator actions | At-most-once per step attempt | No automatic resume after a crash (`jarvis.recovery.interrupted`) |
| Gateway WS | Gap-free on reconnect | `since_seq` resume protocol |
