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
    jarvis-lake upload [--dry-run]  upload what is complete now (--upload-to), then exit
    jarvis-lake fleet URI [SQL]     the fleet's uploaded history: one line per robot, or a query
    jarvis-lake demo-history URI    a made-up week of history for demo robots, to try the above

The data directory is --data-dir, else $JARVIS_DATA, else ~/.local/share/jarvis (as the bringup).
With --upload-to (an s3:// or file:// URI), `run` also uploads the lake's Parquet, and with
--upload-blackbox the black box's snapshots, as they are complete (jarvis_lake.upload).
"""

import argparse
import contextlib
import json
import os
import pathlib
import sys

from jarvis_lake import analytics, demo_history, fleet, lake, storage, upload
from jarvis_lake.service import LakeService

GIB = 1 << 30


def _lake(args):
    retention = lake.Retention(
        bags_max_age_days=args.bags_max_days,
        bags_max_bytes=int(args.bags_max_gib * GIB),
        lake_max_age_days=args.lake_max_days,
        lake_max_bytes=int(args.lake_max_gib * GIB),
        blackbox_max_bytes=int(args.blackbox_max_gib * GIB),
        min_free_bytes=int(args.min_free_gib * GIB),
    )
    the_lake = lake.Lake(args.data_dir, args.lake_dir, retention)
    uploader = _uploader(args, the_lake)
    if uploader:
        the_lake.waiting = uploader.waiting
    return the_lake


def _uploader(args, the_lake):
    if not args.upload_to:
        return None
    return upload.Uploader(
        the_lake,
        args.upload_to,
        args.robot,
        blackbox=args.upload_blackbox,
        budget_s=args.upload_budget_s,
        max_lag_s=args.upload_max_lag_hours * 3600,
    )


def _print(value):
    print(json.dumps(value, indent=2, default=str))


def cmd_run(args):
    the_lake = _lake(args)
    service = LakeService(
        the_lake,
        scan_period=args.scan_period,
        retention_period=args.retention_period,
        uploader=_uploader(args, the_lake),
    )
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
    _rows(_lake(args).connect().execute(args.query), args.format)
    return 0


def cmd_fleet(args):
    if args.summary:
        _print(fleet.summary(args.target, args.hours))
        return 0
    _rows(fleet.connect(args.target).execute(args.query or fleet.SUMMARY), args.format)
    return 0


def cmd_demo_history(args):
    robots = [r for r in args.robots.split(",") if r]
    files = demo_history.write(args.target, robots, args.days, seed=args.seed)
    print(f"{len(files)} file(s) of made-up history for {', '.join(robots)} in {upload.redact(args.target)}")
    return 0


def _rows(cur, fmt):
    names = [d[0] for d in cur.description]
    rows = cur.fetchall()
    if fmt == "json":
        _print([dict(zip(names, r, strict=True)) for r in rows])
    elif fmt == "csv":
        import csv

        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(names)
        writer.writerows(rows)
    else:
        widths = [max([len(n)] + [len(str(r[i])) for r in rows]) for i, n in enumerate(names)]
        print("  ".join(n.ljust(w) for n, w in zip(names, widths, strict=True)).rstrip())
        for r in rows:
            print("  ".join(str(v).ljust(w) for v, w in zip(r, widths, strict=True)).rstrip())


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
    """The lake now; the upload as the running service last reported it (its errors, its retries),
    else as --upload-to sees it from here."""
    the_lake = _lake(args)
    status = the_lake.status()
    service = storage.read_json(the_lake.layout.status, {})
    uploader = _uploader(args, the_lake)
    if service.get("upload"):
        status["upload"] = service["upload"] | {"reported_at": service.get("updated_at")}
    elif uploader:
        status["upload"] = uploader.status()
    _print(status)
    return 0


def cmd_upload(args):
    uploader = _uploader(args, _lake(args))
    if not uploader:
        print("jarvis-lake: where to? --upload-to s3://bucket/prefix (or file:///mnt/share)", file=sys.stderr)
        return 2
    pending = uploader.pending()
    if args.dry_run:
        for item in pending:
            print(f"{item.path} -> {item.key} ({item.size} bytes)")
        print(f"{len(pending)} file(s) to upload")
        return 0
    uploader.budget_s = float("inf")
    n = uploader.run()
    print(f"uploaded {n} of {len(pending)} file(s) to {upload.redact(args.upload_to)}")
    if uploader.last_error:
        print(f"jarvis-lake: {uploader.last_error}", file=sys.stderr)
        return 1
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
    p.add_argument("--min-free-gib", type=float, default=1.0, help="free disk to keep (disk pressure)")
    p.add_argument("--robot", default="robot", help="this robot's id: where its uploads go")
    p.add_argument(
        "--upload-to", default="", help="upload history there: s3://bucket/prefix, file:///mnt/share"
    )
    p.add_argument("--upload-blackbox", action="store_true", help="also the black box's snapshots")
    p.add_argument("--upload-budget-s", type=float, default=60.0, help="seconds of uploading per pass")
    p.add_argument(
        "--upload-max-lag-hours",
        type=float,
        default=24.0,
        help="older than this, waiting is behind (a fault)",
    )
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

    s = sub.add_parser("upload", help="upload what is complete (--upload-to), then exit")
    s.add_argument("--dry-run", action="store_true", help="only list what would go")
    s.set_defaults(func=cmd_upload)

    s = sub.add_parser("fleet", help="query the fleet's uploaded history")
    s.add_argument("target", help="where the robots upload: s3://bucket/prefix, file:///mnt/share")
    s.add_argument("query", nargs="?", default="", help="SQL over the views (default: one line per robot)")
    s.add_argument("--format", choices=("table", "csv", "json"), default="table")
    s.add_argument("--summary", action="store_true", help="the dashboard's fleet history, as JSON")
    s.add_argument("--hours", type=float, default=168.0, help="the summary's window")
    s.set_defaults(func=cmd_fleet)

    s = sub.add_parser("demo-history", help="made-up history for demo robots (never a real fleet's storage)")
    s.add_argument("target", help="where demo robots upload: s3://bucket/prefix, file:///tmp/fleet")
    s.add_argument("--robots", default="amr-1,amr-2,amr-3")
    s.add_argument("--days", type=float, default=7.0)
    s.add_argument("--seed", type=int, default=7)
    s.set_defaults(func=cmd_demo_history)
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
