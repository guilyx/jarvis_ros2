# ros.log.fatal: a node logged a fatal error

**Severity** CRITICAL · **Raised by** the rosout bridge · **Clears** 5 min after the last fatal message

## What it means

A node logged at FATAL: it has most likely exited, or is about to. Jarvis saved a black-box
recording (the topics around the moment) with the incident.

## Check

1. Is the node still there? `ros2 node list`. A `ros.node.missing` fault on the same node says
   it is gone (if the node watchdog watches it).
2. Read the fatal message and the errors before it: the incident's timeline, or Events filtered on
   `ros.log.*` and the component. `log.file` and `log.line` point at the code.
3. Read the process' own output: the launch terminal, `journalctl -u <service>`, the container
   logs. A crash (segfault, abort, out of memory) shows there, not on `/rosout`.
4. Download the black-box recording (System → Recordings lists the incidents each file covers)
   and open it in Foxglove to see what the robot saw.

## Fix

- Restart the process once you know why it died; a restart into the same condition dies again.
- If it died on a bad parameter or file, fix that first.

## Escalate when

- Always, if the node is part of navigation, safety or the drive: the robot should not operate
  without it.
- It dies again after a restart.
