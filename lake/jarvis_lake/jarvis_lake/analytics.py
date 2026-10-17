# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""KPIs and analytics over a time window, as plain SQL on the lake's views.

The KPIs are the dashboard's (docs/design/07-dashboard.md). The analytics borrow from ros2_medkit's
diagnostics view of a robot (ADR-0010): health per component, which faults matter most (Pareto),
when they happen (heatmap), which ones flap, and how well each recovery plan works.

Every function takes a DuckDB connection from `views.connect()` and a window `[since, until)` of
timezone-aware datetimes, and returns JSON-ready dicts and lists.
"""

import datetime

TERMINAL = ("SUCCEEDED", "FAILED", "ABORTED", "REJECTED", "EXPIRED")
SEVERITY_NAMES = {10: "DEBUG", 20: "INFO", 30: "WARN", 40: "ERROR", 50: "CRITICAL"}


def window(hours, now=None):
    """The last `hours` hours, ending now (UTC)."""
    until = now or datetime.datetime.now(tz=datetime.UTC)
    return until - datetime.timedelta(hours=hours), until


def _rows(con, sql, params):
    cur = con.execute(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, (_json(v) for v in r), strict=True)) for r in cur.fetchall()]


def _json(value):
    if isinstance(value, datetime.datetime):
        return value.astimezone(datetime.UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, datetime.timedelta):
        return value.total_seconds()
    if isinstance(value, float) and value != value:  # NaN
        return None
    return value


def _one(con, sql, params):
    rows = _rows(con, sql, params)
    return rows[0] if rows else {}


def kpis(con, since, until):
    """The Overview's headline numbers for the window."""
    p = [since, until]
    events = _one(
        con,
        """SELECT count(*) AS events,
                  count(*) FILTER (WHERE severity_level >= 30) AS warnings_or_worse
           FROM events WHERE ts >= ? AND ts < ?""",
        p,
    )
    faults = _one(
        con,
        """SELECT count(*) FILTER (WHERE confirmed_at IS NOT NULL) AS confirmed,
                  avg(epoch(cleared_at) - epoch(confirmed_at))
                      FILTER (WHERE cleared_at IS NOT NULL AND confirmed_at IS NOT NULL)
                      AS mttr_s
           FROM fault_episodes WHERE first_seen >= ? AND first_seen < ?""",
        p,
    )
    incidents = _one(
        con,
        """SELECT count(*) AS opened,
                  count(*) FILTER (WHERE state IN ('OPEN', 'RECOVERING', 'ESCALATED')) AS open,
                  count(*) FILTER (WHERE state = 'ESCALATED') AS escalated_now,
                  count(*) FILTER (WHERE resolved_at IS NOT NULL) AS resolved,
                  count(*) FILTER (WHERE resolved_at IS NOT NULL AND NOT escalated)
                      AS resolved_without_escalation
           FROM incidents WHERE opened_at >= ? AND opened_at < ?""",
        p,
    )
    executions = _one(
        con,
        """SELECT count(*) FILTER (WHERE outcome IS NOT NULL) AS finished,
                  count(*) FILTER (WHERE outcome = 'SUCCEEDED') AS succeeded
           FROM executions WHERE created_at >= ? AND created_at < ? AND NOT dry_run""",
        p,
    )
    resolved = incidents.get("resolved") or 0
    finished = executions.get("finished") or 0
    return {
        "window": {"since": _json(since), "until": _json(until)},
        "events": events.get("events", 0),
        "warnings_or_worse": events.get("warnings_or_worse", 0),
        "faults_confirmed": faults.get("confirmed", 0),
        "mttr_s": faults.get("mttr_s"),
        "incidents_opened": incidents.get("opened", 0),
        "incidents_open": incidents.get("open", 0),
        "incidents_escalated": incidents.get("escalated_now", 0),
        "auto_recovery_rate": (incidents["resolved_without_escalation"] / resolved if resolved else None),
        "recoveries_finished": finished,
        "recovery_success_rate": executions["succeeded"] / finished if finished else None,
    }


def event_rate(con, since, until, bucket_s=60):
    """Events per bucket and severity: the Overview's sparkline."""
    return _rows(
        con,
        """SELECT time_bucket(to_seconds(?), ts) AS bucket, severity, count(*) AS events
           FROM events WHERE ts >= ? AND ts < ? GROUP BY ALL ORDER BY bucket, severity""",
        [bucket_s, since, until],
    )


def top_codes(con, since, until, limit=10):
    """Fault codes by confirmed episodes, with a cumulative share (a Pareto chart)."""
    return _rows(
        con,
        """WITH counts AS (
               SELECT code, count(*) AS episodes,
                      avg(epoch(cleared_at) - epoch(confirmed_at)) AS mttr_s
               FROM fault_episodes
               WHERE confirmed_at >= ? AND confirmed_at < ? GROUP BY code)
           SELECT code, episodes, mttr_s,
                  sum(episodes) OVER (ORDER BY episodes DESC, code
                      ROWS UNBOUNDED PRECEDING) / sum(episodes) OVER () AS cumulative_share
           FROM counts ORDER BY episodes DESC, code LIMIT ?""",
        [since, until, limit],
    )


def components(con, since, until):
    """Health per component: the entity view (worst severity, faults, MTTR, MTBF, last fault)."""
    hours = max((until - since).total_seconds() / 3600, 1e-9)
    rows = _rows(
        con,
        """WITH ev AS (
               SELECT component, count(*) AS events, max(severity_level) AS worst_level,
                      max(ts) AS last_event
               FROM events WHERE ts >= ? AND ts < ? AND component <> '' GROUP BY component),
           ep AS (
               SELECT component, count(*) AS faults,
                      count(*) FILTER (WHERE cleared_at IS NULL) AS open_faults,
                      avg(epoch(cleared_at) - epoch(confirmed_at)) AS mttr_s,
                      max(confirmed_at) AS last_fault
               FROM fault_episodes
               WHERE confirmed_at >= ? AND confirmed_at < ? GROUP BY component)
           SELECT ev.component, ev.events, ev.worst_level, ev.last_event,
                  coalesce(ep.faults, 0) AS faults, coalesce(ep.open_faults, 0) AS open_faults,
                  ep.mttr_s, ep.last_fault
           FROM ev LEFT JOIN ep USING (component) ORDER BY faults DESC, worst_level DESC,
                ev.component""",
        [since, until, since, until],
    )
    for r in rows:
        r["worst_severity"] = SEVERITY_NAMES.get(r.pop("worst_level"), "INFO")
        r["mtbf_h"] = hours / r["faults"] if r["faults"] else None
    return rows


def heatmap(con, since, until, bucket_s=3600):
    """Confirmed faults per component and time bucket (when do things break?)."""
    return _rows(
        con,
        """SELECT component, time_bucket(to_seconds(?), confirmed_at) AS bucket,
                  count(*) AS faults
           FROM fault_episodes WHERE confirmed_at >= ? AND confirmed_at < ?
           GROUP BY ALL ORDER BY component, bucket""",
        [bucket_s, since, until],
    )


def flapping(con, since, until, min_cycles=3):
    """Faults that keep coming back: at least `min_cycles` confirm-then-clear cycles."""
    return _rows(
        con,
        """SELECT code, component, count(*) AS cycles,
                  avg(epoch(cleared_at) - epoch(confirmed_at)) AS mean_active_s,
                  min(confirmed_at) AS first, max(confirmed_at) AS last
           FROM fault_episodes
           WHERE confirmed_at >= ? AND confirmed_at < ? AND cleared_at IS NOT NULL
           GROUP BY code, component HAVING count(*) >= ?
           ORDER BY cycles DESC, code""",
        [since, until, min_cycles],
    )


def plans(con, since, until):
    """Recovery effectiveness per plan: runs, outcomes, success rate, duration, escalations."""
    rows = _rows(
        con,
        """SELECT plan, count(*) AS runs,
                  count(*) FILTER (WHERE outcome = 'SUCCEEDED') AS succeeded,
                  count(*) FILTER (WHERE outcome = 'FAILED') AS failed,
                  count(*) FILTER (WHERE outcome IN ('REJECTED', 'EXPIRED', 'ABORTED'))
                      AS not_run,
                  count(*) FILTER (WHERE escalated) AS escalated,
                  avg(epoch(finished_at) - epoch(started_at))
                      FILTER (WHERE outcome = 'SUCCEEDED') AS mean_duration_s,
                  count(DISTINCT requester) AS requesters
           FROM executions WHERE created_at >= ? AND created_at < ? AND NOT dry_run
           GROUP BY plan ORDER BY runs DESC, plan""",
        [since, until],
    )
    for r in rows:
        decided = r["succeeded"] + r["failed"]
        r["success_rate"] = r["succeeded"] / decided if decided else None
    return rows


def rules(con, since, until):
    """Fires per rule in the window, live and in dry run."""
    return _rows(
        con,
        """SELECT rule, count(*) FILTER (WHERE NOT dry_run) AS fires,
                  count(*) FILTER (WHERE dry_run) AS dry_run_fires, max(ts) AS last_fire
           FROM rule_matches WHERE ts >= ? AND ts < ? GROUP BY rule ORDER BY fires DESC, rule""",
        [since, until],
    )
