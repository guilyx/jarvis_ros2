# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Windows: the slice of history an analysis looks at (docs/design/06-agent.md §3).

incident   every event of the incident, plus ±5 min of WARN+ events from its components, the
           active faults, the latest STATE values, the incident's recoveries and the history of
           its fault codes
sliding    the last N minutes of WARN+ events (on-demand questions)
"""

import dataclasses

from jarvis_agent.events import component, iso_ns, ns
from jarvis_agent.model import Trigger, TriggerType
from jarvis_agent.model import Window as WindowInfo

MINUTE = 60 * 1_000_000_000


@dataclasses.dataclass
class Window:
    trigger: Trigger
    events: list  # oldest first, deduplicated by id
    incident: dict | None = None
    faults: list = dataclasses.field(default_factory=list)  # the incident's fault episodes
    executions: list = dataclasses.field(default_factory=list)  # the incident's recoveries
    active_faults: list = dataclasses.field(default_factory=list)
    state: dict = dataclasses.field(default_factory=dict)  # (component, code) -> latest STATE event
    history: dict = dataclasses.field(default_factory=dict)  # fault code -> past episodes
    recoveries: list = dataclasses.field(default_factory=list)  # every past recovery (for success rates)
    plans: list = dataclasses.field(default_factory=list)
    question: str = ""
    seen: set = dataclasses.field(default_factory=set)  # event ids the analyst's tools showed it

    @property
    def ids(self):
        """What an analyst may cite: the window's events and what its tools showed it."""
        return {e["id"] for e in self.events} | self.seen

    @property
    def correlation_id(self):
        return (self.incident or {}).get("incident_id", "")

    def info(self):
        stamps = [ns(e["stamp"]) for e in self.events if ns(e["stamp"]) is not None]
        start, end = (min(stamps), max(stamps)) if stamps else (0, 0)
        return WindowInfo(
            **{"from": iso_ns(start)},
            to=iso_ns(end),
            event_count=len(self.events),
            correlation_id=self.correlation_id,
        )


class WindowManager:
    def __init__(self, query, context_s=300.0, sliding_s=600.0, max_events=2000):
        self.query = query
        self.context_ns = int(context_s * 1e9)
        self.sliding_ns = int(sliding_s * 1e9)
        self.max_events = max_events

    def incident(self, trigger, incident_id):
        found = self.query.incident(incident_id)
        if not found:
            return None
        timeline = list(found.get("timeline") or [])
        stamps = [ns(e["stamp"]) for e in timeline if ns(e["stamp"]) is not None]
        components = sorted({component(e) for e in timeline if component(e)})
        context = []
        if stamps:
            since, until = iso_ns(min(stamps) - self.context_ns), iso_ns(max(stamps) + self.context_ns)
            for c in components:
                context += self.query.events(since=since, until=until, min_severity="WARN", component=c)
                context += self.query.events(since=since, until=until, component=c, kinds=("STATE",))
        events = _merge(timeline, context)[-self.max_events :]
        state = {}
        for e in events:
            if e.get("kind") == "STATE":
                state[(component(e), e["code"])] = e
        codes = sorted({f["code"] for f in found.get("faults") or []})
        return Window(
            trigger=trigger,
            events=events,
            incident=found,
            faults=list(found.get("faults") or []),
            executions=list(found.get("executions") or []),
            active_faults=self.query.active_faults(),
            state=state,
            history={code: self.query.fault_history(code) for code in codes},
            recoveries=self.query.recoveries(),
            plans=self.query.plans(),
        )

    def sliding(self, trigger, until_ns, question=""):
        since = iso_ns(until_ns - self.sliding_ns)
        events = self.query.events(since=since, until=iso_ns(until_ns + 1), min_severity="WARN")
        codes = sorted({e["code"] for e in events if e.get("kind") in ("FAULT_STATE", "FAULT_RAISE")})
        return Window(
            trigger=trigger or Trigger(type=TriggerType.ON_DEMAND, ref=question),
            events=events[-self.max_events :],
            active_faults=self.query.active_faults(),
            history={code: self.query.fault_history(code) for code in codes},
            recoveries=self.query.recoveries(),
            plans=self.query.plans(),
            question=question,
        )


def _merge(*lists):
    seen, out = set(), []
    for e in sorted((e for lst in lists for e in lst), key=lambda e: int(e.get("seq") or 0)):
        if e["id"] not in seen:
            seen.add(e["id"])
            out.append(e)
    return out
