# ADR-0009 — Target ROS 2 Jazzy (LTS) first

- **Status**: Accepted (2026-10-14)
- **Date**: 2026-10-13

## Context
We need one primary distro for CI and APIs, while the core stays distro-independent.

## Decision
- **Primary: ROS 2 Jazzy Jalisco** (LTS, Ubuntu 24.04, supported to 2029).
- Simulation (v1 has no real robot): **Gazebo Harmonic + Nav2** TurtleBot 4 simulation, the Jazzy-paired toolchain.
- CI matrix adds the newer LTS (Lyrical) once the runtime is stable. Humble is best-effort: the core is unaffected, and the ROS layer avoids Jazzy-only APIs where cheap.

## Alternatives considered
- **Humble first**: larger installed base today, but EOL in 2027 and older toolchains (GCC 11).
- **Rolling**: newest APIs, but a moving target.

## Consequences
- ➕ Modern toolchain (GCC 13, C++20), long support window.
- ➖ Teams on Humble may need a backport branch for the ROS layer.
