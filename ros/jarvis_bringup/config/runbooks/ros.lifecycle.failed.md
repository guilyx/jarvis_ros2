# ros.lifecycle.failed: a managed node's transition failed

**Severity** ERROR · **Raised by** the lifecycle bridge · **Clears** when the node is active again

## What it means

A lifecycle transition of a managed node failed (it went through error processing): the node
could not configure, activate or clean up. It is not doing its work.

## Check

1. The node's logs at the time of the transition: the reason is there (a missing parameter, a
   device that would not open, a dependency not up).
2. `ros2 lifecycle get /<node>`: `unconfigured` or `finalized` after an error.
3. What it depends on: its devices, the topics and services it needs at configure.

## Fix

- Fix the cause, then bring it back:
  `ros2 jarvis recover restart-lifecycle-node -a node=/<node> -a component=<component>`.
- A `finalized` node can't come back by transitions: restart its process.

## Escalate when

- It fails again with the same reason: the configuration or the device needs a person.
