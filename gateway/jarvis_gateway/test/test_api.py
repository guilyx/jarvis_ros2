# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The REST API over a FakeJarvis: auth, reads, commands and their audit trail."""

import json
import stat

from conftest import ADMIN, OPERATOR, VIEWER
from jarvis_gateway.adapters.fake import FakeJarvis
from jarvis_gateway.config import Principal

# -- auth ------------------------------------------------------------------------------------------


def test_anonymous_viewer_reads_but_cannot_write(gw):
    assert gw.get("/api/v1/me", headers={}) == {"name": "anonymous", "role": "viewer"}
    r = gw.client.post("/api/v1/faults/x:ack", json={})
    assert r.status_code == 403
    assert "operator" in r.json()["detail"]


def test_tokens_name_the_caller_and_bad_ones_are_refused(gw):
    assert gw.get("/api/v1/me", headers=OPERATOR) == {"name": "olga", "role": "operator"}
    assert gw.get("/api/v1/me", headers={}, token="a-token")["name"] == "ada"
    assert gw.client.get("/api/v1/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_without_anonymous_access_a_token_is_required(make_gateway):
    h = make_gateway(anonymous=None)
    assert h.client.get("/api/v1/me").status_code == 401
    assert h.client.get("/api/v1/me", headers=VIEWER).status_code == 200


def test_roles_are_ordered(gw):
    # viewers read, operators act on faults and recoveries, admins change modes and rules
    assert (
        gw.client.put("/api/v1/recovery/mode", json={"mode": "DISABLED"}, headers=OPERATOR).status_code == 403
    )
    assert gw.client.put("/api/v1/recovery/mode", json={"mode": "DISABLED"}, headers=ADMIN).status_code == 200


# -- health and system -----------------------------------------------------------------------------


def test_health_reports_snapshots(gw):
    h = gw.get("/api/v1/health", headers={})
    assert h["status"] == "ok"
    assert h["connected"] and h["faults_snapshot"] and h["executions_snapshot"]


def test_system_lists_lifecycle_states(gw):
    gw.do(
        gw.fake.emit,
        "STATE",
        "ros.lifecycle.jarvis.event_hub.state",
        "INFO",
        "event_hub",
        node="/jarvis/event_hub",
        value="inactive",
    )
    gw.do(
        gw.fake.emit,
        "STATE",
        "ros.lifecycle.jarvis.event_hub.state",
        "INFO",
        "event_hub",
        node="/jarvis/event_hub",
        value="active",
    )
    s = gw.get("/api/v1/system")
    assert s["components"]["/jarvis/event_hub"]["state"] == "active"
    assert s["connected"] and s["events_received"] == 2
    assert s["config"] is None  # rules and plans from the launch, not the fleet


def test_system_says_where_the_rules_and_plans_come_from(make_gateway, tmp_path):
    # config_sync's status (jarvis_fleet), with config_from.
    status = {"source": "s3://fleet/config", "state": "rejected", "revision": "4f1c2a9e0b7d"}
    path = tmp_path / "data" / "config" / "status.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(status))
    assert make_gateway().get("/api/v1/system")["config"] == status


# -- events ----------------------------------------------------------------------------------------


def _seed(gw):
    gw.do(gw.fake.emit, "LOG", "ros.log.info", "INFO", "base", "hello")
    gw.do(gw.fake.emit, "FAULT_RAISE", "ros.topic.stale", "ERROR", "lidar", "/scan is stale")
    gw.do(gw.fake.emit, "LOG", "ros.log.warn", "WARN", "lidar", "slow")
    gw.do(gw.fake.emit, "SYSTEM", "jarvis.started", "INFO", "", "up")


def test_events_newest_first_with_filters(gw):
    _seed(gw)
    page = gw.get("/api/v1/events")
    assert [e["seq"] for e in page["events"]] == ["4", "3", "2", "1"]
    assert page["next_before_seq"] is None
    assert [e["code"] for e in gw.get("/api/v1/events", code="ros.*")["events"]] == [
        "ros.log.warn",
        "ros.topic.stale",
        "ros.log.info",
    ]
    assert len(gw.get("/api/v1/events", min_severity="WARN")["events"]) == 2
    assert len(gw.get("/api/v1/events", component="lidar")["events"]) == 2
    assert gw.get("/api/v1/events", kind="SYSTEM")["events"][0]["code"] == "jarvis.started"
    assert gw.get("/api/v1/events", q="stale")["events"][0]["seq"] == "2"
    assert [e["seq"] for e in gw.get("/api/v1/events", order="asc", after_seq="2")["events"]] == ["3", "4"]


def test_events_page_backwards(gw):
    _seed(gw)
    first = gw.get("/api/v1/events", limit=3)
    assert [e["seq"] for e in first["events"]] == ["4", "3", "2"]
    assert first["next_before_seq"] == "2"
    rest = gw.get("/api/v1/events", limit=3, before_seq=first["next_before_seq"])
    assert [e["seq"] for e in rest["events"]] == ["1"]


def test_event_detail_has_its_causal_neighbours(gw):
    cause = gw.do(gw.fake.emit, "FAULT_RAISE", "ros.topic.stale", "ERROR", "lidar")
    effect = gw.do(gw.fake.emit, "RULE_MATCH", "jarvis.rule.matched", "INFO", "lidar", causation=cause["id"])
    detail = gw.get(f"/api/v1/events/{effect['id']}")
    assert detail["parent"]["id"] == cause["id"]
    assert [c["id"] for c in gw.get(f"/api/v1/events/{cause['id']}")["children"]] == [effect["id"]]
    assert gw.client.get("/api/v1/events/missing").status_code == 404


def test_events_keep_their_shape(gw):
    gw.do(gw.fake.emit, "LOG", "ros.log.info", "INFO", "base", "hi", count=3, ok=True)
    e = gw.get("/api/v1/events")["events"][0]
    assert e["seq"] == "1" and isinstance(e["seq"], str)
    assert e["attrs"] == {"count": 3, "ok": True}
    assert e["source"]["robot"] == "fake-bot"
    assert e["stamp"].endswith("Z")


# -- faults and incidents --------------------------------------------------------------------------


def test_ack_is_attributed_to_the_caller(gw):
    f = gw.do(gw.fake.raise_fault, "ros.topic.stale", "lidar")
    assert [x["fault_id"] for x in gw.get("/api/v1/faults")["faults"]] == [f["fault_id"]]
    r = gw.client.post(f"/api/v1/faults/{f['fault_id']}:ack", json={"note": "on it"}, headers=OPERATOR)
    assert r.status_code == 200, r.text
    assert gw.get("/api/v1/faults")["faults"][0]["acknowledged_by"] == "olga"
    audit = gw.get("/api/v1/audit")["events"]
    assert audit[0]["code"] == "fault.acknowledge"
    assert audit[0]["attrs"]["user"] == "olga"
    assert audit[0]["attrs"]["note"] == "on it"


def test_refused_commands_are_409(gw):
    r = gw.client.post("/api/v1/faults/nope:ack", json={}, headers=OPERATOR)
    assert r.status_code == 409
    assert "unknown fault" in r.json()["detail"]


def test_clear_resolves_the_incident_and_close_ends_it(gw):
    f = gw.do(gw.fake.raise_fault, "ros.topic.stale", "lidar", latch=True)
    incident = f["incident_id"]
    assert gw.get("/api/v1/incidents")["incidents"][0]["state"] == "OPEN"
    r = gw.client.post(f"/api/v1/faults/{f['fault_id']}:clear", json={"reason": "fixed"}, headers=OPERATOR)
    assert r.status_code == 200
    assert gw.get("/api/v1/faults")["faults"] == []
    assert gw.get(f"/api/v1/incidents/{incident}")["state"] == "RESOLVED"
    assert gw.client.post(f"/api/v1/incidents/{incident}:close", json={}, headers=OPERATOR).status_code == 200
    detail = gw.get(f"/api/v1/incidents/{incident}")
    assert detail["state"] == "CLOSED"
    codes = [e["code"] for e in detail["timeline"]]
    assert codes[0] == "ros.topic.stale" and "incident.close" in codes
    assert [x["fault_id"] for x in detail["faults"]] == [f["fault_id"]]
    assert gw.client.post(f"/api/v1/incidents/{incident}:close", json={}, headers=OPERATOR).status_code == 409


def test_fault_detail_and_history(gw):
    f = gw.do(gw.fake.raise_fault, "ros.topic.stale", "lidar")
    gw.do(gw.fake.heal, f["fault_id"])
    assert gw.get("/api/v1/faults")["faults"] == []
    history = gw.get("/api/v1/faults", state="all")["history"]
    assert history[0]["fault_id"] == f["fault_id"]
    detail = gw.get(f"/api/v1/faults/{f['fault_id']}")
    assert detail["fault"] is None
    assert [t["state"] for t in detail["transitions"]] == ["ACTIVE", "CLEARED"]
    assert gw.client.get("/api/v1/faults/nope").status_code == 404


def test_open_incidents_filter(gw):
    a = gw.do(gw.fake.raise_fault, "ros.topic.stale", "lidar")
    gw.do(gw.fake.raise_fault, "ros.node.missing", "camera")
    gw.do(gw.fake.heal, a["fault_id"])
    assert [i["component"] for i in gw.get("/api/v1/incidents", state="open")["incidents"]] == ["camera"]
    assert len(gw.get("/api/v1/incidents")["incidents"]) == 2


# -- recovery --------------------------------------------------------------------------------------


def _settled(gw, execution_id):
    def check():
        x = gw.get(f"/api/v1/recoveries/{execution_id}")["execution"]
        return x if x and x["state"] in ("SUCCEEDED", "FAILED", "ABORTED", "REJECTED") else None

    return gw.wait(check)


def test_operator_runs_a_plan(gw):
    r = gw.client.post(
        "/api/v1/recoveries",
        json={
            "plan_id": "restart-lifecycle-node",
            "args": {"node": "/a", "component": "a"},
            "rationale": "stuck",
        },
        headers=OPERATOR,
    )
    assert r.status_code == 202, r.text
    execution_id = r.json()["data"]["execution_id"]
    x = _settled(gw, execution_id)
    assert x["state"] == "SUCCEEDED"
    assert x["requester"] == {"type": "OPERATOR", "id": "olga"}
    past = gw.get(f"/api/v1/recoveries/{execution_id}")["history"]
    assert past["plan"] == "restart-lifecycle-node"
    assert [e["attrs"]["phase"] for e in past["events"]][-1] == "SUCCEEDED"
    listing = gw.get("/api/v1/recoveries")
    assert listing["mode"] == "ENABLED"
    assert listing["recent"][0]["execution_id"] == execution_id
    assert listing["history"][0]["execution_id"] == execution_id
    assert any(e["attrs"].get("execution_id") == execution_id for e in gw.get("/api/v1/audit")["events"])


def test_unknown_plan_is_refused(gw):
    r = gw.client.post("/api/v1/recoveries", json={"plan_id": "nope"}, headers=OPERATOR)
    assert r.status_code == 409


def test_failed_plan(gw):
    gw.fake.plans.append(dict(gw.fake.plans[0], id="fail-always"))
    r = gw.client.post("/api/v1/recoveries", json={"plan_id": "fail-always"}, headers=OPERATOR)
    assert _settled(gw, r.json()["data"]["execution_id"])["state"] == "FAILED"


def test_approval_flow(gw):
    gw.fake.plans[1]["requires_approval"] = ["OPERATOR"]
    r = gw.client.post("/api/v1/recoveries", json={"plan_id": "safe-stop"}, headers=OPERATOR)
    assert r.json()["data"]["state"] == "PENDING_APPROVAL"
    execution_id = r.json()["data"]["execution_id"]
    live = gw.get("/api/v1/recoveries")["live"]
    assert live[0]["needs_approval"]
    ok = gw.client.post(f"/api/v1/recoveries/{execution_id}:approve", json={"note": "go"}, headers=ADMIN)
    assert ok.status_code == 200
    x = _settled(gw, execution_id)
    assert x["state"] == "SUCCEEDED" and x["approved_by"] == "ada"
    again = gw.client.post(f"/api/v1/recoveries/{execution_id}:approve", json={}, headers=ADMIN)
    assert again.status_code == 409


def test_four_eyes(gw):
    """olga asks for a run that needs approval: she may withdraw it, not approve it; ada approves."""
    gw.fake.plans[1]["requires_approval"] = ["OPERATOR"]
    r = gw.client.post("/api/v1/recoveries", json={"plan_id": "safe-stop"}, headers=OPERATOR)
    execution_id = r.json()["data"]["execution_id"]
    own = gw.client.post(f"/api/v1/recoveries/{execution_id}:approve", json={"note": "me"}, headers=OPERATOR)
    assert own.status_code == 409
    assert own.json()["detail"] == "olga requested this run: another operator must approve it"
    assert gw.get("/api/v1/recoveries")["live"][0]["state"] == "PENDING_APPROVAL"
    ok = gw.client.post(f"/api/v1/recoveries/{execution_id}:approve", json={"note": "go"}, headers=ADMIN)
    assert ok.status_code == 200
    assert _settled(gw, execution_id)["approved_by"] == "ada"


def test_reject_and_cancel(gw):
    gw.fake.plans[1]["requires_approval"] = ["OPERATOR"]
    a = gw.client.post("/api/v1/recoveries", json={"plan_id": "safe-stop"}, headers=OPERATOR).json()["data"]
    assert (
        gw.client.post(
            f"/api/v1/recoveries/{a['execution_id']}:reject", json={}, headers=OPERATOR
        ).status_code
        == 200
    )
    assert _settled(gw, a["execution_id"])["state"] == "REJECTED"
    gw.fake.step_s = 5.0
    b = gw.client.post(
        "/api/v1/recoveries", json={"plan_id": "restart-lifecycle-node"}, headers=OPERATOR
    ).json()["data"]
    r = gw.client.post(
        f"/api/v1/recoveries/{b['execution_id']}:cancel", json={"reason": "wrong"}, headers=OPERATOR
    )
    assert r.status_code == 200
    x = _settled(gw, b["execution_id"])
    assert x["state"] == "ABORTED" and "olga" in x["reason"]
    audit = " ".join(e.get("message", "") for e in gw.get("/api/v1/audit")["events"])
    assert "rejected by olga" in audit and "cancelled by olga" in audit


def test_kill_switch(gw):
    r = gw.client.put(
        "/api/v1/recovery/mode", json={"mode": "DISABLED", "reason": "maintenance"}, headers=ADMIN
    )
    assert r.json()["data"]["previous_mode"] == "ENABLED"
    assert gw.get("/api/v1/recoveries")["mode"] == "DISABLED"
    assert (
        gw.client.post("/api/v1/recoveries", json={"plan_id": "safe-stop"}, headers=OPERATOR).status_code
        == 409
    )
    assert gw.client.put("/api/v1/recovery/mode", json={"mode": "OFF"}, headers=ADMIN).status_code == 422
    assert gw.get("/api/v1/audit")["events"][0]["code"] == "jarvis.recovery.mode"


def test_plans_carry_their_parameters_and_stats(gw):
    gw.client.post("/api/v1/recoveries", json={"plan_id": "safe-stop"}, headers=OPERATOR)
    gw.wait(lambda: gw.get("/api/v1/recoveries")["recent"])
    plans = {p["id"]: p for p in gw.get("/api/v1/plans")["plans"]}
    assert plans["restart-lifecycle-node"]["params"]["node"]["required"]
    assert plans["safe-stop"]["stats"]["runs"] == 1
    one = gw.get("/api/v1/plans/safe-stop")
    assert one["steps"] == ["cancel", "stop"] and len(one["executions"]) == 1
    assert gw.client.get("/api/v1/plans/nope").status_code == 404


def test_unreachable_jarvis_is_503(gw):
    async def hang(*args, **kwargs):
        raise ConnectionError("/jarvis/recovery/execute is not available")

    gw.fake.recover = hang
    r = gw.client.post("/api/v1/recoveries", json={"plan_id": "safe-stop"}, headers=OPERATOR)
    assert r.status_code == 503
    assert "not available" in r.json()["detail"]


# -- rules -----------------------------------------------------------------------------------------


def test_rules_and_their_state(gw):
    rules = {r["id"]: r for r in gw.get("/api/v1/rules")["rules"]}
    assert rules["lidar-stale-restart"]["enabled"]
    r = gw.client.patch(
        "/api/v1/rules/lidar-stale-restart", json={"dry_run": True, "reason": "testing"}, headers=ADMIN
    )
    assert r.status_code == 200
    assert gw.get("/api/v1/rules/lidar-stale-restart")["dry_run"] is True
    assert gw.get("/api/v1/audit")["events"][0]["attrs"]["operator"] == "ada"
    assert gw.client.get("/api/v1/rules/nope").status_code == 404
    assert gw.client.patch("/api/v1/rules/nope", json={"enabled": False}, headers=ADMIN).status_code == 409


def test_rule_detail_shows_its_yaml_and_matches(gw, tmp_path):
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(
        "schema: jarvis.rules/v1\nrules:\n"
        "  - id: lidar-stale-restart\n    when: {event: {code: ros.topic.stale}}\n"
    )
    gw.fake.rules[0]["file"] = str(rules_file)
    gw.do(
        gw.fake.emit,
        "RULE_MATCH",
        "jarvis.rule.matched",
        "INFO",
        "lidar",
        rule="lidar-stale-restart",
        dry_run=False,
    )
    detail = gw.get("/api/v1/rules/lidar-stale-restart")
    assert "ros.topic.stale" in detail["yaml"]
    assert len(detail["matches"]) == 1
    assert gw.get("/api/v1/rules")["rules"][0]["fires_window"] == 1


def test_reload(gw):
    r = gw.client.post("/api/v1/rules:reload", json={"validate_only": True}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["data"]["errors"] == []
    gw.client.post("/api/v1/plans:reload", json={}, headers=ADMIN)
    assert gw.get("/api/v1/events")["events"][0]["code"] == "jarvis.recovery.reloaded"


def _fake_jarvis_lint(tmp_path, output, code):
    script = tmp_path / "jarvis"
    script.write_text(f"#!/bin/sh\necho \"$@\" > {tmp_path}/args\nprintf '{output}'\nexit {code}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_validate_runs_jarvis_lint(make_gateway, tmp_path):
    out = "error: RULES/candidate.yaml: rule x: unknown plan y\\n1 error(s), 0 warning(s)\\n".replace(
        "RULES", str(tmp_path / "never")
    )
    h = make_gateway(jarvis_bin=_fake_jarvis_lint(tmp_path, out, 1), lint_plans_dir="/plans")
    r = h.client.post("/api/v1/rules:validate", json={"yaml": "rules: []"}, headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["message"] == "1 error(s), 0 warning(s)"
    assert body["data"]["findings"][0]["level"] == "error"
    assert "--plans /plans" in (tmp_path / "args").read_text()
    assert h.client.post("/api/v1/rules:validate", json={"yaml": ""}, headers=OPERATOR).status_code == 403


def test_validate_without_jarvis_is_503(make_gateway):
    h = make_gateway(jarvis_bin="/nonexistent/jarvis")
    assert h.client.post("/api/v1/rules:validate", json={"yaml": ""}, headers=ADMIN).status_code == 503


# -- analytics -------------------------------------------------------------------------------------


def test_stats_and_analytics(gw):
    f = gw.do(gw.fake.raise_fault, "ros.topic.stale", "lidar")
    gw.do(gw.fake.raise_fault, "ros.node.missing", "camera", "WARN")
    gw.do(gw.fake.heal, f["fault_id"])
    s = gw.get("/api/v1/stats", hours=1)
    assert s["faults_open"]["WARN"] == 1 and s["faults_open"]["ERROR"] == 0
    assert s["kpis"]["incidents_opened"] == 2
    assert s["top_codes"]
    assert s["event_rate"]
    components = {r["component"]: r for r in gw.get("/api/v1/analytics/components", hours=1)["rows"]}
    assert set(components) == {"lidar", "camera"}
    assert gw.get("/api/v1/analytics/flapping", min_cycles=1)["rows"][0]["code"] == "ros.topic.stale"
    assert gw.get("/api/v1/analytics/heatmap")["rows"]
    assert gw.client.get("/api/v1/analytics/nope").status_code == 404


def test_insights_decode_their_payload(gw):
    payload = '{"hypotheses": [{"cause": "cable", "confidence": 0.8}]}'
    gw.do(gw.fake.emit, "INSIGHT", "jarvis.agent.insight", "INFO", "lidar", "loose cable", payload=payload)
    found = gw.get("/api/v1/insights")["insights"]
    assert found[0]["payload"]["hypotheses"][0]["cause"] == "cable"
    one = gw.get(f"/api/v1/insights/{found[0]['id']}")
    assert one["event"]["payload"]["hypotheses"][0]["confidence"] == 0.8
    other = gw.do(gw.fake.emit, "LOG", "ros.log.info", "INFO", "base", "not an insight")
    assert gw.client.get(f"/api/v1/insights/{other['id']}").status_code == 404


def sse(text):
    """[(event, data)] of a server-sent event stream."""
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_asking_the_agent_streams_progress_then_the_insight(gw):
    incident = gw.do(gw.fake.raise_fault, "sensor.scan.stale", "lidar")["incident_id"]
    r = gw.client.post("/api/v1/agent/ask", json={"incident_id": incident}, headers=OPERATOR)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = sse(r.text)
    assert [k for k, _ in events[:-1]] == ["progress"] * (len(events) - 1)
    assert [d["stage"] for _, d in events[:-1]] == ["window", "triage", "analyst", "done"]
    kind, insight = events[-1]
    assert kind == "insight" and insight["trigger"] == {"type": "ON_DEMAND", "ref": incident}
    assert insight["window"]["correlation_id"] == incident and "asked by olga" in insight["flags"]
    published = gw.get("/api/v1/insights")["insights"]  # it is an INSIGHT event too
    assert published[0]["payload"]["insight_id"] == insight["insight_id"]
    assert published[0]["payload_schema"] == "jarvis.insight/v1"

    r = gw.client.post("/api/v1/agent/ask", json={"question": "what happened?"}, headers=OPERATOR)
    assert sse(r.text)[-1][1]["title"].startswith("About “what happened?”")


def test_asking_needs_an_operator_and_a_question(gw):
    assert gw.client.post("/api/v1/agent/ask", json={"question": "hi"}, headers=VIEWER).status_code == 403
    assert gw.client.post("/api/v1/agent/ask", json={"question": "  "}, headers=OPERATOR).status_code == 422
    r = gw.client.post("/api/v1/agent/ask", json={"incident_id": "nope"}, headers=OPERATOR)
    assert sse(r.text) == [("error", {"message": "no incident 'nope'"})]


def test_a_missing_agent_is_503(gw):
    async def gone(*args, **kwargs):
        raise ConnectionError("/jarvis/agent/analyze is not available (is the agent running?)")
        yield  # an async generator, like the real one

    gw.fake.ask = gone
    r = gw.client.post("/api/v1/agent/ask", json={"question": "hi"}, headers=OPERATOR)
    assert r.status_code == 503 and "is the agent running" in r.json()["detail"]


# -- the rest --------------------------------------------------------------------------------------


def test_metrics(gw):
    gw.do(gw.fake.raise_fault, "ros.topic.stale", "lidar")
    gw.client.post("/api/v1/faults/nope:ack", json={}, headers=OPERATOR)
    text = gw.client.get("/metrics").text
    assert 'jarvis_faults_open{severity="ERROR"} 1.0' in text
    assert 'jarvis_events_total{kind="FAULT_RAISE",severity="ERROR"} 1.0' in text
    assert 'jarvis_gateway_commands_total{command="ack",outcome="refused"} 1.0' in text
    assert "jarvis_recovery_enabled 1.0" in text


def test_openapi_documents_the_api(gw):
    paths = gw.client.get("/openapi.json").json()["paths"]
    assert "/api/v1/faults/{fault_id}:ack" in paths
    assert "/api/v1/events" in paths


def test_placeholder_home_without_a_dashboard(gw):
    assert "Jarvis gateway" in gw.client.get("/").text


def test_serves_the_dashboard(make_gateway, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id=root></div>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (dist / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("no")
    h = make_gateway(dashboard_dir=str(dist))
    assert h.client.get("/").text == "<div id=root></div>"
    assert h.client.get("/faults/abc").text == "<div id=root></div>"  # client-side routes
    assert h.client.get("/assets/app.js").text == "console.log(1)"
    assert h.client.get("/favicon.svg").text == "<svg/>"
    assert h.client.get("/../secret.txt").text != "no"
    assert h.client.get("/api/v1/me").json()["role"] == "viewer"


def test_backfills_what_the_hub_had_before_the_gateway_started(make_gateway):
    fake = FakeJarvis(step_s=0.01)
    for i in range(5):
        fake.emit("LOG", "ros.log.info", "INFO", "base", f"early {i}")
    fake.notify("old news")
    sent = []
    h = make_gateway(fake=fake)
    h.gateway.notifier.webhooks = [object()]  # anything delivered would show up as an error
    h.gateway.notifier.deliver = lambda e: sent.append(e)
    h.wait(lambda: h.gateway.backfilled)
    assert [e["seq"] for e in h.get("/api/v1/events")["events"]] == ["6", "5", "4", "3", "2", "1"]
    assert sent == []
    assert h.get("/api/v1/system")["events_received"] == 0


def test_settings_from_yaml_and_environment(tmp_path, monkeypatch):
    from jarvis_gateway.config import Settings

    path = tmp_path / "gateway.yaml"
    path.write_text(
        "schema: jarvis.gateway/v1\nport: 9000\nanonymous: null\n"
        "tokens: [{token: t1, name: alice, role: operator}]\n"
        "webhooks: [{name: ops, url: 'http://x', channels: [operators], format: slack}]\n"
        "lint: {plans_dir: /p}\n"
    )
    monkeypatch.setenv("JARVIS_GATEWAY_TOKENS", "t2:bob:admin")
    s = Settings.load(str(path), host="0.0.0.0")
    assert (s.host, s.port, s.anonymous, s.lint_plans_dir) == ("0.0.0.0", 9000, None, "/p")
    assert s.tokens == {"t1": Principal("alice", "operator"), "t2": Principal("bob", "admin")}
    assert s.webhooks[0].format == "slack"
    path.write_text("schema: jarvis.gateway/v9\n")
    try:
        Settings.load(str(path))
        raise AssertionError("expected a schema error")
    except ValueError as e:
        assert "jarvis.gateway/v1" in str(e)
    monkeypatch.setenv("JARVIS_GATEWAY_TOKENS", "t3:eve:root")
    try:
        Settings.load()
        raise AssertionError("expected a role error")
    except ValueError as e:
        assert "unknown role" in str(e)


def test_cli_ignores_ros_arguments():
    from jarvis_gateway.cli import parser, settings_from, strip_ros_args

    argv = ["--port", "1", "--ros-args", "-r", "__node:=gateway", "--", "--fake"]
    assert strip_ros_args(argv) == ["--port", "1", "--fake"]
    args = parser().parse_args(["--anonymous-role", "none", "--port", "7"])
    s = settings_from(args)
    assert s.anonymous is None and s.port == 7
