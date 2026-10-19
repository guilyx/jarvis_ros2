# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Maintenance windows: an operator tells Jarvis to keep its hands off a component for a while.
Through the API (who may, what is listed, ending it), and the notifications silenced meanwhile."""

import asyncio

import httpx
from conftest import ADMIN, OPERATOR, VIEWER
from jarvis_gateway.config import Webhook
from jarvis_gateway.notifier import Notifier


def notify(component):
    return {
        "id": f"n-{component}",
        "code": "jarvis.notify",
        "kind": "SYSTEM",
        "message": f"{component} keeps going stale",
        "source": {"robot": "bot", "node": "/jarvis/rule_engine", "component": component, "host": ""},
        "attrs": {"channel": "operators", "rule": "r"},
    }


def test_an_operator_puts_a_component_under_maintenance_and_ends_it(make_gateway):
    h = make_gateway()
    body = {"component": "lidar", "minutes": 30, "reason": "recalibrating"}
    assert h.client.post("/api/v1/maintenance", json=body, headers=VIEWER).status_code == 403
    r = h.client.post("/api/v1/maintenance", json=body, headers=OPERATOR)
    assert r.status_code == 200, r.text
    assert r.json()["message"].startswith("lidar under maintenance until ")

    [m] = h.wait(lambda: h.get("/api/v1/maintenance")["maintenance"])
    assert (m["component"], m["reason"]) == ("lidar", "recalibrating")
    assert m["operator"] and m["until"] > m["since"]
    started = h.wait(
        lambda: next(
            (
                e
                for e in h.get("/api/v1/events", limit=20)["events"]
                if e["code"] == "jarvis.maintenance.started"
            ),
            None,
        )
    )
    assert started["source"]["component"] == "lidar" and started["attrs"]["reason"] == "recalibrating"
    # The live snapshot carries it, for the dashboard.
    assert h.get("/api/v1/recoveries")["mode"]

    r = h.client.delete("/api/v1/maintenance/lidar", headers=OPERATOR)
    assert r.status_code == 200 and r.json()["message"] == "maintenance of lidar ended"
    h.wait(lambda: h.get("/api/v1/maintenance")["maintenance"] == [])
    assert h.client.delete("/api/v1/maintenance/lidar", headers=OPERATOR).status_code == 409


def test_how_long(make_gateway):
    h = make_gateway()
    for minutes in (0, -5, 7 * 24 * 60 + 1):
        r = h.client.post(
            "/api/v1/maintenance", json={"component": "lidar", "minutes": minutes}, headers=ADMIN
        )
        assert r.status_code == 422, minutes
    assert (
        h.client.post("/api/v1/maintenance", json={"component": "", "minutes": 5}, headers=ADMIN).status_code
        == 422
    )


def test_notifications_about_a_component_under_maintenance_are_silenced(make_gateway):
    sent = []

    def record(request):
        sent.append(request)
        return httpx.Response(200)

    notifier = Notifier(
        [Webhook("ops", "http://hooks/ops", channels=["operators"])],
        client=httpx.AsyncClient(transport=httpx.MockTransport(record)),
    )
    h = make_gateway(notifier=notifier)
    h.client.post("/api/v1/maintenance", json={"component": "lidar", "minutes": 30}, headers=OPERATOR)
    h.wait(lambda: h.get("/api/v1/maintenance")["maintenance"])

    assert h.do(notifier.deliver, notify("lidar")) is None and not sent
    record_ = notifier.deliveries[-1]
    assert record_["silenced"] and record_["error"].startswith("silenced: lidar is under maintenance until ")
    h.do(notifier.deliver, notify("camera"))
    assert len(sent) == 1, "another component is not silenced"

    h.client.delete("/api/v1/maintenance/lidar", headers=OPERATOR)
    h.wait(lambda: h.get("/api/v1/maintenance")["maintenance"] == [])
    h.do(notifier.deliver, notify("lidar"))
    assert len(sent) == 2, "after the maintenance, delivered again"
    # The System view lists what was silenced
    assert any(d.get("silenced") for d in h.get("/api/v1/system")["notifications"])


def test_the_fake_rule_keeps_off_too():
    from jarvis_gateway.adapters.fake import FakeJarvis

    fake = FakeJarvis()
    asyncio.run(fake.set_maintenance("lidar", 600, "alice", "new firmware"))
    assert fake.under_maintenance("lidar")["operator"] == "alice"
    assert asyncio.run(fake.set_maintenance("lidar", 8 * 86400, "alice")).success is False
    assert asyncio.run(fake.set_maintenance("", 60, "alice")).success is False
