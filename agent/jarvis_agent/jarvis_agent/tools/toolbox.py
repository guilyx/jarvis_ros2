# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The model's tools (docs/design/06-agent.md §5): read-only, over the same EventQuery as the
windows. Every event is redacted before the model sees it, every result is bounded, and every event
id a result shows counts as seen (the harness accepts those as evidence). `submit_insight` ends the
loop (core/analyst.py).

`propose_recovery` (offered at autonomy supervised or autonomous) only records a proposal: the agent
routes it after the insight is checked, and the recovery gates decide (core/agent.py).

Absent on purpose: shell, service calls, parameter writes, files. Breadth comes from the plan
catalog, which humans review.
"""

import contextlib
import json
import re

from jarvis_agent.core.checks import validate_args
from jarvis_agent.core.triage import redact
from jarvis_agent.core.windows import Proposal
from jarvis_agent.events import component
from jarvis_agent.model import SEVERITIES

MAX_RESULT_CHARS = 12_000
MAX_RUNBOOK_CHARS = 8_000
MAX_ROWS = 100
KINDS = [
    "LOG",
    "STATE",
    "FAULT_RAISE",
    "FAULT_CLEAR",
    "FAULT_STATE",
    "RULE_MATCH",
    "RECOVERY",
    "INSIGHT",
    "OPERATOR",
    "SYSTEM",
]


def _fn(name, description, properties=None, required=()):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties or {}, "required": list(required)},
        },
    }


_STR = {"type": "string"}
_LIMIT = {"type": "integer", "minimum": 1, "maximum": MAX_ROWS}

SPECS = [
    _fn(
        "query_events",
        "Events, oldest first, as compact rows (id, time, kind, severity, code, component, message, "
        "attributes). Narrow it down: at most 100 rows.",
        {
            "from": {"type": "string", "description": "ISO 8601, inclusive"},
            "to": {"type": "string", "description": "ISO 8601, exclusive"},
            "codes": {"type": "array", "items": _STR, "description": "code globs, e.g. sensor.*"},
            "kinds": {"type": "array", "items": {"type": "string", "enum": KINDS}},
            "min_severity": {"type": "string", "enum": list(SEVERITIES)},
            "component": _STR,
            "correlation_id": {"type": "string", "description": "an incident id"},
            "limit": _LIMIT,
        },
    ),
    _fn(
        "get_event",
        "One event in full: all its attributes and its payload.",
        {"event_id": _STR},
        ["event_id"],
    ),
    _fn("get_active_faults", "The faults active now, with their state."),
    _fn(
        "get_fault_history",
        "Past episodes of a fault code, newest first: when it was raised, confirmed and cleared, on "
        "which component, in which incident.",
        {"code": _STR, "limit": _LIMIT},
        ["code"],
    ),
    _fn(
        "get_incident",
        "An incident: its state, fault episodes, recoveries and timeline.",
        {"incident_id": _STR},
        ["incident_id"],
    ),
    _fn(
        "get_state",
        "The latest STATE values in the window (lifecycle states, modes, levels), by component.",
        {"component": _STR},
    ),
    _fn(
        "list_recovery_plans",
        "The recovery catalog: each plan's parameters, preconditions, who may run it, its "
        "agent_policy, and how it did on each fault code before.",
    ),
    _fn("get_recovery_plan", "One recovery plan in full.", {"plan_id": _STR}, ["plan_id"]),
    _fn(
        "get_recovery_history",
        "Past recovery executions and their outcomes, newest first.",
        {"plan_id": _STR, "limit": _LIMIT},
    ),
    _fn("get_rule", "A rule's definition and its recent matches.", {"rule_id": _STR}, ["rule_id"]),
    _fn(
        "get_runbook",
        "The runbook of a fault code, if its catalog entry has one: what the fault means, what to "
        "check and what fixes it, as the robot's team wrote it.",
        {"code": _STR},
        ["code"],
    ),
]
NAMES = {s["function"]["name"] for s in SPECS}
MAX_PROPOSALS = 3
PROPOSE = _fn(
    "propose_recovery",
    "Ask Jarvis to run a plan of the catalog now. It never runs directly: the autonomy policy and the "
    "recovery gates decide, and an operator may have to approve it. Only for a plan whose "
    "preconditions hold, with all its required arguments, citing the events that call for it. At "
    "most 3; mention them in your recommendations too.",
    {
        "plan_id": _STR,
        "args": {"type": "object", "additionalProperties": {"type": "string"}},
        "rationale": {"type": "string", "description": "Why, for the operator who approves it"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence": {"type": "array", "items": _STR, "description": "Event ids"},
    },
    ["plan_id", "args", "rationale", "confidence", "evidence"],
)


def row(e):
    """An event as the model reads it."""
    out = {
        "id": e["id"],
        "time": e.get("stamp", ""),
        "kind": e.get("kind", ""),
        "severity": e.get("severity", ""),
        "code": e.get("code", ""),
    }
    if component(e):
        out["component"] = component(e)
    if e.get("correlation_id"):
        out["incident"] = e["correlation_id"]
    if e.get("message"):
        out["message"] = e["message"][:300]
    attrs = e.get("attrs") or {}
    if attrs:
        out["attrs"] = {k: str(v)[:200] for k, v in list(attrs.items())[:16]}
    return out


def _error(text):
    return json.dumps({"error": text})


ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}")


def _id(value, what):
    """An id the model passes in a URL path: an id, nothing else (no `/`, no `..`)."""
    value = str(value)
    if not ID.fullmatch(value) or ".." in value:
        raise ValueError(f"'{value[:80]}' is not {what}")
    return value


class Toolbox:
    def __init__(self, query, window, result, redact_patterns=(), proposals=False):
        self.query = query
        self.window = window
        self.result = result
        self.redact = tuple(redact_patterns)
        self.names = NAMES | ({"propose_recovery"} if proposals else set())

    def run(self, name, arguments):
        """The tool's result as JSON text for the model; errors are results too (the model can
        correct itself). Event ids in what it returns join the window's seen ids."""
        if name not in self.names:
            return _error(f"no tool '{name}'; the tools are {', '.join(sorted(self.names))}")
        try:
            args = json.loads(arguments or "{}")
        except ValueError:
            return _error("the arguments are not JSON")
        if not isinstance(args, dict):
            return _error("the arguments must be a JSON object")
        try:
            out = getattr(self, f"_{name}")(args)
        except (KeyError, TypeError, ValueError) as e:
            return _error(f"bad arguments: {e}")
        except Exception as e:  # noqa: BLE001 - the gateway may fail; the model hears it, the loop goes on
            return _error(f"{name} failed: {type(e).__name__}")
        text = _bounded(out)
        with contextlib.suppress(ValueError):  # cut mid-value: what it shows is not citable
            self.window.seen.update(_event_ids(json.loads(text)))
        return text

    def _rows(self, events):
        return [row(e) for e in redact(list(events), self.redact)]

    def _query_events(self, a):
        limit = min(int(a.get("limit") or MAX_ROWS), MAX_ROWS)
        events = self.query.events(
            since=a.get("from"),
            until=a.get("to"),
            min_severity=a.get("min_severity"),
            component=a.get("component") or "",
            kinds=tuple(a.get("kinds") or ()),
            codes=tuple(a.get("codes") or ()),
            correlation_id=a.get("correlation_id") or "",
            limit=limit + 1,
        )
        out = {"events": self._rows(events[:limit])}
        if len(events) > limit:
            out["more"] = "narrow the filters or the time range for the rest"
        return out

    def _get_event(self, a):
        found = self.query.event(_id(a["event_id"], "an event id"))
        if not found:
            return {"error": f"no event '{a['event_id']}'"}
        (e,) = redact([found], self.redact)
        full = row(e)
        full["attrs"] = {k: str(v)[:1000] for k, v in (e.get("attrs") or {}).items()}
        for key in ("payload", "payload_schema", "seq", "causation_id"):
            if e.get(key):
                full[key] = str(e[key])[:4000]
        full["source"] = e.get("source") or {}
        return full

    def _get_active_faults(self, a):
        return {"faults": self.query.active_faults()}

    def _get_fault_history(self, a):
        limit = min(int(a.get("limit") or 20), MAX_ROWS)
        return {"episodes": self.query.fault_history(a["code"], limit=limit)}

    def _get_incident(self, a):
        found = self.query.incident(_id(a["incident_id"], "an incident id"))
        if not found:
            return {"error": f"no incident '{a['incident_id']}'"}
        out = {k: v for k, v in found.items() if k not in ("timeline", "faults", "executions")}
        out["faults"] = found.get("faults") or []
        out["recoveries"] = found.get("executions") or []
        out["timeline"] = self._rows((found.get("timeline") or [])[:MAX_ROWS])
        return out

    def _get_state(self, a):
        wanted = a.get("component") or ""
        latest = [e for (c, _), e in sorted(self.window.state.items()) if not wanted or c == wanted]
        return {"state": self._rows(latest)}

    def _list_recovery_plans(self, a):
        record = self.result.plan_record
        plans = []
        for p in self.window.plans:
            plans.append(
                {
                    **{k: p[k] for k in ("id", "description", "params", "preconditions") if k in p},
                    "agent_policy": p.get("agent_policy", "never"),
                    "allowed_requesters": p.get("allowed_requesters", []),
                    "record": {
                        code: f"fixed {ok} of {runs}" for code, (ok, runs) in record.get(p["id"], {}).items()
                    },
                }
            )
        return {"plans": plans}

    def _get_recovery_plan(self, a):
        found = next((p for p in self.window.plans if p["id"] == a["plan_id"]), None)
        return found or {"error": f"no plan '{a['plan_id']}'"}

    def _get_recovery_history(self, a):
        limit = min(int(a.get("limit") or 20), MAX_ROWS)
        found = [x for x in self.query.recoveries() if not a.get("plan_id") or x.get("plan") == a["plan_id"]]
        return {"executions": found[:limit]}

    def _propose_recovery(self, a):
        plan = next((p for p in self.window.plans if p["id"] == a["plan_id"]), None)
        if plan is None:
            return {"error": f"no plan '{a['plan_id']}' in the catalog"}
        policy = plan.get("agent_policy", "never")
        if policy in ("never", "advise") or "AGENT" not in (plan.get("allowed_requesters") or []):
            return {"error": f"{plan['id']} takes no agent proposals (agent_policy {policy}): recommend it"}
        if not isinstance(a.get("args"), dict):
            raise ValueError("args must be an object")
        args = {k: (str(v).lower() if isinstance(v, bool) else str(v)) for k, v in a["args"].items()}
        problems = validate_args(plan, args)
        if problems:
            return {"error": f"{plan['id']}: {'; '.join(problems)}"}
        confidence = float(a["confidence"])
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        evidence = [i for i in a.get("evidence") or [] if isinstance(i, str) and i in self.window.ids]
        if not evidence:
            return {"error": "cite at least one event id you were shown"}
        if len(self.window.proposals) >= MAX_PROPOSALS:
            return {"error": f"at most {MAX_PROPOSALS} proposals"}
        self.window.proposals.append(
            Proposal(plan["id"], args, str(a.get("rationale") or "")[:500], confidence, evidence)
        )
        return {"recorded": plan["id"], "next": "Jarvis routes it once you submit your insight"}

    def _get_rule(self, a):
        found = self.query.rule(_id(a["rule_id"], "a rule id"))
        if not found:
            return {"error": f"no rule '{a['rule_id']}' (or its definition is not available here)"}
        out = {k: v for k, v in found.items() if k != "matches"}
        if isinstance(out.get("yaml"), str):
            out["yaml"] = out["yaml"][:4000]
        matches = found.get("matches") or []
        out["matches"] = self._rows(matches[-20:]) if matches and "kind" in matches[0] else matches[-20:]
        return out

    def _get_runbook(self, a):
        found = self.query.runbook(_id(a["code"], "a fault code"))
        if not found:
            return {"error": f"no runbook for '{a['code']}' in the fault catalog"}
        out = {"code": a["code"], "runbook": found[:MAX_RUNBOOK_CHARS]}
        if len(found) > MAX_RUNBOOK_CHARS:
            out["cut"] = f"the first {MAX_RUNBOOK_CHARS} characters of {len(found)}"
        return out


def _bounded(out):
    """JSON text of at most MAX_RESULT_CHARS: the longest lists lose their tail until it fits."""
    text = json.dumps(out, separators=(",", ":"), default=str)
    while len(text) > MAX_RESULT_CHARS:
        lists = [(len(v), k) for k, v in out.items() if isinstance(v, list) and len(v) > 1]
        if not lists:
            return text[:MAX_RESULT_CHARS]  # one huge value: cut (still data, maybe not JSON)
        _, key = max(lists)
        out = {**out, key: out[key][: len(out[key]) // 2], "truncated": True}
        text = json.dumps(out, separators=(",", ":"), default=str)
    return text


def _event_ids(value):
    """Ids of the event rows in a result (dicts with an id and a kind)."""
    if isinstance(value, dict):
        found = {value["id"]} if isinstance(value.get("id"), str) and "kind" in value else set()
        for v in value.values():
            found |= _event_ids(v)
        return found
    if isinstance(value, list):
        return set().union(*(_event_ids(v) for v in value)) if value else set()
    return set()
