# Changelog

All notable changes to Jarvis, by release. Each package also keeps a `CHANGELOG.rst` (the ROS
release tooling reads those); this file is the release notes. Versions follow
[Semantic Versioning](https://semver.org): a breaking change to `jarvis_interfaces`, the event
schemas, the rule and plan languages or the API is a new major version.

## Forthcoming

The first release: record, manage and recover from faults on ROS 2 Jazzy robots, with history,
a dashboard and an agent that explains what happened.

### Record and manage faults
- One event stream (`/jarvis/events`) for everything Jarvis knows: faults, rule matches, recovery
  steps, insights, operator actions. Validated, sequenced, rate-limited, and recorded to MCAP.
- Faults from your nodes (`jarvis_reporter`, C++ and Python) and, without any code, from
  `/diagnostics`, `/rosout`, action status, lifecycle transitions, silent or slow topics and
  missing nodes.
- A fault catalog: debounce, heal, latch, severity floors, root cause and muted symptoms,
  incidents, freeze frames, a black-box recording when a fault confirms, and a runbook per code.
- Crash durability: open faults, incidents and pending approvals survive a restart; a recording
  cut by a crash is recovered.

### Recover automatically
- Rules over the stream: events, counts, sequences with absence, combinations; partitions,
  guards, cooldowns, rate limits and shadow mode.
- Recovery plans behind seven gates (mode, requester, preconditions, rate limits, locks,
  approvals, four-eyes by default) with verification steps and escalation ladders; a kill switch.
- Actuators for lifecycle nodes, services, parameters, black-box snapshots and Nav2.
- `ros2 jarvis lint` and `backtest`: check rules before they ship, replay recordings through them.

### History, dashboard and API
- The lake: recordings as Parquet, queried with DuckDB; KPIs, analytics, retention with a
  free-disk floor.
- The gateway: REST API, a gap-free live stream, roles, an audit trail, webhooks, Prometheus
  metrics; TLS and single sign-on through an authenticating proxy.
- The dashboard: overview, events, faults with their runbooks, incident timelines, recovery and
  approvals, rules, analytics, the agent.

### The agent
- Explains incidents and failed recoveries with the evidence behind each claim: a deterministic
  analyst offline, or any model through LiteLLM with read-only tools.
- Proposes recoveries through the same gates as everyone (an operator approves by default);
  answers questions from the dashboard; scored on labelled incidents.
- Redaction where it reads, and secrets scrubbed from every message to a model.
- AI assistants (Claude Code, Claude Desktop, Cursor) ask the robot what is wrong over MCP:
  `jarvis-mcp` reads faults, runbooks, incidents, events and history through the gateway, with
  its tokens and audit trail, and requests recoveries through the gates only if allowed.

### Production
- Measured against the requirements: load, soak, crash and disk-pressure tests in CI.
- An SROS2 profile with one enclave per process, tested with security enforced.
- Guides: integration, rules cookbook, operator, security.
