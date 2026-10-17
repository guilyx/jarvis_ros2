# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Events as lake rows: the columns of schemas/event.schema.json, flattened for Parquet.

The decoder works on any object with the fields of jarvis_interfaces/msg/Event (a message decoded
from MCAP without ROS, or an rclpy message), so the constants below are repeated from the .msg
files instead of imported. A test keeps them in step.
"""

import datetime
import json

import pyarrow as pa

KINDS = {
    0: "UNSPECIFIED",
    1: "LOG",
    2: "STATE",
    3: "FAULT_RAISE",
    4: "FAULT_CLEAR",
    5: "FAULT_STATE",
    6: "RULE_MATCH",
    7: "RECOVERY",
    8: "INSIGHT",
    9: "OPERATOR",
    10: "SYSTEM",
}
SEVERITIES = {10: "DEBUG", 20: "INFO", 30: "WARN", 40: "ERROR", 50: "CRITICAL"}
SEVERITY_LEVELS = {name: level for level, name in SEVERITIES.items()}
ATTR_STRING, ATTR_INT, ATTR_DOUBLE, ATTR_BOOL = 0, 1, 2, 3

EVENTS_TOPIC = "/jarvis/events"
EVENT_TYPE = "jarvis_interfaces/msg/Event"

TIMESTAMP = pa.timestamp("ns", tz="UTC")
SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("seq", pa.uint64()),
        ("stamp", TIMESTAMP),
        ("recv_stamp", TIMESTAMP),  # null when the event never went through the hub
        ("ts", TIMESTAMP),  # recv_stamp, else stamp: the time to sort and bucket by
        ("robot", pa.string()),
        ("node", pa.string()),
        ("component", pa.string()),
        ("host", pa.string()),
        ("kind", pa.string()),
        ("code", pa.string()),
        ("severity", pa.string()),
        ("severity_level", pa.uint8()),
        ("message", pa.string()),
        ("attrs", pa.string()),  # JSON object
        ("tags", pa.list_(pa.string())),
        ("payload", pa.string()),
        ("payload_schema", pa.string()),
        ("correlation_id", pa.string()),
        ("causation_id", pa.string()),
        ("split", pa.string()),  # the MCAP split the row came from
    ]
)
COLUMNS = SCHEMA.names


def nanoseconds(t):
    """builtin_interfaces/Time as integer nanoseconds since the epoch (None when zero)."""
    ns = int(t.sec) * 1_000_000_000 + int(t.nanosec)
    return ns or None


def attr_value(a):
    if a.type == ATTR_INT:
        return int(a.int_value)
    if a.type == ATTR_DOUBLE:
        return float(a.double_value)
    if a.type == ATTR_BOOL:
        return bool(a.bool_value)
    return a.string_value


def row(msg, split=""):
    """One Event message as a lake row (a dict over COLUMNS)."""
    stamp = nanoseconds(msg.stamp)
    recv = nanoseconds(msg.recv_stamp)
    severity = SEVERITIES.get(msg.severity, "INFO")
    return {
        "id": msg.id,
        "seq": int(msg.seq),
        "stamp": stamp,
        "recv_stamp": recv,
        "ts": recv or stamp,
        "robot": msg.source.robot,
        "node": msg.source.node,
        "component": msg.source.component,
        "host": msg.source.host,
        "kind": KINDS.get(msg.kind, "UNSPECIFIED"),
        "code": msg.code,
        "severity": severity,
        "severity_level": SEVERITY_LEVELS[severity],
        "message": msg.message,
        "attrs": json.dumps({a.key: attr_value(a) for a in msg.attrs}, separators=(",", ":")),
        "tags": list(msg.tags),
        "payload": msg.payload,
        "payload_schema": msg.payload_schema,
        "correlation_id": msg.correlation_id,
        "causation_id": msg.causation_id,
        "split": split,
    }


def utc_date(ns):
    """The UTC calendar day of a nanosecond timestamp, as YYYY-MM-DD (the lake's partition key)."""
    return datetime.datetime.fromtimestamp(ns / 1e9, tz=datetime.UTC).strftime("%Y-%m-%d")


def table(rows):
    """Rows as an Arrow table with the lake schema."""
    return pa.Table.from_pylist([{c: r.get(c) for c in COLUMNS} for r in rows], schema=SCHEMA)
