# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""What a model may see (NFR-13, milestone 5.4): the redact patterns over every record the agent
reads, the scrubber over every message it sends, and ids that stay ids."""

import copy
import json

import pytest
from conftest import STALL
from jarvis_agent.adapters.replay import ReplayQuery
from jarvis_agent.config import AgentConfig
from jarvis_agent.core.redaction import REDACTED, Redactor, Scrubber, check_patterns, redacting
from jarvis_agent.llm.fake import call, turn
from test_llm import analyze, body

EVENT = {
    "id": "e1",
    "seq": "7",
    "kind": "FAULT_STATE",
    "code": "sensor.scan.stale",
    "severity": "ERROR",
    "stamp": "2026-10-17T09:14:03Z",
    "source": {"node": "/lidar", "component": "lidar", "host": "robot-7.lab.example.com"},
    "message": "lidar silent",
    "attrs": {"fault_id": "f1", "state": "ACTIVE", "gps.lat": "48.85", "GPS.Lon": "2.35", "diag.hw": "SN-1"},
    "operator": "alice",
    "faults": [{"code": "x", "freeze_frame": {"gps.lat": "48.85", "fault_id": "f1"}}],
}


def test_patterns_hide_fields_and_keys_without_regard_to_case():
    r = Redactor(["attrs.gps.*", "source.host", "Message"])
    e = r.record(EVENT)
    assert e["attrs"] == {
        "fault_id": "f1",
        "state": "ACTIVE",
        "gps.lat": REDACTED,
        "GPS.Lon": REDACTED,
        "diag.hw": "SN-1",
    }
    assert e["source"] == {"node": "/lidar", "component": "lidar", "host": REDACTED}
    assert e["message"] == REDACTED
    assert e["faults"][0]["freeze_frame"] == {"gps.lat": REDACTED, "fault_id": "f1"}, (
        "freeze frames copy attrs"
    )
    assert EVENT["attrs"]["gps.lat"] == "48.85", "a copy: the record read is left as it was"


def test_what_an_analysis_runs_on_stays_whatever_the_patterns_say():
    e = Redactor(["attrs.*", "attrs", "source", "*_id"]).record(EVENT)
    assert e["attrs"] == {
        "fault_id": "f1",
        "state": "ACTIVE",
        "gps.lat": REDACTED,
        "GPS.Lon": REDACTED,
        "diag.hw": REDACTED,
    }
    assert e["source"] == {"node": REDACTED, "component": "lidar", "host": REDACTED}, "keys stay, values go"
    assert (e["id"], e["seq"], e["code"], e["severity"]) == ("e1", "7", "sensor.scan.stale", "ERROR")
    for named in ("seq", "attrs.fault_id", "attrs.STATE", "source.component", "component"):
        with pytest.raises(ValueError, match="never redacted"):
            check_patterns([named])


def test_identities_are_hidden_wherever_jarvis_records_them():
    e = Redactor(["@identities"]).record(
        {**EVENT, "attrs": {"user": "bob", "requester_id": "carol", "state": "x"}}
    )
    assert e["operator"] == REDACTED and e["attrs"] == {
        "user": REDACTED,
        "requester_id": REDACTED,
        "state": "x",
    }


@pytest.mark.parametrize("bad", ["attrs.gps.*", [""], [3], ["@everyone"]])
def test_bad_patterns_are_refused_at_load(tmp_path, bad):
    with pytest.raises(ValueError, match="redact"):
        check_patterns(bad)
    path = tmp_path / "agent.yaml"
    path.write_text(f"schema: jarvis.agent/v1\nredact: {json.dumps(bad)}\n")
    with pytest.raises(ValueError, match="redact"):
        AgentConfig.load(str(path))


def test_the_redacting_query_wraps_once():
    class Q:
        def incident(self, _):
            return copy.deepcopy(EVENT)

        def plans(self):
            return ["p"]

    q = redacting(Q(), ["message"])
    assert redacting(q, ["message"]) is q and redacting(Q(), []).__class__ is Q
    assert q.incident("x")["message"] == REDACTED and q.plans() == ["p"]


def test_the_scrubber_hides_secrets_and_addresses():
    s = Scrubber(
        environ={"OPENROUTER_API_KEY": "or-v1-0123456789abcdef", "SHORT_TOKEN": "abc", "HOME": "/root"}
    )
    text = (
        "key or-v1-0123456789abcdef, sk-ant-api03-AAAAAAAAAAAAAAAAAAAA, AKIAABCDEFGHIJKLMNOP, "
        "ghp_" + "a" * 36 + ", Bearer abc.def.ghi-jkl, password=hunter2, "
        '{"api_key": "zz9"}, mail ops@example.com, event 01a14a41-27c0-73bf-bd09-b93746be9cb3 at /root'
    )
    out = s.text(text)
    for secret in ("or-v1-0123456789abcdef", "sk-ant", "AKIA", "ghp_", "abc.def", "hunter2", "zz9", "ops@"):
        assert secret not in out, secret
    assert "password=[secret]" in out and '"api_key": "[secret]"' in out, "what it was stays readable"
    assert "01a14a41-27c0-73bf-bd09-b93746be9cb3" in out and "/root" in out, "ids and other text stay"
    assert "ops@example.com" in Scrubber(emails=False, environ={}).text("ops@example.com")
    sent = s.messages(
        [
            {"role": "user", "content": "token: abcdef123"},
            {"role": "system", "content": [{"type": "text", "text": "ops@example.com"}]},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c", "type": "function",
                 "function": {"name": "f", "arguments": '{"q": "ops@example.com"}'}}
            ]},
        ]
    )  # fmt: skip
    assert "abcdef123" not in json.dumps(sent) and "ops@" not in json.dumps(sent)


def test_a_model_sees_nothing_the_patterns_hide(events, plans, monkeypatch):
    """The recorded demo tour, with a location, an operator, a secret and an address planted in
    it: the model analyses the stall through every tool, and none of them reaches it."""
    monkeypatch.setenv("JARVIS_TEST_API_KEY", "planted-key-0123456789")
    planted = copy.deepcopy(events)
    for e in planted:
        e["attrs"] = {**(e.get("attrs") or {}), "gps.lat": "48.8566", "operator": "alice"}
        e["message"] = f"{e['message']} (ops@example.com, planted-key-0123456789)"
    query = ReplayQuery(planted, plans)
    first = query.incident(STALL)["timeline"][1]
    script = [
        turn(
            call("query_events", correlation_id=STALL),
            call("get_incident", incident_id=STALL),
            call("get_event", event_id=first["id"]),
            call("get_fault_history", code="sensor.scan.stale"),
            call("get_active_faults"),
            call("get_recovery_history"),
        ),
        turn(call("submit_insight", **body(query))),
    ]
    patterns = ["attrs.gps.*", "@identities"]
    insight, llm = analyze(query, script, redact=patterns, agent_kw={"redact": patterns})
    assert insight.analyst == "llm" and len(llm.calls) == 2
    sent = json.dumps(llm.calls)
    for hidden in ("48.8566", "alice", "planted-key-0123456789", "ops@example.com"):
        assert hidden not in sent, hidden
    assert first["id"] in sent and first["attrs"]["fault_id"] in sent, "what the analysis runs on"
    results = [json.loads(m["content"]) for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert all("error" not in r for r in results), results
    assert {x["requester_id"] for x in results[-1]["executions"]} == {REDACTED}, "who asked for them"


@pytest.mark.parametrize("bad", ["../faults", "a/b", "", "x" * 200, "id?x=1"])
def test_ids_from_the_model_stay_ids(query, bad):
    script = [
        turn(call("get_event", event_id=bad), call("get_incident", incident_id=bad)),
        turn(call("submit_insight", **body(query))),
    ]
    _, llm = analyze(query, script)
    results = [json.loads(m["content"]) for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert [r["error"].split(":")[0] for r in results] == ["bad arguments"] * 2, results


def test_the_gateway_adapter_keeps_an_id_in_its_segment():
    httpx = pytest.importorskip("httpx")
    from jarvis_agent.adapters.gateway import GatewayQuery

    seen = []

    def handler(request):
        seen.append(request.url.raw_path.decode())
        return httpx.Response(404)

    q = GatewayQuery("http://gw", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert q.incident("../faults") is None and q.rule("a/b?c") is None and q.runbook("x/y") is None
    assert seen == ["/api/v1/incidents/..%2Ffaults", "/api/v1/rules/a%2Fb%3Fc", "/api/v1/runbooks/x%2Fy"]
