# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""jarvis-lake: the lake service and its tools.

    jarvis-lake run                 convert splits as they close, enforce retention (ROS optional)
    jarvis-lake sync [--recover]    convert what is pending now, then exit
    jarvis-lake convert PATH...     convert given splits or bag directories
    jarvis-lake sql "SELECT ..."    query the views (events, fault_episodes, incidents, ...)
    jarvis-lake kpis [--hours 24]   the dashboard's KPIs and analytics, as JSON
    jarvis-lake status              counts, sizes and the last conversion
    jarvis-lake retention [--dry-run]

The data directory is --data-dir, else $JARVIS_DATA, else ~/.local/share/jarvis (as the bringup).
"""

import argparse
import contextlib
import json
import os
import pathlib
import sys

from jarvis_lake import analytics, lake, storage
from jarvis_lake.service import LakeService

GIB = 1 << 30


def _lake(args):
    retention = lake.Retention(
        bags_max_age_days=args.bags_max_days,
        bags_max_bytes=int(args.bags_max_gib * GIB),
        lake_max_age_days=args.lake_max_days,
        lake_max_bytes=int(args.lake_max_gib * GIB),
        blackbox_max_bytes=int(args.blackbox_max_gib * GIB),
    )
    return lake.Lake(args.data_dir, args.lake_dir, retention)


def _print(value):
    print(json.dumps(value, indent=2, default=str))


def cmd_run(args):
    service = LakeService(_lake(args), scan_period=args.scan_period, retention_period=args.retention_period)
    if args.ros:
        from jarvis_lake.adapters import ros2  # rclpy only here

        ros2.run(service)
        return 0
    import threading

    with contextlib.suppress(KeyboardInterrupt):
        service.run(threading.Event())
    return 0


def cmd_sync(args):
    the_lake = _lake(args)
    results = the_lake.sync(recover=args.recover)
    for r in results:
        note = " (torn split, recovered)" if r.truncated else ""
        print(f"{r.split}: {r.rows} events{note}")
    if not results:
        print("nothing to convert")
    return 0


def cmd_convert(args):
    the_lake = _lake(args)
    splits = []
    for path in map(pathlib.Path, args.paths):
        if path.is_dir():
            splits += sorted(path.glob("*.mcap"), key=storage.split_index)
        elif path.suffix == ".mcap":
            splits.append(path)
        else:
            print(f"jarvis-lake: {path} is neither an .mcap split nor a bag directory", file=sys.stderr)
            return 2
    for split in splits:
        r = the_lake.convert(split)
        note = " (torn split, recovered)" if r.truncated else ""
        print(f"{split}: {r.rows} events{note}")
    return 0


def cmd_sql(args):
    con = _lake(args).connect()
    cur = con.execute(args.query)
    names = [d[0] for d in cur.description]
    rows = cur.fetchall()
    if args.format == "json":
        _print([dict(zip(names, r, strict=True)) for r in rows])
    elif args.format == "csv":
        import csv

        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(names)
        writer.writerows(rows)
    else:
        widths = [max([len(n)] + [len(str(r[i])) for r in rows]) for i, n in enumerate(names)]
        print("  ".join(n.ljust(w) for n, w in zip(names, widths, strict=True)))
        for r in rows:
            print("  ".join(str(v).ljust(w) for v, w in zip(r, widths, strict=True)))
    return 0


def cmd_kpis(args):
    con = _lake(args).connect()
    since, until = analytics.window(args.hours)
    _print(
        {
            "kpis": analytics.kpis(con, since, until),
            "top_codes": analytics.top_codes(con, since, until),
            "components": analytics.components(con, since, until),
            "plans": analytics.plans(con, since, until),
            "rules": analytics.rules(con, since, until),
            "flapping": analytics.flapping(con, since, until),
        }
    )
    return 0


def cmd_status(args):
    _print(_lake(args).status())
    return 0


def cmd_retention(args):
    report = _lake(args).enforce_retention(dry_run=args.dry_run)
    _print(vars(report) | {"dry_run": args.dry_run})
    return 0


def parser():
    p = argparse.ArgumentParser(
        prog="jarvis-lake", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    default_data = os.environ.get("JARVIS_DATA", "~/.local/share/jarvis")
    p.add_argument("--data-dir", default=default_data, help="holds bags/, blackbox/ and lake/")
    p.add_argument("--lake-dir", default=None, help="where Parquet goes (default DATA_DIR/lake)")
    p.add_argument("--bags-max-days", type=float, default=7.0)
    p.add_argument("--bags-max-gib", type=float, default=10.0)
    p.add_argument("--lake-max-days", type=float, default=180.0)
    p.add_argument("--lake-max-gib", type=float, default=5.0)
    p.add_argument("--blackbox-max-gib", type=float, default=2.0)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("run", help="the service")
    s.add_argument("--ros", action=argparse.BooleanOptionalAction, default=True)
    s.add_argument("--scan-period", type=float, default=30.0, help="seconds between passes")
    s.add_argument("--retention-period", type=float, default=600.0)
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("sync", help="convert what is pending, then exit")
    s.add_argument("--recover", action="store_true", help="also the newest torn split")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("convert", help="convert splits or bag directories")
    s.add_argument("paths", nargs="+")
    s.set_defaults(func=cmd_convert)

    s = sub.add_parser("sql", help="query the views")
    s.add_argument("query")
    s.add_argument("--format", choices=("table", "csv", "json"), default="table")
    s.set_defaults(func=cmd_sql)

    s = sub.add_parser("kpis", help="KPIs and analytics as JSON")
    s.add_argument("--hours", type=float, default=24.0)
    s.set_defaults(func=cmd_kpis)

    sub.add_parser("status", help="counts and sizes").set_defaults(func=cmd_status)

    s = sub.add_parser("retention", help="enforce retention now")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_retention)
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # `ros2 run` and launch append ROS arguments: keep them away from argparse.
    if "--ros-args" in argv:
        argv = argv[: argv.index("--ros-args")]
    args = parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print(f"jarvis-lake: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
