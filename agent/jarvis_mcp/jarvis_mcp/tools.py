# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The tools an assistant gets: what Jarvis knows about the robot, through the gateway.

Reading needs a viewer token. Asking the agent needs an operator. Requesting a recovery needs an
operator and `--allow-recovery`: it is a request, not a command. It goes through every gate of
the orchestrator like anyone's (mode, preconditions, rate limits, locks, approval, four-eyes),
and it is a dry run unless the model asks otherwise.
"""

import json

from jarvis_mcp.gateway import GatewayError
from jarvis_mcp.server import ToolError

MAX_TEXT = 24_000  # characters per result: enough for a page of events, not a whole history
ANALYTICS = ("components", "top_codes", "flapping", "plans", "rules", "event_rate", "heatmap")
SEVERITIES = ("DEBUG", "INFO", "WARN", "ERROR", "CRITICAL")
READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}


def _tool(name, title, description, properties=None, required=(), annotations=READ):
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "required": list(required),
            "additionalProperties": False,
        },
        "annotations": annotations,
    }


def _str(description, **extra):
    return {"type": "string", "description": description, **extra}


def _int(description, minimum=1, maximum=1000, default=None):
    out = {"type": "integer", "description": description, "minimum": minimum, "maximum": maximum}
    if default is not None:
        out["default"] = default
    return out


TOOLS = [
    _tool(
        "jarvis_status",
        "Robot status",
        "Start here. The robot's state in one call: the gateway's health, the recovery mode, "
        "open faults by severity, open incidents, recoveries running or waiting for approval.",
    ),
    _tool(
        "list_faults",
        "Open faults",
        "The faults open now (PENDING, ACTIVE, LATCHED), with their code, component, severity, "
        "occurrences and the incident they belong to. A fault with muted_by set is a symptom of "
        "another (its root cause). With include_history, also the episodes that ended.",
        {
            "code": _str("glob on the fault code, e.g. ros.topic.* (optional)"),
            "component": _str("only this component (optional)"),
            "include_history": {"type": "boolean", "description": "also past episodes", "default": False},
        },
    ),
    _tool(
        "get_fault",
        "One fault",
        "One fault in detail: its freeze frame (the robot's state when it confirmed), its "
        "transitions, and the recoveries that ran for its incident.",
        {"fault_id": _str("the fault's id, from list_faults")},
        ["fault_id"],
    ),
    _tool(
        "get_runbook",
        "Runbook",
        "What the robot's team wrote to check and do about a fault code (Markdown). Follow it "
        "before suggesting anything else.",
        {"code": _str("a fault code, e.g. sensor.scan.stale")},
        ["code"],
    ),
    _tool(
        "list_incidents",
        "Incidents",
        "Incidents, newest first: faults of one component grouped with their symptoms and the "
        "recoveries for them. States: OPEN, RECOVERING, ESCALATED, RESOLVED, CLOSED.",
        {
            "state": _str(
                "only this state (optional)", enum=["OPEN", "RECOVERING", "ESCALATED", "RESOLVED", "CLOSED"]
            ),
            "limit": _int("how many", maximum=200, default=20),
        },
    ),
    _tool(
        "get_incident",
        "One incident",
        "One incident: its faults, recoveries, the agent's analysis and what operators did.",
        {"incident_id": _str("the incident's id (a fault's incident_id, or from list_incidents)")},
        ["incident_id"],
    ),
    _tool(
        "search_events",
        "Search events",
        "The event stream (faults, rule matches, recovery steps, insights, operator actions), "
        "newest first. Narrow it: an incident's events are those with its correlation_id.",
        {
            "correlation_id": _str("an incident id: its events only (optional)"),
            "code": _str("glob on the event code, e.g. jarvis.recovery.* (optional)"),
            "kind": _str(
                "only this kind (optional)",
                enum=[
                    "FAULT_RAISE",
                    "FAULT_CLEAR",
                    "FAULT_STATE",
                    "LOG",
                    "STATE",
                    "RULE",
                    "RECOVERY",
                    "INSIGHT",
                    "OPERATOR",
                    "SYSTEM",
                ],
            ),
            "min_severity": _str("at least this severity (optional)", enum=list(SEVERITIES)),
            "component": _str("only this component (optional)"),
            "since": _str("ISO 8601 time, e.g. 2026-10-18T09:00:00Z (optional)"),
            "text": _str("words in the message (optional)"),
            "limit": _int("how many", maximum=500, default=50),
        },
    ),
    _tool(
        "list_recoveries",
        "Recoveries",
        "Recovery executions: running, waiting for approval (with who asked and why), and the "
        "ones that finished, with each gate's verdict and step.",
        {"limit": _int("how many finished ones", maximum=200, default=20)},
    ),
    _tool(
        "list_plans",
        "Recovery plans",
        "The recovery plans the robot has: what each does, its parameters, who may request it and "
        "whether it needs approval. Only these can run; Jarvis has no other actions.",
    ),
    _tool(
        "analytics",
        "Analytics",
        "History from the robot's data lake over the last hours. components: health per "
        "component; top_codes: Pareto of fault codes; flapping: faults that come and go; plans: "
        "how often each recovery plan worked; rules: what each rule did; event_rate.",
        {
            "name": _str("which view", enum=list(ANALYTICS)),
            "hours": {
                "type": "number",
                "description": "the window",
                "minimum": 0.1,
                "maximum": 720,
                "default": 24,
            },
        },
        ["name"],
    ),
    _tool(
        "ask_agent",
        "Ask the Jarvis agent",
        "Ask the robot's own agent to analyse an incident or answer a question about the last "
        "minutes. It answers with hypotheses, each with the events that support it, and a "
        "recommended plan. Needs an operator token. Takes seconds to a minute.",
        {
            "incident_id": _str("the incident to explain (optional)"),
            "question": _str("a question (optional; one of the two is needed)"),
        },
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    ),
]

RECOVER = _tool(
    "request_recovery",
    "Request a recovery",
    "Ask Jarvis to run one of its recovery plans (see list_plans). This is a request: it goes "
    "through every gate (recovery mode, preconditions, rate limits, locks, approval) and may be "
    "refused or wait for an operator's approval. It is a dry run unless dry_run is false: every "
    "gate runs, nothing moves, and you see what would happen. Explain why in rationale; it is "
    "recorded under this token's name. Never request a recovery the user did not ask for.",
    {
        "plan_id": _str("a plan id from list_plans"),
        "args": {
            "type": "object",
            "description": "the plan's parameters",
            "additionalProperties": {"type": "string"},
        },
        "rationale": _str("why: what you saw, and what you expect it to fix"),
        "dry_run": {"type": "boolean", "description": "simulate only (the default)", "default": True},
        "incident_id": _str("the incident it is for (optional)"),
    },
    ["plan_id", "rationale"],
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)

INSTRUCTIONS = (
    "Jarvis manages faults and recoveries on a ROS 2 robot. Start with jarvis_status. To explain "
    "a problem: list_faults (a fault with muted_by is a symptom: look at its root), get_runbook "
    "for its code, get_incident and search_events with its correlation_id. Cite fault codes, "
    "components and times. Recoveries are requests that pass through gates: never request one "
    "the user did not ask for, and prefer a dry run first."
)


class Toolbox:
    def __init__(self, gateway, allow_recovery=False):
        self.gateway = gateway
        self.allow_recovery = allow_recovery

    def specs(self):
        return TOOLS + ([RECOVER] if self.allow_recovery else [])

    def call(self, name, arguments):
        try:
            out = getattr(self, "_" + name)(**arguments)
        except TypeError as e:
            raise ToolError(f"bad arguments for {name}: {e}") from None
        except GatewayError as e:
            hint = {401: " (the token is missing or wrong)", 403: " (this token's role may not)"}.get(
                e.status, ""
            )
            raise ToolError(f"the gateway refused: {e}{hint}") from None
        return _fit(out)

    # -- reads ---------------------------------------------------------------------------------------

    def _jarvis_status(self):
        g = self.gateway
        faults = g.get("/faults")["faults"]
        recoveries = g.get("/recoveries", limit=1)
        incidents = [
            i
            for i in g.get("/incidents", limit=200)["incidents"]
            if i.get("state") not in ("RESOLVED", "CLOSED")
        ]
        by_severity = {}
        for f in faults:
            if not f.get("muted_by"):
                by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1
        return {
            "health": g.get("/health"),
            "recovery_mode": recoveries["mode"],
            "open_faults": {
                "total": len(faults),
                "muted": sum(1 for f in faults if f.get("muted_by")),
                "by_severity": by_severity,
            },
            "open_incidents": [
                {k: i.get(k) for k in ("incident_id", "title", "state", "severity", "component", "opened_at")}
                for i in incidents[:10]
            ],
            "recoveries": {
                "running": [x["plan_id"] for x in recoveries["live"] if x["state"] == "RUNNING"],
                "waiting_for_approval": [
                    {
                        "execution_id": x["execution_id"],
                        "plan_id": x["plan_id"],
                        "requested_by": x["requester"],
                    }
                    for x in recoveries["live"]
                    if x["state"] == "PENDING_APPROVAL"
                ],
            },
        }

    def _list_faults(self, code="", component="", include_history=False):
        return self.gateway.get(
            "/faults", code=code, component=component, state="all" if include_history else "active"
        )

    def _get_fault(self, fault_id):
        return self.gateway.get(f"/faults/{_seg(fault_id)}")

    def _get_runbook(self, code):
        try:
            return self.gateway.get(f"/runbooks/{_seg(code)}")["markdown"]
        except GatewayError as e:
            if e.status == 404:
                return f"No runbook for {code}: the catalog has none for it."
            raise

    def _list_incidents(self, state="", limit=20):
        return self.gateway.get("/incidents", state=state, limit=limit)

    def _get_incident(self, incident_id):
        return self.gateway.get(f"/incidents/{_seg(incident_id)}")

    def _search_events(
        self, correlation_id="", code="", kind="", min_severity="", component="", since="", text="", limit=50
    ):
        return self.gateway.get(
            "/events",
            correlation_id=correlation_id,
            code=code,
            kind=kind,
            min_severity=min_severity,
            component=component,
            q=text,
            limit=limit,
            **({"from": since} if since else {}),
        )

    def _list_recoveries(self, limit=20):
        return self.gateway.get("/recoveries", limit=limit)

    def _list_plans(self):
        return self.gateway.get("/plans")

    def _analytics(self, name, hours=24):
        if name not in ANALYTICS:
            raise ToolError(f"no analytics '{name}': one of {', '.join(ANALYTICS)}")
        return self.gateway.get(f"/analytics/{name}", hours=hours)

    # -- the agent and recovery ----------------------------------------------------------------------

    def _ask_agent(self, incident_id="", question=""):
        if not incident_id and not question.strip():
            raise ToolError("ask about an incident (incident_id), or ask a question")
        stages = []
        for event, data in self.gateway.stream(
            "/agent/ask", {"incident_id": incident_id, "question": question}
        ):
            if event == "progress":
                stages.append(data.get("stage"))
            elif event == "insight":
                return {"stages": stages, "insight": data}
            elif event == "error":
                raise ToolError(f"the agent could not answer: {data.get('message', data)}")
        raise ToolError("the agent's answer stream ended without an insight")

    def _request_recovery(self, plan_id, rationale, args=None, dry_run=True, incident_id=""):
        body = {
            "plan_id": plan_id,
            "args": {k: str(v) for k, v in (args or {}).items()},
            "rationale": rationale,
            "dry_run": bool(dry_run),
            "correlation_id": incident_id,
        }
        out = self.gateway.post("/recoveries", body)
        return {
            **out,
            "note": "a request: follow it with list_recoveries (it may wait for an operator's approval)",
        }


def _seg(value):
    """One path segment: the gateway's ids and codes never contain '/' or '?', an argument might."""
    from urllib.parse import quote

    return quote(str(value), safe="")


def _fit(value):
    """Keeps a result within MAX_TEXT: long lists are cut, and the cut is said."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= MAX_TEXT:
        return value
    if isinstance(value, dict):
        for key, items in value.items():
            if isinstance(items, list) and len(items) > 1:
                keep = max(1, len(items) * MAX_TEXT // len(text) - 1)
                return _fit(
                    {
                        **value,
                        key: items[:keep],
                        f"{key}_truncated": f"{len(items) - keep} more not shown: narrow the query",
                    }
                )
    return text[:MAX_TEXT] + "\n[truncated: narrow the query]"
