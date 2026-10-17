# ros.action.follow_path.aborted: the controller could not follow the path

**Severity** WARN · **Raised by** the action status bridge · **Clears** with the next path it follows to the end

## What it means

The controller server gave up following a path: an obstacle on it, the robot unable to keep up
with it, or the path going where the robot can't. When the navigator then aborts its goal, this
fault is muted as a symptom of `ros.action.navigate_to_pose.aborted`: look at that one first.

## Check

1. Is there a navigator abort in the same incident? Then work on that one.
2. Is something in front of the robot (scan, local costmap)?
3. Is the drive responding? Commands on `/cmd_vel` with no motion (odometry flat) point at the
   motors, the base controller, or an e-stop.
4. `controller_server` logs: "failed to make progress", "collision ahead" say which.

## Fix

- Clear the way; Nav2 replans by itself.
- Phantom obstacles in the local costmap: clear it.
- The drive not responding: check the base and its faults.

## Escalate when

- It aborts on clear floor: the controller's tuning or the robot's footprint is wrong.
