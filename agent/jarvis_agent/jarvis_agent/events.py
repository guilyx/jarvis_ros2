# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Events as the gateway and the schemas write them (JSON), and what the agent needs from them."""

import datetime
import re

from jarvis_agent.model import SEVERITY_LEVEL

_STAMP = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:?\d\d)?$")


def ns(stamp):
    """An ISO 8601 stamp (nanosecond precision) as integer nanoseconds since the epoch, or None."""
    if not stamp:
        return None
    m = _STAMP.match(stamp)
    if not m:
        return None
    base = datetime.datetime.fromisoformat(m.group(1) + (m.group(3) or "Z").replace("Z", "+00:00"))
    return int(base.timestamp()) * 1_000_000_000 + int((m.group(2) or "0").ljust(9, "0"))


def iso_ns(value):
    base = datetime.datetime.fromtimestamp(value // 1_000_000_000, tz=datetime.UTC)
    return base.strftime("%Y-%m-%dT%H:%M:%S") + f".{value % 1_000_000_000:09d}Z"


def level(event):
    return SEVERITY_LEVEL.get(event.get("severity"), 20)


def component(event):
    return (event.get("source") or {}).get("component", "")


def attr(event, key, default=None):
    return (event.get("attrs") or {}).get(key, default)


def incident_of(event):
    return event.get("correlation_id") or attr(event, "incident_id") or ""


def clock(stamp):
    """09:14:03.120 (UTC): short enough for a timeline, precise enough to order it."""
    t = ns(stamp)
    if t is None:
        return "?"
    return iso_ns(t)[11:23]


def seconds(delta_ns):
    s = delta_ns / 1e9
    if abs(s) < 10:
        return f"{s:.1f} s"
    if abs(s) < 120:
        return f"{s:.0f} s"
    return f"{s / 60:.1f} min"
