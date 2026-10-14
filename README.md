# jarvis_ros2

Jarvis is an event, fault &amp; recovery management system for ROS 2 robots.

- **Record**: every event and fault, with a proper fault lifecycle (debounce, heal, latch, ack).
- **Recover**: automatically, from declarative rules over sequences of events, behind safety gates.
- **Explain**: an agent reads windows of events and produces evidence-backed insights and recovery proposals.
- **Visualize**: a live web dashboard with incident timelines and approvals.

The core is ROS-agnostic; ROS 2 is the communication layer.

## Status

**M1 (record & manage faults) is in.** Next up: M2, rules and automated recovery
([roadmap](docs/design/09-roadmap.md)). The design package starts at
[`docs/design/README.md`](docs/design/README.md).

## Quick start

ROS 2 Jazzy, from the root of this repository:

```bash
rosdep install --from-paths core ros --ignore-src -y
colcon build && source install/setup.bash

ros2 launch jarvis_demo demo.launch.py          # Jarvis + a fake lidar, no simulator needed
ros2 run jarvis_demo inject scan-stall          # in another shell: the lidar stops
ros2 topic echo /jarvis/faults                  # sensor.scan.stale goes PENDING, then ACTIVE
```

Other scenarios: `inject --help`. With Nav2 and Gazebo installed, `nav2_sim.launch.py` runs the same
demo on a simulated TurtleBot 4. Recordings (MCAP, opens in Foxglove) land in
`~/.local/share/jarvis/demo/`.

Reporting from your own node is one line, in C++ or Python:

```cpp
jarvis_reporter::Reporter jarvis(node, "lidar");
jarvis.fault("sensor.lidar.stale", Severity::Error, "no scan for 1.2 s", {{"silence_ms", 1200}});
jarvis.clear("sensor.lidar.stale");
```

Or don't report at all: the bridges turn `/diagnostics`, `/rosout` errors and aborted action goals into
faults on their own.

## Packages

| Package | What it does |
|---------|--------------|
| [`core/jarvis_core`](core/jarvis_core) | ROS-free C++ domain: event hub, fault lifecycle, incidents, catalog |
| [`ros/jarvis_interfaces`](ros/jarvis_interfaces) | Messages and services |
| [`ros/jarvis_ros`](ros/jarvis_ros) | Event hub and fault manager lifecycle components |
| [`ros/jarvis_reporter`](ros/jarvis_reporter) | C++ and Python client libraries for apps |
| [`ros/jarvis_bridges`](ros/jarvis_bridges) | Diagnostics, rosout and action status bridges |
| [`ros/jarvis_bringup`](ros/jarvis_bringup) | Launch, parameters, default fault catalog, recorder and black box |
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
