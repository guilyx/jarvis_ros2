# Putting Jarvis on your robot

From a working ROS 2 Jazzy robot to Jarvis recording its faults, restarting what hangs, and
showing all of it in a dashboard. Each step works on its own: stop after the first three and you
already have fault records, incidents, history and the dashboard; rules and recovery come next.

1. [Install it and start it](#1-install-it-and-start-it)
2. [Report from your nodes](#2-report-from-your-nodes)
3. [Let the bridges watch](#3-let-the-bridges-watch)
4. [Catalog your faults](#4-catalog-your-faults)
5. [Recover automatically](#5-recover-automatically)
6. [Launch it with your robot](#6-launch-it-with-your-robot)
7. [Recordings and the lake](#7-recordings-and-the-lake)
8. [The gateway, the dashboard, the agent](#8-the-gateway-the-dashboard-the-agent)
9. [Check that it works](#9-check-that-it-works)
10. [Gotchas](#10-gotchas)

What the pieces are: [02 · Architecture](../design/02-architecture.md). Closing it up for
production: [security guide](security.md). The people who run it: [operator guide](operator.md).

## 1. Install it and start it

Jarvis builds in your colcon workspace next to your robot's packages:

```bash
cd ~/ros2_ws/src && git clone https://github.com/guilyx/jarvis_ros2
cd ~/ros2_ws && rosdep install --from-paths src --ignore-src -y
J=src/jarvis_ros2   # the Python services' pinned dependencies (a venv, or --break-system-packages)
pip install -r $J/lake/jarvis_lake/requirements.txt -r $J/gateway/jarvis_gateway/requirements.txt \
            -r $J/agent/jarvis_agent/requirements.txt
(cd $J/dashboard && npm ci && npm run build)    # the dashboard bundle (Node 22)
colcon build && source install/setup.bash
export JARVIS_DASHBOARD=$PWD/$J/dashboard/dist
ros2 launch jarvis_bringup jarvis.launch.py robot_id:=amr-07
```

That starts everything with the defaults: the core (event hub, fault manager, rule engine,
recovery) and the bridges in one component container, the recorder and the black box, the lake,
the gateway with the dashboard on <http://127.0.0.1:8080>, and the agent. Without changing
anything on the robot, the bridges already turn `/diagnostics`, `/rosout` errors, aborted action
goals and lifecycle failures into faults. (The Docker image, `docker/Dockerfile`, has all of it
built, the dashboard included.)

## 2. Report from your nodes

`jarvis_reporter` is how a node says what is wrong, in C++ (header only) or Python. It publishes
on `/jarvis/ingest` (reliable); the event hub validates, stamps and orders everything.

```cpp
// package.xml: <depend>jarvis_reporter</depend>
// CMakeLists.txt: find_package(jarvis_reporter REQUIRED)
//                 target_link_libraries(lidar_driver jarvis_reporter::jarvis_reporter)
#include <jarvis_reporter/reporter.hpp>
using jarvis_reporter::Reporter;
using jarvis_reporter::Severity;

Reporter jarvis(node, "lidar");   // a Node or LifecycleNode pointer, and the component it is about
jarvis.fault("sensor.lidar.stale", Severity::Error, "no scan for 1.2 s", {{"silence_ms", 1200}});
jarvis.clear("sensor.lidar.stale", "scans are back");
jarvis.state("robot.mode", "AUTONOMOUS");
jarvis.log("sensor.lidar.slow", Severity::Warn, "scan rate low", {{"rate_hz", 9.5}});
```

```python
# package.xml: <exec_depend>jarvis_reporter</exec_depend>
from jarvis_reporter import Reporter, Severity

jarvis = Reporter(node, "lidar")
jarvis.fault("sensor.lidar.stale", Severity.ERROR, "no scan for 1.2 s", {"silence_ms": 1200})
jarvis.clear("sensor.lidar.stale", "scans are back")
jarvis.state("robot.mode", "AUTONOMOUS")
```

| Call | Sends | Notes |
|------|-------|-------|
| `fault(code, severity, message, attrs)` | `FAULT_RAISE` | Call it as often as you check: it sends the first time, then at most once per `repeat_period` (1 s) while the fault holds. The catalog decides when reports become a confirmed fault. |
| `clear(code, message, attrs)` | `FAULT_CLEAR` | Only for a code this reporter raised. Python's `clear(..., force=True)` sends anyway: after a restart, the fault manager has restored the fault your previous run raised. |
| `state(code, value)` | `STATE` (`attrs.value`) | Only when the value changes. Rules and guards read the latest value; fault freeze frames include them. |
| `log(code, severity, message, attrs)` | `LOG` | Every call (the hub rate-limits each code). |
| `wait_for_hub()`, `flush()` | | For short-lived tools: wait until the hub is matched before the first report, and until everything is acknowledged before exiting. |

- **Codes** are dotted lowercase, `<area>.<thing>.<problem>`: `sensor.lidar.stale`,
  `power.charging.failed`, `drive.motor_left.overcurrent`. They are what rules, the catalog,
  analytics and runbooks key on: pick them once and keep them.
- **The component** is what the fault is about (`lidar`, `base`, `arm`), not the node: one fault
  per code and component. Two lidars are two components.
- **Attributes** are scalars (text, integers, floats, booleans): at most 32 per event, keys of 64
  characters at most. They become the fault's freeze frame, and rules can match them.
- **Severity**: `WARN` degrades, `ERROR` stops a function, `CRITICAL` stops the robot. Faults at
  `ERROR` and above open incidents. A `CRITICAL` report confirms at once, whatever the debounce.

## 3. Let the bridges watch

The bridges turn what ROS already says into events, with no change to your nodes. Each is a
lifecycle node under `/jarvis/bridges/` (`bridges:=false` turns them all off).

| Bridge | Watches | Raises |
|--------|---------|--------|
| diagnostics | `/diagnostics` | `diag.<status name>` while a status is not OK (STALE counts as CRITICAL), on the component of its `hardware_id`; the status' values come along as attributes |
| rosout | `/rosout` | `LOG` events from `WARN`; `ros.log.error` and `ros.log.fatal` faults per logger, which heal after a quiet period |
| action status | every action server (or `actions`) | `ros.action.<action>.aborted` on an aborted goal, cleared by the next success; the goal status as `STATE` |
| lifecycle | every managed node (or `nodes`) | `ros.lifecycle.inactive`, `ros.lifecycle.failed`; each node's state as `STATE` |
| topic watchdog | the topics you list | `ros.topic.stale` (silent, or no publisher), `ros.topic.slow` (below a rate) |
| node watchdog | the nodes you list | `ros.node.missing` |

The watchdogs watch nothing until you tell them what matters. Their parameters live in the
params file (`params_file:=`, by default `jarvis_bringup/config/jarvis.yaml`). **The launch file
uses one params file, it doesn't merge**: copy `jarvis.yaml` into your package and add to it.

```yaml
# my_robot_jarvis/config/jarvis.yaml: a copy of jarvis_bringup's, plus:
/jarvis/bridges/topic_watchdog:
  ros__parameters:
    tick_period_s: 0.1
    topics: [scan, odom, camera]
    scan: { topic: /scan, component: lidar, stale_after_s: 1.0, min_rate_hz: 5.0 }
    odom: { topic: /odom, component: base, stale_after_s: 0.5 }
    camera: { topic: /camera/image_raw, component: camera, stale_after_s: 2.0, startup_grace_s: 20.0 }

/jarvis/bridges/node_watchdog:
  ros__parameters:
    grace_s: 5.0
    period_s: 1.0
    nodes: [/controller_server, /planner_server, /lidar_driver]   # fully qualified

/jarvis/bridges/diagnostics:
  ros__parameters:
    topic: /diagnostics
    code_prefix: diag
    ignore_hardware_ids: [jarvis]
    ignore_names: ["*: Frequency"]          # statuses you don't want as faults
```

- A topic watch takes `topic`, `component` (default: the topic's last segment), `stale_after_s`
  (1.0, 0 turns it off), `min_rate_hz` (0, off), `rate_window_s` (2.0), `startup_grace_s` (5.0),
  `severity` (`ERROR`) and `type` (read from the graph if empty). Set `component`: `/front/scan`
  and `/rear/scan` would otherwise both be `scan`, and share one fault.
- Write floats with a decimal point (`5.0`, not `5`): ROS parameters are typed.
- Under simulation (`use_sim_time:=true`) the watchdogs tick on `/clock`: with no clock, they
  never fire.
- The lifecycle bridge also watches Jarvis' own managed nodes; `ignore_nodes` narrows it.

### Already running ros2_medkit?

Keep it. With `medkit:=true`, a bridge (`jarvis_medkit`, `/jarvis/bridges/medkit`) reports to Jarvis
the faults medkit's fault manager confirms, so Jarvis's rules, recovery and agent work on them:

```bash
# rosdep (step 1) installs what it needs: ros2_medkit_msgs, which your medkit install already has
ros2 launch jarvis_bringup jarvis.launch.py medkit:=true    # medkit_fault_manager:=/fault_manager
```

| ros2_medkit | Jarvis |
|-------------|--------|
| A fault confirmed (`fault_confirmed`), seen again (`fault_updated`) | A `FAULT_RAISE`, per reporting source |
| `MOTOR_OVERHEAT` | `medkit.motor_overheat` (`code_prefix`); the original in the attribute `medkit.fault_code` |
| A reporting source (`/powertrain/motor_left`) | The node, and its last segment as the component (`motor_left`) |
| Severity INFO / WARN / ERROR / CRITICAL (0-3) | INFO / WARN / ERROR / CRITICAL |
| Cleared, healed (`fault_cleared`), and the symptoms cleared with it | A `FAULT_CLEAR` |

- medkit debounces, Jarvis takes its word: only confirmed faults come over, and medkit's muted
  symptoms stay in medkit (`include_muted: true` brings them).
- medkit's events are not replayed to a late joiner: every `resync_period_s` (10 s) the bridge reads
  medkit's `list_faults` and makes Jarvis agree. At start it also clears the `medkit.*` faults Jarvis
  still has open from before and medkit no longer confirms.
- Clearing a medkit fault in Jarvis does not clear it in medkit, and medkit seeing it again raises it
  again: clear it in medkit (its web UI, or `/fault_manager/clear_fault`).
- Name your rules and catalog entries after the Jarvis codes: `medkit.motor_overheat`, `medkit.*`.
- With SROS2, the bridge runs in the `/jarvis/medkit` enclave; medkit's own nodes need their
  enclaves in your robot's policy.

The other way round, the gateway serves Jarvis's faults to medkit's web UI ([SOVD guide](sovd.md)).

## 4. Catalog your faults

The catalog says what each code means and how it behaves: when reports become a confirmed fault,
when it heals, whether it waits for a human, what to record. Start from a copy of
[`jarvis_bringup/config/faults.yaml`](../../ros/jarvis_bringup/config/faults.yaml) (the bridges'
codes) and add yours; `catalog:=` points Jarvis at it. Codes not in it still work, with the
`defaults` and flagged uncataloged.

```yaml
schema: jarvis.faults/v1
defaults:
  severity: ERROR
  debounce: immediate
  heal: { on_clear: true }

faults:
  - code: sensor.lidar.stale
    description: The lidar stopped publishing; the costmaps go blind.
    debounce: { persist: 500ms }     # reported for half a second before it counts
    capture: { blackbox: true }      # save the black box when it confirms
  - code: drive.motor_left.overcurrent
    description: The left motor draws more than its rating.
    severity: CRITICAL
    debounce: { count: 3, within: 2s }
  - code: safety.estop.engaged
    description: The emergency stop is pressed.
    severity: CRITICAL
    latch: true                      # stays until an operator clears it, after looking
    incident: always
  - code: power.battery.low
    severity: WARN
    heal: { on_clear: true, after: 10m }   # or by itself after 10 min without a report

correlation:
  - id: lidar-blind-navigation
    root: [sensor.lidar.stale]
    symptoms: ["ros.action.*.aborted", "ros.topic.slow"]
    window: 5s                       # symptoms within 5 s of the root are muted under it
```

| Field | What it does |
|-------|--------------|
| `severity` | A floor: a reporter can raise a fault above it, never below |
| `debounce` | `immediate`, `{count: N, within: D}`, `{persist: D}`, or `{counter: {confirm_at, heal_at}}` |
| `heal` | `on_clear` (a `FAULT_CLEAR` heals it) and `after` (healed after that long without a report) |
| `latch` | Healed faults stay `LATCHED` until an operator clears them |
| `incident` | `auto` (from `incident_min_severity`, `ERROR`), `always`, `never` |
| `capture.blackbox` | Snapshot the black box (the last minutes of `blackbox_topics`) when it confirms |
| `runbook` | A Markdown file, relative to the catalog (see below) |
| `correlation` | `root` and `symptoms` (globs): symptoms are muted and joined to the root's incident; `clear_with_root` clears them with it |

Durations are `500ms`, `5s`, `10m`, `1h` (a bare number is seconds). The fault manager reads the
catalog when it starts: a change needs a restart. `ros2 jarvis lint --catalog` checks it.

### Runbooks

A runbook says what to do about a fault: what it means, what to check, what fixes it, when to
call someone. The dashboard shows it on the fault, and the agent reads it when it analyses an
incident. The runbook of a code is the first of:

1. the file its catalog entry names: `runbook: runbooks/sensor.lidar.stale.md` (relative to the
   catalog);
2. `runbooks/<code>.md` next to the catalog;
3. the runbooks Jarvis ships for the bridges' codes (`ros.topic.stale`, `ros.node.missing`,
   `jarvis.storage.low`, ...).

So a copied catalog keeps the shipped runbooks, and yours go in `runbooks/` next to it. Start
from [`TEMPLATE.md`](../../ros/jarvis_bringup/config/runbooks/TEMPLATE.md): it opens with
`# <code>: <what happened>`, then *What it means*, *Check*, *Fix*, *Escalate when*. Runbooks are
read when shown: edit one and the next look has it.

## 5. Recover automatically

Rules (`rules:=`) watch the stream and ask for recovery plans (`plans:=`); the orchestrator runs
them behind its gates (mode, requester, preconditions, rate limits, locks, approvals) and
escalates when they don't work. The [rules cookbook](rules-cookbook.md) has the recipes; the
shipped [`restart-lifecycle-node`](../../ros/jarvis_bringup/config/plans/core.yaml) plan and the
[`escalation-notify`](../../ros/jarvis_bringup/config/rules/core.yaml) rule are a start.

- **Each launch argument takes one directory**: copy the shipped `core.yaml` files into yours if
  you want them alongside your own.
- **Actuators**: `/jarvis/recovery` loads the lifecycle, service, parameter and snapshot actuators.
  On a Nav2 robot add `jarvis_nav2::Nav2Actuator` to `actuators` in your `jarvis.yaml` for the
  `nav2.*` actions. Your own: a `jarvis_ros::Actuator` pluginlib plugin, listed the same way.
- **Check in CI**, on every change: `ros2 jarvis lint --rules ... --plans ... --catalog ...`
  (exit code 1 on errors), and replay real recordings through them:
  `ros2 jarvis backtest <bag> --rules ... --plans ... --expect-same`.
- **Start in shadow**: new rules with `dry_run: true`, or the whole robot with
  `recovery_mode:=DRY_RUN` (every gate runs, nothing moves) for its first days. The mode is saved:
  after a restart the robot keeps the one an operator last set.

## 6. Launch it with your robot

Include Jarvis in your robot's bringup, with your configuration:

```python
# my_robot_jarvis/launch/jarvis.launch.py
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    config = PathJoinSubstitution([FindPackageShare("my_robot_jarvis"), "config"])
    jarvis = PathJoinSubstitution([FindPackageShare("jarvis_bringup"), "launch", "jarvis.launch.py"])
    return LaunchDescription(
        [
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(jarvis),
                launch_arguments={
                    "robot_id": "amr-07",
                    "params_file": PathJoinSubstitution([config, "jarvis.yaml"]),
                    "catalog": PathJoinSubstitution([config, "faults.yaml"]),
                    "rules": PathJoinSubstitution([config, "rules"]),
                    "plans": PathJoinSubstitution([config, "plans"]),
                    "gateway_config": PathJoinSubstitution([config, "gateway.yaml"]),
                    "agent_config": PathJoinSubstitution([config, "agent.yaml"]),
                }.items(),
            ),
        ]
    )
```

The arguments people change (`ros2 launch jarvis_bringup jarvis.launch.py --show-args` lists all):

| Argument | Default | |
|----------|---------|---|
| `robot_id` | `robot` | Stamped on every event; names the robot in notifications |
| `data_dir` | `$JARVIS_DATA` or `~/.local/share/jarvis` | Recordings, the lake, state: put it on a disk with room |
| `params_file`, `catalog`, `rules`, `plans` | the shipped ones | Your configuration |
| `recovery_mode` | `ENABLED` | `DRY_RUN` to rehearse, `DISABLED` to never act (a saved mode wins) |
| `use_sim_time` | `false` | In simulation |
| `bridges`, `record`, `blackbox`, `lake`, `gateway`, `agent`, `recovery` | `true` | Turn a part off |
| `medkit`, `medkit_fault_manager` | `false`, `/fault_manager` | Bring ros2_medkit's faults in ([§3](#already-running-ros2_medkit)) |
| `record_extra_topics` | `/diagnostics /rosout` | Recorded with Jarvis' own topics |
| `blackbox_topics`, `blackbox_cache_mb` | `/tf /tf_static /odom /scan /cmd_vel /jarvis/event_batches`, 256 | What the black box keeps, how much |
| `split_duration_s` | 300 | Length of a recording file |
| `lake_min_free_gib` | 1 | Free disk the lake keeps |
| `gateway_host`, `gateway_port`, `gateway_config` | `127.0.0.1`, 8080 | Where the API and the dashboard listen; tokens, webhooks |
| `agent_config` | | The agent's model, autonomy, redaction |

On a robot that runs unattended, start the launch from systemd (or your container runtime) with
`Restart=on-failure`: Jarvis restores its open faults, incidents and pending approvals at
startup, and recovers the recording the crash cut short.

## 7. Recordings and the lake

```text
<data_dir>/
  bags/jarvis_<stamp>/       continuous MCAP of /jarvis/** (+ record_extra_topics), 5-min files
  blackbox/blackbox_<stamp>/ snapshots of the black box, taken when a fault asks for one
  lake/                      the history as Parquet (by day), what the dashboard and analytics query
  state/                     what survives a restart: open faults, incidents, rule and recovery state
```

The lake converts each closed recording into Parquet and applies retention: recordings 7 days or
10 GiB, Parquet 180 days or 5 GiB, the black box 2 GiB, and 1 GiB of free disk kept on each file
system it writes to. A recording is never deleted before it is converted. Short of space, it
deletes converted recordings first, then black-box snapshots, then the oldest Parquet, and raises
`jarvis.storage.low` when nothing more may go.

Only the free-disk floor is a launch argument (`lake_min_free_gib`). For other budgets, run the
lake yourself next to the launch (`lake:=false`):

```bash
jarvis-lake --data-dir /data/jarvis --bags-max-gib 40 --lake-max-days 365 --min-free-gib 5 run
jarvis-lake --data-dir /data/jarvis status      # sizes, pending and failed splits
jarvis-lake --data-dir /data/jarvis sql "SELECT code, count(*) FROM events GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
```

Recordings are standard MCAP: open them in Foxglove, `ros2 bag play` them, or turn one into JSON
Lines with `ros2 jarvis export`.

## 8. The gateway, the dashboard, the agent

The gateway serves the API, the live stream and the dashboard, on `127.0.0.1:8080` by default
(this machine only). `gateway.yaml` (`gateway_config:=`) gives people tokens and roles, and
sends `notify` actions to webhooks:

```yaml
schema: jarvis.gateway/v1
host: 127.0.0.1
port: 8080
anonymous: { name: anonymous, role: viewer }   # null: a token is required
webhooks:
  - { name: ops, url: https://hooks.slack.com/services/..., channels: [operators, safety], format: slack }
```

Tokens go in the environment, not the file: `JARVIS_GATEWAY_TOKENS="<token>:alice:operator,<token>:bob:admin"`.
To reach the dashboard from the LAN, use TLS or single sign-on: the [security guide](security.md).

The agent explains incidents and failed recoveries. Without a model it is deterministic and
nothing leaves the robot; with one (any LiteLLM model string, or a local Ollama or vLLM), set it
in `agent.yaml` (`agent_config:=`) and put the key in the environment:
[06 · Agent](../design/06-agent.md), and the security guide for what a model may see.

## 9. Check that it works

```bash
ros2 jarvis faults                              # open faults (empty on a healthy robot)
ros2 topic echo /jarvis/events --once           # the stream flows (one message per event)
ros2 jarvis rules && ros2 jarvis plans          # what is loaded, and the recovery mode
curl -s localhost:8080/api/v1/health            # the gateway sees Jarvis
```

Then raise a fault the way your robot would, and watch it go through:

```python
# raise_test_fault.py: python3 raise_test_fault.py, then clear it from the dashboard
import rclpy
from jarvis_reporter import Reporter, Severity

rclpy.init()
node = rclpy.create_node("jarvis_check")
jarvis = Reporter(node, "check")
jarvis.wait_for_hub()
jarvis.fault("test.integration.check", Severity.ERROR, "raised by hand to check the integration")
jarvis.flush()
```

It shows up in the dashboard (Overview, Faults), opens an incident, and is in the recording and,
once the file closes, in the lake. Stop a watched topic's publisher to see the watchdog, and
kill a watched node to see `ros.node.missing`.

## 10. Gotchas

- **QoS on `/jarvis/ingest`**: the hub subscribes reliable; a best-effort publisher of your own
  won't be heard. The reporter does it right.
- **Bursts**: Fast DDS caps a history at 5000 samples. Jarvis' processes get a profile that
  raises it (`fastdds_profile`); a producer of yours that sends thousands of events at once needs
  it too (`FASTRTPS_DEFAULT_PROFILES_FILE`).
- **One robot per domain**: Jarvis' names are absolute (`/jarvis/...`). Robots that share a
  network get a `ROS_DOMAIN_ID` each.
- **Short-lived processes** lose what they send before discovery completes: `wait_for_hub()`
  first, `flush()` before exiting.
- **rosout faults heal by time**: the rosout bridge never sends a clear. A catalog without
  `heal.after` for `ros.log.*` leaves them open forever (the shipped one has it).
- **Parameters are read at start**: most Jarvis parameters can't change at runtime (the event
  hub's rate limits and shedding can). Change the file and restart.
- **SROS2**: your nodes need enclaves too under `Enforce`; the security guide has the profile for
  a node that reports to Jarvis.
