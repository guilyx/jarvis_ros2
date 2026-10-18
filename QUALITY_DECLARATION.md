# Quality declaration

This declares the quality of the Jarvis packages against the ROS 2 quality categories of
[REP-2004](https://www.ros.org/reps/rep-2004.html). It covers every package in this repository:

| Package | Kind |
|---------|------|
| `jarvis_core` | C++ library and tools, no ROS dependency |
| `jarvis_interfaces` | messages, services, actions |
| `jarvis_ros`, `jarvis_bridges`, `jarvis_nav2` | C++ ROS components and plugins |
| `jarvis_reporter` | C++ header and Python client library |
| `jarvis_medkit` | Python ROS node: ros2_medkit's faults into Jarvis (optional) |
| `jarvis_cli`, `jarvis_bringup`, `jarvis_demo` | `ros2 jarvis`, launch and configuration, the demo |
| `jarvis_lake`, `jarvis_gateway`, `jarvis_agent` | Python services |
| `jarvis_mcp` | Python client of the gateway (MCP server for AI assistants), standard library only |

**Claimed level: 3.** Most Level 1 requirements are met; the gaps are listed under each section
and summarized at the end. A package moves up once its gaps close.

## 1. Version policy

- [x] **Semantic versioning.** Versions follow [semver](https://semver.org). Every package of the
  repository has the same version, set by `scripts/version.py` and checked in CI.
- [x] **Version ≥ 1.0.0 for a stable API**: the first release will be 1.0.0 (RELEASING.md).
- [x] **Public API.** The public API is: the messages, services and actions of
  `jarvis_interfaces`; the event, fault, execution and insight schemas (`schemas/`); the rule,
  plan and catalog languages; the gateway's REST API and live stream (`/api/v1`, OpenAPI at
  `/docs`); the reporter libraries; the `ros2 jarvis` and `jarvis` command lines; the launch
  arguments of `jarvis.launch.py`.
- [x] **API and ABI stability policy.** A breaking change to the public API is a new major
  version. `jarvis_interfaces` is versioned with the rest: a message change that breaks
  compatibility is a major version (08-ros2-practices §17). Within a major version, ABI is not
  guaranteed for the C++ libraries; rebuild against the version you run.
- [x] **Changes within a ROS distribution.** Releases into a ROS distribution keep the public API
  and its behaviour; a breaking change waits for a new major version.

## 2. Change control

- [x] **Change requests.** Every change goes through a pull request.
- [x] **Contributor origin.** Commits carry their author; contributions are accepted under the
  repository's license (CONTRIBUTING.md, "License").
- [ ] **Peer review.** Pull requests merge once CI is green; a second person's review is not
  required yet. Required reviews on `main` are the step to Level 1.
- [x] **Continuous integration.** Every pull request and every push to `main` runs CI: lint, the
  core with sanitizers, the schemas, each Python service, the dashboard (unit and end-to-end),
  the full ROS build and tests, and the Docker demo with an end-to-end run against it.
  Nightly: a 15-minute soak at 500 events/s and the agent's evaluation.
- [x] **Documentation policy.** A change that alters behaviour updates the documentation in the
  same pull request; the guides' examples are tested (the rules cookbook is linted in CI).

## 3. Documentation

- [x] **Feature documentation**: the [design package](docs/design/README.md), the
  [guides](docs/guides/README.md) (integration, rules cookbook, operator, security) and the
  README.
- [x] **Public API documentation**: the interfaces are commented `.msg`/`.srv`/`.action` files;
  the JSON schemas; the REST API's OpenAPI document; the languages in
  [05 · Rules and recovery](docs/design/05-rules-and-recovery.md) and
  [03 · Data model](docs/design/03-data-model.md).
- [ ] **Generated C++ API reference** (Doxygen or similar): not published yet. The public C++
  headers are commented.
- [x] **License**: Apache-2.0 ([LICENSE](LICENSE)), stated in every `package.xml` and in each
  source file's SPDX header.
- [x] **Copyright statements**: in every source file.
- [x] **Quality declaration**: this document.

## 4. Testing

- [x] **Feature testing.** Each feature has tests: unit (gtest, pytest, vitest), component and
  launch tests (`launch_testing`) on the running stack, end-to-end tests of the dashboard
  (Playwright) on an in-memory Jarvis and on the Docker demo, and tests of the documented
  examples.
- [x] **Public API testing.** The interfaces, the API (every endpoint, roles included), the
  languages (parser, linter, the design document's and the cookbook's examples) and the CLIs are
  tested.
- [x] **Coverage, measured.** CI reports line coverage on every change, in each job's summary:
  the core (gcov, over its unit tests) and the lake, the gateway and the agent (pytest-cov, over
  their ROS-free tests). When it was first measured: core 92 %, lake 80 %, gateway 81 %, agent
  89 %. The Python services' ROS adapters count as uncovered there: the ROS job's tests exercise
  them, and that job is not measured.
- [x] **A coverage threshold.** CI fails when line coverage drops under a floor about 2 points
  below those figures: core 90 %, lake 78 %, gateway 79 %, agent 87 %. Raise a floor when tests
  raise its figure.
- [x] **Performance.** `jarvis-bench` (the core) and `jarvis_load` (the stack) check the targets
  of 01-requirements §5 in CI; the nightly soak checks memory and latency over time; the results
  are in 01-requirements ("Measured").
- [x] **Linters and static analysis.** clang-format, ruff (lint and format), ESLint and
  Prettier, shellcheck; the core runs its tests under AddressSanitizer and
  UndefinedBehaviorSanitizer.
- [ ] **Static analysis beyond sanitizers** (clang-tidy, CodeQL): not in CI yet.
- [x] **Crash and security tests.** A SIGKILL durability test, disk-pressure tests, and the stack
  with SROS2 security enforced (01-requirements, "Measured (5.3)" and "(5.4)").

## 5. Dependencies

- [x] **Direct ROS dependencies** are released ROS 2 Jazzy packages (rclcpp, rclpy, rosbag2,
  lifecycle, Nav2's interfaces, sros2 ...).
- [x] **Non-ROS C++ dependencies**: yaml-cpp and nlohmann-json, through their rosdep keys.
- [ ] **Non-ROS Python dependencies.** The Python services pin their dependencies by version in
  `requirements.txt` (the agent's model client, LiteLLM, is hash-locked). Several (DuckDB, MCAP,
  pinned FastAPI and pyarrow, LiteLLM) have no rosdep key, so a `.deb` of those three packages
  would not bring them: install them with pip, or use the Docker image, which has everything.
- [x] **The dashboard's** npm dependencies are locked (`package-lock.json`) and only its built
  bundle reaches the robot.

## 6. Platform support

- [x] **Tier 1 of ROS 2 Jazzy**: Ubuntu 24.04 on amd64, tested in CI on every change.
- [ ] **arm64** (most robot computers): builds, but not tested in CI yet.
- [ ] **A second RMW in CI**: CI runs Fast DDS (the default); the SROS2 test also passes on
  Cyclone DDS, outside CI.

## 7. Security

- [x] **Vulnerability disclosure policy**: [SECURITY.md](SECURITY.md) (REP-2006).
- [x] **Security features**: an SROS2 profile with one enclave per process, the gateway's roles,
  TLS and single sign-on, redaction and secret scrubbing for the agent
  ([security guide](docs/guides/security.md)).

## Gaps to Level 1

1. Required peer review on `main`.
2. A generated C++ API reference.
3. clang-tidy or CodeQL in CI.
4. rosdep keys (or vendored packages) for the Python services' dependencies.
5. arm64 and a second RMW in CI.
