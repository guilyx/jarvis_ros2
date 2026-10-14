# jarvis_ros2

Jarvis is an event, fault &amp; recovery management system for ROS 2 robots.

- **Record**: every event and fault, with a proper fault lifecycle (debounce, heal, latch, ack).
- **Recover**: automatically, from declarative rules over sequences of events, behind safety gates.
- **Explain**: an agent reads windows of events and produces evidence-backed insights and recovery proposals.
- **Visualize**: a live web dashboard with incident timelines and approvals.

The core is ROS-agnostic; ROS 2 is the communication layer.

## Status

🚧 **Design accepted, M1 in progress.** Start with the design package: [`docs/design/README.md`](docs/design/README.md).

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
