# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Triage, harness checks, autonomy and the rendered context."""

import pytest
from conftest import STALL
from jarvis_agent.core.checks import check, validate_args
from jarvis_agent.core.context import render, row_line
from jarvis_agent.core.policy import AutonomyPolicy
from jarvis_agent.core.triage import collapse, redact, triage
from jarvis_agent.core.windows import WindowManager
from jarvis_agent.model import Hypothesis, Insight, Recommendation, Trigger, TriggerType, Window


def stall_window(query):
    return WindowManager(query).incident(Trigger(type=TriggerType.RECOVERY_FAILED, ref=STALL), STALL)


def test_the_incident_window_adds_context_around_it(query):
    w = stall_window(query)
    own = [e for e in w.events if e.get("correlation_id") == STALL]
    assert len(own) < len(w.events)  # the raises and states around it, from the same component
    assert {e["source"]["component"] for e in w.events if e not in own} <= {"lidar", ""}
    assert [x["outcome"] for x in w.executions] == ["FAILED"]
    assert w.incident["escalated"] is True
    assert "sensor.scan.stale" in w.history


def test_repeats_collapse_and_transitions_stay(query):
    w = stall_window(query)
    rows = collapse(w.events)
    raises = [r for r in rows if r.first["kind"] == "FAULT_RAISE" and r.first["code"] == "sensor.scan.stale"]
    assert len(raises) == 1 and raises[0].count > 3
    transitions = [r for r in rows if r.first["kind"] == "FAULT_STATE"]
    assert all(r.count == 1 for r in transitions)


def test_triage_finds_root_symptoms_and_outcomes(query):
    result = triage(stall_window(query))
    assert result.root["code"] == "sensor.scan.stale"
    assert [s["code"] for s in result.symptoms] == ["diag.scan_relay.output_rate"]
    assert [x["plan"] for x in result.failed] == ["restart-lifecycle-node"]
    assert any(
        f.startswith("first reports: sensor.scan.stale → diag.scan_relay.output_rate") for f in result.facts
    )


def test_redaction_happens_before_anything_is_rendered(query):
    w = stall_window(query)
    hidden = redact(w.events, ["attrs.node", "message"])
    assert all(e.get("message", "[redacted]") == "[redacted]" for e in hidden)
    assert all((e.get("attrs") or {}).get("node", "[redacted]") == "[redacted]" for e in hidden)
    assert w.events[0] is not hidden[0]  # the window itself is untouched


def test_the_rendered_context_fits_its_budget(query):
    w = stall_window(query)
    result = triage(w)
    stable, text = render(w, result)
    assert "restart-lifecycle-node(component, fault?, node)" in stable
    assert "## Facts (computed)" in text and "FAILED after 15 s" in text
    # A budget for everything but the collapsed repeats: transitions are kept first.
    transitions = [r for r in result.rows if r.count == 1 and r.first["kind"] not in ("LOG", "STATE")]
    repeats = [r for r in result.rows if r.count > 1]
    assert transitions and repeats
    head = text.split("## Events")[0]
    budget = (len(stable) + len(head) + sum(len(row_line(r)) + 1 for r in transitions)) // 4 + 1
    _, small = render(w, result, budget_tokens=budget)
    assert f"({len(repeats)} lower-ranked rows left out" in small
    assert small.count("RECOVERY") == text.count("RECOVERY")
    assert "×" not in small.split("## Events")[1]


def _insight(**kw):
    base = dict(
        trigger=Trigger(type=TriggerType.ON_DEMAND),
        window=Window(**{"from": "2026-10-17T00:00:00Z"}, to="2026-10-17T00:00:01Z", event_count=1),
        title="t",
    )
    return Insight(**{**base, **kw})


def test_checks_drop_what_the_analyst_never_saw(plans):
    insight = _insight(
        hypotheses=[
            Hypothesis(cause="real", confidence=0.8, evidence=["e1", "ghost"]),
            Hypothesis(cause="invented", confidence=0.9, evidence=["ghost"]),
        ]
    )
    checked = check(insight, {"e1"}, plans)
    real, invented = sorted(checked.hypotheses, key=lambda h: h.cause, reverse=True)
    assert real.evidence == ["e1"] and real.confidence == 0.8
    assert invented.evidence == [] and invented.confidence == 0.3 and invented.flagged
    assert any("never saw" in f for f in checked.flags)


def test_checks_validate_plans_and_arguments(plans):
    recover = lambda plan, **args: Recommendation(kind="RECOVER", text="x", plan_id=plan, args=args)  # noqa: E731
    insight = _insight(
        recommendations=[
            recover("nope"),
            recover("restart-lifecycle-node", node="relative/name", component="lidar"),
            recover("restart-lifecycle-node", node="/demo/scan_relay", component="lidar", colour="red"),
            recover("restart-lifecycle-node", component="lidar"),  # node left to the operator
            Recommendation(kind="INVESTIGATE", text="look"),
        ]
    )
    checked = check(insight, set(), plans)
    assert [r.kind for r in checked.recommendations] == ["RECOVER", "INVESTIGATE"]
    assert any("unknown plan 'nope'" in f for f in checked.flags)
    assert any("does not match ^/" in f for f in checked.flags)
    assert any("unknown parameter 'colour'" in f for f in checked.flags)
    plan = plans[0]
    assert validate_args(plan, {"component": "lidar"}) == ["missing required parameter 'node'"]
    assert validate_args(plan, {"node": "/a", "component": "b"}) == []


@pytest.mark.parametrize(
    ("level", "policy", "confidence", "analyst", "expected"),
    [
        ("advise", "supervised", 0.9, "llm", "advise"),
        ("autonomous", "supervised", 0.9, "llm", "supervised"),
        ("autonomous", "autonomous", 0.9, "llm", "autonomous"),
        ("autonomous", "autonomous", 0.5, "llm", "supervised"),
        ("autonomous", "autonomous", 0.9, "heuristic", "advise"),
        ("supervised", "never", 0.9, "llm", "observe"),
        ("observe", "autonomous", 0.9, "llm", "observe"),
    ],
)
def test_autonomy_is_the_lower_of_deployment_and_plan(level, policy, confidence, analyst, expected):
    plan = {"id": "p", "agent_policy": policy, "allowed_requesters": ["AGENT", "OPERATOR"]}
    assert AutonomyPolicy(level, min_confidence=0.7).decide(plan, confidence, analyst) == expected


def test_plans_the_agent_may_not_request_are_never_proposed():
    plan = {"id": "p", "agent_policy": "autonomous", "allowed_requesters": ["OPERATOR"]}
    assert AutonomyPolicy("autonomous").decide(plan, 1.0) == "observe"
    with pytest.raises(ValueError):
        AutonomyPolicy("yolo")
