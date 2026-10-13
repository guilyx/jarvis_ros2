# 07 — Gateway & Dashboard

> **TL;DR**
> - **Gateway** (`jarvis_gateway`, FastAPI): REST + WebSocket over the store and the ROS layer.
>   OpenAPI-documented, role-based auth, delivers notifications, serves the dashboard bundle.
>   No Node.js runs on the robot.
> - **Dashboard** (React + TypeScript + Vite): 8 views. The core view is the **Incident timeline**:
>   faults, rule matches, recovery steps and agent insights in swimlanes, with causal links.

---

## 1. Gateway

### Responsibilities
- Read path: SQLite **read-only** (`mode=ro`) for history; `/jarvis/events` + `/jarvis/faults` for live data.
- Write path: never writes the store. Commands go to ROS services and actions (single writer stays the recorder).
- Notifications: subscribes to `jarvis.notify` events and delivers them to webhooks (Slack, PagerDuty, generic HTTP) with retries.
- Auth: bearer tokens with roles `viewer` / `operator` / `admin` from config (OIDC later). Binds to `127.0.0.1` by default; TLS through a reverse proxy.
- Metrics: Prometheus `/metrics` (ingest rate, active faults, executions, agent cost).

### REST API (`/api/v1`)

| Method & path | Role | Purpose |
|---------------|------|---------|
| `GET /events` | viewer | Query: `after_seq, from, to, kind, code (glob), min_severity, component, correlation_id, q, limit` |
| `GET /events/{id}` | viewer | One event, plus causal parent/children |
| `GET /faults` | viewer | `state=active\|all`, `code`, `component` |
| `GET /faults/{fault_id}` | viewer | Episode, freeze frame, occurrences, related executions |
| `POST /faults/{fault_id}:ack` | operator | Acknowledge |
| `POST /faults/{fault_id}:clear` | operator | Clear (latched or forced) |
| `GET /incidents` · `GET /incidents/{id}` | viewer | List; detail with timeline (events by `correlation_id`) |
| `POST /incidents/{id}:close` | operator | Close a resolved incident |
| `GET /rules` · `GET /rules/{id}` | viewer | Definitions + stats (fires 24 h, last fire, suppressed) |
| `PATCH /rules/{id}` | admin | `enabled`, `dry_run` |
| `POST /rules:validate` | admin | Validate YAML (for the future editor and CI) |
| `POST /rules:reload` · `POST /plans:reload` | admin | Hot reload |
| `GET /plans` · `GET /plans/{id}` | viewer | Catalog + success rates |
| `POST /recoveries` | operator | Trigger a plan (`plan_id, args, dry_run, rationale`) |
| `GET /recoveries` · `GET /recoveries/{id}` | viewer | Executions with steps and gate results |
| `POST /recoveries/{id}:approve` · `:reject` · `:cancel` | operator | Approval flow / cancel |
| `PUT /recovery/mode` | admin | `ENABLED` / `DRY_RUN` / `DISABLED` (kill switch) |
| `GET /insights` · `GET /insights/{id}` | viewer | Agent insights |
| `POST /agent/ask` | operator | Question → SSE stream of phases and the final insight |
| `GET /stats?window=24h` | viewer | KPIs (below) |
| `GET /health` | — | Liveness/readiness of gateway + Jarvis components |

### WebSocket `/api/v1/stream`

```jsonc
// client → server
{ "op": "subscribe", "since_seq": 9000, "filter": { "min_severity": "WARN", "kinds": ["FAULT_STATE", "RECOVERY"] } }

// server → client
{ "type": "event",         "data": { /* Event */ } }
{ "type": "faults",        "data": { "last_seq": 9131, "faults": [ /* Fault */ ] } }
{ "type": "backfill_done", "seq": 9131 }
{ "type": "heartbeat",     "seq": 9140 }
```

Gap-free resume protocol: see [04-data-flow §6](04-data-flow.md#6-dashboard-live-stream-with-gap-free-resume).

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
| UI | Tailwind CSS + shadcn/ui (Radix primitives, accessible) |
| Charts | Apache ECharts (sparklines, histograms, timeline/Gantt) |
| Live | Single WS connection, store-backed resume |
| Tests | Vitest + Testing Library; Playwright e2e against `jarvis_demo` |
| Delivery | Static bundle served by the gateway; dark mode default (ops console), light mode supported |

### Navigation

```text
┌──────────────┬──────────────────────────────────────────────────────────────────────────┐
│ JARVIS  amr-07│  ● LIVE   Recovery: ENABLED ▾   Agent: advise    🔔 3     user (operator) │
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
Detail: YAML (read-only v1, editor v2) · match history · backtest results
```

### View 7 — Agent

- Insights feed: cards with title, confidence bar, evidence chips (click → event), recommendations, proposal status.
- **Ask Jarvis** chat: streams phases ("windowing → investigating (3 tool calls) → writing"), and the final answer renders as an insight card.
- Header: autonomy level, budget used today, and an offline/degraded indicator.

### View 8 — System

- Jarvis component lifecycle states, ingest rate, drops/suppressions, queue depth, store size/retention watermark, rule-engine latency, agent status. This mirrors `/diagnostics` (hardware_id `jarvis`).

## 3. UX principles

- **Live by default, pausable.** New rows never jump the list under the cursor.
- **Everything links**: event ↔ fault ↔ incident ↔ execution ↔ insight. Filters live in the URL.
- **Severity colors are consistent** across views and also paired with icons and text (color-blind safe).
- **Dangerous actions confirm** (clear latched fault, disable recovery, approve `supervised` plans) and require an operator note.
