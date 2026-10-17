# ros.action.navigate_to_pose.aborted: Nav2 gave up on a goal

**Severity** ERROR · **Raised by** the action status bridge · **Clears** with the next goal that succeeds

## What it means

`NavigateToPose` aborted the goal after Nav2's own recoveries (spin, back up, clear costmaps)
did not help: the robot could not reach where it was sent. The planner's and controller's aborts
that led to it are muted as symptoms of this fault. Jarvis saved a black-box recording.

## Check

1. Where is the robot, and where was it going? Open the black-box recording in Foxglove (System →
   Recordings lists the incidents each file covers): map, costmaps, scan, the path.
2. Is the way blocked (a person, a pallet, a closed door), or is the goal inside an obstacle?
3. Is localization good? The scan should line up with the map. A `nav.localization.*` fault, or
   the robot jumping on the map, says no.
4. Are the sensors alive? A `ros.topic.stale` on the scan or the camera blinds the costmaps.
5. `bt_navigator`, `planner_server` and `controller_server` logs say which part gave up.

## Fix

- Clear the obstacle, or send the goal again once it's gone.
- Phantom obstacles in the costmap: clear the costmaps (a `clear-costmaps` plan, see the rules
  cookbook) and resend.
- Bad localization: relocalize (set the initial pose, or a `relocalize` plan), then resend.

## Escalate when

- Goals to the same place keep aborting: the map, the goal or the robot's footprint is wrong.
- The robot is stuck and someone has to move it.
