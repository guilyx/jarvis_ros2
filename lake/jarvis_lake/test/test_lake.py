# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""jarvis_lake on synthetic splits: conversion, torn splits, ledger, views, analytics, retention."""

import datetime
import json
import os
import pathlib
import re
import time

import pyarrow.parquet as pq
import pytest
from conftest import MSG, T0, Events, write_split
from jarvis_lake import analytics, cli, convert, model, storage
from jarvis_lake.lake import GIB, Lake, Retention
from jarvis_lake.service import LakeService


def split(data_dir, bag, n):
    return data_dir / "bags" / bag / f"{bag}_{n}.mcap"


def scenario(e):
    """A lidar fault that a rule recovers, a motor fault that escalates, and an operator clear."""
    inc1, inc2 = "inc-1", "inc-2"
    return [
        e("FAULT_RAISE", "sensor.scan.stale", "ERROR"),
        e("FAULT_STATE", "sensor.scan.stale", "ERROR", fault_id="f1", state="PENDING", occurrences=1),
        e(
            "FAULT_STATE",
            "sensor.scan.stale",
            "ERROR",
            correlation=inc1,
            fault_id="f1",
            state="ACTIVE",
            prev_state="PENDING",
            occurrences=1,
        ),
        e(
            "SYSTEM",
            "jarvis.incident.opened",
            "ERROR",
            correlation=inc1,
            message="stale on lidar",
            incident_id=inc1,
            state="OPEN",
        ),
        e("RULE_MATCH", "jarvis.rule.matched", correlation=inc1, rule="lidar-restart", dry_run=False),
        e(
            "RECOVERY",
            "jarvis.recovery.started",
            correlation=inc1,
            phase="STARTED",
            plan="restart",
            execution_id="x1",
            requester="RULE",
            dry_run=False,
        ),
        e(
            "RECOVERY",
            "jarvis.recovery.succeeded",
            correlation=inc1,
            phase="SUCCEEDED",
            plan="restart",
            execution_id="x1",
            requester="RULE",
            dry_run=False,
        ),
        e(
            "FAULT_STATE",
            "sensor.scan.stale",
            "INFO",
            correlation=inc1,
            fault_id="f1",
            state="CLEARED",
            prev_state="ACTIVE",
            occurrences=1,
        ),
        e("SYSTEM", "jarvis.incident.resolved", correlation=inc1, incident_id=inc1, state="RESOLVED"),
        e(
            "FAULT_STATE",
            "drive.overcurrent",
            "CRITICAL",
            component="motor",
            correlation=inc2,
            fault_id="f2",
            state="ACTIVE",
            occurrences=3,
        ),
        e(
            "SYSTEM",
            "jarvis.incident.opened",
            "CRITICAL",
            component="motor",
            correlation=inc2,
            incident_id=inc2,
            state="OPEN",
            message="overcurrent on motor",
        ),
        e(
            "RECOVERY",
            "jarvis.recovery.failed",
            correlation=inc2,
            phase="FAILED",
            plan="reset",
            execution_id="x2",
            requester="AGENT",
            dry_run=False,
        ),
        e(
            "RECOVERY",
            "jarvis.recovery.escalated",
            correlation=inc2,
            phase="ESCALATED",
            plan="reset",
            execution_id="x2",
            requester="AGENT",
            dry_run=False,
        ),
        # A rule asking the fault manager to escalate: no state, so it is not a transition.
        e("SYSTEM", "jarvis.incident.escalate", correlation=inc2, rule="exhausted"),
        e(
            "SYSTEM",
            "jarvis.incident.escalated",
            "ERROR",
            component="motor",
            correlation=inc2,
            incident_id=inc2,
            state="ESCALATED",
        ),
        e(
            "OPERATOR",
            "fault.clear",
            component="motor",
            correlation=inc2,
            message="alice cleared drive.overcurrent",
            operator="alice",
        ),
        # The escalated incident ends resolved too (after a human stepped in).
        e(
            "SYSTEM",
            "jarvis.incident.resolved",
            component="motor",
            correlation=inc2,
            incident_id=inc2,
            state="RESOLVED",
        ),
        # A rule asking to open an incident for an event that has none: not an incident itself.
        e("SYSTEM", "jarvis.incident.open", rule="opener"),
    ]


# -- model ---------------------------------------------------------------------------------------


def test_constants_match_the_message_definitions():
    def constants(text, prefix):
        found = re.findall(rf"uint8 {prefix}([A-Z_]+)=(\d+)", text)
        return {int(v): k for k, v in found}

    event = (MSG / "Event.msg").read_text()
    assert constants(event, "KIND_") == model.KINDS
    assert constants(event, "SEVERITY_") == model.SEVERITIES
    attr = constants((MSG / "Attribute.msg").read_text(), "TYPE_")
    assert attr == {
        model.ATTR_STRING: "STRING",
        model.ATTR_INT: "INT",
        model.ATTR_DOUBLE: "DOUBLE",
        model.ATTR_BOOL: "BOOL",
    }


# -- conversion ----------------------------------------------------------------------------------


def test_a_split_becomes_typed_rows_and_other_topics_are_ignored(data_dir, events):
    e = events("FAULT_STATE", "a.b", "ERROR", fault_id="f", state="ACTIVE", occurrences=2, ratio=0.5)
    e["recv_stamp"] = {"sec": 0, "nanosec": 0}  # never went through the hub: ts falls back
    path = write_split(split(data_dir, "b1", 0), [e], other_topic=True)
    rows, result = convert.read_split(path)
    assert (result.rows, result.finalized, result.truncated, result.skipped) == (1, True, False, 0)
    (r,) = rows
    assert r["kind"] == "FAULT_STATE" and r["severity"] == "ERROR" and r["severity_level"] == 40
    assert json.loads(r["attrs"]) == {"fault_id": "f", "state": "ACTIVE", "occurrences": 2, "ratio": 0.5}
    assert r["recv_stamp"] is None and r["ts"] == r["stamp"]
    assert r["split"] == "b1_0"


def test_events_are_partitioned_by_utc_day_and_conversion_is_idempotent(data_dir):
    e = Events(step=datetime.timedelta(minutes=4))  # 23:50 → 00:30 over eleven events
    path = write_split(split(data_dir, "b1", 0), [e("LOG", "x.y") for _ in range(11)])
    lake = Lake(data_dir)
    (result,) = lake.sync()
    days = sorted(f.parent.name for f in result.files)
    assert days == ["date=2026-10-14", "date=2026-10-15"]
    assert lake.sync() == []  # in the ledger: nothing pending
    lake.convert(path)  # converting again replaces the same files
    count = lake.connect().execute("SELECT count(*), count(DISTINCT id) FROM events").fetchone()
    assert count == (11, 11)


def test_a_torn_split_keeps_its_complete_chunks(data_dir, events):
    path = write_split(
        split(data_dir, "b1", 0),
        [events("LOG", "x.y") for _ in range(50)],
        chunk_size=2000,
        finalize=False,
    )
    os.truncate(path, path.stat().st_size - 300)
    rows, result = convert.read_split(path)
    assert not result.finalized and result.truncated
    assert 0 < result.rows < 50
    assert [r["seq"] for r in rows] == list(range(1, result.rows + 1)), "a prefix, in order"


def test_the_split_being_written_waits_until_the_recorder_moves_on(data_dir, events):
    lake = Lake(data_dir)
    write_split(split(data_dir, "b1", 0), [events("LOG", "x")])
    open_split = write_split(split(data_dir, "b1", 1), [events("LOG", "x")], finalize=False)
    assert lake.pending() == [split(data_dir, "b1", 0)], "b1_1 is still being written"
    assert open_split in lake.pending(recover=True)

    # After a crash, Jarvis comes back with a new bag: the torn split is recovered then.
    write_split(split(data_dir, "b2", 0), [events("LOG", "x")], finalize=False)
    assert lake.pending() == [split(data_dir, "b1", 0), open_split]
    lake.sync()
    assert Lake(data_dir).pending() == [], "the ledger survives a restart"


def test_splits_are_taken_in_numeric_order(data_dir, events):
    for n in (10, 2, 9):
        write_split(split(data_dir, "b1", n), [events("LOG", "x")])
    assert [p.name for p in Lake(data_dir).pending()] == ["b1_2.mcap", "b1_9.mcap", "b1_10.mcap"]


def test_a_changed_split_is_converted_again(data_dir, events):
    path = write_split(split(data_dir, "b1", 0), [events("LOG", "x")])
    lake = Lake(data_dir)
    lake.sync()
    write_split(path, [events("LOG", "x"), events("LOG", "y")])
    assert lake.pending() == [path]


# -- views ---------------------------------------------------------------------------------------


@pytest.fixture
def scenario_lake(data_dir, events):
    write_split(split(data_dir, "b1", 0), scenario(events))
    lake = Lake(data_dir)
    lake.sync()
    return lake


def test_views_rebuild_episodes_incidents_executions_and_the_audit_trail(scenario_lake):
    con = scenario_lake.connect()
    episodes = con.execute(
        "SELECT fault_id, code, last_state, epoch(cleared_at) - epoch(confirmed_at), occurrences "
        "FROM fault_episodes ORDER BY fault_id"
    ).fetchall()
    assert episodes == [
        ("f1", "sensor.scan.stale", "CLEARED", 5.0, 1),
        ("f2", "drive.overcurrent", "ACTIVE", None, 3),
    ]

    incidents = con.execute(
        "SELECT incident_id, title, state, escalated, resolved_at IS NOT NULL FROM incidents "
        "ORDER BY incident_id"
    ).fetchall()
    assert incidents == [
        ("inc-1", "stale on lidar", "RESOLVED", False, True),
        ("inc-2", "overcurrent on motor", "RESOLVED", True, True),
    ]
    executions = con.execute(
        "SELECT execution_id, plan, requester, outcome, escalated, last_phase FROM executions "
        "ORDER BY execution_id"
    ).fetchall()
    assert executions == [
        ("x1", "restart", "RULE", "SUCCEEDED", False, "SUCCEEDED"),
        ("x2", "reset", "AGENT", "FAILED", True, "ESCALATED"),
    ]
    assert con.execute("SELECT operator, code FROM operator_actions").fetchall() == [("alice", "fault.clear")]
    assert con.execute("SELECT rule, dry_run FROM rule_matches").fetchall() == [("lidar-restart", False)]


def test_recent_events_join_the_lake_without_duplicates(scenario_lake):
    e = Events(start=T0 + datetime.timedelta(hours=1))
    e.seq = 16  # the hub's ring buffer overlaps the lake's last two events
    recent = [model.row(_as_msg(e("LOG", "late.event"))) for _ in range(5)]
    con = scenario_lake.connect(recent=model.table(recent))
    assert con.execute("SELECT count(*), max(seq) FROM events").fetchone() == (18 + 3, 21)


# -- analytics -----------------------------------------------------------------------------------


def test_kpis_and_analytics(scenario_lake):
    con = scenario_lake.connect()
    since, until = T0 - datetime.timedelta(hours=1), T0 + datetime.timedelta(hours=1)
    k = analytics.kpis(con, since, until)
    assert (k["events"], k["faults_confirmed"], k["mttr_s"]) == (18, 2, 5.0)
    assert (k["incidents_opened"], k["incidents_open"], k["incidents_escalated"]) == (2, 0, 0)
    assert k["auto_recovery_rate"] == 0.5, "two resolved, one of them after an escalation"
    assert (k["recoveries_finished"], k["recovery_success_rate"]) == (2, 0.5)

    codes = analytics.top_codes(con, since, until)
    assert [(c["code"], c["episodes"]) for c in codes] == [("drive.overcurrent", 1), ("sensor.scan.stale", 1)]
    assert codes[-1]["cumulative_share"] == 1.0

    comps = {c["component"]: c for c in analytics.components(con, since, until)}
    assert comps["motor"]["worst_severity"] == "CRITICAL" and comps["motor"]["open_faults"] == 1
    assert comps["lidar"]["mtbf_h"] == 2.0 and comps["lidar"]["mttr_s"] == 5.0

    plans = {p["plan"]: p for p in analytics.plans(con, since, until)}
    assert plans["restart"]["success_rate"] == 1.0 and plans["reset"]["escalated"] == 1
    assert analytics.rules(con, since, until)[0]["fires"] == 1
    assert sum(b["events"] for b in analytics.event_rate(con, since, until)) == 18
    assert analytics.heatmap(con, since, until, bucket_s=3600)
    assert analytics.flapping(con, since, until, min_cycles=1)[0]["cycles"] == 1
    assert analytics.flapping(con, since, until, min_cycles=2) == []


def test_an_empty_lake_still_answers(data_dir):
    con = Lake(data_dir).connect()
    since, until = analytics.window(24)
    assert analytics.kpis(con, since, until)["events"] == 0
    assert analytics.components(con, since, until) == []


# -- retention -----------------------------------------------------------------------------------


def test_retention_deletes_converted_splits_only_and_never_the_newest_day(data_dir, events):
    old = write_split(split(data_dir, "b1", 0), [events("LOG", "x")])
    write_split(split(data_dir, "b1", 1), [events("LOG", "x")])
    unconverted = write_split(split(data_dir, "b2", 0), [events("LOG", "x")], finalize=False)
    week_ago = time.time() - 8 * 86400
    for p in (old, split(data_dir, "b1", 1), unconverted):
        os.utime(p, (week_ago, week_ago))
    lake = Lake(data_dir, retention=Retention(bags_max_age_days=1))
    lake.sync()
    report = lake.enforce_retention(dry_run=True)
    assert sorted(report.deleted_splits) == [str(old), str(split(data_dir, "b1", 1))]
    assert old.exists(), "a dry run deletes nothing"
    lake.enforce_retention()
    assert not (data_dir / "bags" / "b1").exists(), "the emptied bag goes, metadata and all"
    assert unconverted.exists(), "never before conversion"
    assert lake.layout.partitions(), "the newest day stays"


def test_retention_by_size_and_black_box_budget(data_dir, events):
    e = Events(step=datetime.timedelta(days=1))
    write_split(split(data_dir, "b1", 0), [e("LOG", "x") for _ in range(3)])
    box = data_dir / "blackbox" / "bb_1"
    box.mkdir(parents=True)
    for i in range(3):
        (box / f"bb_1_{i}.mcap").write_bytes(b"x" * 1000)
        os.utime(box / f"bb_1_{i}.mcap", (1000 + i, 1000 + i))
    lake = Lake(data_dir, retention=Retention(lake_max_bytes=1, blackbox_max_bytes=1500))
    lake.sync()
    report = lake.enforce_retention()
    assert len(report.deleted_partitions) == 2, "down to the newest day, which always stays"
    assert sorted(p.name for p in box.iterdir()) == ["bb_1_2.mcap"], "oldest snapshots first"


# -- service and CLI -----------------------------------------------------------------------------


def test_the_service_converts_reports_and_survives_a_bad_split(data_dir, events):
    write_split(split(data_dir, "b1", 0), [events("LOG", "x")])
    bad = split(data_dir, "b1", 1)
    bad.write_bytes(b"\x89MCAP0\r\n" + b"garbage" + b"\x89MCAP0\r\n")  # looks finalized, isn't MCAP
    logs = []
    service = LakeService(Lake(data_dir), log=logs.append)
    write_split(split(data_dir, "b1", 2), [events("LOG", "x")])
    status = service.run_once()
    assert "b1_1.mcap" in status["last_error"], "the bad split is reported"
    assert list(status["splits_failed"]) == [str(bad)]
    assert status["converted_total"] == 2, "the split after the bad one is converted too"
    on_disk = json.loads(storage.Layout(data_dir).status.read_text())
    assert on_disk["schema"] == "jarvis.lake.status/v1" and on_disk["rows"] == 2
    assert service.run_once()["last_error"] == "", "a failed split is not retried until it changes"


def test_cli(data_dir, events, capsys):
    write_split(split(data_dir, "b1", 0), scenario(events))
    base = ["--data-dir", str(data_dir)]
    assert cli.main([*base, "sync"]) == 0
    assert "18 events" in capsys.readouterr().out
    assert cli.main([*base, "sql", "SELECT count(*) AS n FROM incidents", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out) == [{"n": 2}]
    assert (
        cli.main([*base, "sql", "SELECT (count(*) = 2)::INTEGER AS ok FROM incidents", "--format", "csv"])
        == 0
    )
    assert capsys.readouterr().out == "ok\n1\n", "plain lines, for shell scripts"
    assert cli.main([*base, "status", "--ros-args", "-r", "__node:=x"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"] == 18
    assert cli.main([*base, "kpis", "--hours", str(24 * 365 * 10)]) == 0
    assert json.loads(capsys.readouterr().out)["kpis"]["events"] == 18
    assert cli.main([*base, "convert", str(data_dir / "nope.txt")]) == 2


def test_parquet_files_carry_the_lake_schema(scenario_lake):
    files = list(scenario_lake.layout.events.rglob("*.parquet"))
    assert files and all(pq.read_schema(f).names == model.COLUMNS for f in files)


class _Obj(dict):
    __getattr__ = dict.__getitem__


def _as_msg(d):
    """A test event dict as attribute access, like a decoded message."""
    out = _Obj({k: v for k, v in d.items()})
    out["source"] = _Obj(d["source"])
    out["stamp"] = _Obj(d["stamp"])
    out["recv_stamp"] = _Obj(d["recv_stamp"])
    out["attrs"] = [_Obj(a) for a in d["attrs"]]
    return out


def test_gib_constant():
    assert GIB == 1 << 30


def test_only_the_adapters_import_ros():
    package = pathlib.Path(__file__).resolve().parents[1] / "jarvis_lake"
    offenders = [
        str(f.relative_to(package))
        for f in package.rglob("*.py")
        if "adapters" not in f.parts and re.search(r"^\s*(import|from) rclpy", f.read_text(), re.M)
    ]
    assert offenders == []
