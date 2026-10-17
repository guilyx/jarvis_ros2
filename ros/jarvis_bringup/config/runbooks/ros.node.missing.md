# ros.node.missing: an expected node is not on the graph

**Severity** ERROR · **Raised by** the node watchdog · **Clears** when the node is back

## What it means

A node the node watchdog expects has not been on the ROS graph for longer than its grace period
(5 s by default): it crashed, never started, or can't be discovered. The attributes give `node`
and `missing_ms`. Jarvis saved a black-box recording.

## Check

1. `ros2 node list`: is it really gone? If it shows here but Jarvis says missing, discovery is
   failing between the two machines (`ROS_DOMAIN_ID`, the DDS configuration, the network).
2. A `ros.log.fatal` just before on the same node says why it died.
3. The process' own output: the launch terminal, `journalctl -u <service>`, the container logs.

## Fix

- Restart its process (launch respawn, systemd, the container), once you know why it stopped.
- Discovery: same `ROS_DOMAIN_ID` and DDS profile on both sides, the network reachable.

## Escalate when

- It dies again after a restart.
- It is part of navigation, safety or the drive, and the robot is in service.
