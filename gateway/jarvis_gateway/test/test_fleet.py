# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The fleet view: a robot at a glance, the settings, and GET /api/v1/fleet over real gateways (on
the fake Jarvis), one that wants a token and one that is down."""

import httpx
import pytest
from jarvis_gateway.adapters.fake import FakeJarvis
from jarvis_gateway.fleet import Robot, Settings, summarize


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


def test_no_fleet_is_this_robot_alone(gw):
    robots = gw.get("/api/v1/fleet")["robots"]
    assert len(robots) == 1 and robots[0]["local"]
