# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""One analysis: window → triage → analyst → harness checks → proposals → sink.

A proposal (the model's propose_recovery) goes to the recovery gates when the autonomy policy says
supervised or autonomous and a recovery client is there (the ROS node); the orchestrator then
applies its own agent autonomy, the plan's agent_policy and the confidence threshold, and every
other gate. Otherwise it stays advice: a RECOVER recommendation an operator can run.

`ask` is the on-demand path (the Analyze action, the gateway's /agent/ask): explain an incident,
or answer a question about the last minutes; `progress(stage, detail)` hears what it is doing.
"""

import time

from jarvis_agent.core.checks import check, validate_args
from jarvis_agent.core.policy import AutonomyPolicy
from jarvis_agent.core.triage import triage
from jarvis_agent.core.windows import WindowManager
from jarvis_agent.llm.heuristic import HeuristicAnalyst
from jarvis_agent.model import Recommendation, Trigger, TriggerType

MAX_PROPOSALS = 3


class Agent:
    def __init__(
        self,
        query,
        sink=None,
        analyst=None,
        policy=None,
        redact=(),
        windows=None,
        clock=time.time_ns,
        recovery=None,
    ):
        self.query = query
        self.sink = sink
        self.analyst = analyst or HeuristicAnalyst()
        self.policy = policy or AutonomyPolicy()
        self.redact = tuple(redact)
        self.windows = windows or WindowManager(query)
        self.clock = clock
        self.recovery = recovery  # RecoveryClient, or None: proposals stay advice

    def window(self, trigger, question=""):
        if trigger.type in (
            TriggerType.INCIDENT_OPENED,
            TriggerType.RECOVERY_FAILED,
            TriggerType.RULE_REQUEST,
        ) or (trigger.type == TriggerType.ON_DEMAND and trigger.ref):
            window = self.windows.incident(trigger, trigger.ref)  # None when there is no such incident
            if window is not None:
                window.question = question
            return window
        return self.windows.sliding(trigger, self.clock(), question)

    def analyze(self, trigger, causation_id="", question="", progress=None):
        """The checked insight for the trigger (published to the sink), or None without a window."""
        say = progress or _quiet
        window = self.window(trigger, question)
        if window is None:
            return None
        say(
            "window",
            f"{len(window.events)} events"
            + (f", incident {window.correlation_id}" if window.incident else ""),
        )
        result = triage(window, self.redact)
        say("triage", f"{len(result.facts)} facts" + (f", root {result.root['code']}" if result.root else ""))
        insight = check(self.analyst.analyze(window, result, progress=say), window.ids, window.plans)
        say("checks", f"{len(insight.flags)} harness note(s)")
        self._route(window, insight, causation_id, say)
        if self.sink is not None:
            self.sink.publish(insight, correlation_id=window.correlation_id, causation_id=causation_id)
        say("done", insight.title)
        return insight

    def ask(self, incident_id="", question="", progress=None):
        """Explain an incident, or answer a question about the last minutes. LookupError when
        there is no such incident."""
        if not incident_id and not question.strip():
            raise ValueError("ask about an incident, or ask a question")
        trigger = Trigger(type=TriggerType.ON_DEMAND, ref=incident_id)
        insight = self.analyze(trigger, question=question.strip(), progress=progress)
        if insight is None:
            raise LookupError(f"no incident '{incident_id}'")
        return insight

    def _route(self, window, insight, causation_id, say=None):
        say = say or _quiet
        plans = {p["id"]: p for p in window.plans}
        flags = []
        for p in sorted(window.proposals, key=lambda p: p.confidence, reverse=True)[:MAX_PROPOSALS]:
            plan = plans.get(p.plan_id)
            if plan is None or validate_args(plan, p.args):
                flags.append(f"dropped the proposal of {p.plan_id}: unknown plan or bad arguments")
                continue
            if not [i for i in p.evidence if i in window.ids]:
                flags.append(f"dropped the proposal of {p.plan_id}: no evidence")
                continue
            mode = self.policy.decide(plan, p.confidence, insight.analyst)
            if mode in ("supervised", "autonomous") and self.recovery is not None:
                got = self.recovery.submit(
                    p.plan_id,
                    p.args,
                    rationale=f"{p.rationale} (insight {insight.insight_id})".strip(),
                    confidence=p.confidence,
                    correlation_id=window.correlation_id,
                    causation_id=causation_id,
                )
                if got.execution_id:
                    insight.proposals.append(got.execution_id)
                reason = f": {got.reason}" if got.reason else ""
                flags.append(f"proposed {p.plan_id} ({mode}): {got.state}{reason}")
                say("proposals", f"{p.plan_id}: {got.state}")
                continue
            if mode == "observe":
                flags.append(f"dropped the proposal of {p.plan_id}: autonomy observe")
                continue
            advice = [r for r in insight.recommendations if r.kind == "RECOVER" and r.plan_id == p.plan_id]
            if not advice and len(insight.recommendations) < 10:
                insight.recommendations.append(
                    Recommendation(
                        kind="RECOVER",
                        text=p.rationale or f"Run {p.plan_id}",
                        plan_id=p.plan_id,
                        args=p.args,
                        confidence=p.confidence,
                    )
                )
            why = f"autonomy {mode}" if mode == "advise" else "no recovery client here"
            flags.append(f"advised {p.plan_id}, not proposed ({why})")
        insight.flags = list(dict.fromkeys(insight.flags + flags))

    def explain(self, incident_id):
        return self.analyze(Trigger(type=TriggerType.ON_DEMAND, ref=incident_id))


def _quiet(stage, detail=""):
    pass
