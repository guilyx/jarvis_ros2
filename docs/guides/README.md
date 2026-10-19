# Guides

| Guide | For |
|-------|-----|
| [Putting Jarvis on your robot](integration.md) | Integrators: reporting from your nodes, the bridges, the fault catalog and runbooks, launching it with your robot, recordings |
| [Rules and recovery cookbook](rules-cookbook.md) | Whoever writes rules and plans: recipes linted in CI, and the gotchas |
| [Running robots with Jarvis](operator.md) | The people on shift: the dashboard, faults, incidents, approvals, the kill switch |
| [Securing Jarvis](security.md) | Production: SROS2, gateway TLS and single sign-on, what reaches a model |
| [Jarvis for AI assistants](ai-assistants.md) | Claude Code, Claude Desktop, Cursor: asking the robot what is wrong, over MCP |
| [Drivers in containers and systemd units](containers.md) | Drivers that aren't lifecycle nodes: Jarvis restarts their container or unit, only those you allow |
| [Devices that don't run ROS (MQTT)](mqtt.md) | A BMS, a charger, a PLC: faults and states over MQTT, liveness from their last will, Jarvis's open faults back |
| [Watching a fleet](fleet.md) | Several robots in one dashboard: each robot's gateway, side by side, the ones that need someone first |
| [Jarvis for VDA 5050 fleet managers](vda5050.md) | AMR fleets: Jarvis's open faults as the `errors` of the robot's VDA 5050 state |
| [Jarvis for SOVD clients](sovd.md) | The ros2_medkit web UI, its Foxglove panel and other SOVD clients: Jarvis's faults on a SOVD-style API |

The design behind them: [docs/design](../design/README.md).
