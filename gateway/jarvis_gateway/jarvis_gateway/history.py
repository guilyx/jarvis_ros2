# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""History: the lake's views over the converted recordings, plus the gateway's recent events.

Every query sees one timeline: the lake up to its newest seq, then what the gateway received since
(jarvis_lake.views joins them without duplicates). All results are JSON-ready.
"""

import contextlib
import json
import time

from jarvis_lake import analytics, model, views
from jarvis_lake.lake import Lake

SEVERITY_LEVELS = model.SEVERITY_LEVELS
EVENT_COLUMNS = ", ".join(model.COLUMNS)


class History:
    def __init__(self, data_dir, state):
        self.lake = Lake(data_dir)
        self.state = state
        self._lake_max = (0.0, 0)  # (checked at, seq)

    def connect(self):
        return views.connect(self.lake.layout, recent=self.state.recent.table())

    def lake_max_seq(self, max_age_s=30.0):
        """The newest seq the lake has (cached for a while: it changes once per split)."""
        checked, seq = self._lake_max
        if time.monotonic() - checked > max_age_s:
            con = views.connect(self.lake.layout)
            seq = con.execute("SELECT coalesce(max(seq), 0) FROM lake_events").fetchone()[0]
            self._lake_max = (time.monotonic(), seq)
        return seq

    # -- events ------------------------------------------------------------------------------------

    def events(
        self,
        after_seq=None,
        before_seq=None,
        since=None,
        until=None,
        kinds=(),
        codes=(),
        min_severity=None,
        component="",
        correlation_id="",
        q="",
        limit=200,
        newest_first=True,
    ):
        """Events matching the filters, as schema JSON. `codes` are globs (`ros.*`)."""
        where, params = [], []
        if after_seq is not None:
            where.append("seq > ?")
            params.append(int(after_seq))
        if before_seq is not None:
            where.append("seq < ?")
            params.append(int(before_seq))
        if since is not None:
            where.append("ts >= ?")
            params.append(since)
        if until is not None:
            where.append("ts < ?")
            params.append(until)
        if kinds:
            where.append(f"kind IN ({', '.join('?' * len(kinds))})")
            params.extend(kinds)
        if codes:
            where.append("(" + " OR ".join("code GLOB ?" for _ in codes) + ")")
            params.extend(codes)
        if min_severity:
            where.append("severity_level >= ?")
            params.append(SEVERITY_LEVELS.get(min_severity, 0))
        if component:
            where.append("component = ?")
            params.append(component)
        if correlation_id:
            where.append("(correlation_id = ? OR json_extract_string(attrs, '$.incident_id') = ?)")
            params.extend([correlation_id, correlation_id])
        if q:
            where.append("(message ILIKE ? OR code ILIKE ? OR id = ?)")
            params.extend([f"%{q}%", f"%{q}%", q])
        sql = f"SELECT {EVENT_COLUMNS} FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY seq {'DESC' if newest_first else 'ASC'} LIMIT ?"
        params.append(int(limit))
        return [model.event_from_row(r) for r in self._rows(self.connect(), sql, params)]

    def event(self, event_id):
        """One event with its causal parent and children, or None."""
        con = self.connect()
        rows = self._rows(con, f"SELECT {EVENT_COLUMNS} FROM events WHERE id = ?", [event_id])
        if not rows:
            return None
        event = model.event_from_row(rows[0])
        parent = event.get("causation_id")
        parents = (
            self._rows(con, f"SELECT {EVENT_COLUMNS} FROM events WHERE id = ?", [parent]) if parent else []
        )
        children = self._rows(
            con, f"SELECT {EVENT_COLUMNS} FROM events WHERE causation_id = ? ORDER BY seq", [event_id]
        )
        return {
            "event": event,
            "parent": model.event_from_row(parents[0]) if parents else None,
            "children": [model.event_from_row(r) for r in children],
        }

    # -- faults, incidents, executions -------------------------------------------------------------

    def fault_episodes(self, limit=200, code="", component=""):
        where, params = [], []
        if code:
            where.append("code GLOB ?")
            params.append(code)
        if component:
            where.append("component = ?")
            params.append(component)
        sql = "SELECT * FROM fault_episodes"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY first_seen DESC LIMIT ?"
        return self._json(self.connect(), sql, [*params, limit])

    def fault_transitions(self, fault_id):
        return self._json(
            self.connect(),
            "SELECT ts, seq, state, prev_state, occurrences, message, severity FROM fault_transitions "
            "WHERE fault_id = ? ORDER BY seq",
            [fault_id],
        )

    def incidents(self, state="", limit=100):
        sql, params = "SELECT * FROM incidents", []
        if state == "open":
            sql += " WHERE state IN ('OPEN', 'RECOVERING', 'ESCALATED')"
        elif state:
            sql += " WHERE state = ?"
            params.append(state.upper())
        sql += " ORDER BY opened_at DESC LIMIT ?"
        return self._json(self.connect(), sql, [*params, limit])

    def incident(self, incident_id):
        """An incident with its timeline (every event of it, oldest first), or None."""
        con = self.connect()
        found = self._json(con, "SELECT * FROM incidents WHERE incident_id = ?", [incident_id])
        if not found:
            return None
        timeline = self.events(correlation_id=incident_id, limit=5000, newest_first=False)
        executions = self._json(
            con, "SELECT * FROM executions WHERE incident_id = ? ORDER BY created_at", [incident_id]
        )
        faults = self._json(
            con, "SELECT * FROM fault_episodes WHERE incident_id = ? ORDER BY first_seen", [incident_id]
        )
        return {**found[0], "timeline": timeline, "executions": executions, "faults": faults}

    def executions(self, limit=100, plan=""):
        sql, params = "SELECT * FROM executions", []
        if plan:
            sql += " WHERE plan = ?"
            params.append(plan)
        sql += " ORDER BY created_at DESC LIMIT ?"
        return self._json(self.connect(), sql, [*params, limit])

    def execution(self, execution_id):
        con = self.connect()
        found = self._json(con, "SELECT * FROM executions WHERE execution_id = ?", [execution_id])
        if not found:
            return None
        events = self._rows(
            con,
            f"SELECT {EVENT_COLUMNS} FROM events WHERE kind = 'RECOVERY' "
            "AND json_extract_string(attrs, '$.execution_id') = ? ORDER BY seq",
            [execution_id],
        )
        return {**found[0], "events": [model.event_from_row(r) for r in events]}

    def rule_matches(self, rule, limit=50):
        return self._json(
            self.connect(),
            "SELECT * FROM rule_matches WHERE rule = ? ORDER BY seq DESC LIMIT ?",
            [rule, limit],
        )

    def insights(self, limit=50):
        return [_decoded(e) for e in self.events(kinds=["INSIGHT"], limit=limit)]

    def insight(self, event_id):
        """An insight (its JSON payload decoded) with the events it cites, or None."""
        found = self.event(event_id)
        if not found or found["event"]["kind"] != "INSIGHT":
            return None
        return {**found, "event": _decoded(found["event"])}

    def audit(self, limit=200):
        """Who did what: operator actions, and recoveries operators asked for, decided or stopped."""
        sql = (
            f"SELECT {EVENT_COLUMNS} FROM events WHERE kind = 'OPERATOR' OR (kind = 'RECOVERY' AND ("
            "json_extract_string(attrs, '$.requester') = 'OPERATOR' "
            "OR json_extract_string(attrs, '$.phase') = 'APPROVED' "
            "OR message LIKE '%rejected by %' OR message LIKE '%cancelled by %')) "
            "ORDER BY seq DESC LIMIT ?"
        )
        return [model.event_from_row(r) for r in self._rows(self.connect(), sql, [int(limit)])]

    # -- analytics ---------------------------------------------------------------------------------

    def analytics(self, name, hours=24.0, **kwargs):
        since, until = analytics.window(hours)
        fn = {
            "kpis": analytics.kpis,
            "event_rate": analytics.event_rate,
            "top_codes": analytics.top_codes,
            "components": analytics.components,
            "heatmap": analytics.heatmap,
            "flapping": analytics.flapping,
            "plans": analytics.plans,
            "rules": analytics.rules,
        }[name]
        return fn(self.connect(), since, until, **kwargs)

    # -- helpers -----------------------------------------------------------------------------------

    @staticmethod
    def _rows(con, sql, params):
        cur = con.execute(sql, params)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]

    def _json(self, con, sql, params):
        cur = con.execute(sql, params)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, (analytics._json(v) for v in r), strict=True)) for r in cur.fetchall()]


def _decoded(event):
    payload = event.get("payload")
    if isinstance(payload, str) and payload:
        with contextlib.suppress(ValueError):
            event["payload"] = json.loads(payload)
    return event
