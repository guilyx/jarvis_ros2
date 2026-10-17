# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Proposals (milestone 4.3): the model's propose_recovery, checked by the tool, routed by the agent
through the autonomy policy to the one gate (a fake RecoveryClient here; the ROS one in
test_ros_sink.py and the launch test), or kept as advice."""

import json

from conftest import STALL
from jarvis_agent.adapters.replay import ReplayQuery
from jarvis_agent.core.agent import Agent
from jarvis_agent.core.analyst import LLMAnalyst
from jarvis_agent.core.policy import AutonomyPolicy
from jarvis_agent.core.ports import Submitted
from jarvis_agent.llm.fake import FakeLLM, call, turn
from jarvis_agent.model import Trigger, TriggerType
from test_llm import body, schema_validate

ARGS = {"node": "/demo/scan_relay", "component": "lidar"}


class Gate:
    def __init__(self, answer=None):
        self.answer = answer or Submitted("x-1", "PENDING_APPROVAL", "waiting for an operator")
        self.calls = []

    def submit(self, plan_id, args, rationale, confidence, correlation_id="", causation_id=""):
        self.calls.append(
            dict(plan_id=plan_id, args=args, rationale=rationale, confidence=confidence,
                 correlation_id=correlation_id, causation_id=causation_id)
        )  # fmt: skip
        return self.answer


def evidence(query):
    return [query.incident(STALL)["timeline"][1]["id"]]


def propose(query, **changes):
    a = {
        "plan_id": "restart-lifecycle-node",
        "args": ARGS,
        "rationale": "The relay hangs again",
        "confidence": 0.8,
        "evidence": evidence(query),
    }
    a.update(changes)
    return call("propose_recovery", **a)


def run(query, script, level="supervised", gate=None, proposals=True):
    llm = FakeLLM(script)
    agent = Agent(
        query,
        analyst=LLMAnalyst(llm, query, proposals=proposals),
        policy=AutonomyPolicy(level, 0.7),
        recovery=gate,
    )
    insight = agent.analyze(Trigger(type=TriggerType.RECOVERY_FAILED, ref=STALL), causation_id="e-7")
    return insight, llm


def results(llm, n=1):
    return [json.loads(m["content"]) for m in llm.calls[n]["messages"] if m["role"] == "tool"]


def test_a_proposal_goes_to_the_gate_as_the_agent(query):
    gate = Gate()
    insight, llm = run(query, [turn(propose(query)), turn(call("submit_insight", **body(query)))], gate=gate)
    assert results(llm) == [
        {"recorded": "restart-lifecycle-node", "next": "Jarvis routes it once you submit your insight"}
    ]
    (sent,) = gate.calls
    assert sent["plan_id"] == "restart-lifecycle-node" and sent["args"] == ARGS
    assert sent["rationale"] == f"The relay hangs again (insight {insight.insight_id})"
    assert (sent["confidence"], sent["correlation_id"], sent["causation_id"]) == (0.8, STALL, "e-7")
    assert insight.proposals == ["x-1"]
    assert (
        "proposed restart-lifecycle-node (supervised): PENDING_APPROVAL: waiting for an operator"
        in insight.flags
    )
    schema_validate(insight)
    system = llm.calls[0]["messages"][0]["content"]
    assert "propose_recovery" in system
    assert "propose_recovery" in [t["function"]["name"] for t in llm.calls[0]["tools"]]


def test_a_rejection_by_the_gates_is_said(query):
    gate = Gate(Submitted("x-2", "REJECTED", "precondition faults_inactive failed"))
    insight, _ = run(query, [turn(propose(query)), turn(call("submit_insight", **body(query)))], gate=gate)
    assert insight.proposals == ["x-2"]
    assert any("REJECTED: precondition faults_inactive failed" in f for f in insight.flags)


def test_below_supervised_it_stays_advice(query):
    gate = Gate()
    insight, _ = run(
        query, [turn(propose(query)), turn(call("submit_insight", **body(query)))], "advise", gate
    )
    assert gate.calls == [] and insight.proposals == []
    (advice,) = [r for r in insight.recommendations if r.kind == "RECOVER"]
    assert (advice.plan_id, advice.args, advice.text) == (
        "restart-lifecycle-node",
        ARGS,
        "The relay hangs again",
    )
    assert "advised restart-lifecycle-node, not proposed (autonomy advise)" in insight.flags

    insight, _ = run(query, [turn(propose(query)), turn(call("submit_insight", **body(query)))], gate=None)
    assert "advised restart-lifecycle-node, not proposed (no recovery client here)" in insight.flags


def test_a_heuristic_answer_keeps_proposals_as_advice(query):
    gate = Gate()
    bad = call("submit_insight", **body(query, assessment="NOPE"))
    insight, _ = run(query, [turn(propose(query)), turn(bad), turn(bad)], "autonomous", gate)
    assert insight.analyst == "heuristic" and gate.calls == []
    assert "advised restart-lifecycle-node, not proposed (autonomy advise)" in insight.flags


def test_the_tool_checks_proposals(events, plans, query):
    never = {**plans[0], "id": "reboot", "agent_policy": "never"}
    q = ReplayQuery(events, [*plans, never])
    script = [
        turn(
            propose(q, plan_id="format-disk"),
            propose(q, plan_id="reboot"),
            propose(q, args={"node": "scan_relay", "component": "lidar"}),
            propose(q, args={"component": "lidar"}),
            propose(q, evidence=["made-up"]),
            propose(q, confidence=7),
            propose(q),
            propose(q, confidence=0.5),
            propose(q, confidence=0.6),
            propose(q, confidence=0.7),
        ),
        turn(call("submit_insight", **body(q))),
    ]
    insight, llm = run(q, script, gate=Gate())
    out = results(llm)
    assert out[0] == {"error": "no plan 'format-disk' in the catalog"}
    assert out[1]["error"].startswith("reboot takes no agent proposals (agent_policy never)")
    assert "node: does not match ^/" in out[2]["error"]
    assert "missing required parameter 'node'" in out[3]["error"]
    assert out[4] == {"error": "cite at least one event id you were shown"}
    assert out[5]["error"].startswith("bad arguments")
    assert [o.get("recorded") for o in out[6:9]] == ["restart-lifecycle-node"] * 3
    assert out[9] == {"error": "at most 3 proposals"}
    assert len(insight.proposals) == 3


def test_without_proposals_the_tool_is_not_there(query):
    insight, llm = run(
        query, [turn(propose(query)), turn(call("submit_insight", **body(query)))], proposals=False
    )
    assert results(llm)[0]["error"].startswith("no tool 'propose_recovery'")
    assert "propose_recovery" not in [t["function"]["name"] for t in llm.calls[0]["tools"]]
    assert "propose_recovery" not in llm.calls[0]["messages"][0]["content"]
    assert insight.proposals == []


def test_the_cli_offers_proposals_only_to_the_service(monkeypatch):
    from jarvis_agent import cli
    from jarvis_agent.config import AgentConfig

    monkeypatch.setattr(cli, "make_analyst", lambda config, query, proposals=False: ("offered", proposals))
    config = AgentConfig(level="supervised")
    assert cli.make_agent(config, query=None, recovery=Gate()).analyst == ("offered", True)
    assert cli.make_agent(config, query=None).analyst == ("offered", False)  # analyze: no client
    config.level = "advise"
    assert cli.make_agent(config, query=None, recovery=Gate()).analyst == ("offered", False)
