# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""What the agent reads, whatever serves it: the gateway (adapters/gateway.py) or a replay of
recorded events (adapters/replay.py, for tests and evals). Everything is the schemas' JSON.

    incident(id)          {incident_id, title, state, component, opened_at, ..., timeline: [Event],
                           executions: [{execution_id, plan, outcome, ...}], faults: [episode]}
    events(...)           [Event], oldest first
    active_faults()       [Fault]
    fault_history(code)   [episode]: {fault_id, code, component, first_seen, confirmed_at, cleared_at,
                           incident_id, last_state, muted}
    recoveries()          [{execution_id, plan, requester, outcome, incident_id, ...}], newest first
    plans()               [Plan]: {id, description, params, allowed_requesters, agent_policy, ...}
"""

from typing import Protocol


class EventQuery(Protocol):
    def incident(self, incident_id: str) -> dict | None: ...

    def events(
        self,
        since: str | None = None,
        until: str | None = None,
        min_severity: str | None = None,
        component: str = "",
        kinds: tuple = (),
        limit: int = 1000,
    ) -> list[dict]: ...

    def active_faults(self) -> list[dict]: ...

    def fault_history(self, code: str, limit: int = 200) -> list[dict]: ...

    def recoveries(self, limit: int = 500) -> list[dict]: ...

    def plans(self) -> list[dict]: ...


class InsightSink(Protocol):
    def publish(self, insight, correlation_id: str = "", causation_id: str = "") -> None: ...
