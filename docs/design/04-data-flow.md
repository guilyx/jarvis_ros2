# 04 — Data Flow

> **TL;DR** — Producers publish to `/jarvis/ingest`. The event hub validates, sequences and republishes on
> `/jarvis/events`. Every decision Jarvis makes (fault transitions, rule matches, recovery steps, insights)
> loops back through the hub as a new event, with `causation_id` pointing at its cause. rosbag2 records the
> stream, and the lake turns recordings into queryable Parquet.

---

## 0. Big picture

```mermaid
flowchart LR
  P["Producers<br/>reporter libs · bridges"] -->|"/jarvis/ingest"| HUB["/jarvis/event_hub<br/>validate · dedupe · shed · seq"]
  HUB -->|"/jarvis/events"| EV{{"canonical stream"}}

  EV --> FM["/jarvis/fault_manager"]
  FM -->|"FAULT_STATE"| P1["→ /jarvis/ingest"]
  FM -->|"/jarvis/faults (latched)"| FS{{"active faults"}}

  EV --> RE["/jarvis/rule_engine"]
  FS --> RE
  RE -->|"RULE_MATCH"| P2["→ /jarvis/ingest"]
  RE -->|"ExecuteRecovery"| RO["/jarvis/recovery"]
  RO -->|"actuate"| ACT["Actuators → robot"]
  RO -->|"RECOVERY"| P3["→ /jarvis/ingest"]
  RO -->|"/jarvis/executions (latched)"| XS{{"executions"}}

  EV --> AG["/jarvis/agent"]
  AG -->|"INSIGHT"| P4["→ /jarvis/ingest"]
  AG -->|"proposal (requester AGENT)"| RO

  EV --> REC["/jarvis/recorder<br/>rosbag2"]
  FS --> REC
  XS --> REC
  REC --> LAKE["jarvis_lake<br/>Parquet · DuckDB"]

  EV --> GW["/jarvis/gateway"]
  FS --> GW
  LAKE --> GW
  GW <-->|"REST · WS"| UI["Dashboard"]
  GW -->|"ack · clear"| FM
  GW -->|"trigger · approve · cancel · mode"| RO
```

## 1. Ingest

```mermaid
sequenceDiagram
  autonumber
  participant App as App (reporter lib)
  participant In as /jarvis/ingest
  participant H as EventHub
  participant RB as Ring buffer
  participant Out as /jarvis/events
  participant R as rosbag2 recorder

  App->>App: client-side debounce and rate limit
  App->>In: Event (id = UUIDv7, seq = 0)
  In->>H: callback (core input group)
  H->>H: validate schema, sizes, code format
  H->>H: dedupe by id, rate-limit per (source, code)
  H->>H: fill recv_stamp and catalog defaults, assign seq
  H->>RB: append (severity-aware eviction)
  H->>Out: publish
  Out-->>R: recorded to the current MCAP split
```

- **Rejections** (bad schema, oversize, causal depth > 16) produce a rate-limited `jarvis.ingest.rejected` event with the reason.
- **Suppression**: once the token bucket is empty, the hub counts instead of forwarding, then emits one `jarvis.ingest.suppressed` event (`attrs.code`, `attrs.count`, `attrs.window_s`).
- **Load shedding**: above the high-water mark, DEBUG and then INFO are dropped and counted. ≥ ERROR is always forwarded.

## 2. Fault confirmation, incident & black box

```mermaid
sequenceDiagram
  autonumber
  participant B as Topic watchdog (bridge)
  participant H as EventHub
  participant F as FaultManager
  participant K as /jarvis/blackbox (snapshot mode)
  participant FS as /jarvis/faults
  participant ST as State snapshot

  B->>H: FAULT_RAISE ros.topic.stale (component lidar)
  H-->>F: via /jarvis/events
  F->>F: open episode PENDING (debounce persist 1 s)
  F->>H: FAULT_STATE PENDING
  Note over F: timer fires at +1 s with no CLEAR
  F->>F: confirm ACTIVE, sample freeze-frame topics, open incident
  F->>ST: persist open episodes and incident (atomic)
  F->>H: FAULT_STATE ACTIVE (correlation_id = incident)
  F->>FS: publish snapshot (TRANSIENT_LOCAL) and /diagnostics
  F->>K: catalog blackbox = true, call ~/snapshot
  K-->>F: bag path
  F->>H: SYSTEM jarvis.blackbox.captured (attrs.path)
```

Correlation: if `safety.estop.engaged` is ACTIVE and `drive.*` faults confirm within 1 s, they are marked `muted_by` the e-stop episode. They're still recorded, but hidden from the active list, and cleared together with it.

## 3. Rule match → recovery → verify → escalate

```mermaid
sequenceDiagram
  autonumber
  participant EV as /jarvis/events
  participant RE as RuleEngine
  participant RO as Orchestrator
  participant A as Actuator (lifecycle)
  participant N as scan_relay (managed node)
  participant IN as /jarvis/ingest

  EV->>RE: FAULT_STATE ACTIVE ros.topic.stale
  RE->>RE: rule scan-stale-restart matches, guard ok, cooldown ok
  RE->>IN: RULE_MATCH (causation = fault event)
  RE->>RO: ExecuteRecovery (plan restart-lifecycle-node, requester RULE)
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
  L1["L1 · targeted fix<br/>restart node"] -->|"failed"| L2["L2 · broader fix<br/>Nav2 lifecycle reset"]
  L2 -->|"failed"| L3["L3 · safe state<br/>cancel goals + stop"]
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
  participant L as Analyst (LiteLLM + tools)
  participant Q as Query tools (hub, lake)
  participant H as Harness checks
  participant IN as /jarvis/ingest

  EV->>T: FAULT_STATE ACTIVE opens incident inc-42
  T->>T: coalesce triggers for inc-42 (30 s), check budget
  T->>W: build window (inc-42 plus or minus 5 min)
  W->>G: 214 events
  G->>G: collapse repeats, rank, stats, redact attrs
  G->>L: compact context (stable prefix + window)
  loop investigation (max 12 tool calls)
    L->>Q: query_events / get_fault_history / get_runbook / ...
    Q-->>L: results
  end
  L->>H: submit_insight (schema-validated) and maybe propose_recovery
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
  IN-->>GW: event via /jarvis/events and /jarvis/executions
  GW-->>OP: WS push, approval card with rationale and evidence
  OP->>GW: POST /recoveries/exec-77:approve
  GW->>RO: DecideApproval (approve, user)
  RO->>IN: OPERATOR approve + RECOVERY APPROVED, STARTED
  RO-->>AG: result (SUCCEEDED or FAILED)
```

If nobody decides before the TTL, the execution ends as `EXPIRED`. Nothing runs.

## 6. Recording → lake

```mermaid
sequenceDiagram
  autonumber
  participant R as /jarvis/recorder (rosbag2)
  participant FS as Bag directory
  participant L as jarvis_lake
  participant PQ as Parquet + DuckDB

  R->>FS: write split N (MCAP, zstd chunks)
  Note over R: every 5 min or 256 MiB
  R->>FS: close split N, open split N+1
  R-->>L: events/write_split (closed file path)
  L->>FS: read split N (indexed, schemas embedded)
  L->>L: decode Event, Fault, Execution (no ROS needed)
  L->>PQ: write events/date=.../part-N.parquet
  L->>PQ: refresh DuckDB views
  L->>FS: mark split N converted (eligible for retention)
```

On startup the lake also scans for splits it hasn't converted yet, including unfinalized ones left by a crash. Those are recovered first.

## 7. Dashboard live stream with gap-free resume

```mermaid
sequenceDiagram
  autonumber
  participant UI as Dashboard
  participant GW as Gateway
  participant HB as event_hub query_events
  participant LK as Lake (DuckDB)
  participant EV as /jarvis/events

  UI->>GW: WS subscribe (since_seq S, filter min_severity WARN)
  GW->>GW: start buffering live events from EV
  GW->>HB: query_events after_seq S
  alt ring buffer covers S
    HB-->>GW: events up to seq N (complete = true)
  else S is older than the ring buffer
    HB-->>GW: complete = false
    GW->>LK: SELECT where seq > S (older part)
    LK-->>GW: rows
    GW->>HB: query_events for the rest
  end
  GW-->>UI: backfill, then buffered live events with seq > N (dedupe by seq)
  GW-->>UI: backfill_done
  EV-->>GW: live events
  GW-->>UI: live events, fault and execution snapshots
```

## 8. Offline replay & backtesting

```mermaid
flowchart LR
  BAG[("Recorded MCAP<br/>/jarvis/events")] --> RR["Replay reader<br/>(ordered by seq)"]
  RR --> CORE["jarvis_core<br/>FaultManager · RuleEngine · Orchestrator"]
  CK["FakeClock<br/>driven by recv_stamp"] --> CORE
  CORE --> FX["Effects log"]
  FX --> DIFF["Report: rules fired · plans requested<br/>diff vs. what really happened"]
```

- `jarvis backtest --rules ./rules --plans ./plans --bag ./bags/2026-10-13/`
- Actuators are replaced by a **simulated actuator** that returns the outcomes recorded in the bag (or `success`, configurable).
- Every incident fixed in the field becomes a regression test: keep its bag, assert the decisions.

## 9. Delivery guarantees

| Path | Guarantee | Mechanism |
|------|-----------|-----------|
| Producer → hub | At-least-once while the hub is up | RELIABLE QoS, KEEP_LAST 1000, de-dupe by `id` |
| Hub → `/jarvis/events` | In-order, sequenced | Single publisher, monotonic `seq` |
| `/jarvis/events` → consumers | Gap-detectable | Consumers track `seq` and backfill via `query_events` / lake |
| `/jarvis/events` → bag | Recorded unless the process dies (last chunk ≤ ~1 s at risk) | Recorder in the same container, RELIABLE, MCAP recovery |
| Open faults / executions | Survive restarts and power cuts | fsynced atomic state snapshots |
| Orchestrator actions | At-most-once per step attempt | No automatic resume after a crash (`jarvis.recovery.interrupted`) |
| Gateway WS | Gap-free on reconnect | `since_seq` resume across ring buffer and lake |
