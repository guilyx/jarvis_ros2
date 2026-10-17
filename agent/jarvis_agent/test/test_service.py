# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Triggers, the service loop, the gateway adapter and the command line."""

import asyncio
import json

import pytest
from conftest import FIXTURES, STALL, WEDGE
from jarvis_agent import cli
from jarvis_agent.app import run
from jarvis_agent.config import AgentConfig
from jarvis_agent.core.agent import Agent
from jarvis_agent.core.triggers import TriggerPolicy
from jarvis_agent.events import ns
from jarvis_agent.model import TriggerType

S = 1_000_000_000


def ev(i, kind, code, incident="i1", severity="ERROR", **attrs):
    return {
        "id": f"e{i}",
        "seq": str(i),
        "stamp": "2026-10-17T00:00:00Z",
        "source": {"robot": "r", "node": "/n", "component": "c", "host": ""},
        "kind": kind,
        "code": code,
        "severity": severity,
        "correlation_id": incident,
        "attrs": attrs,
    }


def test_an_incident_is_analyzed_once_it_settled():
    policy = TriggerPolicy(settle_s=20)
    policy.on_event(ev(1, "SYSTEM", "jarvis.incident.opened"), 0)
    assert policy.due(19 * S) == []
    [p] = policy.due(20 * S)
    assert (p.trigger.type, p.trigger.ref, p.causation_id) == (TriggerType.INCIDENT_OPENED, "i1", "e1")
    policy.on_event(ev(2, "SYSTEM", "jarvis.incident.opened"), 21 * S)  # the same, again
    assert policy.due(60 * S) == []


def test_a_failed_recovery_upgrades_a_waiting_trigger():
    policy = TriggerPolicy(settle_s=20)
    policy.on_event(ev(1, "SYSTEM", "jarvis.incident.opened"), 0)
    policy.on_event(ev(2, "RECOVERY", "jarvis.recovery.failed", phase="FAILED"), 10 * S)
    assert policy.due(25 * S) == []  # the failure restarted the wait
    [p] = policy.due(30 * S)
    assert p.trigger.type == TriggerType.RECOVERY_FAILED and p.causation_id == "e2"


@pytest.mark.parametrize(
    "event",
    [
        ev(1, "SYSTEM", "jarvis.incident.opened", severity="WARN"),  # below ERROR
        ev(1, "RECOVERY", "jarvis.recovery.failed", phase="FAILED", dry_run=True),
        ev(1, "INSIGHT", "jarvis.insight"),  # its own output
        ev(1, "SYSTEM", "jarvis.incident.opened", incident=""),
    ],
)
def test_what_does_not_trigger(event):
    policy = TriggerPolicy(settle_s=0)
    policy.on_event(event, 0)
    assert policy.due(1) == []


def test_the_hourly_budget():
    policy = TriggerPolicy(settle_s=0, max_per_hour=2)
    for i in range(4):
        policy.on_event(ev(i, "SYSTEM", "jarvis.incident.opened", incident=f"i{i}"), 0)
    assert len(policy.due(1)) == 2 and policy.dropped == 2
    policy.on_event(ev(9, "SYSTEM", "jarvis.incident.opened", incident="later"), 3601 * S)
    assert len(policy.due(3602 * S)) == 1


class ListSink:
    def __init__(self):
        self.published = []

    def publish(self, insight, correlation_id="", causation_id=""):
        self.published.append((insight, correlation_id, causation_id))


def test_the_service_turns_the_stream_into_insights(query, events):
    """The recorded tour, replayed as a live stream: the failed restart of the stall is analyzed."""
    sink = ListSink()
    agent = Agent(query, sink=sink)
    policy = TriggerPolicy(settle_s=0)

    async def stream():
        for e in events:
            yield e
            await asyncio.sleep(0)

    clock = iter(range(10**6)).__next__  # it only needs to move forward
    asyncio.run(run(agent, policy, stream(), clock=lambda: clock() * S, tick_s=0.0))
    for p in policy.due(10**12):  # what was still settling when the stream ended
        agent.analyze(p.trigger, p.causation_id)
    by_incident = {cid: insight for insight, cid, _ in sink.published}
    assert by_incident[STALL].title.endswith("restart-lifecycle-node did not fix it")
    assert all(cause for _, _, cause in sink.published)  # each cites the event that triggered it


def test_the_gateway_adapter_reads_the_same_history(tmp_path):
    """The agent over a real gateway (its in-memory Jarvis): an incident whose restart failed."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from jarvis_agent.adapters.gateway import GatewayQuery
    from jarvis_gateway.adapters.fake import FakeJarvis
    from jarvis_gateway.app import create_app
    from jarvis_gateway.config import Principal, Settings

    fake = FakeJarvis(step_s=0.01)
    fake.plans.append(dict(fake.plans[0], id="fail-restart", agent_policy="supervised"))
    settings = Settings(data_dir=str(tmp_path), anonymous=Principal("test", "operator"))
    with TestClient(create_app(settings, fake)) as client:
        fault = client.portal.call(lambda: fake.raise_fault("ros.topic.stale", "lidar"))
        incident = fault["incident_id"]
        r = client.post("/api/v1/recoveries", json={"plan_id": "fail-restart", "correlation_id": incident})
        assert r.status_code == 202, r.text
        execution = r.json()["data"]["execution_id"]
        for _ in range(200):
            x = client.get(f"/api/v1/recoveries/{execution}").json()["execution"]
            if x and x["state"] == "FAILED":
                break
        insight = Agent(GatewayQuery(str(client.base_url), client=client)).explain(incident)
    assert insight.title == "ros.topic.stale on lidar: fail-restart did not fix it"
    assert insight.window.correlation_id == incident
    assert insight.hypotheses[0].evidence  # real event ids, from the gateway's history
    assert not insight.flags


def test_cli_analyze_a_replay(capsys, tmp_path):
    plans = tmp_path / "plans.yaml"
    plans.write_text(json.dumps({"plans": json.loads((FIXTURES / "plans.json").read_text())}))
    replay = str(FIXTURES / "demo_tour.jsonl")
    assert cli.main(["analyze", WEDGE, "--replay", replay, "--plans", str(plans)]) == 0
    text = capsys.readouterr().out
    assert text.startswith("ERROR  sensor.scan.stale on lidar: fixed by restart-lifecycle-node")
    assert "heuristic analyst (degraded)" in text
    assert cli.main(["analyze", STALL, "--replay", replay, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["window"]["correlation_id"] == STALL and out["model"] is None
    assert cli.main(["analyze", "nope", "--replay", replay]) == 1


def test_cli_ignores_ros_arguments():
    assert cli.strip_ros_args(["run", "--ros-args", "-r", "__node:=agent", "--", "--publish", "stdout"]) == [
        "run",
        "--publish",
        "stdout",
    ]


def test_config(tmp_path, monkeypatch):
    path = tmp_path / "agent.yaml"
    path.write_text(
        "schema: jarvis.agent/v1\ngateway: {url: http://robot:8080}\nautonomy: {level: supervised}\n"
        "triggers: {settle_s: 5}\nredact: ['attrs.gps.*']\n"
    )
    monkeypatch.setenv("JARVIS_AGENT_TOKEN", "secret")
    c = AgentConfig.load(str(path))
    assert (c.gateway_url, c.level, c.settle_s, c.redact, c.token) == (
        "http://robot:8080",
        "supervised",
        5,
        ["attrs.gps.*"],
        "secret",
    )
    path.write_text("schema: jarvis.agent/v1\ngateway: {url: x, token: leaked}\n")
    with pytest.raises(ValueError, match="JARVIS_AGENT_TOKEN"):
        AgentConfig.load(str(path))
    with pytest.raises(ValueError, match="autonomy"):
        AgentConfig.load(None, level="yolo")


def test_stamps():
    assert ns("2026-10-17T09:14:03.120456789Z") % 1_000_000_000 == 120456789
    assert ns("2026-10-17T09:14:03Z") == ns("2026-10-17T09:14:03.000000000+00:00")
    assert ns("not a stamp") is None


def test_the_stream_catches_up_then_resumes():
    """No seq yet: catch_up says where to start (and is retried while the gateway is not up);
    after a disconnect, the stream resumes from the last seq it gave."""
    httpx = pytest.importorskip("httpx")
    pytest.importorskip("websockets")
    from jarvis_agent.adapters.gateway import stream

    sent, answers = [], iter([httpx.ConnectError("not up"), "41"])

    def catch_up():
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    sessions = iter(
        [
            [{"type": "event", "data": {"seq": "42"}}, {"type": "backfill_done", "seq": "42"}],
            [{"type": "event", "data": {"seq": "43"}}],
        ]
    )

    class Socket:
        def __init__(self, messages):
            self.messages = messages

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def send(self, text):
            sent.append(json.loads(text))

        def __aiter__(self):
            async def gen():
                for m in self.messages:
                    yield json.dumps(m)

            return gen()

    async def take(n):
        events = stream(
            "http://gw", connect=lambda url: Socket(next(sessions)), backoff_s=(0,), catch_up=catch_up
        )
        return [(await anext(events))["seq"] for _ in range(n)]

    assert asyncio.run(take(2)) == ["42", "43"]
    assert sent == [{"op": "subscribe", "since_seq": "41"}, {"op": "subscribe", "since_seq": "42"}]
