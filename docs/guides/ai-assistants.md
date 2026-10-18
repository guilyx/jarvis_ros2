# Jarvis for AI assistants (MCP)

`jarvis-mcp` lets an AI assistant (Claude Code, Claude Desktop, Cursor, or any client of the
[Model Context Protocol](https://modelcontextprotocol.io)) ask a robot what is wrong with it. It
reads what Jarvis knows: the open faults and their runbooks, incidents, the event stream, what
recovery did, the history. The assistant can ask the robot's own agent, and, if you allow it,
request a recovery, through the same gates as anyone.

1. [How it works](#1-how-it-works)
2. [Install](#2-install)
3. [Connect an assistant](#3-connect-an-assistant)
4. [The tools](#4-the-tools)
5. [Safety](#5-safety)

## 1. How it works

```
assistant ──stdio (MCP)──> jarvis-mcp ──HTTPS + token──> gateway ──> Jarvis on the robot
```

`jarvis-mcp` runs next to the assistant (your laptop, a CI runner) and talks to the robot's
[gateway](operator.md) with a token, like the dashboard. So the gateway's roles apply, and what the
assistant does is recorded in the audit trail under the token's name. Nothing on the robot changes:
no new port, no new process. It is the Python standard library only, without ROS.

## 2. Install

From a clone of this repository, anywhere Python 3.10+ runs:

```bash
pip install ./agent/jarvis_mcp      # the jarvis-mcp command
```

In a ROS workspace it builds with the rest (`colcon build`), and runs as
`ros2 run jarvis_mcp jarvis-mcp`.

Then give it a token from the gateway (the robot's `gateway.yaml`, or `$JARVIS_GATEWAY_TOKENS`;
[security guide](security.md)): a **viewer** token to read, an **operator** token to ask the agent
or request recoveries. Check it:

```bash
JARVIS_URL=https://robot.local:8443 JARVIS_TOKEN=... jarvis-mcp --check
# https://robot.local:8443: signed in as claude-desk (viewer)
```

## 3. Connect an assistant

The token goes in an environment variable (`JARVIS_TOKEN`, or the one `--token-env` names), never
on the command line.

**Claude Code:**

```bash
claude mcp add jarvis -e JARVIS_URL=https://robot.local:8443 -e JARVIS_TOKEN=... -- jarvis-mcp
```

**Claude Desktop** (`claude_desktop_config.json`), **Cursor** (`.cursor/mcp.json`) and most other
clients:

```json
{
  "mcpServers": {
    "jarvis": {
      "command": "jarvis-mcp",
      "env": { "JARVIS_URL": "https://robot.local:8443", "JARVIS_TOKEN": "..." }
    }
  }
}
```

Options: `--allow-recovery` (see [Safety](#5-safety)), `--ca-file` for a gateway with a private
CA. One server per robot: name them `jarvis-<robot>`.

Then ask: *"What's wrong with the robot?"*, *"Why did the lidar restart fail last night?"*, *"Which
faults flap the most this week, and what do their runbooks say?"*

## 4. The tools

| Tool | What the assistant gets | Role |
|------|-------------------------|------|
| `jarvis_status` | Health, recovery mode, open faults by severity, open incidents, recoveries running or waiting for approval | viewer |
| `list_faults`, `get_fault` | Open faults (or past episodes): code, component, severity, root cause and symptoms; one fault's freeze frame, transitions and recoveries | viewer |
| `get_runbook` | What your team wrote to check and do about a fault code | viewer |
| `list_incidents`, `get_incident` | Incidents with their faults, recoveries, the agent's analysis and what people did | viewer |
| `search_events` | The event stream, by incident (`correlation_id`), code, kind, severity, component, time or text | viewer |
| `list_recoveries`, `list_plans` | What ran, what waits for approval and why; the plans the robot has | viewer |
| `analytics` | Health per component, Pareto of fault codes, flapping faults, how well each plan works | viewer |
| `ask_agent` | The robot's agent analyses an incident or answers a question, with the evidence for each claim | operator |
| `request_recovery` | Asks for a recovery plan (only with `--allow-recovery`) | operator |

Read tools are marked read-only (`readOnlyHint`): clients that honour the hint can run them
without asking you each time. Long results are cut, saying so, and the assistant narrows its query.

## 5. Safety

- **Read-only by default.** Without `--allow-recovery`, the assistant can't change anything on the
  robot: no acknowledging, clearing, approving or mode changes, ever. Those stay with people.
- **The token decides.** A viewer token can't ask the agent or request anything, whatever the
  assistant tries: the gateway refuses (and the assistant is told why).
- **A recovery request is only a request.** With `--allow-recovery` and an operator token,
  `request_recovery` goes through every gate of the orchestrator, like a rule or a person: the
  recovery mode, preconditions (never with the e-stop on), rate limits, locks, approval. It is a
  **dry run** unless the assistant explicitly asks otherwise. A plan that needs approval waits for
  an operator, and four-eyes applies: the person who owns the token can't approve it either.
- **Attributed.** Every request is recorded under the token's name, with the assistant's rationale.
  Give each assistant its own token (`claude-alice`, not `alice`) so the audit trail tells them
  apart.
- **What leaves the robot**: what the tools return, to wherever the assistant sends it (its model
  provider). The agent's redaction applies to `ask_agent`, not to raw events and faults: use a
  token on a gateway you'd show that data to, or keep the assistant on a local model.
