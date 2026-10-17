# 08 — ROS 2 Practices (commercial-grade checklist)

> **TL;DR** — Target **ROS 2 Jazzy** (LTS, Ubuntu 24.04), simulation-first with Gazebo + Nav2. Lifecycle
> components composed in one container, rosbag2 as the system of record, type adapters to keep the core pure, generated typed parameters, explicit QoS, async
> clients, pluginlib extension points, `ros2 jarvis` CLI, gtest/pytest/launch_testing, and CI with
> sanitizers. No vendor-specific middleware code.

---

| # | Practice | Why | How Jarvis applies it |
|---|----------|-----|-----------------------|
| 1 | **Managed (lifecycle) nodes** | Deterministic bring-up/shutdown, controlled restarts | Every runtime component is a `rclcpp_lifecycle::LifecycleNode`. `on_configure`: load config and restore the state snapshot. `on_activate`: start subs/pubs/servers. `on_deactivate`: stop actuation, drain the queue. `on_error`: go to a safe state (no actuation). An `autostart` param exists for simple setups. |
| 2 | **Composable components** | Deploy as one process (intra-process, zero-copy) or many (isolation, debugging) without code changes | `RCLCPP_COMPONENTS_REGISTER_NODE` for each component. `jarvis.launch.py` loads them into `component_container_isolated` (one thread each; `container:=` picks another) with `use_intra_process_comms: true`. A `split:=true` launch arg runs one process per node. |
| 3 | **Type adaptation (REP-2007)** | Core stays ROS-free. Nodes publish/subscribe domain types directly, with no hand conversion. | `rclcpp::TypeAdapter<jarvis::core::Event, jarvis_interfaces::msg::Event>` (and Fault, FaultArray). With intra-process communication, no conversion happens at all. |
| 4 | **Typed, validated parameters** | Catch bad config at startup; self-documenting | `generate_parameter_library` YAML per component: types, ranges, read-only flags, descriptions. Dynamic updates only where safe (e.g. log level, rate limits). |
| 5 | **Right primitive + explicit QoS** | Correct semantics for streams, state, requests and long jobs | Topics for streams, latched (TRANSIENT_LOCAL) topics for state, services for quick request/response, actions for long-running work. See [03 §9](03-data-model.md#9-communication-patterns) and the [QoS table](03-data-model.md#topics--qos). |
| 6 | **QoS events** | Detect silent or dead publishers in the middleware, not by polling | The topic watchdog uses `deadline` and `liveliness` QoS event callbacks where the publisher supports them, and falls back to a rate estimator. |
| 7 | **Callback groups + executors** | No data races; no executor starvation | Core input: one MutuallyExclusive group. Actuator clients: Reentrant group. State snapshot writer: own thread, debounced. No blocking in callbacks. |
| 8 | **Async service/action clients with timeouts** | A hung service must never stall Jarvis | Actuators use `async_send_request` and `async_send_goal`, with deadlines driven by the core's timers. |
| 9 | **Actions for long-running work** | Feedback + cancellation | `ExecuteRecovery`, `Analyze`. Services only for short request/response. |
| 10 | **Node clock everywhere** | `use_sim_time`, deterministic tests | The core gets an `IClock` port. The ROS adapter wraps `node->get_clock()`. Tests use `FakeClock`. |
| 11 | **Lifecycle-aware publishers** | Nothing is published while inactive | `rclcpp_lifecycle::LifecyclePublisher` for all outputs. |
| 12 | **Self-diagnostics** | Jarvis is observable with standard tools (`rqt_robot_monitor`, aggregator) | `diagnostic_updater` per component (hardware_id `jarvis`): ingest rate, ring-buffer fill, drops, recorder health, rule latency, running plans. The diagnostics bridge ignores `hardware_id == jarvis`, so there's no feedback loop. The fault manager also **exports active faults on `/diagnostics`**, so standard tools show them. |
| 13 | **pluginlib extension points** | Customers extend without forking | `jarvis_ros::Actuator` (custom recovery actions) and `jarvis_bridges::Bridge` (custom event sources), registered in plugin XML. |
| 14 | **ros2cli extension** | Ergonomic ops for engineers | `jarvis_cli` adds `ros2 jarvis events --follow`, `faults`, `ack`, `clear`, `recover`, `mode`, `rules`, `lint`, `backtest`. |
| 15 | **Namespacing & remapping** | Multi-robot, no hard-coded names | All names are relative or `~/`; everything sits under the `jarvis` namespace and can be pushed under a robot namespace. |
| 16 | **rosbag2 as the system of record** | Standard format and tooling (Foxglove, PlotJuggler, `ros2 bag`); events and sensor data on one timeline | `rosbag2_transport::Recorder` loaded as a **component** in the Jarvis container records `/jarvis/**` continuously to MCAP (zstd, 5-min splits, QoS overrides matching publishers). The lake listens to `events/write_split`. A second recorder in **snapshot mode** is the black box. |
| 17 | **Interfaces in a separate package** | Stable API, light dependencies for clients | `jarvis_interfaces` only. Reporter libs depend on it, not on the runtime. Breaking msg changes require a major version bump. |
| 18 | **Middleware agnostic** | Fast DDS, Cyclone DDS, rmw_zenoh | No vendor QoS XML required. CI runs the e2e test on two RMWs. |
| 19 | **Security** | Production deployments | Compatible with SROS2 enclaves (`/jarvis/*`). Gateway auth/roles. LLM redaction. |
| 20 | **Package hygiene** | Release via bloom / rosdep | `package.xml` format 3, rosdep keys only, `ament_cmake` / `ament_python` / plain `cmake` for the core, exported CMake targets, `ament_export_*`. |
| 21 | **Linting & formatting** | Consistent quality | `ament_lint_auto`: clang-format, clang-tidy, cppcheck, xmllint, copyright, cmake-lint. Python: ruff + mypy (strict). TS: ESLint + Prettier + `tsc --noEmit`. All in `pre-commit`. |
| 22 | **Testing pyramid** | Confidence without slow suites | gtest (core, deterministic, sanitizers). pytest (agent/gateway). `launch_testing` (ROS e2e with `jarvis_demo`). Playwright (UI). Benchmarks (Google Benchmark). |
| 23 | **CI** | Every PR proves itself | GitHub Actions: `ros-tooling/setup-ros` + `action-ros-ci` (Jazzy), a ROS-free core job, ASan/UBSan job, coverage, docs link check, Docker image build. |
| 24 | **Containers & dev env** | Reproducible builds, easy onboarding | Multi-stage Dockerfile (dev, runtime, slim). `.devcontainer/` with the same image. |
| 25 | **Quality declaration** | Commercial trust | Target REP-2004 Quality Level 2 for `jarvis_core` / `jarvis_interfaces` (versioning, docs, tests, coverage ≥ 90 %). |
| 26 | **Reuse what every node already exposes** | Zero-code adoption | Bridges for `/diagnostics`, `/rosout`, lifecycle `transition_event`, and **action goal status** (`*/_action/status`, e.g. Nav2 `NavigateToPose` ABORTED; idea from ros2_medkit). |
| 27 | **Simulation-first** | Reproducible demos and e2e tests | Gazebo (Harmonic) + Nav2 TurtleBot 4 simulation, `use_sim_time` everywhere, a fault injector node, and launch_testing scenarios on top. |

### What Jarvis deliberately avoids

- Blocking calls inside callbacks (`spin_until_future_complete` in a callback).
- Global singletons and static node handles.
- Wall-clock time in decision logic.
- Business logic in launch files. Launch only wires things; config lives in YAML.
- Hidden middleware tuning required for correctness.
