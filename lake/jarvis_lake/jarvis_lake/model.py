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


# -- schema JSON (schemas/event.schema.json) <-> rows -------------------------------------------


def iso8601(ns):
    """Nanoseconds since the epoch as "2026-10-13T09:14:03.120000000Z" (the schema's format)."""
    if ns is None:
        return None
    sec, frac = divmod(int(ns), 1_000_000_000)
    base = datetime.datetime.fromtimestamp(sec, tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{frac:09d}Z"


def parse_iso8601(text):
    """The inverse of iso8601 (also accepts fewer fraction digits and +00:00). None for None/""."""
    if not text:
        return None
    text = text.strip().replace("+00:00", "Z")
    if text.endswith("Z"):
        text = text[:-1]
    base, _, frac = text.partition(".")
    dt = datetime.datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.UTC)
    return int(dt.timestamp()) * 1_000_000_000 + int((frac + "000000000")[:9] or 0)


def _ns(value):
    """A timestamp from a row (nanoseconds, or a datetime as DuckDB returns it) as nanoseconds."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.UTC)
        delta = value - datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)
        return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000
    return int(value)


def row_from_event(event, split=""):
    """An event as schema JSON (a dict) as a lake row."""
    source = event.get("source") or {}
    stamp = parse_iso8601(event.get("stamp"))
    recv = parse_iso8601(event.get("recv_stamp"))
    severity = event.get("severity") or "INFO"
    return {
        "id": event.get("id", ""),
        "seq": int(event.get("seq") or 0),
        "stamp": stamp,
        "recv_stamp": recv,
        "ts": recv or stamp,
        "robot": source.get("robot", ""),
        "node": source.get("node", ""),
        "component": source.get("component", ""),
        "host": source.get("host", ""),
        "kind": event.get("kind") or "UNSPECIFIED",
        "code": event.get("code", ""),
        "severity": severity,
        "severity_level": SEVERITY_LEVELS.get(severity, 20),
        "message": event.get("message", ""),
        "attrs": json.dumps(event.get("attrs") or {}, separators=(",", ":")),
        "tags": list(event.get("tags") or []),
        "payload": _payload_text(event.get("payload")),
        "payload_schema": event.get("payload_schema", ""),
        "correlation_id": event.get("correlation_id", ""),
        "causation_id": event.get("causation_id", ""),
        "split": split,
    }


def event_from_row(r):
    """A lake row (from Parquet or DuckDB) as schema JSON, empty optional fields left out."""
    out = {
        "id": r["id"],
        "seq": str(r["seq"]),
        "stamp": iso8601(_ns(r["stamp"])),
        "source": {
            "robot": r.get("robot") or "",
            "node": r.get("node") or "",
            "component": r.get("component") or "",
            "host": r.get("host") or "",
        },
        "kind": r["kind"],
        "code": r["code"],
        "severity": r["severity"],
    }
    recv = _ns(r.get("recv_stamp"))
    if recv:
        out["recv_stamp"] = iso8601(recv)
    attrs = r.get("attrs")
    optional = {
        "message": r.get("message"),
        "attrs": json.loads(attrs) if isinstance(attrs, str) and attrs else (attrs or {}),
        "tags": list(r.get("tags") or []),
        "payload": r.get("payload"),
        "payload_schema": r.get("payload_schema"),
        "correlation_id": r.get("correlation_id"),
        "causation_id": r.get("causation_id"),
    }
    out.update({k: v for k, v in optional.items() if v})
    return out


def _payload_text(payload):
    if payload is None or payload == "":
        return ""
    return payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
