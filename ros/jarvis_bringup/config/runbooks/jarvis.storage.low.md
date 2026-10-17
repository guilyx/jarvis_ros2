# jarvis.storage.low: the data disk is nearly full

**Severity** ERROR · **Raised by** the lake · **Clears** when the disk has room again

## What it means

The disk under Jarvis' data directory is below its free-space floor (`lake_min_free_gib`, 1 GiB
by default), and retention already deleted all it may: converted recordings, black-box
snapshots, old days of Parquet. New recordings, and with them the history of what happens next,
are at risk. Recordings not yet converted are never deleted: they are the only copy.

## Check

1. `df -h ~/.local/share/jarvis` (or your `data_dir`): how much is free, on which file system.
2. What uses it: `du -sh ~/.local/share/jarvis/*`, and everything else on that disk (logs, other
   bags, core dumps, Docker images).
3. `jarvis-lake status`: pending or failed splits, the lake's size, its last error. A split that
   keeps failing to convert can't be deleted and grows the backlog.

## Fix

- Free space that isn't Jarvis': old logs, bags recorded by hand, images.
- Convert what is pending (`jarvis-lake sync`); the lake then deletes the converted recordings.
- Lower the budgets: `jarvis-lake --bags-max-gib ... --lake-max-gib ... --blackbox-max-gib ... run`
  (the launch file sets only the floor; see the integration guide, "Recordings and the lake").
- A bigger disk, or a `data_dir` on one.
- The fault clears by itself at the lake's next pass once there is room.

## Escalate when

- The disk fills again within days: what is recorded (`record_extra_topics`, the black box) does
  not fit the robot.
