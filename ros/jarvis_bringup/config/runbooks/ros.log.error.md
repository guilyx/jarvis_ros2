# ros.log.error: a node keeps logging errors

**Severity** ERROR · **Raised by** the rosout bridge, one fault per logger · **Clears** 60 s after the last error

## What it means

A node logged errors repeatedly: at least 3 within 10 s (a single stray error stays an event).
The fault's component is the logger's name. Errors are a symptom: the node is telling you what is
wrong, and the cause is usually in the message.

## Check

1. Read the messages: open the fault's incident, or Events filtered on `ros.log.*` and the
   component. The attributes give the source: `log.file`, `log.function`, `log.line`.
2. Look for a fault that started just before on the same component or one it depends on (a
   missing topic, a stale sensor, a node that left the graph): fix that one first.
3. Is the node still working? `ros2 node info /<node>`, `ros2 topic hz` on what it publishes.

## Fix

- Fix the cause the messages point at (a parameter, a device, a dependency).
- If the node is wedged and is a managed node:
  `ros2 jarvis recover restart-lifecycle-node -a node=/<node> -a component=<component>`.
- If it is not managed, restart its process (launch respawn, systemd).

## Escalate when

- The errors come back within the hour after a restart.
- The node is part of navigation or safety, and the robot is in service.
