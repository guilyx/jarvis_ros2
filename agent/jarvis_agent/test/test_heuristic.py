# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The heuristic analyst on the recorded demo tour: what it concludes, and that it cites real events."""

import json

import pytest
from conftest import ESTOP, ROOT, STALL, WEDGE
from jarvis_agent.core.agent import Agent
from jarvis_agent.model import Trigger, TriggerType


def analyze(query, incident, kind=TriggerType.ON_DEMAND):
    return Agent(query).analyze(Trigger(type=kind, ref=incident))


def test_a_failed_restart_points_past_what_it_restarts(query):
    insight = analyze(query, STALL, TriggerType.RECOVERY_FAILED)
    assert insight.title == "sensor.scan.stale on lidar: restart-lifecycle-node did not fix it"
    assert insight.assessment == "ERROR"
    top = insight.hypotheses[0]
    assert "beyond what restart-lifecycle-node acts on" in top.cause
    assert top.confidence == pytest.approx(0.55)
    assert any("keeps coming back" in h.cause for h in insight.hypotheses)
    kinds = [r.kind for r in insight.recommendations]
    assert kinds == ["INVESTIGATE", "ESCALATE"]
    assert not [r for r in insight.recommendations if r.kind == "RECOVER"]  # it already failed here
    assert insight.degraded and insight.analyst == "heuristic"
    assert insight.window.correlation_id == STALL
    assert "recovery restart-lifecycle-node (RULE lidar-stale-restart): FAILED after 15 s" in insight.facts


def test_a_restart_that_worked_is_credited(query):
    insight = analyze(query, WEDGE)
    assert insight.title == "sensor.scan.stale on lidar: fixed by restart-lifecycle-node"
    assert insight.hypotheses[0].cause.startswith("What restart-lifecycle-node acts on was the problem")
    assert "restart-lifecycle-node fixed sensor.scan.stale 1 of 1 time(s) before" in insight.facts


def test_a_latched_fault_waits_for_an_operator(query):
    insight = analyze(query, ESTOP)
    assert insight.assessment == "CRITICAL"
    assert insight.recommendations[0].text.startswith("safety.estop.engaged is latched")


def test_every_piece_of_evidence_is_a_real_event(query, events):
    ids = {e["id"] for e in events}
    for incident in (WEDGE, STALL, ESTOP):
        insight = analyze(query, incident)
        cited = [i for h in insight.hypotheses for i in h.evidence]
        assert cited and set(cited) <= ids
        assert not insight.flags, insight.flags  # nothing for the harness to fix


def test_insights_follow_the_schema(query):
    jsonschema = pytest.importorskip("jsonschema")
    from referencing import Registry, Resource

    schemas = {p.name: json.loads(p.read_text()) for p in (ROOT / "schemas").glob("*.schema.json")}
    registry = Registry().with_resources(
        [(name, Resource.from_contents(s)) for name, s in schemas.items()]
        + [(s["$id"], Resource.from_contents(s)) for s in schemas.values()]
    )
    validator = jsonschema.Draft202012Validator(schemas["insight.schema.json"], registry=registry)
    for incident in (WEDGE, STALL, ESTOP):
        validator.validate(analyze(query, incident).to_json())


def test_unknown_incidents_have_no_insight(query):
    assert analyze(query, "nope") is None


def test_history_is_what_was_known_then(query):
    # Replaying the first wedge, the later stall must not count as its history.
    first = next(e for e in query.all if e["code"] == "jarvis.incident.opened" and "lidar" in e["message"])
    insight = analyze(query, first["correlation_id"])
    assert not any("earlier episode" in f for f in insight.facts)
    assert not any("fixed" in f and "before" in f for f in insight.facts)


def test_without_an_incident_it_summarizes_the_warnings(query):
    agent = Agent(query, clock=query.now_ns)
    insight = agent.analyze(Trigger(type=TriggerType.ON_DEMAND, ref=""), question="what happened?")
    assert insight.window.event_count > 0
    assert insight.title.endswith("warning(s) in the window")
    assert insight.hypotheses and insight.hypotheses[0].confidence == pytest.approx(0.3)
