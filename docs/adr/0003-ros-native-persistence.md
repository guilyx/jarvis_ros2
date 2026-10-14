# ADR-0003 — ROS-native persistence: rosbag2 (MCAP) as the record, Parquet + DuckDB for history

- **Status**: Accepted (2026-10-14)
- **Date**: 2026-10-13

## Context
The first draft stored events in SQLite with the recorder as the single writer. Review feedback: make the system ROS-native. Publish everything on the graph with the right primitive, record it with rosbag2, and convert recordings to Parquet or a database for history.

## Decision
- **Everything is on the graph**: `/jarvis/events` (single publisher: the event hub), latched `/jarvis/faults` and `/jarvis/executions`, plus services and actions for commands.
- **rosbag2 is the system of record**: a `rosbag2_transport::Recorder` component in the Jarvis container records `/jarvis/**` continuously to MCAP (zstd, 5-min splits, QoS overrides matching publishers).
- **`jarvis_lake`** converts each closed split (on `events/write_split`) to Parquet and maintains DuckDB views for history and KPIs. It uses the `mcap` libraries, so no ROS install is needed.
- **Recent events** come from a bounded ring buffer in the event hub (`~/query_events`). The gateway merges ring buffer + lake by `seq`.
- **State that must survive restarts** (open fault episodes, incidents, acks/latches, running executions, rule counters, hub boot count) lives in small, atomically written, fsynced JSON snapshots.
- A second rosbag2 recorder in **snapshot mode** is the black box for high-rate sensor data.

## Alternatives considered
- **SQLite event store (first draft)**: transactional and queryable in one place, but a custom writer on the hot path, a non-ROS format, and data that standard ROS tools can't open.
- **Fault-state DB like ros2_medkit (SQLite/Postgres)**: proven for fault state, but we only need a few KB of open state; snapshot files are simpler.
- **Event sourcing state from bags on restart**: elegant, but slow and fragile with unfinalized files. Rejected in favor of snapshots.

## Consequences
- ➕ Standard tooling: bags open in Foxglove/PlotJuggler with sensor data on the same timeline; replay feeds backtests directly.
- ➕ Analytics-friendly Parquet; fleet later = ship files to object storage.
- ➕ No database server or custom DB writer on the robot.
- ➖ "Publish-and-record" instead of "persist-then-publish": on a hard crash the last unflushed MCAP chunk (≤ ~1 s) can be lost. Critical state is protected by snapshots.
- ➖ History lags by ≤ one split (5 min), so the ring buffer must cover that window (sized by memory, severity-aware).
- ➖ One more process (lake) to supervise.
