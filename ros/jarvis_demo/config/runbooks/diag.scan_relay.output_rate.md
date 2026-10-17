# diag.scan_relay.output_rate: the lidar is slow

**Severity** WARN · **Raised by** the diagnostics bridge, from the scan relay's diagnostics · **Clears** when the rate is back to normal

## What it means

The scan relay's own diagnostics (`/diagnostics`, status "output rate") report a scan rate below
what the costmaps expect. Navigation still works, but obstacles update late.

## Check

1. `ros2 topic hz /scan` and `ros2 topic hz /scan_raw`: is the lidar slow, or the relay?
2. The status' values on the fault (its freeze frame): the measured and expected rates.
3. Is the machine overloaded (`top`)?

## Fix

- In the demo, `ros2 param set /demo/scan_relay inject none` ends the slowdown.
- On a robot: the lidar's configuration, its link, or the load on the computer.

## Escalate when

- It stays slow: the lidar may be failing.
