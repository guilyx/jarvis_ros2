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


class FleetRobot(Open):
    name: str
    url: str = ""
    online: bool
    local: bool = False  # this gateway's own robot
    robot: str = ""
    status: str = ""
    connected: bool = False
    latency_ms: int | None = None
    error: str | None = None
    mode: str = "UNKNOWN"
    faults: dict[str, int] = Field(default_factory=dict)
    worst: dict[str, str] | None = None
    incidents: int = 0
    latched: int = 0


class Fleet(Open):
    robots: list[FleetRobot]


class Vda5050Error(Open):
    errorType: str
    errorLevel: str
    errorReferences: list[dict[str, str]] = Field(default_factory=list)
    errorDescription: str = ""
    errorHint: str | None = None


class Vda5050Errors(Open):
    version: str
    errors: list[Vda5050Error]


class FaultDetail(Open):
    fault: Fault | None = None
    episode: dict[str, Any] | None = None
    transitions: list[dict[str, Any]] = Field(default_factory=list)
    executions: list[dict[str, Any]] = Field(default_factory=list)


class CatalogEntry(Open):
    code: str
    description: str = ""
    severity: str
    latch: bool = False
    runbook: bool = Field(False, description="it has a runbook: GET /runbooks/{code}")


class Catalog(Open):
    faults: list[CatalogEntry]


class Runbook(Open):
    code: str
    markdown: str


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


class Ask(BaseModel):
    """Explain an incident, or ask about the last minutes (or both: a question about an incident)."""

    incident_id: str = ""
    question: str = Field(default="", max_length=2000)


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


class BacktestRequest(BaseModel):
    yaml: str
    hours: float = Field(24.0, gt=0, le=168, description="how far back to replay (at most a week)")


class BacktestCause(Open):
    id: str
    kind: str
    code: str
    component: str = ""
    severity: str = ""
    message: str = ""
    at: str = ""


class BacktestMatch(Open):
    at: str
    rule: str
    match_id: str = ""
    cause_id: str = ""
    cause: BacktestCause | None = None


class BacktestDecision(Open):
    """A decision one side made and the other didn't: the rule, on which event (none: another rule's)."""

    rule: str
    cause_id: str = ""
    cause: BacktestCause | None = None


class BacktestRecovery(Open):
    at: str
    rule: str
    plan: str
    args: dict[str, str] = Field(default_factory=dict)
    state: str
    reason: str = ""


class BacktestRule(Open):
    rule: str
    fires: int = 0
    dry_fires: int = 0
    suppressed: int = 0
    cooled_down: int = 0
    guard_blocked: int = 0


class Backtest(Open):
    """What a draft would have done over the robot's history (POST /rules:backtest)."""

    success: bool
    message: str = ""
    findings: list[dict[str, str]] = Field(default_factory=list)
    events: int = 0
    from_: str | None = Field(None, alias="from")
    to: str | None = None
    hours: float = 0
    truncated: bool = False
    replaces: list[str] = Field(default_factory=list, description="the draft's rules the robot already has")
    rules: list[BacktestRule] = Field(default_factory=list)
    matches: list[BacktestMatch] = Field(default_factory=list)
    match_count: int = 0
    recoveries: list[BacktestRecovery] = Field(default_factory=list)
    missing: list[BacktestDecision] = Field(
        default_factory=list, description="decided live, not by the draft"
    )
    extra: list[BacktestDecision] = Field(default_factory=list, description="decided by the draft, not live")


class Reload(BaseModel):
    validate_only: bool = False
