# jarvis_ros2

Jarvis is an event, fault &amp; recovery management system for ROS 2 robots.

- **Record**: every event and fault, with a proper fault lifecycle (debounce, heal, latch, ack).
- **Recover**: automatically, from declarative rules over sequences of events, behind safety gates.
- **Explain**: an agent reads windows of events and produces evidence-backed insights and recovery proposals.
- **Visualize**: a live web dashboard with incident timelines and approvals.

The core is ROS-agnostic; ROS 2 is the communication layer.

## Status

**M1 (record & manage faults) is in. M2 (rules & automated recovery) is under way**: the rule
engine, the recovery orchestrator and the actuators run in the stack; demo scenarios and tooling are
next ([roadmap](docs/design/09-roadmap.md)). The design package starts at
[`docs/design/README.md`](docs/design/README.md).

## Try it in Docker

All you need is Docker. No ROS install, works on Linux, macOS and Windows (WSL):

```bash
git clone https://github.com/guilyx/jarvis_ros2 && cd jarvis_ros2
./demo
```

The first run builds the image (about 10 minutes). Then a guided tour plays in a live view: a lidar
stalls, a motor driver overheats, a node errors in a loop, the e-stop is pressed. You watch Jarvis
debounce, correlate, latch and record each one. Ctrl-C leaves the view; Jarvis keeps running.

| Command | What it does |
|---------|--------------|
| `./demo watch` | Live view of open faults and recent events |
| `./demo inject scan-stall` | Break one thing: `scan-stall`, `scan-slow`, `rosout-error`, `diag-error`, `estop` |
| `./demo clear` | Clear the latched e-stop, as an operator would |
| `./demo shell` | A shell with ROS 2 ready: `ros2 topic echo /jarvis/faults` |
| `./demo down` | Stop. Recordings stay in `./demo-data/` |

- **Foxglove**: connect to `ws://localhost:8765` to browse `/jarvis/events`, `/jarvis/faults`, `/scan` and
  `/diagnostics` live. The MCAP recordings in `./demo-data/bags/` open in Foxglove too.
- **Restarts keep open faults**: that's the persistence at work. `./demo clear --all` clears every open
  fault; `rm -rf demo-data` (with `sudo` on Linux: the container writes as root) starts from scratch.
- **No bash** (plain Windows): `docker compose up -d`, then `docker compose exec demo monitor` and
  `docker compose exec demo inject tour`.

## From source

ROS 2 Jazzy, from the root of this repository:

```bash
rosdep install --from-paths core ros --ignore-src -y
colcon build && source install/setup.bash

ros2 launch jarvis_demo demo.launch.py          # Jarvis + a fake lidar, no simulator needed
ros2 run jarvis_demo monitor                    # in another shell: the live view
ros2 run jarvis_demo inject tour                # and in a third: every scenario, narrated
```

With Nav2 and Gazebo installed, `nav2_sim.launch.py` runs the same demo on a simulated TurtleBot 4.

## Use it on your robot

Reporting from your own node is one line, in C++ or Python:

```cpp
jarvis_reporter::Reporter jarvis(node, "lidar");
jarvis.fault("sensor.lidar.stale", Severity::Error, "no scan for 1.2 s", {{"silence_ms", 1200}});
jarvis.clear("sensor.lidar.stale");
```

Or don't report at all: the bridges turn `/diagnostics`, `/rosout` errors, aborted action goals,
lifecycle transitions, silent or slow topics and missing nodes into
faults on their own.

## Recover automatically

A rule watches the event stream and asks for a recovery plan; the orchestrator runs it behind safety
gates (mode, requester, preconditions, rate limits, locks, approvals), verifies the fix and escalates
when it does not work. Rules and plans are YAML directories (`rules:=`, `plans:=` launch arguments):

```yaml
# rules/lidar.yaml
schema: jarvis.rules/v1
rules:
  - id: scan-stale-restart
    when: { event: { kind: FAULT_STATE, code: sensor.scan.stale, attrs.state: ACTIVE } }
    then:
      - recover: { plan: restart-lifecycle-node, args: { node: /scan_relay, component: lidar } }
    policy: { cooldown: 30s, max_fires: { count: 3, per: 1h } }
```

`restart-lifecycle-node` ships in [`jarvis_bringup/config/plans`](ros/jarvis_bringup/config/plans).
Operators use the same gate:

```bash
ros2 action send_goal /jarvis/recovery/execute jarvis_interfaces/action/ExecuteRecovery \
  "{plan_id: restart-lifecycle-node, args: [{key: node, string_value: /scan_relay}, {key: component, string_value: lidar}], dry_run: true}"
ros2 service call /jarvis/recovery/set_mode jarvis_interfaces/srv/SetRecoveryMode "{mode: DISABLED, operator_name: me}"
ros2 topic echo /jarvis/executions   # what runs, what waits for approval, what just finished
```

## Packages

| Package | What it does |
|---------|--------------|
| [`core/jarvis_core`](core/jarvis_core) | ROS-free C++ domain: event hub, fault lifecycle, incidents, catalog, rule engine, recovery orchestrator |
| [`ros/jarvis_interfaces`](ros/jarvis_interfaces) | Messages, services and the `ExecuteRecovery` action |
| [`ros/jarvis_ros`](ros/jarvis_ros) | Event hub, fault manager, rule engine and recovery lifecycle components; built-in actuators (lifecycle, std_srvs, parameters, bag snapshot) |
| [`ros/jarvis_nav2`](ros/jarvis_nav2) | Nav2 actuators: lifecycle manager, costmaps, cancel and send goals |
| [`ros/jarvis_reporter`](ros/jarvis_reporter) | C++ and Python client libraries for apps |
| [`ros/jarvis_bridges`](ros/jarvis_bridges) | Diagnostics, rosout, action status and lifecycle bridges; topic and node watchdogs |
| [`ros/jarvis_bringup`](ros/jarvis_bringup) | Launch, parameters, default fault catalog, rules and plans, recorder and black box |
| [`ros/jarvis_demo`](ros/jarvis_demo) | Lidar relay, fault injector, demo launch files |
| [`schemas`](schemas) | JSON Schema contracts (canonical) |

## Design

| Area | Doc |
|------|-----|
| Requirements | [docs/design/01-requirements.md](docs/design/01-requirements.md) |
| Architecture | [docs/design/02-architecture.md](docs/design/02-architecture.md) |
| Data model & interfaces | [docs/design/03-data-model.md](docs/design/03-data-model.md) |
| Data flow | [docs/design/04-data-flow.md](docs/design/04-data-flow.md) |
| Rules & recovery | [docs/design/05-rules-and-recovery.md](docs/design/05-rules-and-recovery.md) |
| Agent | [docs/design/06-agent.md](docs/design/06-agent.md) |
| Dashboard | [docs/design/07-dashboard.md](docs/design/07-dashboard.md) |
| ROS 2 practices | [docs/design/08-ros2-practices.md](docs/design/08-ros2-practices.md) |
| Roadmap | [docs/design/09-roadmap.md](docs/design/09-roadmap.md) |
| Decisions (ADRs) | [docs/adr/README.md](docs/adr/README.md) |
