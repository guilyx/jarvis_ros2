# 07 — Gateway & Dashboard

> **TL;DR**
> - **Gateway** (`jarvis_gateway`, FastAPI): REST + WebSocket over the ROS graph and the lake.
>   OpenAPI-documented, role-based auth, delivers notifications, serves the dashboard bundle.
>   No Node.js runs on the robot.
> - **Dashboard** (React + TypeScript + Vite): 8 views. The core view is the **Incident timeline**:
>   faults, rule matches, recovery steps and agent insights in swimlanes, with causal links.

---

## 1. Gateway

### Responsibilities
- Read path: live from `/jarvis/events`, `/jarvis/faults`, `/jarvis/executions`; recent history from the event hub (`query_events`); older history from the lake (DuckDB over Parquet, read-only).
- Write path: the gateway never writes data itself. Commands go to ROS services and actions (`fault_manager`, `recovery`, `rule_engine`), so the graph stays the single source of truth.
- Bags: lists recorded MCAP splits and black-box snapshots per incident, with download links (open them in Foxglove).
- Notifications: delivers `jarvis.notify` events (the `notify` action of rules) to webhooks by channel, as Slack messages or the event as JSON, with retries and exponential backoff. Configured in `gateway.yaml`.
- Network: **LAN-only**. Binds to a configured LAN interface (default `127.0.0.1` until configured), with no internet exposure planned. Roles `viewer` / `operator` / `admin` via simple static tokens from config, mainly so writes are attributed in the audit trail. OIDC/TLS are out of scope for v1.
- Metrics: Prometheus `/metrics` (ingest rate, active faults, executions, agent cost).

### REST API (`/api/v1`)

As built (M3.1–3.2). The OpenAPI document is at `/openapi.json`, browsable at `/docs`. `seq` is a
decimal string everywhere: a uint64 does not fit a JavaScript number. Tokens go in
`Authorization: Bearer <token>` (or `?token=`). Requests without one get the `anonymous` principal
(a viewer by default; `null` in the config refuses them).

| Method & path | Role | Purpose |
|---------------|------|---------|
| `GET /health` | — | `ok` once Jarvis answered and both snapshots arrived; `degraded` after 30 s without |
| `GET /me` | viewer | Who the token is: `{name, role}` |
| `GET /system` | viewer | Lifecycle states of the watched nodes, lake status, stream clients, webhook deliveries |
| `GET /events` | viewer | `after_seq, before_seq, from, to, kind, code (glob), min_severity, component, correlation_id, q, limit, order`; `next_before_seq` pages back |
| `GET /events/{id}` | viewer | One event, plus causal parent/children |
| `GET /audit` | viewer | What operators did: OPERATOR events, and the recoveries they asked for, decided or stopped |
| `GET /faults` | viewer | Open faults (live snapshot); `state=all` adds past episodes from history; `code`, `component` |
| `GET /faults/{fault_id}` | viewer | Live fault, episode, state transitions, executions of its incident |
| `POST /faults/{fault_id}:ack` | operator | Acknowledge (`note`) |
| `POST /faults/{fault_id}:clear` | operator | Clear (`reason`, `skip_symptoms`) |
| `GET /incidents` · `GET /incidents/{id}` | viewer | List (`state=open\|RESOLVED\|...`); detail with timeline (events by `correlation_id`), executions, faults |
| `POST /incidents/{id}:close` | operator | Close a resolved incident |
| `GET /rules` · `GET /rules/{id}` | viewer | Status and counters, fires in the window; detail adds the rule's YAML and recent matches |
| `PATCH /rules/{id}` | admin | `enabled`, `dry_run`, `reason` |
| `POST /rules:validate` | admin | `jarvis lint` on YAML against the loaded plans and catalog; changes nothing |
| `POST /rules:backtest` | admin | Rule lab: lints a draft, then replays the last `hours` (≤ 168) of history through `jarvis backtest` with the robot's other rules and its plans in dry run; returns the draft's fires (with their cause events), per-rule counters, recoveries, and the decisions that differ from the live rule of the same id. Changes nothing |
| `POST /rules:reload` · `POST /plans:reload` | admin | Hot reload (`validate_only`) |
| `GET /plans` · `GET /plans/{id}` | viewer | Catalog with parameters + effectiveness in the window; detail adds latest executions |
| `POST /recoveries` | operator | Run a plan (`plan_id, args, dry_run, rationale, correlation_id`) → 202 `{execution_id, state}` |
| `GET /recoveries` · `GET /recoveries/{id}` | viewer | Mode, live and recent executions (steps, gates), history |
| `POST /recoveries/{id}:approve` · `:reject` · `:cancel` | operator | Approval flow / cancel |
| `PUT /recovery/mode` | admin | `ENABLED` / `DRY_RUN` / `DISABLED` (kill switch), `cancel_running` |
| `GET /insights` · `GET /insights/{id}` | viewer | Agent insights (payload decoded), with the events they cite |
| `POST /agent/ask` | operator | `{incident_id?, question?}` → SSE: `progress` ({stage, detail}) as the agent works, then `insight` or `error`; 503 without the agent |
| `GET /stats?hours=24` | viewer | KPIs (below), open faults by severity, event rate, top codes, plans |
| `GET /analytics/{name}?hours=24` | viewer | `components`, `top_codes`, `heatmap`, `flapping`, `plans`, `rules`, `event_rate` |
| `GET /bags` | viewer | Recorded splits and black-box snapshots: time range, size, open, converted, linked incidents |
| `GET /bags/{kind}/{bag}/{file}` | viewer | Download one MCAP file (open it in Foxglove) |
| `GET /metrics` | — | Prometheus: events by kind and severity, open faults, live executions, recovery mode, commands, webhook deliveries, lake rows |

Commands answer `200 {success, message, data}`, `409` when the component refuses (the message says
why), `503` when it does not answer. Every change is recorded by the component that made it, with
the caller's name: the gateway itself writes nothing.

### WebSocket `/api/v1/stream`

```jsonc
// client → server (?token=... when anonymous access is off)
{ "op": "subscribe", "since_seq": "9000", "filter": { "min_severity": "WARN", "kinds": ["FAULT_STATE", "RECOVERY"],
                                                    "codes": ["ros.*"], "component": "", "correlation_id": "" } }
{ "op": "ping" }

// server → client
{ "type": "event",         "data": { /* Event */ } }                   // backfill, then live, in seq order
{ "type": "faults",        "data": { "last_seq": "9131", "faults": [ /* Fault */ ] } }
{ "type": "executions",    "data": { "mode": "ENABLED", "executions": [ /* Execution */ ], "recent": [ ] } }
{ "type": "backfill_done", "seq": "9131", "truncated": false }        // truncated: more than 5000 to replay
{ "type": "heartbeat",     "seq": "9140" }                            // every 15 s without traffic
{ "type": "pong" }
```

Without `since_seq` the stream starts now (no backfill). Close codes: `1008` bad token, `1003` bad
subscribe, `1013` the client fell more than 10 000 messages behind (resume with `since_seq`).
Gap-free resume protocol: see [04-data-flow §7](04-data-flow.md#7-dashboard-live-stream-with-gap-free-resume).

### KPIs (`/stats`)

| KPI | Definition |
|-----|------------|
| Active faults | Count by severity (`ACTIVE` + `LATCHED`) |
| Open incidents | `OPEN` + `RECOVERING` + `ESCALATED` |
| Auto-recovery rate | Incidents resolved with no `ESCALATED` state ÷ resolved incidents |
| Recovery success rate | `SUCCEEDED` ÷ finished executions (overall and per plan) |
| MTTR | Mean time from fault `ACTIVE` → `CLEARED` (overall and per code) |
| Event rate | Events/min by severity (sparkline) |
| Top codes | Most frequent fault codes in the window |
| Agent | Insights, proposals accepted/rejected, cost |

## 2. Dashboard

### Stack

| Concern | Choice |
|---------|--------|
| Framework | React 19 + TypeScript (strict) + Vite |
| Data | TanStack Query (server state), typed client generated from OpenAPI (`openapi-typescript`) |
| Tables | TanStack Table + virtualization (100 k rows) |
| Routing | TanStack Router (typed routes, URL = filter state, so views are shareable) |
| UI | Tailwind CSS + shadcn-style components on Radix primitives (accessible) |
| Charts | Apache ECharts (sparklines, histograms, timeline/Gantt, heatmap), tree-shaken |
| Live | Single WS connection, resume backed by the event hub and the lake |
| Tests | Vitest + Testing Library; Playwright e2e against the gateway's fake and the Docker demo (CI) |
| Delivery | Static bundle served by the gateway; dark mode default (ops console), light mode supported |

### Navigation

```text
┌──────────────┬──────────────────────────────────────────────────────────────────────────┐
│ JARVIS tb4-sim│  ● LIVE   Recovery: ENABLED ▾   Agent: advise    🔔 3     user (operator) │
├──────────────┼──────────────────────────────────────────────────────────────────────────┤
│ ◉ Overview   │                                                                          │
│ ≡ Events     │                         (view content)                                   │
│ ⚠ Faults     │                                                                          │
│ ⧗ Incidents  │                                                                          │
│ ↻ Recovery   │                                                                          │
│ ⚙ Rules      │                                                                          │
│ ✦ Agent      │                                                                          │
│ ♥ System     │                                                                          │
└──────────────┴──────────────────────────────────────────────────────────────────────────┘
```

### View 1 — Overview

```text
┌─ Active faults ─┐ ┌─ Open incidents ┐ ┌─ Auto-recovery ─┐ ┌─ MTTR (24h) ──┐ ┌─ Events/min ──────┐
│ 1 CRIT  2 ERR   │ │ 2  (1 escalated)│ │ 87 %  ▲ 4 pts   │ │ 42 s  ▼ 11 s  │ │ ▁▂▂▃▇▅▂▁▁▂ (sev)  │
│ 4 WARN          │ │                 │ │                 │ │               │ │                   │
└─────────────────┘ └─────────────────┘ └─────────────────┘ └───────────────┘ └───────────────────┘
┌─ Needs attention ─────────────────────────────────────┐ ┌─ Latest insights ────────────────────┐
│ ⏳ Approval: safe-stop (agent, conf 0.84)  [Approve]   │ │ ✦ Front lidar stalls after USB resets │
│ ⚠ ESCALATED inc-42 localization unrecoverable  [Open] │ │   conf 0.62 · 3 evidence · 2 min ago  │
│ ● LATCHED safety.estop.engaged  [Ack] [Clear]         │ │ ✦ Digest 06:00–12:00: 3 incidents …   │
└───────────────────────────────────────────────────────┘ └───────────────────────────────────────┘
┌─ Top fault codes (24h) ───────────────┐ ┌─ Recovery outcomes (24h) ─────────────────────────┐
│ ros.topic.stale        ███████  14    │ │ restart-lifecycle-node  ✔ 11  ✖ 1   92 %          │
│ drive.motor.overcurrent ████     8    │ │ relocalize              ✔ 3   ✖ 2   60 %          │
└───────────────────────────────────────┘ └───────────────────────────────────────────────────┘
```

### View 2 — Live events

```text
[Kinds ▾] [Severity ≥ WARN ▾] [Code: ros.* ] [Component ▾] [Search…]          ⏸ Pause   ⤓ Export
┌──────┬──────────────┬─────────────┬──────┬──────────────────────┬──────────────┬──────────────────────┐
│ seq  │ time         │ kind        │ sev  │ code                 │ component    │ message              │
├──────┼──────────────┼─────────────┼──────┼──────────────────────┼──────────────┼──────────────────────┤
│ 1046 │ 09:14:05.310 │ RECOVERY    │ INFO │ restart-lifecycle-…  │ lidar_front  │ SUCCEEDED in 4.2 s   │
│ 1044 │ 09:14:03.200 │ RULE_MATCH  │ INFO │ lidar-stale-restart  │ lidar_front  │ fired (cooldown 30s) │
│ 1042 │ 09:14:03.124 │ FAULT_STATE │ ERR  │ ros.topic.stale      │ lidar_front  │ ACTIVE (1 occ.)      │
└──────┴──────────────┴─────────────┴──────┴──────────────────────┴──────────────┴──────────────────────┘
Row click → drawer: all fields · attrs table · payload JSON · causal chain (parent ↑ / children ↓) · "Open incident"
```

### View 3 — Faults

```text
[Active | History]                                                  [Severity ▾] [Component ▾] [Ack: any ▾]
┌──────────┬─────────────────────────┬──────────────┬────────┬──────┬────────┬────────┬───────────────────┐
│ sev      │ code                    │ component    │ state  │ occ. │ age    │ ack    │ actions           │
├──────────┼─────────────────────────┼──────────────┼────────┼──────┼────────┼────────┼───────────────────┤
│ CRITICAL │ safety.estop.engaged    │ base         │ LATCHED│ 1    │ 12 min │ —      │ [Ack] [Clear]     │
│ ERROR    │ nav.localization.lost   │ amcl         │ ACTIVE │ 3    │ 2 min  │ alice  │ [Recover ▾]       │
└──────────┴─────────────────────────┴──────────────┴────────┴──────┴────────┴────────┴───────────────────┘
Detail: state timeline · freeze frame · occurrences histogram · runbook · black-box snapshot link · recoveries
```

As built: the fault detail shows the fault, its freeze frame, its **runbook** (5.5: Markdown from the
fault catalog, `GET /api/v1/runbooks/{code}`, rendered as text, never as HTML), its transitions and the
recoveries of its incident. The occurrences histogram is not built; black-box files are under System →
Recordings, with the incidents each covers.

### View 4 — Incident timeline (the core view)

```text
inc-42 · nav.localization.lost · ESCALATED · opened 09:20:11 · 4 min 12 s            [Close] [Ask agent]
time →        09:20:00        09:21:00        09:22:00        09:23:00        09:24:00
Faults      ──■ localization.lost ACTIVE ─────────────────────────────────────────────▶
            ────────■ wifi.degraded ACTIVE ──────■ CLEARED
Rules               ◆ wifi-then-localization (dry-run)
                         ◆ localization-unrecoverable
Recovery         ▕relocalize ▓▓▓▓░ FAILED▏       ▕safe-stop ▓▓ SUCCEEDED▏
Agent                                ✦ insight: "Map server unreachable over Wi-Fi"  conf 0.71
Operator                                                        ● alice ack
            ╰──── causal arrows on hover: fault → rule → recovery → insight ────╯
┌─ Agent summary ─────────────────────────────────────────────────────────────────────────────────┐
│ Hypotheses: 1) Wi-Fi roaming dropped map updates (0.71) [ev 1203, 1207]  2) AMCL divergence (0.2) │
│ Recommendations: ▸ run relocalize after Wi-Fi recovers  ▸ investigate AP handover in zone B       │
└───────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### View 5 — Recovery

```text
[Running (1) | Pending approval (1) | History]                   Mode: ENABLED ▾   [Run plan…]
Pending approval
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│ safe-stop · requested by AGENT (insight ✦ 2f1c) · conf 0.84 · expires in 4:12                    │
│ Rationale: localization lost for 90 s after relocalize failed [ev 1203, 1210]                    │
│ Gates: mode ✔ params ✔ requester ✔ preconditions ✔ rate ✔ locks ✔                               │
│                                                         [Reject]  [Approve]                      │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
Running
  restart-lifecycle-node (RULE lidar-stale-restart)   ● restart ✔ 2.1 s  ● verify ⏳ 3/15 s   [Cancel]
```

### View 6 — Rules

```text
┌───────────────────────────────┬─────────┬─────────┬────────────┬──────────────┬────────────────┐
│ rule                          │ enabled │ dry-run │ fires 24h  │ last fire    │ suppressed     │
├───────────────────────────────┼─────────┼─────────┼────────────┼──────────────┼────────────────┤
│ lidar-stale-restart           │   ●     │   ○     │ 11         │ 09:14:03     │ —              │
│ wifi-then-localization        │   ●     │   ●     │ 2 (shadow) │ 09:21:40     │ —              │
└───────────────────────────────┴─────────┴─────────┴────────────┴──────────────┴────────────────┘
Detail: YAML (read-only) · match history · "Try changes in the rule lab"
Rule lab (/rules/lab): YAML draft · window (1 h – 7 d) · Backtest → fires per rule (suppressed,
cooled down, guard blocked) · timeline · fires with their cause events · recoveries (dry run) ·
for a rule the robot runs, the decisions that differ from live
```

### Analytics (medkit-style health)

Window 1 h / 6 h / 24 h / 7 d: health per component (worst severity, faults, open, MTTR, MTBF, last
fault), Pareto of fault codes (episodes and cumulative share), a heatmap of faults per component and
time, flapping faults (3+ confirm-clear cycles), recovery effectiveness per plan and rule fires.

### View 7 — Agent

- Insights feed: cards with title, confidence bar, evidence chips (click → event), recommendations, proposal status.
- **Ask Jarvis** chat: streams phases ("windowing → investigating (3 tool calls) → writing"), and the final answer renders as an insight card.
- Header: autonomy level, budget used today, and an offline/degraded indicator.

### View 8 — System

- Jarvis component lifecycle states, ingest rate, drops/suppressions, queue depth, recorder health and disk usage (bags, lake), lake conversion lag, rule-engine latency, agent status. This mirrors `/diagnostics` (hardware_id `jarvis`).

### View 9 — Fleet
The robots this gateway lists (`fleet` in gateway.yaml) and itself, one card each, the ones that
need someone first: online, open faults by severity, the worst one, incidents, the recovery mode; a
link to the robot's own dashboard. Read-only, asked from each robot's gateway every 5 s
([guide](../guides/fleet.md)).

## 3. UX principles

- **Live by default, pausable.** New rows never jump the list under the cursor.
- **Everything links**: event ↔ fault ↔ incident ↔ execution ↔ insight. Filters live in the URL.
- **Severity colors are consistent** across views and also paired with icons and text (color-blind safe).
- **Dangerous actions confirm** (clear latched fault, disable recovery, approve `supervised` plans) and require an operator note.
