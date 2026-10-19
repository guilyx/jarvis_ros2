# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The fleet's history: every robot's uploaded Parquet (jarvis_lake.upload), queried with the lake's
own views, each with a `robot` column. Read where it was uploaded, without copying it first:

    jarvis-lake fleet s3://bucket/prefix                   one line per robot
    jarvis-lake fleet s3://bucket/prefix "SELECT robot, code, count(*) FROM fault_episodes GROUP BY ALL"
"""

import pyarrow.dataset as pads
import pyarrow.fs as pafs

from jarvis_lake import model, views

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
