# ros.lifecycle.inactive: a managed node left the active state

**Severity** WARN · **Raised by** the lifecycle bridge · **Clears** when the node is active again

## What it means

A managed (lifecycle) node has been out of `active` for over 5 s: it no longer does its work. A
deliberate restart goes through inactive faster than that and raises nothing. The lifecycle
bridge also records each state as `ros.lifecycle.<node>.state`.

## Check

1. `ros2 lifecycle get /<node>`: where is it now?
2. Who moved it? A lifecycle manager (Nav2's pauses its nodes), an operator, or a Jarvis
   recovery (Recovery view, or the incident's timeline). An intended pause needs nothing.
3. Its logs around the transition.

## Fix

- `ros2 lifecycle set /<node> activate` if it is only deactivated.
- Or the plan: `ros2 jarvis recover restart-lifecycle-node -a node=/<node> -a component=<component>`
  walks it back to active from wherever it is.

## Escalate when

- It drops out of active again after being restored.
