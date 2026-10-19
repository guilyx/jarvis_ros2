# Changelog

All notable changes to Jarvis, by release. Each package also keeps a `CHANGELOG.rst` (the ROS
release tooling reads those); this file is the release notes. Versions follow
[Semantic Versioning](https://semver.org): a breaking change to `jarvis_interfaces`, the event
schemas, the rule and plan languages or the API is a new major version.

## Forthcoming

The first release: record, manage and recover from faults on ROS 2 Jazzy and Lyrical Luth
robots, with history, a dashboard and an agent that explains what happened.

### Record and manage faults
- One event stream for everything Jarvis knows: faults, rule matches, recovery steps, insights,
  operator actions. Validated, sequenced, rate-limited, and recorded to MCAP. It goes out in
  batches (`/jarvis/event_batches`, each within 5 ms), and one message per event on
  `/jarvis/events` for whoever reads it.
- Faults from your nodes (`jarvis_reporter`, C++ and Python) and, without any code, from
  `/diagnostics`, `/rosout`, action status, lifecycle transitions, silent or slow topics,
  missing nodes, and subscriptions whose QoS their publisher cannot match (they get nothing).
- A fault catalog: debounce, heal, latch, severity floors, root cause and muted symptoms,
  incidents, freeze frames, a black-box recording when a fault confirms, and a runbook per code.
- Crash durability: open faults, incidents and pending approvals survive a restart; a recording
  cut by a crash is recovered.

### Recover automatically
- Rules over the stream: events, counts, sequences with absence, combinations; partitions,
  guards, cooldowns, rate limits and shadow mode.
- Recovery plans behind seven gates (mode, requester, preconditions, rate limits, locks,
  approvals, four-eyes by default) with verification steps and escalation ladders; a kill switch.
- Maintenance windows: an operator puts one component under maintenance for a while (dashboard,
  `ros2 jarvis maintenance`, the API). Rules and the agent keep off it, operators still act, and
  notifications about it are silenced; it ends by itself and survives restarts.
- Actuators for lifecycle nodes, services, parameters, black-box snapshots and Nav2; and for any
  service or action, of a type named in the plan (`service.call`, `action.goal`), checked against
  the type when the plans load.
- Drivers that aren't lifecycle nodes: Jarvis restarts their container (Docker, Podman) or systemd
  unit, only those the recovery node allows.
- Behavior trees: a recovery step runs a BehaviorTree.CPP tree (`bt.run`), with nodes that call any
  service or action and read Jarvis's faults; the same nodes for a robot's own trees.
- `ros2 jarvis lint` and `backtest`: check rules before they ship, replay recordings through them.
- The rule lab, in the dashboard: write or change a rule and see what it would have done over
  the robot's recorded history (when it fires, on which event, the recoveries it would ask for,
  what differs from the live rule) before it goes live.
- Rule suggestions: when operators keep running the same plan by hand after the same fault, the
  rule lab drafts the rule that would do it, and backtests it over their runs.

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
- SOVD clients (the ros2_medkit web UI, its Foxglove panel) list, follow and clear Jarvis's faults:
  the gateway serves them on a SOVD-style API at `/sovd/api/v1`.
- Several robots in one dashboard: a gateway lists the others, and its Fleet view shows them side
  by side, the ones that need someone first.
- Fleet managers that speak VDA 5050 see Jarvis's faults in the robot's state: the gateway serves
  them as VDA 5050 errors, with levels and the runbook's first fix as a hint.
- Devices that don't run ROS (a BMS, a charger, a PLC) report faults, states and logs over MQTT
  (`mqtt:=true`, `jarvis_mqtt`); their last will raises `mqtt.device.offline`, and Jarvis's open
  faults go back out, retained, for HMIs and PLCs.
- A robot that runs ros2_medkit keeps it: `medkit:=true` brings the faults its fault manager
  confirms into Jarvis (`jarvis_medkit`), for Jarvis's rules, recovery and agent.

### Production
- Measured against the requirements: load, soak, crash and disk-pressure tests in CI.
- An SROS2 profile with one enclave per process, tested with security enforced.
- Guides: integration, rules cookbook, operator, security.
