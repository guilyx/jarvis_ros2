# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The fleet view: a robot at a glance, the settings, and GET /api/v1/fleet over real gateways (on
the fake Jarvis), one that wants a token and one that is down; and the fleet's uploaded history."""

import json

import httpx
import pytest
from jarvis_gateway import fleet as fleet_module
from jarvis_gateway.adapters.fake import FakeJarvis
from jarvis_gateway.config import Webhook
from jarvis_gateway.fleet import Robot, Settings, Watch, Watcher, summarize
from jarvis_gateway.notifier import Notifier
from jarvis_lake import demo_history


def fault(code, severity="ERROR", state="ACTIVE", muted_by="", incident="i-1", component="lidar"):
    return {
        "fault_id": f"f-{code}",
        "code": code,
        "severity": severity,
        "state": state,
        "muted_by": muted_by,
        "incident_id": incident,
        "source": {"component": component},
        "message": f"{code} on {component}",
    }


def test_a_robot_at_a_glance():
    s = summarize(
        [
            fault("ros.topic.slow", "WARN", incident=""),
            fault("sensor.scan.stale", "ERROR", incident="i-1"),
            fault("nav.costmap.blind", "ERROR", muted_by="f-sensor.scan.stale", incident="i-1"),  # a symptom
            fault("safety.estop.engaged", "CRITICAL", state="LATCHED", incident="i-2", component="base"),
            fault("ros.log.error", "ERROR", state="CLEARED"),
        ],
        "ENABLED",
    )
    assert s["faults"] == {"CRITICAL": 1, "ERROR": 1, "WARN": 1, "total": 3}
    assert s["worst"]["code"] == "safety.estop.engaged" and s["worst"]["component"] == "base"
    assert s["incidents"] == 2 and s["latched"] == 1 and s["mode"] == "ENABLED"
    assert summarize([])["worst"] is None and summarize(None)["faults"]["total"] == 0


def test_settings():
    s = Settings.load([{"name": "amr-2", "url": "http://amr-2.local:8080/"}])
    assert s.robots == [Robot("amr-2", "http://amr-2.local:8080")] and s.timeout_s == 3.0
    s = Settings.load({"robots": [{"url": "https://amr-3:8443", "token_env": "T3"}], "timeout_s": 1})
    assert s.robots[0].name == "https://amr-3:8443" and s.timeout_s == 1.0
    with pytest.raises(ValueError, match="http"):
        Settings.load([{"name": "x", "url": "amr-2.local:8080"}])
    with pytest.raises(ValueError, match="unique"):
        Settings.load([{"name": "a", "url": "http://a"}, {"name": "a", "url": "http://b"}])


class Hosts(httpx.AsyncBaseTransport):
    """Routes requests by host to gateways in this process; any other host is unreachable."""

    def __init__(self, apps):
        self.apps = {host: httpx.ASGITransport(app=app) for host, app in apps.items()}

    async def handle_async_request(self, request):
        transport = self.apps.get(request.url.host)
        if transport is None:
            raise httpx.ConnectError("connection refused", request=request)
        return await transport.handle_async_request(request)


def test_the_fleet_view(make_gateway, monkeypatch):
    amr2 = make_gateway(fake=FakeJarvis(robot="amr-2", step_s=0.01))
    amr3 = make_gateway(fake=FakeJarvis(robot="amr-3", step_s=0.01), anonymous=None)
    monkeypatch.setenv("JARVIS_AMR3_TOKEN", "v-token")
    fleet = Settings.load(
        [
            {"name": "amr-2", "url": "http://amr-2"},
            {"name": "amr-3", "url": "http://amr-3", "token_env": "JARVIS_AMR3_TOKEN"},
            {"name": "amr-4", "url": "http://amr-4"},  # down
        ]
    )
    ops = make_gateway(fleet=fleet)
    ops.gateway.fleet_client = httpx.AsyncClient(
        transport=Hosts({"amr-2": amr2.client.app, "amr-3": amr3.client.app})
    )
    amr2.do(amr2.fake.raise_fault, "safety.estop.engaged", "base", severity="CRITICAL", latch=True)
    amr2.do(amr2.fake.raise_fault, "sensor.scan.stale", "lidar")

    def robots():
        out = ops.get("/api/v1/fleet")["robots"]
        return out if out[1]["faults"]["total"] == 2 else None

    own, two, three, four = ops.wait(robots)
    # The robot's id comes with its events: a quiet robot is "this robot" until then.
    assert own["local"] and own["online"] and own["name"] == (own["robot"] or "this robot")
    assert two["online"] and two["robot"] == "amr-2" and two["url"] == "http://amr-2"
    assert two["faults"]["CRITICAL"] == 1 and two["latched"] == 1
    assert two["worst"]["code"] == "safety.estop.engaged" and two["mode"] in (
        "ENABLED",
        "DRY_RUN",
        "DISABLED",
    )
    assert three["online"] and three["name"] == "amr-3"  # with the token from the environment
    assert not four["online"] and four["error"] == "unreachable" and four["faults"]["total"] == 0

    # Without the token, amr-3 refuses: offline, and why.
    monkeypatch.delenv("JARVIS_AMR3_TOKEN")
    three = ops.get("/api/v1/fleet")["robots"][2]
    assert not three["online"] and three["error"] == "refused the token"
    # A viewer may look.
    assert ops.client.get("/api/v1/fleet", headers={"Authorization": "Bearer v-token"}).status_code == 200


def test_a_rollout_across_the_fleet(make_gateway, tmp_path):
    """Each robot says which revision of the fleet's rules and plans it runs, and which it refused."""

    def robot(name, **status):
        data = tmp_path / name
        (data / "config").mkdir(parents=True)
        base = {"source": "s3://fleet/config", "state": "in-sync", "revision": "4f1c2a9e0b7d"}
        (data / "config" / "status.json").write_text(json.dumps({**base, **status}))
        return make_gateway(fake=FakeJarvis(robot=name, step_s=0.01), data_dir=str(data))

    amr2 = robot("amr-2")
    amr3 = robot(
        "amr-3",
        state="rejected",
        rejected={"revision": "9b2e41c07d3a", "at": 1.0, "errors": ["rules/lidar.yaml: unknown plan", "x"]},
    )
    plain = make_gateway(fake=FakeJarvis(robot="amr-4", step_s=0.01), data_dir=str(tmp_path / "amr-4"))
    fleet = Settings.load([{"name": n, "url": f"http://{n}"} for n in ("amr-2", "amr-3", "amr-4")])
    ops = make_gateway(fleet=fleet, data_dir=str(tmp_path / "ops"))
    ops.gateway.fleet_client = httpx.AsyncClient(
        transport=Hosts({"amr-2": amr2.client.app, "amr-3": amr3.client.app, "amr-4": plain.client.app})
    )
    own, two, three, four = ops.get("/api/v1/fleet")["robots"]
    assert own["config"] is None and four["config"] is None  # their own rules and plans
    assert two["config"]["revision"] == "4f1c2a9e0b7d" and two["config"]["rejected"] is None
    assert three["config"] == {
        "revision": "4f1c2a9e0b7d",
        "state": "rejected",
        "source": "s3://fleet/config",
        "since": None,
        "rejected": "9b2e41c07d3a",
        "error": "rules/lidar.yaml: unknown plan",
    }
    assert amr3.get("/api/v1/health")["config"]["rejected"]["revision"] == "9b2e41c07d3a"


def test_no_fleet_is_this_robot_alone(gw):
    robots = gw.get("/api/v1/fleet")["robots"]
    assert len(robots) == 1 and robots[0]["local"]


def test_the_fleets_history(make_gateway, tmp_path):
    """GET /api/v1/fleet/history over what the robots uploaded (made up here, as a demo would)."""
    target = f"file://{tmp_path / 'fleet'}"
    demo_history.write(target, ["amr-7", "amr-8"], days=3)
    ops = make_gateway(fleet=Settings.load({"robots": [], "history": target}))
    history = ops.get("/api/v1/fleet/history", hours=72)
    assert history["target"] == target and history["hours"] == 72 and history["bucket_s"] == 86400
    assert [r["robot"] for r in history["robots"]] == ["amr-7", "amr-8"]
    amr8 = history["robots"][1]
    assert amr8["faults_confirmed"] > history["robots"][0]["faults_confirmed"], "amr-8's flaky lidar"
    assert 0 <= amr8["recovery_success_rate"] <= 1 and amr8["history_until"].endswith("Z")
    top = history["top_codes"][0]
    assert top["episodes"] >= top["robots"] >= 1 and top["robot_names"]
    assert {p["robot"] for p in history["trend"]} == {"amr-7", "amr-8"}
    # Read at most once a minute per window: the Fleet page refreshes, the bucket is read once.
    assert ops.get("/api/v1/fleet/history", hours=72)["updated_at"] == history["updated_at"]
    assert ops.get("/api/v1/fleet/history", hours=24)["hours"] == 24
    # A viewer may look; the window is bounded.
    assert (
        ops.client.get("/api/v1/fleet/history", headers={"Authorization": "Bearer v-token"}).status_code
        == 200
    )
    assert ops.client.get("/api/v1/fleet/history?hours=0").status_code == 422


def test_no_fleet_history_says_how(gw):
    r = gw.client.get("/api/v1/fleet/history")
    assert r.status_code == 404 and "fleet.history" in r.json()["detail"]


def test_an_unreadable_history_is_an_error_with_why(make_gateway, tmp_path, monkeypatch):
    def unreachable(target, hours):
        raise OSError("AWS Error NETWORK_CONNECTION: Could not connect to server")

    monkeypatch.setattr(fleet_module.lake_fleet, "summary", unreachable)
    ops = make_gateway(fleet=Settings.load({"history": f"file://{tmp_path / 'fleet'}"}))
    r = ops.client.get("/api/v1/fleet/history")
    assert r.status_code == 502 and r.json()["detail"] == (
        "cannot read the fleet's history: OSError: AWS Error NETWORK_CONNECTION: Could not connect to server"
    )
    with pytest.raises(ValueError, match="fleet.history"):
        Settings.load({"history": "nope://x"})


def test_watch_settings():
    assert Settings.load({"robots": []}).watch is None
    assert Settings.load({"robots": [], "watch": True}).watch == Watch()
    w = Settings.load(
        {"robots": [], "watch": {"period_s": 5, "offline_after_s": 60, "channel": "robots"}}
    ).watch
    assert (w.period_s, w.offline_after_s, w.channel) == (5.0, 60.0, "robots")
    with pytest.raises(ValueError, match="period_s"):
        Settings.load({"robots": [], "watch": {"period_s": 0}})


def look(name, online=True, error="", rejected=None, revision="4f1c", local=False):
    config = {"revision": revision, "rejected": rejected, "error": "unknown plan" if rejected else ""}
    return {
        "name": name,
        "url": f"http://{name}",
        "online": online,
        "error": error,
        "local": local,
        "config": config,
    }


def test_the_watcher_alerts_on_what_changed():
    now = [1000.0]
    w = Watcher(Watch(offline_after_s=60, channel="robots"), clock=lambda: now[0])
    codes = lambda alerts: [(a["source"]["robot"], a["code"].rsplit(".", 1)[1]) for a in alerts]  # noqa: E731

    assert w.observe([look("me", local=True, online=False), look("amr-2"), look("amr-3")]) == []
    # amr-3 drops out: nothing until the grace is over, then once.
    now[0] += 10
    assert w.observe([look("amr-2"), look("amr-3", online=False, error="unreachable")]) == []
    now[0] += 60
    (offline,) = w.observe([look("amr-2"), look("amr-3", online=False, error="unreachable")])
    assert codes([offline]) == [("amr-3", "robot_offline")]
    assert offline["severity"] == "ERROR" and offline["message"] == "offline: unreachable for 60 s"
    assert offline["attrs"]["channel"] == "robots" and offline["source"]["component"] == "fleet"
    now[0] += 600
    assert w.observe([look("amr-2"), look("amr-3", online=False)]) == []
    (back,) = w.observe([look("amr-2"), look("amr-3")])
    assert codes([back]) == [("amr-3", "robot_online")] and back["message"] == "back online after 11 min"

    # A blip shorter than the grace is not offline.
    w.observe([look("amr-2", online=False), look("amr-3")])
    now[0] += 30
    assert w.observe([look("amr-2"), look("amr-3")]) == []

    # Rules and plans refused: once per revision; taken again.
    (refused,) = w.observe([look("amr-2", rejected="9b2e"), look("amr-3")])
    assert codes([refused]) == [("amr-2", "config_rejected")] and refused["severity"] == "WARN"
    assert refused["message"] == "refused rules and plans 9b2e: unknown plan; still on 4f1c"
    assert w.observe([look("amr-2", rejected="9b2e"), look("amr-3")]) == []
    assert codes(w.observe([look("amr-2", rejected="77aa"), look("amr-3")])) == [("amr-2", "config_rejected")]
    (taken,) = w.observe([look("amr-2", revision="5d0c"), look("amr-3")])
    assert taken["message"] == "runs rules and plans 5d0c (refused 77aa before)"
    assert [a["id"] for a in w.alerts] == sorted(
        {a["id"] for a in w.alerts}, key=[a["id"] for a in w.alerts].index
    )


def test_the_gateway_watches_the_fleet_and_alerts_the_webhooks(make_gateway):
    sent = []
    hook = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200)))
    notifier = Notifier(
        [Webhook("robots", "http://chat/hook", channels=["fleet"], format="slack")], client=hook
    )
    amr2 = make_gateway(fake=FakeJarvis(robot="amr-2", step_s=0.01))
    fleet = Settings.load(
        {
            "robots": [{"name": "amr-2", "url": "http://amr-2"}, {"name": "amr-4", "url": "http://amr-4"}],
            "watch": {"period_s": 0.1, "offline_after_s": 0.5},
        }
    )
    ops = make_gateway(fleet=fleet, notifier=notifier)
    ops.gateway.fleet_client = httpx.AsyncClient(transport=Hosts({"amr-2": amr2.client.app}))

    def alerted():
        a = ops.get("/api/v1/fleet/alerts")
        return a if a["alerts"] and sent else None

    alerts = ops.wait(alerted)
    assert alerts["watching"] and alerts["channel"] == "fleet" and alerts["offline_after_s"] == 0.5
    assert [(a["source"]["robot"], a["code"]) for a in alerts["alerts"]] == [
        ("amr-4", "jarvis.fleet.robot_offline")
    ]
    assert json.loads(sent[0].content)["text"].startswith("[amr-4] offline: unreachable for ")
    metrics = ops.client.get("/metrics").text
    assert 'jarvis_fleet_robot_online{robot="amr-4"} 0.0' in metrics
    assert 'jarvis_fleet_robot_online{robot="amr-2"} 1.0' in metrics


def test_no_watch_no_alerts(gw):
    assert gw.get("/api/v1/fleet/alerts") == {
        "watching": False,
        "period_s": None,
        "offline_after_s": None,
        "channel": "",
        "alerts": [],
    }
