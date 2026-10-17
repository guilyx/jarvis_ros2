# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""When to analyze (docs/design/06-agent.md §2).

    INCIDENT_OPENED   an incident opened at ERROR or worse
    RECOVERY_FAILED   a recovery FAILED, or Jarvis escalated
    RULE_REQUEST      a rule asked for it (`jarvis.agent.request`)

An analysis waits `settle_s` after its trigger, so the window holds what happened next (the
recovery that ran, the symptoms), and triggers for the same incident in that time merge into one.
Each incident is analyzed at most once per trigger type. At most `max_per_hour` analyses run per
hour; the rest are dropped and counted.
"""

import collections
import dataclasses

from jarvis_agent.events import attr, incident_of, level
from jarvis_agent.model import SEVERITY_LEVEL, Trigger, TriggerType

STRENGTH = [TriggerType.INCIDENT_OPENED, TriggerType.RULE_REQUEST, TriggerType.RECOVERY_FAILED]
HOUR = 3600 * 1_000_000_000


@dataclasses.dataclass
class Pending:
    trigger: Trigger
    due_ns: int
    causation_id: str


class TriggerPolicy:
    def __init__(self, min_severity="ERROR", settle_s=20.0, max_per_hour=20):
        self.min_level = SEVERITY_LEVEL[min_severity]
        self.settle_ns = int(settle_s * 1e9)
        self.max_per_hour = max_per_hour
        self.pending = {}  # incident id -> Pending
        self.done = set()  # (incident id, trigger type)
        self.started = collections.deque()  # when analyses started (the budget)
        self.dropped = 0

    def classify(self, event):
        """The trigger an event is, if any."""
        kind, code = event.get("kind"), event.get("code", "")
        incident = incident_of(event)
        if kind == "INSIGHT" or not incident:
            return None
        if kind == "SYSTEM" and code == "jarvis.incident.opened" and level(event) >= self.min_level:
            return TriggerType.INCIDENT_OPENED
        if (
            kind == "RECOVERY"
            and attr(event, "phase") in ("FAILED", "ESCALATED")
            and not attr(event, "dry_run")
        ):
            return TriggerType.RECOVERY_FAILED
        if code == "jarvis.agent.request":
            return TriggerType.RULE_REQUEST
        return None

    def on_event(self, event, now_ns):
        kind = self.classify(event)
        if kind is None:
            return
        incident = incident_of(event)
        if (incident, kind) in self.done:
            return
        current = self.pending.get(incident)
        if current is None:
            self.pending[incident] = Pending(
                Trigger(type=kind, ref=incident), now_ns + self.settle_ns, event["id"]
            )
        elif STRENGTH.index(kind) > STRENGTH.index(current.trigger.type):
            current.trigger = Trigger(type=kind, ref=incident)
            current.causation_id = event["id"]
            current.due_ns = max(current.due_ns, now_ns + self.settle_ns)

    def due(self, now_ns):
        """The analyses to run now, within the hourly budget."""
        while self.started and self.started[0] <= now_ns - HOUR:
            self.started.popleft()
        out = []
        for incident, p in sorted(self.pending.items(), key=lambda kv: kv[1].due_ns):
            if p.due_ns > now_ns:
                continue
            del self.pending[incident]
            self.done.add((incident, p.trigger.type))
            if len(self.started) >= self.max_per_hour:
                self.dropped += 1
                continue
            self.started.append(now_ns)
            out.append(p)
        return out
