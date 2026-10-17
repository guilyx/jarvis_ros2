# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The agent over a Jarvis gateway: its REST API for history (lake + recent, one timeline) and its
live stream, resumed from the last seq after any disconnect. Works on the robot or anywhere on the
LAN that reaches the gateway."""

import asyncio
import json

import httpx


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
        return self._get(f"/incidents/{incident_id}")

    def events(self, since=None, until=None, min_severity=None, component="", kinds=(), limit=1000):
        page = self._get(
            "/events",
            **{"from": since, "to": until},
            min_severity=min_severity,
            component=component,
            kind=list(kinds),
            limit=min(limit, 1000),
            order="asc",
        )
        return (page or {}).get("events", [])

    def active_faults(self):
        return (self._get("/faults") or {}).get("faults", [])

    def fault_history(self, code, limit=200):
        return ((self._get("/faults", state="all", code=code) or {}).get("history") or [])[:limit]

    def recoveries(self, limit=500):
        return (self._get("/recoveries", limit=min(limit, 1000)) or {}).get("history", [])

    def plans(self):
        return (self._get("/plans") or {}).get("plans", [])


async def stream(base_url, token="", since=None, connect=None, backoff_s=(1, 2, 5, 10)):
    """Live events, in seq order, forever: reconnects and resumes from the last seq seen."""
    import websockets

    url = base_url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://") + "/api/v1/stream"
    if token:
        url += f"?token={token}"
    connect = connect or websockets.connect
    failures = 0
    while True:
        try:
            async with connect(url) as ws:
                await ws.send(json.dumps({"op": "subscribe", **({"since_seq": since} if since else {})}))
                failures = 0
                async for raw in ws:
                    message = json.loads(raw)
                    if message.get("type") == "event":
                        since = message["data"]["seq"]
                        yield message["data"]
                    elif message.get("type") == "backfill_done" and since is None:
                        since = message["seq"]
        except (OSError, TimeoutError, websockets.ConnectionClosed, websockets.InvalidStatus):
            pass
        await asyncio.sleep(backoff_s[min(failures, len(backoff_s) - 1)])
        failures += 1
