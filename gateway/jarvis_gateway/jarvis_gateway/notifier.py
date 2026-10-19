# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Delivers `notify` actions of rules (events `jarvis.notify`) to webhooks, with retries.

A webhook takes the channels it listens to (`*` for all). `slack` posts {"text": ...}; `generic`
posts the event as it is on the stream. Failures are retried with exponential backoff and kept in a
short history for the System view.
"""

import asyncio
import collections
import time

import httpx

NOTIFY_CODE = "jarvis.notify"


class Notifier:
    def __init__(self, webhooks, client=None, sleep=asyncio.sleep, on_delivery=None, silenced=None):
        self.webhooks = list(webhooks)
        # silenced(event) -> why a notification is not sent (its component is under maintenance), or None
        self.silenced = silenced or (lambda event: None)
        self._client = client
        self._sleep = sleep
        self._on_delivery = on_delivery or (lambda record: None)
        self.deliveries = collections.deque(maxlen=100)
        self._tasks = set()

    def on_event(self, event):
        """A listener for LiveState: schedules delivery of notify events (on the event loop)."""
        if event.get("code") != NOTIFY_CODE or not self.webhooks:
            return
        task = asyncio.get_running_loop().create_task(self.deliver(event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def deliver(self, event):
        channel = (event.get("attrs") or {}).get("channel", "default")
        if why := self.silenced(event):
            record = {"webhook": "", "channel": channel, "event_id": event.get("id"), "attempts": 0}
            record.update(ok=False, status=None, error=f"silenced: {why}", silenced=True, at=time.time())
            self.deliveries.append(record)
            return
        hooks = [w for w in self.webhooks if "*" in w.channels or channel in w.channels]
        await asyncio.gather(*(self._send(w, event, channel) for w in hooks))

    async def _send(self, hook, event, channel):
        body = self._body(hook, event)
        record = {"webhook": hook.name, "channel": channel, "event_id": event.get("id"), "attempts": 0}
        client = self._client or httpx.AsyncClient()
        try:
            for attempt in range(1, hook.retries + 1):
                record["attempts"] = attempt
                try:
                    r = await client.post(hook.url, json=body, timeout=hook.timeout_s)
                    if r.status_code < 300:
                        record.update(ok=True, status=r.status_code, error="")
                        break
                    record.update(ok=False, status=r.status_code, error=f"HTTP {r.status_code}")
                except httpx.HTTPError as e:
                    record.update(ok=False, status=None, error=f"{type(e).__name__}: {e}")
                if attempt < hook.retries:
                    await self._sleep(2 ** (attempt - 1))
        finally:
            if self._client is None:
                await client.aclose()
        record["at"] = time.time()
        self.deliveries.append(record)
        self._on_delivery(record)
        return record

    @staticmethod
    def _body(hook, event):
        if hook.format == "slack":
            robot = (event.get("source") or {}).get("robot", "")
            return {"text": f"[{robot}] {event.get('message', '')}" if robot else event.get("message", "")}
        return {"event": event}

    async def drain(self):
        """Waits for deliveries in flight (shutdown, tests)."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
