# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""jarvis_interfaces messages as the JSON of schemas/*.schema.json (the core's codec, in Python)."""

import datetime
import json

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


def stamp_or_none(t):
    return None if is_zero(t) else iso8601(t)


def source_to_dict(s):
    return {"robot": s.robot, "node": s.node, "component": s.component, "host": s.host}


def fault_to_dict(f):
    """A Fault message as schemas/fault.schema.json."""
    return {
        "fault_id": f.fault_id,
        "code": f.code,
        "source": source_to_dict(f.source),
        "reporting_sources": list(f.reporting_sources),
        "state": FAULT_STATES.get(f.state, "PENDING"),
        "severity": SEVERITIES.get(f.severity, "INFO"),
        "occurrences": f.occurrences,
        "first_seen": stamp_or_none(f.first_seen),
        "last_seen": stamp_or_none(f.last_seen),
        "confirmed_at": stamp_or_none(f.confirmed_at),
        "cleared_at": stamp_or_none(f.cleared_at),
        "acknowledged": f.acknowledged,
        "acknowledged_by": f.acknowledged_by,
        "latch": f.latch,
        "cataloged": f.cataloged,
        "muted_by": f.muted_by,
        "incident_id": f.incident_id,
        "snapshot_ref": f.snapshot_ref,
        "description": f.description,
        "message": f.message,
        "freeze_frame": attrs(f.freeze_frame),
    }


def execution_to_dict(x):
    """An Execution message as schemas/execution.schema.json."""
    confidence = x.confidence if x.confidence == x.confidence and x.confidence > 0 else None
    return {
        "execution_id": x.execution_id,
        "plan_id": x.plan_id,
        "plan_rev": x.plan_rev,
        "args": {k: str(v) for k, v in attrs(x.args).items()},
        "requester": {"type": REQUESTERS.get(x.requester_type, "OPERATOR"), "id": x.requester_id},
        "correlation_id": x.correlation_id,
        "causation_id": x.causation_id,
        "rationale": x.rationale,
        "confidence": confidence,
        "dry_run": x.dry_run,
        "state": EXECUTION_STATES.get(x.state, "QUEUED"),
        "reason": x.reason,
        "gates": [{"gate": g.gate, "passed": g.passed, "detail": g.detail} for g in x.gates],
        "needs_approval": x.needs_approval,
        "approved_by": x.approved_by,
        "locks": list(x.locks),
        "steps": [
            {
                "id": s.id,
                "action": s.action,
                "state": STEP_STATES.get(s.state, "PENDING"),
                "attempts": s.attempts,
                "started": stamp_or_none(s.started),
                "finished": stamp_or_none(s.finished),
                "message": s.message,
            }
            for s in x.steps
        ],
        "current_step": x.current_step,
        "failed_step": x.failed_step,
        "created": stamp_or_none(x.created),
        "started": stamp_or_none(x.started),
        "finished": stamp_or_none(x.finished),
        "expires": stamp_or_none(x.expires),
        "depth": x.depth,
        "parent_id": x.parent_id,
        "ladder_of": x.ladder_of,
        "ladder": {"running": x.ladder_running},
        "escalated": x.escalated,
    }


def executions_to_dict(msg):
    """An ExecutionArray: {mode, executions, recent}."""
    return {
        "mode": msg.mode,
        "executions": [execution_to_dict(x) for x in msg.executions],
        "recent": [execution_to_dict(x) for x in msg.recent],
    }


def plan_to_dict(p):
    """A PlanInfo message: what an operator (or the dashboard) needs to run the plan."""
    try:
        params = json.loads(p.params_json) if p.params_json else {}
    except ValueError:
        params = {}
    return {
        "id": p.id,
        "description": p.description,
        "rev": p.rev,
        "file": p.file,
        "params": params,
        "allowed_requesters": list(p.allowed_requesters),
        "requires_approval": list(p.requires_approval),
        "agent_policy": p.agent_policy,
        "steps": list(p.steps),
        "timeout_s": p.timeout_s,
    }


def rule_to_dict(r):
    """A RuleStatus message."""
    return {
        "id": r.id,
        "description": r.description,
        "file": r.file,
        "enabled": r.enabled,
        "dry_run": r.dry_run,
        "fires": r.fires,
        "dry_fires": r.dry_fires,
        "suppressed": r.suppressed,
        "cooled_down": r.cooled_down,
        "guard_blocked": r.guard_blocked,
        "action_failures": r.action_failures,
        "partitions": r.partitions,
    }
