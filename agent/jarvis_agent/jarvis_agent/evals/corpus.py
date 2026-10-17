# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The eval corpus: one directory per case, `label.yaml` (schema jarvis.eval/v1) and the events.

    schema: jarvis.eval/v1
    description: what happened, in a sentence or two
    events: events.jsonl             # the recording (ros2 jarvis export); default: next to the label
    plans: plans.json                # the plan catalog then (a JSON list, or {plans: [...]})
    drop: ["demo.tour.*"]            # codes taken out first (narration that gives the answer away)
    at: 2026-10-17T14:26:11.700Z     # the recording is cut here: when the live agent would analyze
    trigger: {type: RECOVERY_FAILED, incident: <id>}   # or {type: ON_DEMAND, question: "..."}
    root_cause:
      code: sensor.scan.stale        # the fault it comes down to (a glob)
      component: lidar
      text: what a good answer says (for the judge)
      mentions: [[lidar itself, upstream], [restart]]  # each group: one of these words (the rules)
    must_cite: [[<id>, <id>], [<id>]]  # each group: cite one of these
    expect:
      assessment: [ERROR, CRITICAL]
      plans: {acceptable: [], forbidden: [restart-lifecycle-node], required: false}
      escalate: true                 # an ESCALATE recommendation (omit: either way)

Seeded from the demo's recorded tour (evals/recordings); a real incident joins as its own directory
with its export.
"""

import dataclasses
import datetime
import fnmatch
import json
import pathlib

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from jarvis_agent.adapters.replay import load_jsonl
from jarvis_agent.events import incident_of, ns
from jarvis_agent.model import Severity, Trigger, TriggerType, iso

SCHEMA = "jarvis.eval/v1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LabelTrigger(_Strict):
    type: TriggerType
    incident: str = ""
    question: str = ""


class RootCause(_Strict):
    code: str
    component: str = ""
    text: str = Field(min_length=1)
    mentions: list[list[str]] = Field(default_factory=list)


class Plans(_Strict):
    acceptable: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)
    required: bool = False  # a RECOVER recommendation of an acceptable plan is expected


class Expect(_Strict):
    assessment: list[Severity] = Field(default_factory=list)
    plans: Plans = Field(default_factory=Plans)
    escalate: bool | None = None


class Label(_Strict):
    schema_: str = Field(alias="schema")
    description: str = ""
    events: str = "events.jsonl"
    plans: str = ""
    drop: list[str] = Field(default_factory=list)
    at: str = ""
    trigger: LabelTrigger
    root_cause: RootCause
    must_cite: list[list[str]] = Field(default_factory=list)
    expect: Expect = Field(default_factory=Expect)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("at", mode="before")
    @classmethod
    def _stamp(cls, value):  # YAML reads an unquoted time as a datetime
        return iso(value) if isinstance(value, datetime.datetime) else value


@dataclasses.dataclass
class Case:
    name: str
    label: Label
    events: list  # the recording, cut at `at` and without the dropped codes
    plans: list
    at_ns: int | None

    @property
    def trigger(self):
        return Trigger(type=self.label.trigger.type, ref=self.label.trigger.incident)

    @property
    def question(self):
        return self.label.trigger.question


class CorpusError(ValueError):
    pass


def load_case(folder):
    folder = pathlib.Path(folder)
    try:
        label = Label.model_validate(yaml.safe_load((folder / "label.yaml").read_text()) or {})
    except (OSError, yaml.YAMLError, ValueError) as e:
        raise CorpusError(f"{folder.name}: {e}") from None
    if label.schema_ != SCHEMA:
        raise CorpusError(f"{folder.name}: schema {label.schema_!r}, not {SCHEMA}")
    at_ns = ns(label.at) if label.at else None
    if label.at and at_ns is None:
        raise CorpusError(f"{folder.name}: at: not a time: {label.at}")
    events = [
        e
        for e in _read(folder, label.events, load_jsonl)
        if not any(fnmatch.fnmatchcase(e.get("code", ""), d) for d in label.drop)
        and (at_ns is None or (ns(e.get("stamp")) or 0) <= at_ns)
    ]
    plans = []
    if label.plans or (folder / "plans.json").exists():
        raw = _read(folder, label.plans or "plans.json", lambda p: json.loads(p.read_text()))
        plans = (raw.get("plans") or []) if isinstance(raw, dict) else raw
    case = Case(folder.name, label, events, plans, at_ns)
    if problems := check(case):
        raise CorpusError(f"{folder.name}: " + "; ".join(problems))
    return case


def load(corpus, names=()):
    """The cases of a corpus directory (all, or those named), by name."""
    corpus = pathlib.Path(corpus)
    folders = sorted(p for p in corpus.iterdir() if (p / "label.yaml").is_file()) if corpus.is_dir() else []
    if not folders:
        raise CorpusError(f"no cases in {corpus} (a case is a directory with a label.yaml)")
    if names:
        missing = sorted(set(names) - {p.name for p in folders})
        if missing:
            raise CorpusError(f"no case {', '.join(missing)} in {corpus}")
        folders = [p for p in folders if p.name in names]
    return [load_case(p) for p in folders]


def check(case):
    """What is wrong with a case: a label must only name what its recording holds."""
    label, problems = case.label, []
    ids = {e["id"] for e in case.events}
    if not case.events:
        problems.append("no events (before `at`)")
    t = label.trigger
    if t.type == TriggerType.ON_DEMAND and not (t.incident or t.question):
        problems.append("an ON_DEMAND trigger needs an incident or a question")
    if t.type != TriggerType.ON_DEMAND and not t.incident:
        problems.append(f"a {t.type} trigger needs an incident")
    if t.incident and not any(
        incident_of(e) == t.incident and e.get("code") == "jarvis.incident.opened" for e in case.events
    ):
        problems.append(f"incident {t.incident} does not open in the recording (before `at`)")
    for group in label.must_cite:
        if not group:
            problems.append("must_cite: an empty group")
        problems += [f"must_cite: no event {i}" for i in group if i not in ids]
    catalog = {p.get("id") for p in case.plans}
    named = label.expect.plans.acceptable + label.expect.plans.forbidden
    problems += [f"expect.plans: no plan {p} in the catalog" for p in named if p not in catalog]
    if label.expect.plans.required and not label.expect.plans.acceptable:
        problems.append("expect.plans: required without an acceptable plan")
    if not any(fnmatch.fnmatchcase(e.get("code", ""), label.root_cause.code) for e in case.events):
        problems.append(f"root_cause.code: no {label.root_cause.code} event")
    return problems


def _read(folder, name, reader):
    path = (folder / name).resolve()
    try:
        return reader(path)
    except (OSError, ValueError) as e:
        raise CorpusError(f"{folder.name}: {name}: {e}") from None
