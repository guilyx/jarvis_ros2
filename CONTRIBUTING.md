# Contributing to Jarvis

Start with the design package: [`docs/design/README.md`](docs/design/README.md). Changing a decision
means a new ADR in [`docs/adr/`](docs/adr/README.md) that supersedes the old one.

## Layout

| Path | What | Build |
|------|------|-------|
| `core/jarvis_core` | ROS-free C++20 core (event hub, fault manager, incidents) | plain CMake, also via colcon |
| `ros/jarvis_interfaces` | Messages and services | colcon |
| `ros/jarvis_ros` | Lifecycle components (event hub, fault manager) | colcon |
| `schemas/` | Canonical JSON Schemas + contract tests | pytest |
| `docs/` | Design, ADRs | — |

## Set up

**Option A: devcontainer (recommended).** Open the repo in VS Code and "Reopen in Container". It
builds `docker/Dockerfile` (target `dev`, ROS 2 Jazzy) and runs `colcon build`.

**Option B: local ROS 2 Jazzy** (Ubuntu 24.04):

```bash
source /opt/ros/jazzy/setup.bash
rosdep install --from-paths core ros --ignore-src -y
colcon build --symlink-install
```

**Option C: no apt access to packages.ros.org.** Use the [RoboStack](https://robostack.github.io)
conda packages:

```bash
micromamba create -p ./.ros -c robostack-jazzy -c conda-forge python=3.12 ros-jazzy-ros-base \
  ros-jazzy-rclcpp-lifecycle ros-jazzy-rclcpp-components ros-jazzy-diagnostic-updater \
  ros-jazzy-rosbag2 ros-jazzy-launch-testing-ament-cmake colcon-common-extensions \
  cxx-compiler cmake ninja yaml-cpp nlohmann_json gtest gmock
micromamba activate ./.ros
colcon build
```

## Build and test

```bash
# Everything (what CI's "ROS 2 Jazzy" job runs)
colcon build && colcon test --return-code-on-test-failure && colcon test-result --verbose

# Just the core, without ROS, with sanitizers (CI's "Core" job)
cmake -S core/jarvis_core -B build/core -G Ninja -DJARVIS_SANITIZE=ON -DJARVIS_WERROR=ON
cmake --build build/core && ctest --test-dir build/core --output-on-failure

# Coverage, as CI reports it: add -DJARVIS_COVERAGE=ON to the core's cmake line, then
gcovr -r core/jarvis_core --filter core/jarvis_core/src/ --filter core/jarvis_core/include/ build/core
PYTHONPATH=lake/jarvis_lake pytest -q --cov=jarvis_lake lake/jarvis_lake/test  # same for the others

# Contracts
pytest -q schemas/tests
```

Launch tests start real Jarvis nodes with the usual names, and colcon tests packages in parallel, so
each launch test runs in its own ROS domain: `add_launch_test(... ENV ROS_DOMAIN_ID=<n>)`. Domains 61
to 64 are taken (`jarvis_ros`, `jarvis_bridges`, `jarvis_bringup`, `jarvis_demo`); a new launch test
takes the next free one.

Launch tests wait for outcomes (`keep_trying` / `wait_for`) instead of sleeping: discovery and
coalesced writes take a variable time, and fixed sleeps are what made the first CI run flaky.

## Style

- C++: `.clang-format` (the ament settings). Python: `ruff` (`ruff.toml`).
- `pre-commit install` runs both, plus the "core stays ROS-free" check, on every commit.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org):
  `feat(core): …`, `fix(ros): …`, `docs: …`.

## Definition of done (every PR)

- [ ] CI green: lint, core with sanitizers, schemas, Jazzy build and tests
- [ ] New behavior is tested at the lowest layer that can test it (core first)
- [ ] Docs and ADRs updated if behavior or contracts changed
- [ ] No `rclcpp` / `rclpy` in `core/`
- [ ] Schema, C++ codec and `.msg` stay in sync (contract tests pass)

## License

Jarvis is under the [Apache License 2.0](LICENSE). By opening a pull request you agree that your
contribution is licensed under it too. New source files start with the two header lines the
others have (`Copyright 2026 <you>`, `SPDX-License-Identifier: Apache-2.0`).

## Releases and changelogs

Every package has one version (`scripts/version.py` prints it; CI checks they agree). A change
worth telling users about adds a line under `Forthcoming` in [CHANGELOG.md](CHANGELOG.md) and in
the `CHANGELOG.rst` of the packages it touches. How a release is cut: [RELEASING.md](RELEASING.md).
Security issues: [SECURITY.md](SECURITY.md), not a public issue.
