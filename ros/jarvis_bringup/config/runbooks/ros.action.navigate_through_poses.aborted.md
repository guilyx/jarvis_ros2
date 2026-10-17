# ros.action.navigate_through_poses.aborted: Nav2 gave up on a route

**Severity** ERROR · **Raised by** the action status bridge · **Clears** with the next route that succeeds

## What it means

`NavigateThroughPoses` aborted a route after Nav2's own recoveries: the robot could not get
through its waypoints. The planner's and controller's aborts that led to it are muted as symptoms.
Jarvis saved a black-box recording.

## Check

1. Which waypoint was it going to? The black-box recording shows the path, the costmaps and
   the scan: System → Recordings, then open it in Foxglove.
2. Is the way blocked, or is a waypoint inside an obstacle or off the map?
3. Is localization good (the scan lines up with the map)?
4. The `bt_navigator` and `controller_server` logs say which part gave up.

## Fix

- Clear the obstacle and send the route again.
- A waypoint in a bad spot: fix the route.
- Phantom obstacles: clear the costmaps and resend. Bad localization: relocalize first.

## Escalate when

- The same route keeps failing at the same place.
- The robot is stuck and someone has to move it.
