# power.battery.low: the battery is low and not charging

**Severity** WARN · **Raised by** the demo's base (`/demo/base`) · **Clears** once the robot charges, or the battery is above 15 %

## What it means

The battery is below 15 % and the robot is off its dock. It still drives, but not for long: past
a few percent it stops where it is, and someone has to push it home.

## Check

1. Did Jarvis already send it to the dock? The rule `battery-low-dock` runs `dock-and-charge`:
   see the Recovery view.
2. `ros2 topic echo /battery_state --once`: `percentage`, and `power_supply_status` (1 = charging).
3. Is the docking server up? `ros2 action info /dock_robot`.

## Fix

- Dock it: `ros2 jarvis recover dock-and-charge -a dock=home` (what the rule does by itself).
- Docking failed with `FAILED_TO_DETECT_DOCK` (904): the dock's marker is hidden or the robot is
  too far off its staging pose. In the demo, `ros2 param set /demo/base inject none`, then dock
  again.

## Escalate when

- Docking failed twice: someone drives the robot to the dock, or plugs it in where it is.
