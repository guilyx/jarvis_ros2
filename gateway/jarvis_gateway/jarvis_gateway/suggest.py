# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Rule suggestions (roadmap 8.4): the rules operators keep doing by hand.

    GET /api/v1/rules:suggest?hours=168&min_runs=2

An operator who runs the same plan after the same fault, again and again, is doing a rule's job.
Over the history window, each recovery an operator asked for is tied to the fault it answered: the
open fault of its incident (the recovery's correlation id), else the open fault its args name (its
component, or one of its attributes), else the only open fault. Runs are grouped by fault code,
plan and args, where an arg equal to the fault's component or to one of its attributes becomes a
template (`{{ event.source.component }}`, `{{ event.attrs.node }}`), so runs on different
components fold into one rule. A group of at least `min_runs` runs that no rule of the robot already
covers (one that matches the code and recovers with the plan) is a suggestion, with a draft rule
for the rule lab to backtest.
"""

import datetime
import json
import pathlib
import re
import statistics

import yaml

from jarvis_gateway.backtest import MAX_EVENTS

OPEN = ("ACTIVE", "LATCHED")
SEVERITY = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3, "CRITICAL": 4}
ENDED = ("SUCCEEDED", "FAILED", "ABORTED", "REJECTED", "EXPIRED")
MAX_SUGGESTIONS = 20
MAX_EVIDENCE = 20


def _attrs(event):
    return event.get("attrs") or {}


def _stamp(event):
    return datetime.datetime.fromisoformat(event["stamp"].replace("Z", "+00:00"))


def _component(event):
    return (event.get("source") or {}).get("component", "")


def template(args, fault):
    """The run's args, with what came from the fault as templates over the rule's event."""
    component = _component(fault)
    attrs = {k: v for k, v in _attrs(fault).items() if isinstance(v, str) and v}
    out = {}
    for name, value in sorted(args.items()):
        if value and value == component:
            out[name] = "{{ event.source.component }}"
        elif value and attrs.get(name) == value:
            out[name] = f"{{{{ event.attrs.{name} }}}}"
        else:
            key = next((k for k, v in sorted(attrs.items()) if v == value and value), None)
            out[name] = f"{{{{ event.attrs.{key} }}}}" if key else value
    return out


def _answered(recovery, args, open_faults):
    """The fault an operator's recovery answered, or None. The candidates: the open faults of its
    incident, else those its args name, else the only open fault. A hang opens its symptoms too
    (the relay's rate drops with its topic), so of the candidates, the likeliest root: not muted,
    the one that explains most of the args, the most severe, the first to open."""
    faults = list(open_faults.values())  # in the order they opened
    named = {v for v in args.values() if v}

    def explained(f):
        return len(named & ({_component(f)} | {str(v) for v in _attrs(f).values()}))

    incident = recovery.get("correlation_id", "")
    candidates = [f for f in faults if incident and f.get("correlation_id") == incident]
    candidates = candidates or [f for f in faults if explained(f)]
    if not candidates:
        return faults[0] if len(faults) == 1 else None
    opened = {id(f): i for i, f in enumerate(faults)}
    return max(
        candidates,
        key=lambda f: (
            not _attrs(f).get("muted_by"),
            explained(f),
            SEVERITY.get(f.get("severity"), 0),
            -opened[id(f)],
        ),
    )


def covered(rules_dir):
    """(code, plan) pairs the robot's rules already recover: a rule matching the code (anywhere in
    `when`) whose actions recover with the plan."""
    pairs = set()
    for path in sorted(pathlib.Path(rules_dir).glob("*.y*ml")) if rules_dir else []:
        try:
            doc = yaml.safe_load(path.read_text()) or {}
        except (OSError, yaml.YAMLError):
            continue
        for rule in (doc.get("rules") or []) if isinstance(doc, dict) else []:
            if not isinstance(rule, dict):
                continue
            when = json.dumps(rule.get("when"))
            for action in rule.get("then") or []:
                plan = ((action or {}).get("recover") or {}).get("plan") if isinstance(action, dict) else None
                if plan:
                    pairs |= {(code, plan) for code in re.findall(r"[a-z0-9_]+(?:\.[a-z0-9_]+)+", when)}
    return pairs


def rule_id(plan, code):
    slug = re.sub(r"[^a-z0-9]+", "-", f"{code}-{plan}".lower()).strip("-")
    return slug[:60].rstrip("-")


def draft(suggestion):
    """The suggestion as a rules document: what the operators did, as a rule."""
    s = suggestion
    who = ", ".join(s["operators"]) or "operators"
    where = ", ".join(s["components"]) or "?"
    rule = {
        "id": s["id"],
        "description": f"Operators ran {s['plan']} by hand {s['runs']} times after {s['code']} "
        f"({where}; {who}). Jarvis does it for them.",
        "partition_by": ["source.component"],
        "when": {"event": {"kind": "FAULT_STATE", "code": s["code"], "attrs.state": "ACTIVE"}},
        "then": [{"recover": {"plan": s["plan"], **({"args": s["args"]} if s["args"] else {})}}],
        "policy": {"cooldown": "30s", "max_fires": {"count": 3, "per": "10m"}},
    }
    return yaml.safe_dump({"schema": "jarvis.rules/v1", "rules": [rule]}, sort_keys=False, width=100)


def suggest(history, hours, rules_dir="", min_runs=2, now=None):
    """Suggestions from the last `hours` of history, most runs first."""
    now = now or datetime.datetime.now(datetime.UTC)
    events = history.events(
        since=now - datetime.timedelta(hours=hours),
        kinds=["FAULT_STATE", "RECOVERY"],
        newest_first=False,
        limit=MAX_EVENTS,
    )
    open_faults = {}  # fault_id (or code@component) -> its last ACTIVE/LATCHED event
    runs = {}  # execution_id -> run
    for e in events:
        attrs = _attrs(e)
        if e.get("kind") == "FAULT_STATE":
            key = attrs.get("fault_id") or f"{e.get('code')}@{_component(e)}"
            if attrs.get("state") in OPEN:
                open_faults.setdefault(key, e)  # as it went active: when, and with what
            elif attrs.get("state") == "CLEARED":
                open_faults.pop(key, None)
            continue
        execution = attrs.get("execution_id")
        if not execution:
            continue
        if execution in runs:
            if attrs.get("phase") in ENDED:
                runs[execution]["outcome"] = attrs["phase"]
            continue
        if attrs.get("requester") != "OPERATOR" or attrs.get("dry_run") in (True, "true"):
            continue
        args = {k[4:]: str(v) for k, v in attrs.items() if k.startswith("arg.")}
        fault = _answered(e, args, open_faults)
        if fault is None:
            continue
        runs[execution] = {
            "execution_id": execution,
            "at": e["stamp"],
            "operator": attrs.get("requester_id", ""),
            "plan": attrs.get("plan", ""),
            "code": fault.get("code", ""),
            "component": _component(fault),
            "fault_event_id": fault.get("id", ""),
            "after_s": round(max(0.0, (_stamp(e) - _stamp(fault)).total_seconds()), 1),
            "args": template(args, fault),
            "outcome": attrs.get("phase") if attrs.get("phase") in ENDED else "",
        }

    groups = {}
    for run in runs.values():
        key = (run["code"], run["plan"], json.dumps(run["args"], sort_keys=True))
        groups.setdefault(key, []).append(run)
    done = covered(rules_dir)
    out = []
    for (code, plan, _), group in groups.items():
        if len(group) < min_runs or (code, plan) in done:
            continue
        s = {
            "id": rule_id(plan, code),
            "code": code,
            "plan": plan,
            "args": group[0]["args"],
            "runs": len(group),
            "succeeded": sum(1 for r in group if r["outcome"] == "SUCCEEDED"),
            "operators": sorted({r["operator"] for r in group if r["operator"]}),
            "components": sorted({r["component"] for r in group if r["component"]}),
            "first": group[0]["at"],
            "last": group[-1]["at"],
            "median_after_s": statistics.median(r["after_s"] for r in group),
            "evidence": [
                {
                    k: r[k]
                    for k in (
                        "at",
                        "execution_id",
                        "operator",
                        "component",
                        "fault_event_id",
                        "after_s",
                        "outcome",
                    )
                }
                for r in group[-MAX_EVIDENCE:]
            ],
        }
        s["yaml"] = draft(s)
        out.append(s)
    out.sort(key=lambda s: (-s["runs"], -s["succeeded"], s["id"]))
    seen = {}
    for s in out:  # the same plan after the same fault, with other args: number them
        n = seen[s["id"]] = seen.get(s["id"], 0) + 1
        if n > 1:
            s["id"] = f"{s['id'][:57]}-{n}"
            s["yaml"] = draft(s)
    return {"hours": hours, "events": len(events), "suggestions": out[:MAX_SUGGESTIONS]}
