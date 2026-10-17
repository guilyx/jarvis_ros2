# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Webhooks for `notify` actions: formats, channels, retries, and the delivery record."""

import asyncio

import httpx
from jarvis_gateway.config import Webhook
from jarvis_gateway.notifier import Notifier

EVENT = {
    "id": "e1",
    "seq": "7",
    "code": "jarvis.notify",
    "kind": "SYSTEM",
    "severity": "WARN",
    "message": "recovery gave up on lidar",
    "source": {"robot": "bot-7", "node": "/jarvis/rule_engine", "component": "", "host": ""},
    "attrs": {"channel": "operators", "rule": "escalation-notify"},
}


def _notifier(webhooks, handler):
    requests = []

    def record(request):
        requests.append(request)
        return handler(request, len(requests))

    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    sleeps = []

    async def sleep(s):
        sleeps.append(s)

    return Notifier(webhooks, client=client, sleep=sleep), requests, sleeps


def test_slack_and_generic_bodies():
    hooks = [Webhook("slack", "http://s/hook", format="slack"), Webhook("raw", "http://r/hook")]
    n, requests, _ = _notifier(hooks, lambda r, i: httpx.Response(200))
    asyncio.run(n.deliver(EVENT))
    bodies = {r.url.host: r.read() for r in requests}
    assert b'"text":"[bot-7] recovery gave up on lidar"' in bodies["s"].replace(b": ", b":")
    assert b'"rule":"escalation-notify"' in bodies["r"].replace(b": ", b":")
    assert all(d["ok"] for d in n.deliveries)


def test_channels_pick_the_webhooks():
    hooks = [
        Webhook("ops", "http://ops/", channels=["operators"]),
        Webhook("eng", "http://eng/", channels=["eng"]),
    ]
    n, requests, _ = _notifier(hooks, lambda r, i: httpx.Response(204))
    asyncio.run(n.deliver(EVENT))
    assert [r.url.host for r in requests] == ["ops"]


def test_retries_with_backoff_then_records_the_failure():
    n, requests, sleeps = _notifier(
        [Webhook("ops", "http://ops/", retries=3)], lambda r, i: httpx.Response(503)
    )
    asyncio.run(n.deliver(EVENT))
    assert len(requests) == 3 and sleeps == [1, 2]
    d = n.deliveries[-1]
    assert (d["ok"], d["status"], d["attempts"], d["error"]) == (False, 503, 3, "HTTP 503")


def test_recovers_after_a_transport_error():
    def flaky(request, i):
        if i == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200)

    n, requests, sleeps = _notifier([Webhook("ops", "http://ops/")], flaky)
    asyncio.run(n.deliver(EVENT))
    assert n.deliveries[-1]["ok"] and n.deliveries[-1]["attempts"] == 2


def test_only_notify_events_are_delivered(make_gateway):
    seen = []

    class Recorder(Notifier):
        async def deliver(self, event):
            seen.append(event["message"])

    h = make_gateway(notifier=Recorder([Webhook("ops", "http://ops/")]))
    h.do(h.fake.emit, "LOG", "ros.log.info", "INFO", "", "not this one")
    h.do(h.fake.notify, "lidar needs a human")
    h.wait(lambda: seen)
    assert seen == ["lidar needs a human"]
