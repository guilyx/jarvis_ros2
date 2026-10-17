# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The agent's output: an Insight (schemas/insight.schema.json, payload of INSIGHT events)."""

import datetime
import enum
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SEVERITIES = ("DEBUG", "INFO", "WARN", "ERROR", "CRITICAL")
SEVERITY_LEVEL = {name: 10 * (i + 1) for i, name in enumerate(SEVERITIES)}
Severity = Literal["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"]


class TriggerType(enum.StrEnum):
    INCIDENT_OPENED = "INCIDENT_OPENED"
    RECOVERY_FAILED = "RECOVERY_FAILED"
    RULE_REQUEST = "RULE_REQUEST"
    DIGEST = "DIGEST"
    ON_DEMAND = "ON_DEMAND"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Trigger(Strict):
    type: TriggerType
    ref: str = ""  # incident id, event id or question


class Window(Strict):
    from_: str = Field(alias="from")
    to: str
    event_count: int = Field(ge=0)
    correlation_id: str = ""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Hypothesis(Strict):
    cause: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list, max_length=20)
    counter_evidence: list[str] = Field(default_factory=list, max_length=20)
    flagged: bool = False


class Recommendation(Strict):
    kind: Literal["RECOVER", "INVESTIGATE", "CONFIGURE", "ESCALATE"]
    text: str = Field(min_length=1, max_length=500)
    plan_id: str | None = None
    args: dict[str, str] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class Usage(Strict):
    provider: str = ""
    name: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class Insight(Strict):
    insight_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trigger: Trigger
    window: Window
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    assessment: Severity = "INFO"
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=3)
    recommendations: list[Recommendation] = Field(default_factory=list, max_length=10)
    proposals: list[str] = Field(default_factory=list, max_length=3)
    degraded: bool = False
    analyst: Literal["heuristic", "llm"] = "heuristic"
    flags: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list, max_length=20)
    model: Usage | None = None
    created: str = Field(default_factory=lambda: iso_now())

    def to_json(self):
        """As schemas/insight.schema.json (`window.from`, no nulls but `model`)."""
        out = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        out["model"] = self.model.model_dump(mode="json") if self.model else None
        return out

    @property
    def confidence(self):
        return max((h.confidence for h in self.hypotheses), default=0.0)


def iso_now():
    return iso(datetime.datetime.now(tz=datetime.UTC))


def iso(t):
    """A datetime as the schemas write it: UTC, nine fractional digits."""
    t = t.astimezone(datetime.UTC)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond:06d}000Z"
