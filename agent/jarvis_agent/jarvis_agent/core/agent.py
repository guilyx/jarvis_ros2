# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""One analysis: window → triage → analyst → harness checks → (proposals) → sink."""

import time

from jarvis_agent.core.checks import check
from jarvis_agent.core.policy import AutonomyPolicy
from jarvis_agent.core.triage import triage
from jarvis_agent.core.windows import WindowManager
from jarvis_agent.llm.heuristic import HeuristicAnalyst
from jarvis_agent.model import Trigger, TriggerType


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
    ):
        self.query = query
        self.sink = sink
        self.analyst = analyst or HeuristicAnalyst()
        self.policy = policy or AutonomyPolicy()
        self.redact = tuple(redact)
        self.windows = windows or WindowManager(query)
        self.clock = clock

    def window(self, trigger, question=""):
        if trigger.type in (
            TriggerType.INCIDENT_OPENED,
            TriggerType.RECOVERY_FAILED,
            TriggerType.RULE_REQUEST,
        ):
            return self.windows.incident(trigger, trigger.ref)
        if trigger.type == TriggerType.ON_DEMAND and trigger.ref:
            return self.windows.incident(trigger, trigger.ref)  # None when there is no such incident
        return self.windows.sliding(trigger, self.clock(), question)

    def analyze(self, trigger, causation_id="", question=""):
        """The checked insight for the trigger (published to the sink), or None without a window."""
        window = self.window(trigger, question)
        if window is None:
            return None
        result = triage(window, self.redact)
        insight = check(self.analyst.analyze(window, result), window.ids, window.plans)
        plans = {p["id"]: p for p in window.plans}
        for r in insight.recommendations:
            if r.kind == "RECOVER":
                mode = self.policy.decide(plans.get(r.plan_id), r.confidence or 0.0, insight.analyst)
                if mode != "advise" and mode != "observe":
                    insight.flags.append(
                        f"{r.plan_id}: {mode} proposals arrive with milestone 4.3; advised only"
                    )
        if self.sink is not None:
            self.sink.publish(insight, correlation_id=window.correlation_id, causation_id=causation_id)
        return insight

    def explain(self, incident_id):
        return self.analyze(Trigger(type=TriggerType.ON_DEMAND, ref=incident_id))
