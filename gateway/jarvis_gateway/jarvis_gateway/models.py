# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Request and response shapes of /api/v1, for validation and the OpenAPI document (the dashboard
generates its typed client from it). Responses accept extra fields: the core may add some."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Open(BaseModel):
    model_config = ConfigDict(extra="allow")


# -- shared ----------------------------------------------------------------------------------------


class Source(Open):
    robot: str = ""
    node: str = ""
    component: str = ""
    host: str = ""


class Event(Open):
    """schemas/event.schema.json. `seq` is a decimal string: uint64 does not fit a JS number."""

    id: str
    seq: str
    stamp: str
    recv_stamp: str | None = None
    source: Source
    kind: str
    code: str
    severity: str
    message: str | None = None
    attrs: dict[str, Any] | None = None
    tags: list[str] | None = None
    payload: Any = None
    payload_schema: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None


class Outcome(Open):
    """What a command did. The component that owns the change records it on the event stream."""

    success: bool
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class Me(Open):
    name: str
    role: Literal["viewer", "operator", "admin"]


# -- reads -----------------------------------------------------------------------------------------


class EventPage(Open):
    events: list[Event]
    next_before_seq: str | None = Field(None, description="pass as before_seq for older events")


class EventDetail(Open):
    event: Event
    parent: Event | None = None
    children: list[Event] = Field(default_factory=list)


class Fault(Open):
    fault_id: str
    code: str
    source: Source
    state: str
    severity: str
    occurrences: int = 0
    acknowledged: bool = False
    acknowledged_by: str = ""
    latch: bool = False
    muted_by: str = ""
    incident_id: str = ""
    message: str = ""


class Faults(Open):
    faults: list[Fault]
    history: list[dict[str, Any]] | None = None


class FaultDetail(Open):
    fault: Fault | None = None
    episode: dict[str, Any] | None = None
    transitions: list[dict[str, Any]] = Field(default_factory=list)
    executions: list[dict[str, Any]] = Field(default_factory=list)


class Incidents(Open):
    incidents: list[dict[str, Any]]


class IncidentDetail(Open):
    incident_id: str
    state: str | None = None
    title: str | None = None
    timeline: list[Event] = Field(default_factory=list)
    executions: list[dict[str, Any]] = Field(default_factory=list)
    faults: list[dict[str, Any]] = Field(default_factory=list)


class Recoveries(Open):
    mode: str
    live: list[dict[str, Any]]
    recent: list[dict[str, Any]]
    history: list[dict[str, Any]]


class Plans(Open):
    mode: str
    plans: list[dict[str, Any]]


class Rules(Open):
    rules: list[dict[str, Any]]


class Stats(Open):
    hours: float
    kpis: dict[str, Any]
    faults_open: dict[str, int]
    event_rate: list[dict[str, Any]]
    top_codes: list[dict[str, Any]]
    plans: list[dict[str, Any]]


class Health(Open):
    status: Literal["ok", "degraded", "starting"]
    connected: bool
    faults_snapshot: bool
    executions_snapshot: bool
    last_event_age_s: float | None = None
    robot: str = ""
    lake: dict[str, Any] | None = None


# -- commands --------------------------------------------------------------------------------------


class Note(BaseModel):
    note: str = ""


class Clear(BaseModel):
    reason: str = ""
    skip_symptoms: bool = False


class Recover(BaseModel):
    plan_id: str
    args: dict[str, str | int | float | bool] = Field(default_factory=dict)
    dry_run: bool = False
    rationale: str = ""
    correlation_id: str = ""


class Cancel(BaseModel):
    reason: str = ""


class Mode(BaseModel):
    mode: Literal["ENABLED", "DRY_RUN", "DISABLED"]
    reason: str = ""
    cancel_running: bool = False


class RuleState(BaseModel):
    enabled: bool | None = None
    dry_run: bool | None = None
    reason: str = ""


class Validate(BaseModel):
    yaml: str


class Reload(BaseModel):
    validate_only: bool = False
