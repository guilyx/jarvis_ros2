# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Triage: deterministic work before any model sees the window (docs/design/06-agent.md §4).

- Repeats collapse into one row (`sensor.scan.stale ×37, first … last …`); transitions (fault
  states, rule matches, recoveries, operator actions, incident changes) always stay one row each.
- Rows are ranked by severity, kind and closeness to what triggered the analysis.
- Facts a model gets wrong from raw rows are computed here: orderings, durations, counts, which
  faults were muted as symptoms, how recoveries went, and what history says about each code.
- Configured attributes and fields are redacted before anything is rendered (core/redaction.py).
"""

import dataclasses

from jarvis_agent.core.redaction import Redactor
from jarvis_agent.events import clock, component, incident_of, level, ns, seconds

COLLAPSIBLE = {"LOG", "FAULT_RAISE", "FAULT_CLEAR", "STATE"}
KIND_WEIGHT = {
    "FAULT_STATE": 30,
    "RECOVERY": 25,
    "RULE_MATCH": 20,
    "OPERATOR": 20,
    "SYSTEM": 15,
    "INSIGHT": 10,
    "FAULT_RAISE": 5,
}
TERMINAL = ("SUCCEEDED", "FAILED", "ABORTED", "REJECTED", "EXPIRED")


@dataclasses.dataclass
class Row:
    events: list  # oldest first; one unless collapsed

    @property
    def first(self):
        return self.events[0]

    @property
    def last(self):
        return self.events[-1]

    @property
    def count(self):
        return len(self.events)

    @property
    def ids(self):
        return [e["id"] for e in self.events]


@dataclasses.dataclass
class Triage:
    rows: list  # in time order
    facts: list
    ranked: list  # rows, most relevant first
    root: dict | None  # the incident's first confirmed, unmuted fault episode
    symptoms: list  # episodes muted as symptoms of another
    failed: list  # the incident's recoveries that did not succeed
    succeeded: list
    precursors: list  # WARN+ reports on the root's component shortly before it was first reported
    plan_record: dict  # plan id -> {code -> [successes, runs]} from earlier incidents
    own: list = dataclasses.field(default_factory=list)  # the incident's own events
    earlier: dict = dataclasses.field(default_factory=dict)  # code -> its earlier episodes


def redact(events, patterns):
    """Copies of the events with what the patterns name hidden (core/redaction.py: `attrs.gps.*`,
    `message`, `@identities` ...)."""
    return Redactor(patterns).records(events)


def collapse(events):
    rows, open_rows = [], {}
    for e in events:
        if e.get("kind") not in COLLAPSIBLE:
            rows.append(Row([e]))
            continue
        key = (e.get("kind"), e["code"], component(e), e.get("severity"))
        row = open_rows.get(key)
        if row is None:
            row = open_rows[key] = Row([e])
            rows.append(row)
        else:
            row.events.append(e)
    return rows


def rank(rows, trigger_ns=None):
    def score(row):
        e = row.last
        s = level(e) + KIND_WEIGHT.get(e.get("kind"), 0) + min(row.count, 20)
        t = ns(e["stamp"])
        if trigger_ns is not None and t is not None:
            s += max(0.0, 30 - abs(trigger_ns - t) / 1e9 / 10)  # within 5 min of the trigger
        return s

    return sorted(rows, key=score, reverse=True)


PRECURSOR_KINDS = ("FAULT_RAISE", "FAULT_STATE", "LOG", "STATE")
PRECURSOR_S = 120


def triage(window, redact_patterns=()):
    events = redact(window.events, redact_patterns)
    rows = collapse(events)
    incident = window.correlation_id
    # The incident's own events tell its story; the context around it is for precursors only.
    own = [e for e in events if incident_of(e) == incident] if incident else events
    facts = []
    episodes = sorted(window.faults, key=lambda f: ns(f.get("confirmed_at") or f.get("first_seen")) or 0)
    confirmed = [f for f in episodes if f.get("confirmed_at")]
    symptoms = [f for f in confirmed if f.get("muted")]
    roots = [f for f in confirmed if not f.get("muted")] or confirmed
    root = roots[0] if roots else None
    # When the root was first reported (its first raise comes before its first state), and history
    # is what was known then: a replay must not see its own future.
    before_ns = first_report(root, events, window.history.get(root["code"], ())) if root else None

    for f in confirmed:
        start, end = ns(f["confirmed_at"]), ns(f.get("cleared_at"))
        how = f"cleared after {seconds(end - start)}" if end else f"still {f.get('last_state', 'open')}"
        muted = " (muted: a symptom)" if f.get("muted") else ""
        facts.append(f"{f['code']} on {f['component']}: confirmed {clock(f['confirmed_at'])}, {how}{muted}")

    firsts = {}
    for e in own:
        if level(e) >= 30 and e.get("kind") in ("FAULT_STATE", "FAULT_RAISE") and e["code"] not in firsts:
            firsts[e["code"]] = ns(e["stamp"])
    if len(firsts) > 1:
        order = sorted(firsts, key=firsts.get)
        t0 = firsts[order[0]]
        chain = [order[0]] + [f"{c} (+{seconds(firsts[c] - t0)})" for c in order[1:6]]
        facts.append("first reports: " + " → ".join(chain))

    for row in collapse(own):
        if row.count >= 3:
            e = row.first
            facts.append(
                f"{e['code']} ×{row.count} on {component(e) or '?'} "
                f"({clock(e['stamp'])} → {clock(row.last['stamp'])})"
            )

    failed, succeeded = [], []
    for x in window.executions:
        outcome = x.get("outcome") or x.get("last_phase")
        took = ""
        if x.get("started_at") and x.get("finished_at"):
            took = f" after {seconds(ns(x['finished_at']) - ns(x['started_at']))}"
        dry = ", dry run" if x.get("dry_run") else ""
        facts.append(
            f"recovery {x['plan']} ({x.get('requester')} {x.get('requester_id', '')}{dry}): {outcome}{took}"
        )
        if outcome == "SUCCEEDED":
            succeeded.append(x)
        elif outcome in TERMINAL:
            failed.append(x)

    precursors = []
    if root:
        # Not before another incident on the same component ended: what came then is explained there.
        floor = before_ns - PRECURSOR_S * 1e9
        for e in events:
            t = ns(e["stamp"])
            other = incident_of(e) not in ("", incident) and component(e) == root["component"]
            if other and t is not None and t < before_ns:
                floor = max(floor, t)
        for e in events:
            t = ns(e["stamp"])
            if (
                t is not None
                and floor < t < before_ns
                and e.get("kind") in PRECURSOR_KINDS
                and level(e) >= 30
                and component(e) == root["component"]
                and e["code"] != root["code"]
                and incident_of(e) in ("", incident)  # another incident's events are explained there
            ):
                precursors.append(e)
        if precursors:
            p = precursors[-1]
            facts.append(f"{p['code']} preceded {root['code']} by {seconds(before_ns - ns(p['stamp']))}")

    earlier = {}
    for code, past in sorted(window.history.items()):
        found = [
            f
            for f in past
            if f.get("incident_id") != incident
            and f.get("confirmed_at")
            and (before_ns is None or ns(f["confirmed_at"]) < before_ns)
        ]
        earlier[code] = found
        if found:
            last = max(found, key=lambda f: ns(f["confirmed_at"]))
            facts.append(
                f"{code}: {len(found)} earlier episode(s), the last at {clock(last['confirmed_at'])}"
            )
    record = plan_record(window, before_ns)
    for plan, codes in sorted(record.items()):
        for code, (ok, runs) in sorted(codes.items()):
            facts.append(f"{plan} fixed {code} {ok} of {runs} time(s) before")

    trigger_ns = None
    if window.trigger.ref:
        for e in events:
            if e["id"] == window.trigger.ref:
                trigger_ns = ns(e["stamp"])
    if trigger_ns is None and root:
        trigger_ns = ns(root["confirmed_at"])
    return Triage(
        rows=rows,
        facts=facts[:20],
        ranked=rank(rows, trigger_ns),
        root=root,
        symptoms=symptoms,
        failed=failed,
        succeeded=succeeded,
        precursors=precursors,
        plan_record=record,
        own=own,
        earlier=earlier,
    )


def first_report(root, events, history=(), lookback_s=30):
    """The first raise of the root's code on its component shortly before its episode began (and
    after the previous episode of that code cleared: those raises were its own)."""
    start = ns(root.get("first_seen") or root["confirmed_at"])
    floor = start - lookback_s * 1e9
    for f in history:
        cleared = ns(f.get("cleared_at"))
        if f.get("fault_id") != root.get("fault_id") and cleared is not None and cleared < start:
            floor = max(floor, cleared)
    raises = [
        ns(e["stamp"])
        for e in events
        if e.get("kind") == "FAULT_RAISE"
        and e["code"] == root["code"]
        and component(e) == root["component"]
        and floor < ns(e["stamp"]) <= start
    ]
    return min([start, *raises])


def plan_record(window, before_ns=None):
    """{plan: {code: [successes, runs]}}: how each plan did on earlier incidents with each code."""
    codes_by_incident = {}
    for code, episodes in window.history.items():
        for f in episodes:
            if f.get("incident_id") and not f.get("muted"):
                codes_by_incident.setdefault(f["incident_id"], set()).add(code)
    out = {}
    for x in window.recoveries:
        if x.get("dry_run") or x.get("incident_id") == window.correlation_id:
            continue
        if before_ns is not None and (ns(x.get("created_at")) or 0) >= before_ns:
            continue
        outcome = x.get("outcome")
        if outcome not in ("SUCCEEDED", "FAILED"):
            continue
        for code in codes_by_incident.get(x.get("incident_id"), ()):
            ok_runs = out.setdefault(x["plan"], {}).setdefault(code, [0, 0])
            ok_runs[0] += outcome == "SUCCEEDED"
            ok_runs[1] += 1
    return out
