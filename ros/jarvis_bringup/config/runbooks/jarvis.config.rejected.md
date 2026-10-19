# jarvis.config.rejected: the fleet's rules and plans were refused

**Severity** WARN · **Raised by** config_sync · **Clears** when the robot runs what the fleet gives

## What it means

The robot takes its rules and plans from the fleet's storage (`config_from`). The revision there
now was refused: it fails `jarvis lint` (a rule asks for a plan that does not exist, a plan uses an
action the robot has not, a file is not valid YAML), or the recovery orchestrator or the rule
engine would not load it (an actuator's allowlist refuses a target, say). The robot keeps running
the revision it had, and does not try the refused one again until the source changes.

## Check

1. The fault's message has the first error, the refused revision and the one the robot runs; the
   dashboard's System page and `<data_dir>/config/status.json` have every error.
2. The files themselves: `<data_dir>/config/versions/<refused revision>/` holds what the robot
   fetched; `jarvis lint --rules <that>/rules --plans <that>/plans --catalog <catalog>` says the
   same as the robot.
3. A robot-specific file (`<source>/robots/<robot>/...`) replaces the shared one of the same
   name: one robot refusing what the others take is usually one of those.

## Fix

- Fix the files at the source (lint them before uploading: the same command as above). The robot
  takes the new revision on its next look (`config_period_s`, 60 s by default), and the fault
  clears.
- Or put back the previous files: the robot is then in sync with what it runs, and the fault
  clears.

## Escalate when

- The status reports that the revision before could not be loaded again: the robot's rules and
  plans may be half of each. Restart Jarvis on that robot (it loads `<data_dir>/config/active`).
