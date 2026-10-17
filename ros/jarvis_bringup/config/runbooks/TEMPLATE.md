# <fault.code>: <what happened, in a few words>

**Severity** ERROR · **Raised by** <the bridge, or your node's reporter> · **Clears** <when it heals on its own; or "latched: an operator clears it">

> A runbook is for the person on call at 3 a.m.: what the fault means, what to look at, what
> fixes it, and when to stop and call someone. Commands to run, names of nodes and topics, what
> "good" looks like. Delete this note in yours.

## What it means

One or two sentences: what stopped working, and what the robot can't do because of it.

## Check

1. The first thing to look at, with the command or the dashboard view.
2. The next most likely cause.

## Fix

- What fixes it, from the cheapest to the most invasive. Name the recovery plan if one exists:
  `ros2 jarvis recover <plan> -a key=value`.

## Escalate when

- It comes back within the hour after a fix, or the fix above did not work.
- Who to call, and what to send them (the incident link, the black-box recording).
