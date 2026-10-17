# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""MCAP splits written the way rosbag2 writes them: ros2msg schemas built from the repo's .msg files."""

import datetime
import json
import pathlib

import pytest
from mcap_ros2.writer import Writer

REPO = pathlib.Path(__file__).resolve().parents[3]
MSG = REPO / "ros" / "jarvis_interfaces" / "msg"
SEPARATOR = "=" * 80

KIND = {
    "LOG": 1,
    "STATE": 2,
    "FAULT_RAISE": 3,
    "FAULT_CLEAR": 4,
    "FAULT_STATE": 5,
    "RULE_MATCH": 6,
    "RECOVERY": 7,
    "INSIGHT": 8,
    "OPERATOR": 9,
    "SYSTEM": 10,
}
SEVERITY = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40, "CRITICAL": 50}
T0 = datetime.datetime(2026, 10, 14, 23, 50, tzinfo=datetime.UTC)  # ten minutes before midnight


def event_msgdef():
    deps = [
        ("builtin_interfaces/Time", "int32 sec\nuint32 nanosec\n"),
        ("jarvis_interfaces/Attribute", (MSG / "Attribute.msg").read_text()),
        ("jarvis_interfaces/Source", (MSG / "Source.msg").read_text()),
    ]
    text = (MSG / "Event.msg").read_text()
    for name, body in deps:
        text += f"\n{SEPARATOR}\nMSG: {name}\n{body}"
    return text


def stamp(t):
    ns = int(t.timestamp()) * 1_000_000_000 + t.microsecond * 1000
    return {"sec": ns // 1_000_000_000, "nanosec": ns % 1_000_000_000}


def attrs(values):
    out = []
    for key, value in values.items():
        a = {"key": key, "type": 0, "string_value": "", "int_value": 0, "double_value": 0.0}
        a["bool_value"] = False
        if isinstance(value, bool):
            a["type"], a["bool_value"] = 3, value
        elif isinstance(value, int):
            a["type"], a["int_value"] = 1, value
        elif isinstance(value, float):
            a["type"], a["double_value"] = 2, value
        else:
            a["string_value"] = str(value)
        out.append(a)
    return out


class Events:
    """Builds Event messages (as dicts) with increasing seq and time."""

    def __init__(self, start=T0, step=datetime.timedelta(seconds=1)):
        self.t = start
        self.step = step
        self.seq = 0

    def __call__(self, kind, code, severity="INFO", component="lidar", correlation="", at=None, **a):
        self.seq += 1
        when = at or self.t
        self.t = when + self.step
        return {
            "id": f"ev-{self.seq:04d}",
            "seq": self.seq,
            "stamp": stamp(when),
            "recv_stamp": stamp(when),
            "source": {"robot": "bot", "node": "/n", "component": component, "host": ""},
            "kind": KIND[kind],
            "code": code,
            "severity": SEVERITY[severity],
            "message": a.pop("message", ""),
            "attrs": attrs(a),
            "tags": [],
            "payload": "",
            "payload_schema": "",
            "correlation_id": correlation,
            "causation_id": "",
        }


def write_split(path, events, chunk_size=1 << 20, finalize=True, other_topic=False):
    """An MCAP split with /jarvis/events (and optionally another topic, which the lake ignores)."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "wb")  # noqa: SIM115 - an unfinalized split leaves the file as the crash would
    writer = Writer(f, chunk_size=chunk_size)
    schema = writer.register_msgdef("jarvis_interfaces/msg/Event", event_msgdef())
    clock = writer.register_msgdef("builtin_interfaces/msg/Time", "int32 sec\nuint32 nanosec\n")
    for e in events:
        t = e["recv_stamp"]["sec"] * 1_000_000_000 + e["recv_stamp"]["nanosec"]
        writer.write_message("/jarvis/events", schema, e, log_time=t, publish_time=t)
        if other_topic:
            writer.write_message("/clock", clock, e["stamp"], log_time=t, publish_time=t)
    if finalize:
        writer.finish()
        f.close()
    else:
        f.flush()
        f.close()  # no footer, no index: what a crash leaves
    return path


@pytest.fixture
def events():
    return Events()


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data"


def as_json(text):
    return json.loads(text)
