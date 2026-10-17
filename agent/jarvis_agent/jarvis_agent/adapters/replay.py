# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""An EventQuery over recorded events (a JSONL export, an eval case, a test): no gateway, no ROS.

It rebuilds what the lake's views give the gateway (incidents, fault episodes, executions) from
the events alone, the same way.
"""

import fnmatch
import json
import pathlib

from jarvis_agent.events import attr, component, incident_of, level, ns
from jarvis_agent.model import SEVERITY_LEVEL

TERMINAL = ("SUCCEEDED", "FAILED", "ABORTED", "REJECTED", "EXPIRED")


def load_jsonl(path):
    return [json.loads(line) for line in pathlib.Path(path).read_text().splitlines() if line.strip()]


def episodes(events):
    out = {}
    for e in events:
        if e.get("kind") != "FAULT_STATE" or not attr(e, "fault_id"):
            continue
        f = out.setdefault(
            attr(e, "fault_id"),
            {
                "fault_id": attr(e, "fault_id"),
                "code": e["code"],
                "component": component(e),
                "severity_level": 0,
                "first_seen": e["stamp"],
                "confirmed_at": None,
                "cleared_at": None,
                "occurrences": None,
                "muted": False,
                "incident_id": "",
                "last_state": "",
            },
        )
        state = attr(e, "state")
        f["severity_level"] = max(f["severity_level"], level(e))
        if state in ("ACTIVE", "LATCHED") and f["confirmed_at"] is None:
            f["confirmed_at"] = e["stamp"]
        if state == "CLEARED":
            f["cleared_at"] = e["stamp"]
        f["occurrences"] = attr(e, "occurrences", f["occurrences"])
        f["muted"] = f["muted"] or bool(attr(e, "muted_by"))
        f["incident_id"] = e.get("correlation_id") or f["incident_id"]
        f["last_state"] = state
    return list(out.values())


def executions(events):
    out = {}
    for e in events:
        execution_id = attr(e, "execution_id")
        if e.get("kind") != "RECOVERY" or not execution_id:
            continue
        x = out.setdefault(
            execution_id,
            {
                "execution_id": execution_id,
                "plan": attr(e, "plan"),
                "requester": attr(e, "requester"),
                "requester_id": attr(e, "requester_id", ""),
                "dry_run": bool(attr(e, "dry_run")),
                "incident_id": None,
                "created_at": e["stamp"],
                "started_at": None,
                "finished_at": None,
                "outcome": None,
                "escalated": False,
                "last_phase": "",
            },
        )
        phase = attr(e, "phase")
        x["incident_id"] = e.get("correlation_id") or x["incident_id"]
        if phase == "STARTED" and x["started_at"] is None:
            x["started_at"] = e["stamp"]
        if phase in TERMINAL:
            x["finished_at"], x["outcome"] = e["stamp"], phase
        x["escalated"] = x["escalated"] or phase == "ESCALATED"
        x["last_phase"] = phase
    return list(out.values())


class ReplayQuery:
    def __init__(self, events, plans=()):
        self.all = sorted(events, key=lambda e: int(e.get("seq") or 0))
        self._plans = list(plans)

    def incident(self, incident_id):
        timeline = [e for e in self.all if incident_of(e) == incident_id]
        changes = [
            e
            for e in timeline
            if e.get("kind") == "SYSTEM" and e["code"].startswith("jarvis.incident.") and attr(e, "state")
        ]
        if not changes:
            return None
        states = [attr(e, "state") for e in changes]
        return {
            "incident_id": incident_id,
            "component": component(changes[0]),
            "title": changes[0].get("message", ""),
            "severity_level": max(level(e) for e in changes),
            "opened_at": changes[0]["stamp"],
            "escalated": "ESCALATED" in states,
            "state": states[-1],
            "timeline": timeline,
            "faults": episodes(timeline),
            "executions": [x for x in executions(timeline) if x["incident_id"] == incident_id],
        }

    def events(
        self,
        since=None,
        until=None,
        min_severity=None,
        component="",
        kinds=(),
        limit=1000,
        codes=(),
        correlation_id="",
    ):
        lo, hi = ns(since), ns(until)
        min_level = SEVERITY_LEVEL.get(min_severity or "", 0)
        out = [
            e
            for e in self.all
            if (lo is None or ns(e["stamp"]) >= lo)
            and (hi is None or ns(e["stamp"]) < hi)
            and level(e) >= min_level
            and (not component or e.get("source", {}).get("component") == component)
            and (not kinds or e.get("kind") in kinds)
            and (not codes or any(fnmatch.fnmatchcase(e["code"], c) for c in codes))
            and (not correlation_id or incident_of(e) == correlation_id)
        ]
        return out[:limit]

    def event(self, event_id):
        return next((e for e in self.all if e["id"] == event_id), None)

    def active_faults(self):
        return [
            {
                "fault_id": f["fault_id"],
                "code": f["code"],
                "source": {"component": f["component"]},
                "state": f["last_state"],
                "incident_id": f["incident_id"],
            }
            for f in episodes(self.all)
            if f["last_state"] not in ("CLEARED", "")
        ]

    def fault_history(self, code, limit=200):
        found = [f for f in episodes(self.all) if f["code"] == code]
        return sorted(found, key=lambda f: ns(f["first_seen"]) or 0, reverse=True)[:limit]

    def recoveries(self, limit=500):
        return sorted(executions(self.all), key=lambda x: ns(x["created_at"]) or 0, reverse=True)[:limit]

    def plans(self):
        return list(self._plans)

    def rule(self, rule_id):
        return None  # a recording has the rules' matches, not their definitions

    def now_ns(self):
        """The time of the last event: replays run in their own time."""
        return ns(self.all[-1]["stamp"]) if self.all else 0
