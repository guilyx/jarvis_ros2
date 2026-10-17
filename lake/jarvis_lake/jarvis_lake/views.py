# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""DuckDB over the lake: views that turn the event stream back into faults, incidents, executions.

Everything is derived from `events`, the one table the lake stores. Recent events that are not in
the lake yet (the event hub's ring buffer covers the last split) can be passed in as an Arrow
table: they join `events` above the lake's newest seq, so a query sees both without duplicates.

    events            every event (lake + recent)
    fault_transitions FAULT_STATE events: one row per state change of a fault episode
    fault_episodes    one row per fault episode: first seen, confirmed, cleared, time to recover
    incidents         one row per incident: opened, escalated, resolved, closed
    executions        one row per recovery execution: plan, requester, outcome, duration
    rule_matches      RULE_MATCH events
    operator_actions  what operators did (the audit trail): acks, clears, closes, mode changes
"""

import duckdb

from jarvis_lake import model

COLUMN_LIST = ", ".join(model.COLUMNS)


def _attr(key):
    return f"json_extract_string(attrs, '$.{key}')"


VIEWS = {
    "fault_transitions": f"""
        SELECT ts, seq, id, code, component, severity, severity_level, message,
               {_attr("fault_id")} AS fault_id, {_attr("state")} AS state,
               {_attr("prev_state")} AS prev_state,
               TRY_CAST({_attr("occurrences")} AS BIGINT) AS occurrences,
               coalesce({_attr("muted_by")}, '') AS muted_by, correlation_id AS incident_id
        FROM events WHERE kind = 'FAULT_STATE'""",
    "fault_episodes": """
        SELECT fault_id,
               any_value(code) AS code,
               any_value(component) AS component,
               max(severity_level) AS severity_level,
               min(ts) AS first_seen,
               min(ts) FILTER (WHERE state IN ('ACTIVE', 'LATCHED')) AS confirmed_at,
               max(ts) FILTER (WHERE state = 'CLEARED') AS cleared_at,
               max(occurrences) AS occurrences,
               bool_or(muted_by <> '') AS muted,
               max(incident_id) AS incident_id,
               arg_max(state, seq) AS last_state
        FROM fault_transitions GROUP BY fault_id""",
    "incidents": f"""
        SELECT coalesce({_attr("incident_id")}, correlation_id) AS incident_id,
               any_value(component) AS component,
               arg_min(message, seq) AS title,
               max(severity_level) AS severity_level,
               min(ts) AS opened_at,
               min(ts) FILTER (WHERE {_attr("state")} = 'ESCALATED') AS escalated_at,
               max(ts) FILTER (WHERE {_attr("state")} = 'RESOLVED') AS resolved_at,
               max(ts) FILTER (WHERE {_attr("state")} = 'CLOSED') AS closed_at,
               bool_or({_attr("state")} = 'ESCALATED') AS escalated,
               arg_max({_attr("state")}, seq) AS state
        FROM events WHERE kind = 'SYSTEM' AND code LIKE 'jarvis.incident.%'
          -- the fault manager's transitions; rules asking it to open or escalate one carry no state
          AND {_attr("incident_id")} IS NOT NULL AND {_attr("state")} IS NOT NULL
        GROUP BY 1""",
    "executions": f"""
        SELECT {_attr("execution_id")} AS execution_id,
               any_value({_attr("plan")}) AS plan,
               any_value({_attr("requester")}) AS requester,
               any_value({_attr("requester_id")}) AS requester_id,
               bool_or({_attr("dry_run")} = 'true') AS dry_run,
               any_value(nullif({_attr("parent_id")}, '')) AS parent_id,
               any_value(nullif({_attr("ladder_of")}, '')) AS ladder_of,
               max(correlation_id) AS incident_id,
               min(ts) AS created_at,
               min(ts) FILTER (WHERE {_attr("phase")} = 'STARTED') AS started_at,
               max(ts) FILTER (WHERE {_attr("phase")} IN
                   ('SUCCEEDED', 'FAILED', 'ABORTED', 'REJECTED', 'EXPIRED')) AS finished_at,
               arg_max({_attr("phase")}, seq) FILTER (WHERE {_attr("phase")} IN
                   ('SUCCEEDED', 'FAILED', 'ABORTED', 'REJECTED', 'EXPIRED')) AS outcome,
               bool_or({_attr("phase")} = 'ESCALATED') AS escalated,
               arg_max({_attr("phase")}, seq) AS last_phase
        FROM events WHERE kind = 'RECOVERY' AND {_attr("execution_id")} IS NOT NULL
        GROUP BY 1""",
    "rule_matches": f"""
        SELECT ts, seq, id, {_attr("rule")} AS rule, {_attr("dry_run")} = 'true' AS dry_run,
               component, correlation_id AS incident_id, causation_id
        FROM events WHERE kind = 'RULE_MATCH'""",
    "operator_actions": f"""
        SELECT ts, seq, id, code, message, component, correlation_id AS incident_id,
               coalesce({_attr("operator")}, {_attr("operator_name")}, {_attr("user")}, '')
                   AS operator
        FROM events WHERE kind = 'OPERATOR'""",
}


def connect(layout, recent=None, database=":memory:"):
    """A DuckDB connection with the lake's views. `recent`: an Arrow table of newer events."""
    con = duckdb.connect(database)
    con.execute("SET TimeZone = 'UTC'")  # buckets and dates in UTC, whatever the host says
    files = list(layout.events.glob("date=*/*.parquet")) if layout.events.is_dir() else []
    if files:
        pattern = str(layout.events / "date=*" / "*.parquet")
        con.execute(
            f"CREATE VIEW lake_events AS SELECT {COLUMN_LIST} "
            f"FROM read_parquet('{_quote(pattern)}', union_by_name = true)"
        )
    else:
        con.register("lake_events_empty", model.table([]))
        con.execute(f"CREATE VIEW lake_events AS SELECT {COLUMN_LIST} FROM lake_events_empty")
    if recent is not None and recent.num_rows:
        con.register("recent_events", recent.select(model.COLUMNS))
        con.execute(
            f"CREATE VIEW events AS SELECT {COLUMN_LIST} FROM lake_events UNION ALL "
            f"SELECT {COLUMN_LIST} FROM recent_events "
            "WHERE seq > (SELECT coalesce(max(seq), 0) FROM lake_events)"
        )
    else:
        con.execute(f"CREATE VIEW events AS SELECT {COLUMN_LIST} FROM lake_events")
    for name, sql in VIEWS.items():
        con.execute(f"CREATE VIEW {name} AS {sql}")
    return con


def _quote(text):
    return text.replace("'", "''")
