# ros.topic.qos_incompatible: a subscription gets nothing, its QoS can't match the publisher's

**Severity** ERROR · **Raised by** the QoS watchdog · **Clears** once every publisher and subscription of the topic can match

## What it means

A publisher and a subscription of the same topic ask for QoS that cannot match, so DDS never
connects them: the subscription receives nothing, and ROS logs nothing about it. The classic case
is a sensor driver publishing best effort (`SensorDataQoS`) and a node subscribing reliable (the
default). The node then works on no data: a costmap without scans, a map without odometry.

The attributes say where: `topic`, `publisher` and `subscription` (node names), `reason` (rmw's own
words, e.g. "best effort publisher and reliable subscription") and `pairs` (how many pairs of the
topic cannot match).

## Check

1. `ros2 topic info -v <topic>`: the QoS of every publisher and subscription. Compare reliability,
   durability, deadline and liveliness.
2. Which one changed? A driver update, a new parameter file (`qos_overrides.<topic>.subscription...`),
   a node built against another default.

## Fix

- Make the subscription ask for no more than the publisher offers: subscribe best effort to a
  best-effort sensor (`rclcpp::SensorDataQoS()`), volatile to a volatile publisher. A subscription
  asking for less always matches.
- Or raise the publisher (reliable, transient local) if every subscriber needs it, and the link can
  carry it.
- Nodes that accept QoS overrides take them from parameters, without a rebuild:
  `qos_overrides./<topic>.subscription.reliability: best_effort`.

## Escalate when

- The topic is a safety input (scan, e-stop, bumper): the robot is blind to it until fixed. Stop it.
- It appeared after an update you didn't make: whoever shipped it should know.
