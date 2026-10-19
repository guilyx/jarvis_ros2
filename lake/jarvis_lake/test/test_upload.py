# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""History off the robot (jarvis_lake.upload) and the fleet's queries over it (jarvis_lake.fleet).

The target is a directory (file://) here: the same code paths as object storage but the last copy.
With JARVIS_TEST_S3 set to an S3 URI (CI starts moto's S3 server), the last test runs against it too."""

import contextlib
import datetime
import json
import os
import pathlib
import uuid

import pyarrow.fs as pafs
import pytest
from conftest import Events, write_split
from jarvis_lake import cli, fleet, upload
from jarvis_lake.lake import GIB, Lake, Retention
from jarvis_lake.service import LakeService
from test_lake import scenario, snapshots, split


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def robot_events(robot, start=None, step=datetime.timedelta(minutes=2)):
    """Events of one robot; the scenario's, two minutes apart, cross midnight: two days of Parquet."""
    e = Events(start=start, step=step) if start else Events(step=step)

    def make(*args, **kwargs):
        ev = e(*args, **kwargs)
        ev["source"]["robot"] = robot
        return ev

    return make


def converted_lake(data_dir, robot="amr-7", **kwargs):
    write_split(split(data_dir, "b1", 0), scenario(robot_events(robot)))
    lake = Lake(data_dir, **kwargs)
    lake.sync()
    return lake


def uploaded(target):
    root = pathlib.Path(target.removeprefix("file://"))
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


@pytest.fixture
def target(tmp_path):
    return f"file://{tmp_path / 'fleet'}"


def test_uploads_what_is_complete_where_the_fleet_reads_it(data_dir, target):
    lake = converted_lake(data_dir)
    up = upload.Uploader(lake, target, "amr-7")
    assert [i.key.rsplit("/", 2)[-2] for i in up.pending()] == ["date=2026-10-14", "date=2026-10-15"]
    assert up.run() == 2
    assert uploaded(target) == [
        "robots/amr-7/events/date=2026-10-14/b1_0.parquet",
        "robots/amr-7/events/date=2026-10-15/b1_0.parquet",
    ]
    assert up.pending() == [] and up.run() == 0
    status = up.status()
    assert status["pending_files"] == 0 and status["uploaded_total"] == 2 and status["last_error"] == ""
    # The ledger survives a restart: nothing goes twice.
    saved = json.loads((data_dir / "lake" / "uploads.json").read_text())
    assert len(saved["files"]) == 2
    assert upload.Uploader(lake, target, "amr-7").pending() == []


def test_a_file_that_changes_goes_again(data_dir, target):
    lake = converted_lake(data_dir)
    up = upload.Uploader(lake, target, "amr-7")
    up.run()
    day = next((data_dir / "lake" / "events").glob("date=2026-10-15/*.parquet"))
    stat = day.stat()
    os.utime(day, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))  # converted again
    assert [i.path for i in up.pending()] == [day]
    assert up.run() == 1


def test_black_box_snapshots_only_when_asked_and_closed(data_dir, target):
    lake = converted_lake(data_dir)
    snapshots(data_dir, "bb_1", 2)
    snapshots(data_dir, "bb_2", 1, finalized=False)  # the black box is writing it
    assert len(upload.Uploader(lake, target, "amr-7").pending()) == 2
    up = upload.Uploader(lake, target, "amr-7", blackbox=True)
    up.run()
    assert [f for f in uploaded(target) if "/blackbox/" in f] == [
        "robots/amr-7/blackbox/bb_1/bb_1_0.mcap",
        "robots/amr-7/blackbox/bb_1/bb_1_1.mcap",
    ]


def test_offline_it_backs_off_then_catches_up(data_dir, tmp_path):
    lake = converted_lake(data_dir)
    blocked = tmp_path / "share"
    blocked.write_text("not a directory: as good as an unreachable target")
    clock = Clock()
    up = upload.Uploader(lake, f"file://{blocked}", "amr-7", clock=clock)
    assert up.run() == 0
    assert "b1_0.parquet" in up.last_error and up.retry_at == clock.t + 30
    clock.t += 10
    assert up.run() == 0, "not before its retry time"
    clock.t += 30
    up.run()
    assert up.retry_at == clock.t + 60, "doubling"
    blocked.unlink()  # back online
    clock.t += 60
    assert up.run() == 2
    assert up.last_error == "" and up.retry_at == 0.0
    assert up.status()["retry_at"] is None


def test_each_pass_has_a_budget_but_always_makes_progress(data_dir, target):
    lake = converted_lake(data_dir)
    up = upload.Uploader(lake, target, "amr-7", budget_s=0)
    assert up.run() == 1
    assert up.run() == 1
    assert up.run() == 0


def test_behind_after_the_max_lag(data_dir, target):
    lake = converted_lake(data_dir)
    newest = max(p.stat().st_mtime for p in (data_dir / "lake" / "events").rglob("*.parquet"))
    clock = Clock(newest + 7200)
    up = upload.Uploader(lake, target, "amr-7", max_lag_s=3600, clock=clock)
    status = up.status()
    assert status["behind"] and status["oldest_pending_s"] >= 7200
    message = upload.behind_message(status)
    assert message.startswith("history is not leaving the robot: 2 file(s)") and "2.0 h" in message
    up.run()
    assert upload.behind_message(up.status()) is None
    assert upload.behind_message(None) is None


def test_retention_waits_for_the_upload(data_dir, target):
    e = robot_events("amr-7", step=datetime.timedelta(days=1))
    write_split(split(data_dir, "b1", 0), [e("LOG", "x") for _ in range(3)])
    box = snapshots(data_dir, "bb_1", 3)
    lake = Lake(data_dir, retention=Retention(lake_max_bytes=1, blackbox_max_bytes=1500))
    lake.sync()
    up = upload.Uploader(lake, target, "amr-7", blackbox=True)
    lake.waiting = up.waiting
    report = lake.enforce_retention()
    assert report.deleted_partitions == [] and report.deleted_blackbox == [], "nothing is uploaded yet"
    up.run()
    report = lake.enforce_retention()
    assert len(report.deleted_partitions) == 2 and len(report.deleted_blackbox) == 2
    assert report.lost == [] and sorted(p.name for p in box.iterdir()) == ["bb_1_2.mcap"]


def test_disk_pressure_does_not_wait_and_counts_what_is_lost(data_dir, target):
    e = robot_events("amr-7", step=datetime.timedelta(days=1))
    write_split(split(data_dir, "b1", 0), [e("LOG", "x") for _ in range(3)])
    lake = Lake(data_dir, retention=Retention(min_free_bytes=GIB), disk_free=lambda _: 0)
    lake.sync()
    up = upload.Uploader(lake, target, "amr-7")
    service = LakeService(lake, uploader=up, log=lambda _: None)
    service._retention()
    assert up.lost == 2, "two older days of Parquet went before they were uploaded"
    assert json.loads((data_dir / "lake" / "uploads.json").read_text())["lost"] == 2


def test_the_service_uploads_after_converting_and_reports(data_dir, target):
    write_split(split(data_dir, "b1", 0), scenario(robot_events("amr-7")))
    lake = Lake(data_dir)
    service = LakeService(lake, uploader=upload.Uploader(lake, target, "amr-7"), log=lambda _: None)
    status = service.run_once()
    assert status["upload"]["pending_files"] == 0 and status["upload"]["uploaded_total"] == 2
    assert status["upload"]["target"] == target and status["last_error"] == ""
    on_disk = json.loads((data_dir / "lake" / "status.json").read_text())
    assert on_disk["upload"]["uploaded_total"] == 2


def test_an_unreachable_target_is_not_the_lakes_error(data_dir, tmp_path):
    write_split(split(data_dir, "b1", 0), scenario(robot_events("amr-7")))
    blocked = tmp_path / "share"
    blocked.write_text("x")
    lake = Lake(data_dir)
    service = LakeService(
        lake, uploader=upload.Uploader(lake, f"file://{blocked}", "amr-7"), log=lambda _: None
    )
    status = service.run_once()
    assert status["last_error"] == "" and status["upload"]["last_error"]
    assert status["upload"]["pending_files"] == 2


def test_the_target_never_shows_credentials():
    assert upload.redact("s3://key:secret@bucket/fleet?endpoint_override=minio:9000&scheme=http") == (
        "s3://bucket/fleet?endpoint_override=minio%3A9000&scheme=http"
    )
    assert (
        upload.redact("s3://bucket/x?access_key=a&secret_key=b&region=eu-west-3")
        == "s3://bucket/x?region=eu-west-3"
    )
    assert upload.robot_name("amr 7/../x") == "amr_7_.._x"
    assert upload.robot_name("") == "robot"


def test_the_fleet_answers_across_robots(tmp_path, target):
    for robot in ("amr-7", "amr-8"):
        data = tmp_path / robot
        upload.Uploader(converted_lake(data, robot), target, robot).run()
    con = fleet.connect(target)
    summary = con.execute(fleet.SUMMARY).fetchall()
    assert [(r[0], r[5], r[6]) for r in summary] == [("amr-7", 2, 2), ("amr-8", 2, 2)]
    rows = con.execute("SELECT robot, code FROM fault_episodes ORDER BY robot, code").fetchall()
    assert ("amr-8", "sensor.scan.stale") in rows
    assert con.execute("SELECT count(DISTINCT robot) FROM incidents").fetchone() == (2,)


def test_the_cli_uploads_and_queries_the_fleet(data_dir, target, capsys):
    write_split(split(data_dir, "b1", 0), scenario(robot_events("amr-7")))
    common = ["--data-dir", str(data_dir), "--robot", "amr-7", "--upload-to", target]
    assert cli.main([*common, "sync"]) == 0
    assert cli.main([*common, "upload", "--dry-run"]) == 0
    assert "2 file(s) to upload" in capsys.readouterr().out
    assert cli.main([*common, "upload"]) == 0
    assert "uploaded 2 of 2 file(s)" in capsys.readouterr().out
    assert cli.main([*common, "status"]) == 0
    assert json.loads(capsys.readouterr().out)["upload"]["pending_files"] == 0
    assert cli.main(["fleet", target]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == [
        "robot",
        "events",
        "first_event",
        "last_event",
        "faults",
        "incidents",
        "recoveries",
    ]
    assert lines[1].startswith("amr-7")
    assert cli.main(["upload"]) == 2, "where to?"


@pytest.mark.skipif(not os.environ.get("JARVIS_TEST_S3"), reason="no S3 (JARVIS_TEST_S3=s3://bucket?...)")
def test_s3(data_dir):
    """Against an S3 API (moto's server in CI), credentials in the environment. A bucket of its own:
    the test makes it (allow_bucket_creation=true in the URI); the lake never does."""
    base = os.environ["JARVIS_TEST_S3"]
    s3, bucket = pafs.FileSystem.from_uri(base)
    with contextlib.suppress(OSError):
        s3.create_dir(bucket.split("/")[0])
    path, _, query = base.partition("?")
    target = f"{path.rstrip('/')}/{uuid.uuid4().hex}?{query}"
    lake = converted_lake(data_dir, "amr-9")
    up = upload.Uploader(lake, target, "amr-9")
    assert up.run() == 2, up.last_error
    assert up.pending() == []
    con = fleet.connect(target)
    assert con.execute("SELECT robot, count(*) FROM incidents GROUP BY robot").fetchall() == [("amr-9", 2)]
