# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The agent over a Jarvis gateway: its REST API for history (lake + recent, one timeline) and its
live stream, resumed from the last seq after any disconnect. Works on the robot or anywhere on the
LAN that reaches the gateway."""

import asyncio
import json
import urllib.parse

import httpx


def _seg(value):
    """One path segment: an id with a `/` or `..` in it stays one segment, never another route."""
    return urllib.parse.quote(str(value), safe="")


class GatewayQuery:
    def __init__(self, base_url, token="", client=None, timeout=15.0):
        self.base = base_url.rstrip("/") + "/api/v1"
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.client = client or httpx.Client(headers=headers, timeout=timeout)

    def _get(self, path, **params):
        r = self.client.get(
            self.base + path, params={k: v for k, v in params.items() if v not in (None, "", ())}
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def incident(self, incident_id):
        return self._get(f"/incidents/{_seg(incident_id)}")

    def events(
        self,
        since=None,
        until=None,
        min_severity=None,
        component="",
        kinds=(),
        limit=1000,
        codes=(),
        correlation_id="",
    ):
        page = self._get(
            "/events",
            **{"from": since, "to": until},
            min_severity=min_severity,
            component=component,
            kind=list(kinds),
            code=list(codes),
            correlation_id=correlation_id,
            limit=min(limit, 1000),
            order="asc",
        )
        return (page or {}).get("events", [])

    def event(self, event_id):
        found = self._get(f"/events/{_seg(event_id)}")
        return (found or {}).get("event") if found else None

    def active_faults(self):
        return (self._get("/faults") or {}).get("faults", [])

    def fault_history(self, code, limit=200):
        return ((self._get("/faults", state="all", code=code) or {}).get("history") or [])[:limit]

    def recoveries(self, limit=500):
        return (self._get("/recoveries", limit=min(limit, 1000)) or {}).get("history", [])

    def plans(self):
        return (self._get("/plans") or {}).get("plans", [])

    def rule(self, rule_id):
        return self._get(f"/rules/{_seg(rule_id)}")

    def seq_before(self, since):
        """The since_seq that resumes the stream at the first event from `since` (ISO 8601) on, or
        None when nothing happened since then."""
        found = self.events(since=since, limit=1)
        return str(int(found[0]["seq"]) - 1) if found else None


async def stream(base_url, token="", since=None, connect=None, backoff_s=(1, 2, 5, 10), catch_up=None):
    """Live events, in seq order, forever: reconnects and resumes from the last seq seen.

    Without a seq yet, `catch_up()` (called until the first connection) says where to start: what
    happened while the agent was starting, or the gateway was not up yet, is not missed."""
    import websockets
    from websockets.exceptions import WebSocketException

    url = base_url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://") + "/api/v1/stream"
    headers = {"Authorization": f"Bearer {token}"} if token else {}  # not in the URL: URLs get logged
    connect = connect or websockets.connect
    failures = 0
    while True:
        try:
            if since is None and catch_up is not None:
                since = await asyncio.to_thread(catch_up)
            async with connect(url, additional_headers=headers) as ws:
                await ws.send(json.dumps({"op": "subscribe", **({"since_seq": since} if since else {})}))
                failures = 0
                async for raw in ws:
                    message = json.loads(raw)
                    if message.get("type") == "event":
                        since = message["data"]["seq"]
                        yield message["data"]
                    elif message.get("type") == "backfill_done" and since is None:
                        since = message["seq"]
        except (OSError, TimeoutError, WebSocketException, httpx.HTTPError):
            pass  # not up yet, or gone: try again
        await asyncio.sleep(backoff_s[min(failures, len(backoff_s) - 1)])
        failures += 1
