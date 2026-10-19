# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The fleet's history: every robot's uploaded Parquet (jarvis_lake.upload), queried with the lake's
own views, each with a `robot` column. Read where it was uploaded, without copying it first:

    jarvis-lake fleet s3://bucket/prefix                   one line per robot
    jarvis-lake fleet s3://bucket/prefix "SELECT robot, code, count(*) FROM fault_episodes GROUP BY ALL"
    jarvis-lake fleet s3://bucket/prefix --summary --hours 168      the dashboard's fleet history, as JSON

`summary` is what the gateway serves the dashboard's Fleet page (GET /api/v1/fleet/history): per
robot, the window's numbers and how far its history goes; the fault codes that cost the fleet most;
confirmed faults per robot over time.
"""

import pyarrow.dataset as pads
import pyarrow.fs as pafs

from jarvis_lake import analytics, model, views

SUMMARY = """
SELECT e.robot,
       count(*) AS events,
       min(e.ts) AS first_event,
       max(e.ts) AS last_event,
       (SELECT count(*) FROM fault_episodes f WHERE f.robot = e.robot) AS faults,
       (SELECT count(*) FROM incidents i WHERE i.robot = e.robot) AS incidents,
       (SELECT count(*) FROM executions x WHERE x.robot = e.robot AND NOT x.dry_run) AS recoveries
FROM events e GROUP BY e.robot ORDER BY e.robot"""


def files(target):
    """(filesystem, [Parquet event files of every robot]) under the target."""
    fs, base = pafs.FileSystem.from_uri(target)
    selector = pafs.FileSelector(f"{base.rstrip('/')}/robots", recursive=True, allow_not_found=True)
    paths = [
        info.path
        for info in fs.get_file_info(selector)
        if info.is_file
        and info.path.endswith(".parquet")
        and "/events/date=" in info.path
        and not info.base_name.startswith(".")
    ]
    return fs, sorted(paths)


def dataset(target):
    """The fleet's events as one Arrow dataset, in the lake's schema (older files lacking a column
    read it as null)."""
    fs, paths = files(target)
    return pads.dataset(paths, schema=model.SCHEMA, format="parquet", filesystem=fs)


def connect(target):
    """DuckDB with the lake's views over the whole fleet."""
    return views.connect(None, source=dataset(target))


def robots(con, since, until):
    """One row per robot that uploaded anything: the window's numbers (as the Overview's KPIs),
    and how far its history goes (`history_until`: what is not uploaded yet is not here)."""
    p = [since, until]
    rows = analytics._rows(
        con,
        """WITH h AS (SELECT robot, max(ts) AS history_until FROM events GROUP BY robot),
           e AS (SELECT robot, count(*) AS events,
                        count(*) FILTER (WHERE severity_level >= 30) AS warnings_or_worse
                 FROM events WHERE ts >= ? AND ts < ? GROUP BY robot),
           f AS (SELECT robot, count(*) FILTER (WHERE confirmed_at IS NOT NULL) AS faults_confirmed,
                        avg(epoch(cleared_at) - epoch(confirmed_at))
                            FILTER (WHERE cleared_at IS NOT NULL AND confirmed_at IS NOT NULL) AS mttr_s
                 FROM fault_episodes WHERE first_seen >= ? AND first_seen < ? GROUP BY robot),
           i AS (SELECT robot, count(*) AS incidents_opened,
                        count(*) FILTER (WHERE escalated) AS incidents_escalated,
                        count(*) FILTER (WHERE resolved_at IS NOT NULL) AS resolved,
                        count(*) FILTER (WHERE resolved_at IS NOT NULL AND NOT escalated) AS resolved_alone
                 FROM incidents WHERE opened_at >= ? AND opened_at < ? GROUP BY robot),
           x AS (SELECT robot, count(*) FILTER (WHERE outcome IS NOT NULL) AS recoveries_finished,
                        count(*) FILTER (WHERE outcome = 'SUCCEEDED') AS recoveries_succeeded
                 FROM executions WHERE created_at >= ? AND created_at < ? AND NOT dry_run GROUP BY robot)
           SELECT h.robot, h.history_until,
                  coalesce(e.events, 0) AS events, coalesce(e.warnings_or_worse, 0) AS warnings_or_worse,
                  coalesce(f.faults_confirmed, 0) AS faults_confirmed, f.mttr_s,
                  coalesce(i.incidents_opened, 0) AS incidents_opened,
                  coalesce(i.incidents_escalated, 0) AS incidents_escalated,
                  coalesce(i.resolved, 0) AS resolved, coalesce(i.resolved_alone, 0) AS resolved_alone,
                  coalesce(x.recoveries_finished, 0) AS recoveries_finished,
                  coalesce(x.recoveries_succeeded, 0) AS recoveries_succeeded
           FROM h LEFT JOIN e USING (robot) LEFT JOIN f USING (robot) LEFT JOIN i USING (robot)
                  LEFT JOIN x USING (robot)
           ORDER BY h.robot""",
        p * 4,
    )
    for r in rows:
        resolved, alone = r.pop("resolved"), r.pop("resolved_alone")
        finished, succeeded = r["recoveries_finished"], r.pop("recoveries_succeeded")
        r["auto_recovery_rate"] = alone / resolved if resolved else None
        r["recovery_success_rate"] = succeeded / finished if finished else None
    return rows


def top_codes(con, since, until, limit=10):
    """The fault codes that cost the fleet most: confirmed episodes, on how many robots, MTTR."""
    return analytics._rows(
        con,
        """SELECT code, count(*) AS episodes, count(DISTINCT robot) AS robots,
                  array_sort(list(DISTINCT robot)) AS robot_names,
                  avg(epoch(cleared_at) - epoch(confirmed_at)) AS mttr_s
           FROM fault_episodes WHERE confirmed_at >= ? AND confirmed_at < ?
           GROUP BY code ORDER BY episodes DESC, robots DESC, code LIMIT ?""",
        [since, until, limit],
    )


def trend(con, since, until, bucket_s):
    """Confirmed faults per robot and time bucket."""
    return analytics._rows(
        con,
        """SELECT time_bucket(to_seconds(?), confirmed_at) AS bucket, robot, count(*) AS faults
           FROM fault_episodes WHERE confirmed_at >= ? AND confirmed_at < ?
           GROUP BY ALL ORDER BY bucket, robot""",
        [bucket_s, since, until],
    )


def bucket_for(hours):
    """An hour up to two days, then a day: a trend of tens of points."""
    return 3600 if hours <= 48 else 86400


def summary(target, hours=168.0, now=None, con=None):
    """The fleet's history over the last `hours`, for the dashboard (and `jarvis-lake fleet --summary`)."""
    con = con or connect(target)
    since, until = analytics.window(hours, now)
    bucket_s = bucket_for(hours)
    return {
        "hours": hours,
        "window": {"since": analytics._json(since), "until": analytics._json(until)},
        "bucket_s": bucket_s,
        "robots": robots(con, since, until),
        "top_codes": top_codes(con, since, until),
        "trend": trend(con, since, until, bucket_s),
    }
