# ADR-0011 — Our own Event type; `diagnostic_msgs` in and out, not as the model

- **Status**: Accepted (2026-10-14)
- **Date**: 2026-10-13

## Context
Review question: should the event table just be an array of diagnostics (`diagnostic_msgs/DiagnosticArray`)?

## Decision
- **No.** `DiagnosticStatus` is a periodic status snapshot: no id, no per-status timestamp, string-only values, no lifecycle, no causality, and no way to express rule matches, recoveries, insights or operator actions.
- **Diagnostics in**: a bridge turns level *changes* into `FAULT_RAISE`/`FAULT_CLEAR` (`name→code`, `hardware_id→component`, `values→attrs`). The DEM `counter` debounce handles 1 Hz flapping sources.
- **Diagnostics out**: the fault manager publishes active faults on `/diagnostics` (hardware_id `jarvis`), so `rqt_robot_monitor`, `diagnostic_aggregator` and Foxglove work unchanged. The bridge ignores `jarvis`, so there's no loop.

## Alternatives considered
- **DiagnosticArray as the event stream**: zero new interfaces, but it loses edges, ids, typed values and causality, and re-records steady state every second.
- **Extend `diagnostic_msgs`**: not ours to change; ros2_medkit made the same call ("why we did not extend /diagnostics").

## Consequences
- ➕ Full compatibility with the diagnostics ecosystem in both directions.
- ➖ One more message type (`jarvis_interfaces/Event`), kept small and stable.
