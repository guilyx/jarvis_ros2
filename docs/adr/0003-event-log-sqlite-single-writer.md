# ADR-0003 — Event log as source of truth, SQLite (WAL) on the robot, single writer

- **Status**: Proposed
- **Date**: 2026-10-13

## Context
We need durable, queryable storage on the robot: no server process, crash safe, small, and readable by the gateway and CLI concurrently.

## Decision
- **Everything is an event** in an append-only log. Faults, incidents and executions are **projections** (rebuildable tables).
- **SQLite in WAL mode** (`synchronous=NORMAL`), with group commit ≤ 20 ms.
- **Single writer**: only `/jarvis/recorder` writes. Others publish to `/jarvis/ingest` or call services.
- **Persist-then-publish** with a monotonic `seq` for gap detection and backfill.
- rosbag2/MCAP for raw high-rate data around faults (black box), linked from events.

## Alternatives considered
- **PostgreSQL/TimescaleDB on the robot**: powerful, but a server process to run, tune and upgrade. Better suited as a future fleet store.
- **MCAP only**: great for raw data, poor for indexed queries and mutable projections.
- **Embedded KV (RocksDB/LMDB)**: fast, but no ad-hoc query language; we'd reinvent indexes.

## Consequences
- ➕ Zero-ops, power-cut safe, SQL + JSON1 for queries, trivial to copy an incident (`.db` file).
- ➖ Single-node only (by design). Fleet replication happens later via `seq` cursors.
