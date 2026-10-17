# ros.topic.stale: a watched topic went silent

**Severity** ERROR · **Raised by** the topic watchdog · **Clears** with the next message on the topic

## What it means

A topic the topic watchdog watches got no message for longer than its `stale_after_s`, or lost
its publisher. Whatever consumes it is working on old data or none (a silent scan blinds the
costmaps). Jarvis saved a black-box recording.

The attributes say which and why: `topic`, `silence_ms`, `publishers` (how many), and `reason`:
`no_publisher` (the publisher is gone) or `silent` (it is there but sends nothing).

## Check

1. `reason: no_publisher`: the node that publishes it is gone or was renamed.
   `ros2 topic info -v <topic>` lists publishers; `ros2 node list`; look for `ros.node.missing`
   and `ros.log.fatal` on that node.
2. `reason: silent`: the publisher is up but sends nothing. Is it a managed node that left the
   active state (`ros2 lifecycle get /<node>`, a `ros.lifecycle.inactive` fault)? Is the device
   behind it connected (cable, power, USB, network)? Its logs usually say "timeout" or "no data".
3. `ros2 topic hz <topic>`: is anything arriving now?

## Fix

- A managed driver: restart it.
  `ros2 jarvis recover restart-lifecycle-node -a node=/<driver> -a component=<component> -a fault=ros.topic.stale`
- A plain node: restart its process.
- A device: power-cycle or reconnect it, then restart the driver.

## Escalate when

- It goes silent again within the hour after a restart: the device or its cable is failing.
- The robot is moving and the topic is a safety sensor: stop it first (kill switch:
  `ros2 jarvis mode DISABLED` stops Jarvis' recoveries; your e-stop stops the robot).
