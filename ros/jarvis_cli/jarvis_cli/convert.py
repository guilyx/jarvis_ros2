# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""jarvis_interfaces messages as the JSON of schemas/*.schema.json (the core's codec, in Python)."""

import datetime

from jarvis_interfaces.msg import Attribute, Event, Execution, Fault, StepProgress


def _names(cls, prefix):
    return {getattr(cls, n): n[len(prefix) :] for n in dir(cls) if n.startswith(prefix)}


KINDS = _names(Event, "KIND_")
SEVERITIES = _names(Event, "SEVERITY_")
FAULT_STATES = _names(Fault, "STATE_")
EXECUTION_STATES = _names(Execution, "STATE_")
REQUESTERS = _names(Execution, "REQUESTER_")
STEP_STATES = _names(StepProgress, "STATE_")


def iso8601(t):
    """builtin_interfaces/Time as "2026-10-13T09:14:03.120000000Z" (UTC, nine digits)."""
    base = datetime.datetime.fromtimestamp(t.sec, tz=datetime.UTC)
    return base.strftime("%Y-%m-%dT%H:%M:%S") + f".{t.nanosec:09d}Z"


def is_zero(t):
    return t.sec == 0 and t.nanosec == 0


def attr_value(a):
    if a.type == Attribute.TYPE_INT:
        return a.int_value
    if a.type == Attribute.TYPE_DOUBLE:
        return a.double_value
    if a.type == Attribute.TYPE_BOOL:
        return a.bool_value
    return a.string_value


def attrs(values):
    return {a.key: attr_value(a) for a in values}


def make_attrs(mapping):
    """{key: str|int|float|bool} as Attribute[] (plan arguments travel as strings)."""
    out = []
    for key, value in mapping.items():
        a = Attribute(key=key)
        if isinstance(value, bool):
            a.type, a.bool_value = Attribute.TYPE_BOOL, value
        elif isinstance(value, int):
            a.type, a.int_value = Attribute.TYPE_INT, value
        elif isinstance(value, float):
            a.type, a.double_value = Attribute.TYPE_DOUBLE, value
        else:
            a.type, a.string_value = Attribute.TYPE_STRING, str(value)
        out.append(a)
    return out


def event_to_dict(e):
    """An Event message as schemas/event.schema.json, field for field like jarvis_core::to_json."""
    out = {
        "id": e.id,
        "seq": str(e.seq),
        "stamp": iso8601(e.stamp),
        "source": {
            "robot": e.source.robot,
            "node": e.source.node,
            "component": e.source.component,
            "host": e.source.host,
        },
        "kind": KINDS.get(e.kind, "UNSPECIFIED"),
        "code": e.code,
        "severity": SEVERITIES.get(e.severity, "INFO"),
    }
    if not is_zero(e.recv_stamp):
        out["recv_stamp"] = iso8601(e.recv_stamp)
    optional = {
        "message": e.message,
        "attrs": attrs(e.attrs),
        "tags": list(e.tags),
        "payload": e.payload,
        "payload_schema": e.payload_schema,
        "correlation_id": e.correlation_id,
        "causation_id": e.causation_id,
    }
    out.update({k: v for k, v in optional.items() if v})
    return out


def parse_assignments(items):
    """["node=/a", "count=3"] as {"node": "/a", "count": "3"}; raises ValueError on a bad item."""
    out = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise ValueError(f"'{item}' is not key=value")
        out[key] = value
    return out
