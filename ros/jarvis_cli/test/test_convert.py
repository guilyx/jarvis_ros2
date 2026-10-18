# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The Python export writes the core's JSON: the C++ `jarvis backtest` reads what we write."""

import json
import pathlib
import shutil
import subprocess

import pytest
import rosbag2_py
from builtin_interfaces.msg import Time
from jarvis_cli.convert import event_to_dict, iso8601, make_attrs, parse_assignments
from jarvis_cli.verb.export import export
from jarvis_interfaces.msg import Attribute, Event, EventBatch
from rclpy.serialization import serialize_message


def fault_state(n, state, seconds):
    e = Event(
        id=f"evt-{n}",
        seq=n,
        kind=Event.KIND_FAULT_STATE,
        code="ros.topic.stale",
        severity=Event.SEVERITY_ERROR,
    )
    e.stamp = Time(sec=seconds, nanosec=120_000_000)
    e.recv_stamp = Time(sec=seconds, nanosec=124_000_000)
    e.source.robot, e.source.node, e.source.component = "bot", "/jarvis/fault_manager", "lidar"
    e.attrs = make_attrs(
        {"state": state, "fault_id": "f1", "occurrences": 2, "confirmed": True, "ratio": 0.5}
    )
    e.correlation_id = "inc-1"
    return e


def test_event_to_dict_matches_the_schema_layout():
    d = event_to_dict(fault_state(7, "ACTIVE", 1760433243))
    assert d == {
        "id": "evt-7",
        "seq": "7",
        "stamp": "2025-10-14T09:14:03.120000000Z",
        "recv_stamp": "2025-10-14T09:14:03.124000000Z",
        "source": {"robot": "bot", "node": "/jarvis/fault_manager", "component": "lidar", "host": ""},
        "kind": "FAULT_STATE",
        "code": "ros.topic.stale",
        "severity": "ERROR",
        "attrs": {"state": "ACTIVE", "fault_id": "f1", "occurrences": 2, "confirmed": True, "ratio": 0.5},
        "correlation_id": "inc-1",
    }
    assert "recv_stamp" not in event_to_dict(Event(id="x", code="a.b")), "zero times are left out"


def test_helpers():
    assert iso8601(Time(sec=0, nanosec=5)) == "1970-01-01T00:00:00.000000005Z"
    assert parse_assignments(["node=/a", "x==y", "empty="]) == {"node": "/a", "x": "=y", "empty": ""}
    with pytest.raises(ValueError):
        parse_assignments(["oops"])
    types = [a.type for a in make_attrs({"s": "x", "i": 1, "d": 1.5, "b": False})]
    assert types == [Attribute.TYPE_STRING, Attribute.TYPE_INT, Attribute.TYPE_DOUBLE, Attribute.TYPE_BOOL]


def write_bag(bag, events, batched):
    """A bag as the recorder writes it (/jarvis/event_batches), or as it did before 1.0."""
    writer = rosbag2_py.SequentialWriter()
    writer.open(
        rosbag2_py.StorageOptions(uri=str(bag), storage_id="mcap"), rosbag2_py.ConverterOptions("", "")
    )
    topic, kind = ("/jarvis/event_batches", "EventBatch") if batched else ("/jarvis/events", "Event")
    writer.create_topic(
        rosbag2_py.TopicMetadata(
            id=0, name=topic, type=f"jarvis_interfaces/msg/{kind}", serialization_format="cdr"
        )
    )
    if batched:  # the first two together, the last alone
        groups = [events[:2], events[2:]]
        for group in groups:
            t = group[-1].recv_stamp.sec * 1_000_000_000
            writer.write(topic, serialize_message(EventBatch(events=group)), t)
    else:
        for e in events:
            writer.write(topic, serialize_message(e), e.recv_stamp.sec * 1_000_000_000)
    del writer  # closes the bag


@pytest.mark.skipif(shutil.which("jarvis") is None, reason="the jarvis tool is not on PATH")
@pytest.mark.parametrize("batched", [True, False], ids=["batched", "before-1.0"])
def test_an_exported_bag_backtests(tmp_path, batched):
    bag = tmp_path / "bag"
    states = [("ACTIVE", 100), ("CLEARED", 105), ("ACTIVE", 200)]
    write_bag(bag, [fault_state(n, s, t) for n, (s, t) in enumerate(states, start=1)], batched)

    events = tmp_path / "events.jsonl"
    with open(events, "w") as out:
        assert export(bag, out) == 3
    lines = events.read_text().splitlines()
    assert json.loads(lines[0])["attrs"]["state"] == "ACTIVE"

    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "r.yaml").write_text(
        "schema: jarvis.rules/v1\nrules:\n"
        "  - id: stale\n"
        "    when: { event: { kind: FAULT_STATE, code: ros.topic.stale, attrs.state: ACTIVE } }\n"
        "    then: [{ notify: { message: stale } }]\n"
    )
    out = subprocess.run(
        ["jarvis", "backtest", "--rules", str(rules), "--json", str(events)],
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(out.stdout)
    assert report["events"] == 3
    assert [m["cause_id"] for m in report["matches"]] == ["evt-1", "evt-3"]
    assert report["comparison"]["recorded"] == 0, "no live rule engine in this bag"
    assert pathlib.Path(bag).exists()
