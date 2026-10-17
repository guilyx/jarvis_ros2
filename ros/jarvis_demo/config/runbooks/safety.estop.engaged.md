# safety.estop.engaged: the emergency stop is pressed

**Severity** CRITICAL · **Raised by** the robot's safety node (in the demo, `inject estop`) · **Clears** latched: an operator clears it after checking the area

## What it means

Someone pressed the emergency stop, or the safety system triggered it. The robot does not move.
Every recovery plan that has `faults_inactive: [safety.estop.engaged]` in its preconditions is
refused while it is on, so Jarvis restarts nothing behind a person's back. The fault stays
**latched** after the button is released: a human has to look and clear it.

## Check

1. Why was it pressed? Ask whoever is there; the incident's timeline shows what happened just
   before, and the black-box recording what the robot saw.
2. Is the area clear, and the robot undamaged?
3. Is the button released (the robot's safety status)?

## Fix

- Release the e-stop on the robot, then clear the fault: Faults → **Clear**, with a reason (what
  you checked). It is recorded under your name in the audit trail.
- In the demo: `ros2 run jarvis_demo inject clear`.

## Escalate when

- It was triggered by the safety system and nobody knows why.
- Anyone was hurt or anything damaged: follow your site's incident procedure first.
