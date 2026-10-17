# sensor.scan.stale: the lidar stopped publishing

**Severity** ERROR · **Raised by** the demo's scan relay (`/demo/scan_relay`) · **Clears** when scans flow again

## What it means

`/scan` stopped: Nav2's costmaps and collision monitor are blind, so navigation is not safe. The
relay between the simulated lidar (`/scan_raw`) and Nav2 reports it when its output goes quiet.
Jarvis saved a black-box recording.

Two causes look the same from outside: the relay hung (a restart fixes it), or the lidar itself
stopped sending (a restart can't).

## Check

1. Did Jarvis already restart the relay? The rule `lidar-stale-restart` runs
   `restart-lifecycle-node` on it: see the Recovery view, or the incident's timeline.
2. Is the lidar sending? `ros2 topic hz /scan_raw`. Nothing there: the lidar is the problem.
3. `ros2 lifecycle get /demo/scan_relay`: active?

## Fix

- The relay hung: `ros2 jarvis recover restart-lifecycle-node -a node=/demo/scan_relay -a component=lidar -a fault=sensor.scan.stale`
  (what the rule does by itself).
- The lidar stopped: a restart of the relay won't help. In the demo, `ros2 param set
  /demo/scan_relay inject none` brings it back; on a robot, check the lidar's power and cable.

## Escalate when

- Jarvis escalated it (three restarts within ten minutes, `lidar-restarts-exhausted`): someone
  has to look at the lidar.
