# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""An in-memory Jarvis for tests and for working on the dashboard without a robot.

It keeps the shapes and the audit trail of the real components (events, fault and execution
snapshots, OPERATOR events for every command), with simplified behavior: a recovery runs its steps
on a timer and succeeds, unless its plan is named `fail-*`. With `scenario`, things happen on their
own: the e-stop is latched, a lidar goes quiet and a rule restarts its relay, a camera drops off the
graph and comes back.
"""

import asyncio
import datetime
import json
import uuid

from jarvis_gateway.port import CommandResult, JarvisPort

SEVERITY_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40, "CRITICAL": 50}

DEFAULT_PLANS = [
    {
        "id": "restart-lifecycle-node",
        "description": "Cycle a managed node back to active, then verify that its faults clear.",
        "rev": "fake",
        "file": "plans/core.yaml",
        "params": {
            "node": {"type": "string", "required": True, "description": "absolute node name"},
            "component": {"type": "string", "required": True, "description": "its component"},
            "fault": {"type": "string", "required": False, "default": "*", "description": "faults"},
        },
        "allowed_requesters": ["OPERATOR", "RULE", "AGENT", "PLAN"],
        "requires_approval": [],
        "agent_policy": "supervised",
        "steps": ["restart", "verify"],
        "timeout_s": 90.0,
    },
    {
        "id": "safe-stop",
        "description": "Stop the robot and wait for an operator.",
        "rev": "fake",
        "file": "plans/core.yaml",
        "params": {},
        "allowed_requesters": ["OPERATOR", "RULE", "AGENT"],
        "requires_approval": ["AGENT"],
        "agent_policy": "supervised",
        "steps": ["cancel", "stop"],
        "timeout_s": 30.0,
    },
]
DEFAULT_RULES = [
    {
        "id": "lidar-stale-restart",
        "description": "The lidar went quiet. Restart its relay.",
        "file": "",
        "enabled": True,
        "dry_run": False,
    },
    {
        "id": "escalation-notify",
        "description": "Tell the operators when recovery gives up.",
        "file": "",
        "enabled": True,
        "dry_run": False,
    },
]


def now_iso(clock=None):
    t = clock() if clock else datetime.datetime.now(tz=datetime.UTC)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond:06d}000Z"


class FakeJarvis(JarvisPort):
    def __init__(self, robot="fake-bot", step_s=0.2, capacity=10000, clock=None, scenario=False):
        self.robot = robot
        self.scenario = scenario
        self.step_s = step_s
        self.capacity = capacity
        self.clock = clock
        self.sink = None
        self.events = []  # the "event hub": every event, oldest first (bounded)
        self.seq = 0
        self.faults = {}  # fault_id -> fault
        self.incidents = {}  # incident_id -> {"state", "component", "title"}
        self.executions = {}  # execution_id -> execution
        self.recent = []
        self.mode = "ENABLED"
        self.plans = [dict(p) for p in DEFAULT_PLANS]
        self.rules = [dict(r) for r in DEFAULT_RULES]
        self._tasks = set()
        self._connected = False

    # -- port --------------------------------------------------------------------------------------

    async def start(self, sink):
        self.sink = sink
        self._connected = True
        self._publish_faults()
        self._publish_executions()
        if self.scenario:
            self._spawn(self._scenario())

    async def stop(self):
        for t in list(self._tasks):
            t.cancel()
        self._connected = False

    @property
    def connected(self):
        return self._connected

    async def query_events(self, after_seq=0, limit=1000):
        later = [e for e in self.events if int(e["seq"]) > after_seq]
        page = later[:limit]
        next_seq = int(page[-1]["seq"]) if len(later) > limit else 0
        oldest = int(self.events[0]["seq"]) if self.events else 0
        complete = after_seq + 1 >= oldest or after_seq == 0 and oldest <= 1
        return page, next_seq, complete

    # -- making things happen (tests and the dev scenario) -----------------------------------------

    def emit(
        self,
        kind,
        code,
        severity="INFO",
        component="",
        message="",
        correlation="",
        causation="",
        payload=None,
        **attrs,
    ):
        self.seq += 1
        stamp = now_iso(self.clock)
        e = {
            "id": str(uuid.uuid4()),
            "seq": str(self.seq),
            "stamp": stamp,
            "recv_stamp": stamp,
            "source": {"robot": self.robot, "node": "/fake", "component": component, "host": ""},
            "kind": kind,
            "code": code,
            "severity": severity,
        }
        if message:
            e["message"] = message
        if attrs:
            e["attrs"] = attrs
        if correlation:
            e["correlation_id"] = correlation
        if causation:
            e["causation_id"] = causation
        if payload is not None:
            e["payload"] = payload
        self.events.append(e)
        del self.events[: max(0, len(self.events) - self.capacity)]
        if self.sink:
            self.sink.on_event(e)
        return e

    def raise_fault(self, code, component, severity="ERROR", latch=False, message=""):
        """A fault goes ACTIVE (or LATCHED) and opens an incident."""
        fault_id = str(uuid.uuid4())
        incident = str(uuid.uuid4())
        title = f"{code} on {component}"
        self.incidents[incident] = {"state": "OPEN", "component": component, "title": title}
        stamp = now_iso(self.clock)
        self.faults[fault_id] = {
            "fault_id": fault_id,
            "code": code,
            "source": {"robot": self.robot, "node": "/fake", "component": component, "host": ""},
            "reporting_sources": ["/fake"],
            "state": "LATCHED" if latch else "ACTIVE",
            "severity": severity,
            "occurrences": 1,
            "first_seen": stamp,
            "last_seen": stamp,
            "confirmed_at": stamp,
            "acknowledged": False,
            "acknowledged_by": "",
            "latch": latch,
            "cataloged": True,
            "muted_by": "",
            "incident_id": incident,
            "snapshot_ref": "",
            "description": "",
            "message": message,
            "freeze_frame": {},
        }
        self.emit("FAULT_RAISE", code, severity, component, message)
        self.emit(
            "FAULT_STATE",
            code,
            severity,
            component,
            f"ACTIVE: {message}",
            incident,
            fault_id=fault_id,
            state="ACTIVE",
            prev_state="PENDING",
            occurrences=1,
        )
        self.emit(
            "SYSTEM",
            "jarvis.incident.opened",
            severity,
            component,
            title,
            incident,
            incident_id=incident,
            state="OPEN",
            prev_state="NONE",
            root_fault_id=fault_id,
        )
        if latch:
            self.faults[fault_id]["state"] = "LATCHED"
            self.emit(
                "FAULT_STATE",
                code,
                severity,
                component,
                "LATCHED",
                incident,
                fault_id=fault_id,
                state="LATCHED",
                prev_state="ACTIVE",
                occurrences=1,
            )
        self._publish_faults()
        return self.faults[fault_id]

    def heal(self, fault_id, reason="healed"):
        f = self.faults.pop(fault_id)
        component = f["source"]["component"]
        self.emit(
            "FAULT_STATE",
            f["code"],
            "INFO",
            component,
            f"CLEARED: {reason}",
            f["incident_id"],
            fault_id=fault_id,
            state="CLEARED",
            prev_state=f["state"],
            occurrences=f["occurrences"],
            reason=reason,
        )
        self._resolve_if_clear(f["incident_id"])
        self._publish_faults()

    def notify(self, message, channel="operators"):
        return self.emit("SYSTEM", "jarvis.notify", "INFO", "", message, channel=channel, rule="test")

    # -- faults and incidents ----------------------------------------------------------------------

    async def ack_fault(self, fault_id, operator, note=""):
        f = self.faults.get(fault_id)
        if not f:
            return CommandResult(False, f"unknown fault '{fault_id}'")
        f["acknowledged"], f["acknowledged_by"] = True, operator
        self.emit(
            "OPERATOR",
            "fault.acknowledge",
            "INFO",
            f["source"]["component"],
            f"{operator} acknowledged {f['code']}",
            f["incident_id"],
            fault_id=fault_id,
            user=operator,
            fault_code=f["code"],
            note=note,
        )
        self._publish_faults()
        return CommandResult(True, "acknowledged")

    async def clear_fault(self, fault_id, operator, reason="", skip_symptoms=False):
        f = self.faults.get(fault_id)
        if not f:
            return CommandResult(False, f"unknown fault '{fault_id}'")
        self.emit(
            "OPERATOR",
            "fault.clear",
            "INFO",
            f["source"]["component"],
            f"{operator} cleared {f['code']}",
            f["incident_id"],
            fault_id=fault_id,
            user=operator,
            fault_code=f["code"],
            reason=reason,
        )
        self.heal(fault_id, reason or f"cleared by {operator}")
        return CommandResult(True, "cleared")

    async def close_incident(self, incident_id, operator, note=""):
        inc = self.incidents.get(incident_id)
        if not inc:
            return CommandResult(False, f"unknown incident '{incident_id}'")
        if inc["state"] != "RESOLVED":
            return CommandResult(False, f"incident is {inc['state']}, not RESOLVED")
        self.emit(
            "OPERATOR",
            "incident.close",
            "INFO",
            inc["component"],
            f"{operator} closed {inc['title']}",
            incident_id,
            incident_id=incident_id,
            user=operator,
        )
        self._incident(incident_id, "CLOSED", f"closed by {operator}")
        return CommandResult(True, "closed")

    # -- recovery ----------------------------------------------------------------------------------

    async def recover(self, plan_id, args, operator, rationale="", dry_run=False, correlation_id=""):
        plan = next((p for p in self.plans if p["id"] == plan_id), None)
        if plan is None:
            return CommandResult(False, f"unknown plan '{plan_id}'")
        if self.mode == "DISABLED":
            return CommandResult(False, "recovery is DISABLED", {"state": "REJECTED"})
        x = self._new_execution(plan, args, "OPERATOR", operator, rationale, dry_run, correlation_id)
        needs = "OPERATOR" in plan["requires_approval"]
        if needs:
            x.update(state="PENDING_APPROVAL", needs_approval=True, reason="waiting for approval")
            self._recovery(x, "PENDING_APPROVAL", "WARN", f"approval needed to run {plan_id}")
            self._publish_executions()
        else:
            self._start(x)
        return CommandResult(True, x["reason"], {"execution_id": x["execution_id"], "state": x["state"]})

    async def decide(self, execution_id, approve, operator, note=""):
        x = self.executions.get(execution_id)
        if not x or x["state"] != "PENDING_APPROVAL":
            return CommandResult(False, "not waiting for approval")
        if approve:
            x["approved_by"] = operator
            self._recovery(x, "APPROVED", "INFO", f"approved by {operator}", approved_by=operator)
            self._start(x)
        else:
            self._recovery(x, "REJECTED", "WARN", f"rejected by {operator}")
            self._finish(x, "REJECTED", f"rejected by {operator}")
        return CommandResult(True, "approved" if approve else "rejected")

    async def cancel(self, execution_id, operator, reason=""):
        x = self.executions.get(execution_id)
        if not x or x["state"] in ("SUCCEEDED", "FAILED", "ABORTED", "REJECTED", "EXPIRED"):
            return CommandResult(False, "unknown or finished execution")
        self._finish(x, "ABORTED", f"cancelled by {operator}" + (f": {reason}" if reason else ""))
        return CommandResult(True, "cancelled")

    async def set_mode(self, mode, operator, reason="", cancel_running=False):
        if mode not in ("ENABLED", "DRY_RUN", "DISABLED"):
            return CommandResult(False, f"unknown mode '{mode}'")
        previous, self.mode = self.mode, mode
        self.emit(
            "OPERATOR",
            "jarvis.recovery.mode",
            "WARN" if mode != "ENABLED" else "INFO",
            "",
            f"{operator} set recovery {previous} -> {mode}" + (f": {reason}" if reason else ""),
            mode=mode,
            previous_mode=previous,
            operator=operator,
            cancel_running=cancel_running,
        )
        self._publish_executions()
        return CommandResult(True, f"{previous} -> {mode}", {"previous_mode": previous})

    async def list_plans(self):
        return [dict(p) for p in self.plans], self.mode

    # -- rules -------------------------------------------------------------------------------------

    async def list_rules(self):
        return [
            dict(r, fires=0, dry_fires=0, suppressed=0, cooled_down=0, guard_blocked=0) for r in self.rules
        ]

    async def set_rule_state(self, rule_id, enabled, dry_run, operator, reason=""):
        rule = next((r for r in self.rules if r["id"] == rule_id), None)
        if rule is None:
            return CommandResult(False, f"unknown rule '{rule_id}'")
        if enabled is not None:
            rule["enabled"] = enabled
        if dry_run is not None:
            rule["dry_run"] = dry_run
        self.emit(
            "OPERATOR",
            "rule.state",
            "INFO",
            "",
            f"{operator} set rule {rule_id}",
            rule=rule_id,
            enabled=rule["enabled"],
            dry_run=rule["dry_run"],
            operator=operator,
        )
        return CommandResult(True, "updated")

    async def reload(self, target, validate_only, operator):
        count = len(self.rules if target == "rules" else self.plans)
        if not validate_only:
            code = "jarvis.rules.reloaded" if target == "rules" else "jarvis.recovery.reloaded"
            self.emit("SYSTEM", code, "INFO", "", f"Loaded {count} {target}", operator=operator)
        return CommandResult(True, f"{count} {target}", {"revision": "fake", "errors": [], "warnings": []})

    # -- internals ---------------------------------------------------------------------------------

    def _new_execution(self, plan, args, requester, requester_id, rationale, dry_run, correlation):
        execution_id = str(uuid.uuid4())
        x = {
            "execution_id": execution_id,
            "plan_id": plan["id"],
            "plan_rev": plan["rev"],
            "args": dict(args),
            "requester": {"type": requester, "id": requester_id},
            "correlation_id": correlation,
            "causation_id": "",
            "rationale": rationale,
            "confidence": None,
            "dry_run": dry_run,
            "state": "QUEUED",
            "reason": "",
            "gates": [{"gate": g, "passed": True, "detail": ""} for g in ("mode", "plan", "requester")],
            "needs_approval": False,
            "approved_by": "",
            "locks": [],
            "steps": [
                {"id": s, "action": s, "state": "PENDING", "attempts": 0, "message": ""}
                for s in plan["steps"]
            ],
            "current_step": 0,
            "failed_step": "",
            "created": now_iso(self.clock),
            "started": None,
            "finished": None,
            "expires": None,
            "depth": 0,
            "parent_id": "",
            "ladder_of": "",
            "ladder": {"running": False},
            "escalated": False,
        }
        self.executions[execution_id] = x
        return x

    def _start(self, x):
        x.update(state="RUNNING", reason="running", started=now_iso(self.clock))
        self._recovery(x, "STARTED", "INFO", f"started {x['plan_id']}")
        self._publish_executions()
        self._spawn(self._run(x))

    def _spawn(self, coro):
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _scenario(self, period_s=2.0):
        self.emit("SYSTEM", "jarvis.started", "INFO", "", "fake Jarvis started")
        # Someone pressed the e-stop: it stays latched until an operator clears it.
        self.raise_fault("safety.estop.engaged", "base", "CRITICAL", latch=True, message="e-stop pressed")
        lidar = camera = None
        tick = 0
        while True:
            await asyncio.sleep(period_s)
            tick += 1
            self.emit("LOG", "ros.log.info", "DEBUG", "base", f"odometry ok ({tick})")
            phase = tick % 20
            if phase == 3:
                lidar = self.raise_fault(
                    "ros.topic.stale", "lidar", message="/scan has not been published for 2.0 s"
                )
                incident = lidar["incident_id"]
                self.emit(
                    "RULE_MATCH",
                    "jarvis.rule.matched",
                    "INFO",
                    "lidar",
                    "lidar-stale-restart matched",
                    incident,
                    rule="lidar-stale-restart",
                    dry_run=False,
                )
                plan = self.plans[0]
                args = {"node": "/scan_relay", "component": "lidar", "fault": "ros.topic.stale"}
                self._start(
                    self._new_execution(plan, args, "RULE", "lidar-stale-restart", "", False, incident)
                )
            elif phase == 5 and lidar and lidar["fault_id"] in self.faults:
                self.heal(lidar["fault_id"], "/scan is back")
            elif phase == 11:
                camera = self.raise_fault(
                    "ros.node.missing", "camera", "WARN", message="/camera is not on the graph"
                )
            elif phase == 16 and camera and camera["fault_id"] in self.faults:
                self.heal(camera["fault_id"], "/camera is back")

    async def _run(self, x):
        for i, step in enumerate(x["steps"]):
            if x["state"] != "RUNNING":
                return
            x["current_step"] = i
            step.update(state="RUNNING", attempts=1)
            self._publish_executions()
            await asyncio.sleep(self.step_s)
            if x["state"] != "RUNNING":
                return
            if x["plan_id"].startswith("fail-") and i == len(x["steps"]) - 1:
                step.update(state="FAILED", message="did not work")
                x["failed_step"] = step["id"]
                self._recovery(x, "STEP_FAILED", "WARN", f"step {step['id']} failed", step=step["id"])
                self._finish(x, "FAILED", f"step {step['id']} failed: did not work")
                return
            step.update(state="SUCCEEDED", message="ok")
            self._recovery(x, "STEP_SUCCEEDED", "INFO", f"step {step['id']}: ok", step=step["id"])
        self._finish(x, "SUCCEEDED", "all steps succeeded")

    def _finish(self, x, state, reason):
        x.update(state=state, reason=reason, finished=now_iso(self.clock))
        severity = {"SUCCEEDED": "INFO", "FAILED": "ERROR"}.get(state, "WARN")
        self._recovery(x, state, severity, f"{x['plan_id']} {state.lower()}: {reason}", reason=reason)
        self.recent.append(self.executions.pop(x["execution_id"]))
        self.recent = self.recent[-20:]
        self._publish_executions()

    def _recovery(self, x, phase, severity, message, **attrs):
        self.emit(
            "RECOVERY",
            f"jarvis.recovery.{phase.lower()}",
            severity,
            "",
            message,
            x["correlation_id"],
            phase=phase,
            plan=x["plan_id"],
            execution_id=x["execution_id"],
            requester=x["requester"]["type"],
            requester_id=x["requester"]["id"],
            dry_run=x["dry_run"],
            **attrs,
        )

    def _incident(self, incident_id, state, reason):
        inc = self.incidents[incident_id]
        prev, inc["state"] = inc["state"], state
        self.emit(
            "SYSTEM",
            f"jarvis.incident.{state.lower()}",
            "INFO",
            inc["component"],
            f"{inc['title']} {state} ({reason})",
            incident_id,
            incident_id=incident_id,
            state=state,
            prev_state=prev,
            reason=reason,
        )

    def _resolve_if_clear(self, incident_id):
        if incident_id in self.incidents and not any(
            f["incident_id"] == incident_id for f in self.faults.values()
        ):
            self._incident(incident_id, "RESOLVED", "all faults cleared")

    def _publish_faults(self):
        if self.sink:
            self.sink.on_faults(json.loads(json.dumps(list(self.faults.values()))))

    def _publish_executions(self):
        if self.sink:
            self.sink.on_executions(
                json.loads(
                    json.dumps(
                        {
                            "mode": self.mode,
                            "executions": list(self.executions.values()),
                            "recent": self.recent,
                        }
                    )
                )
            )
