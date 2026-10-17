# ros.action.compute_path_to_pose.aborted: no path to the goal

**Severity** WARN · **Raised by** the action status bridge · **Clears** with the next path it finds

## What it means

The planner server found no path from where the robot is to the goal on the global costmap. When
the navigator then aborts its goal, this fault is muted as a symptom of
`ros.action.navigate_to_pose.aborted`: look at that one first.

## Check

1. Is the goal reachable on the map: not inside an obstacle, not in a closed room?
2. Does the global costmap show something blocking every way (a door closed in the map, an
   inflated obstacle filling a corridor)?
3. Is the robot localized where it really is? A robot the map places inside a wall finds no path.

## Fix

- Choose a reachable goal, or clear what blocks the way.
- Obstacles that are gone but still in the costmap: clear the global costmap.
- Relocalize if the robot is misplaced on the map.

## Escalate when

- The map is wrong (a new wall, a door that changed): someone has to update it.
