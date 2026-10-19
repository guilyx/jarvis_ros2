# jarvis.upload.behind: the history is not leaving the robot

**Severity** WARN · **Raised by** the lake · **Clears** when what waited is uploaded

## What it means

The lake uploads its Parquet (and, if asked, the black box's snapshots) to the fleet's storage
(`lake_upload_to`). Files have now waited longer than the upload's max lag
(`lake_upload_max_lag_hours`, 24 h by default): the target is unreachable, or refuses them. The
robot keeps working and keeps its history; retention keeps what waits, until the disk runs short
(then `jarvis.storage.low`, and what had to go before it was uploaded is counted as lost).

## Check

1. `ros2 run jarvis_lake jarvis-lake --upload-to <target> status`: the `upload` section has how many files wait,
   since when, and the last error (also on the dashboard's System page).
2. The network from the robot to the target: `curl -sI https://<endpoint>` (or `ls` the share).
3. The credentials the lake runs with: `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (or the
   AWS config file, an instance role), the bucket name and its region, the policy allowing
   `s3:PutObject` under `<prefix>/robots/<robot>/`.

## Fix

- Restore the network or the credentials: the lake tries again by itself (every 30 s, then up to
  every 10 min), oldest files first, and the fault clears once they are up.
- Push the backlog now, e.g. while the robot is docked on a good network:
  `ros2 run jarvis_lake jarvis-lake --upload-to <target> --robot <robot_id> upload`.
- A robot that only uploads when docked overnight: raise `lake_upload_max_lag_hours` above its
  longest shift.

## Escalate when

- The lake's status counts files `lost`: the disk filled before they could go up. The history of
  those hours exists only on this robot's disk, if at all.
