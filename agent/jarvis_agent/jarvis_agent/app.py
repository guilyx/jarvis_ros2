# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The agent as a service: live events in, triggers, analyses (2 at a time), insights out."""

import asyncio
import json
import logging
import sys
import time

log = logging.getLogger("jarvis_agent")


class StdoutSink:
    def __init__(self, out=sys.stdout):
        self.out = out

    def publish(self, insight, correlation_id="", causation_id=""):
        self.out.write(json.dumps(insight.to_json()) + "\n")
        self.out.flush()


async def run(agent, policy, events, clock=time.time_ns, concurrency=2, tick_s=1.0):
    """Until `events` ends (or forever: a live stream does not)."""
    slots = asyncio.Semaphore(concurrency)
    running = set()

    async def analyze(pending):
        async with slots:
            try:
                insight = await asyncio.to_thread(agent.analyze, pending.trigger, pending.causation_id)
            except Exception:  # noqa: BLE001 - one failed analysis must not stop the agent
                log.exception("analysis of %s failed", pending.trigger.ref)
                return
            if insight is not None:
                log.info("insight for %s: %s", pending.trigger.ref, insight.title)

    def start_due():
        for pending in policy.due(clock()):
            task = asyncio.create_task(analyze(pending))
            running.add(task)
            task.add_done_callback(running.discard)

    async def ticker():
        while True:
            start_due()
            await asyncio.sleep(tick_s)

    tick = asyncio.create_task(ticker())
    try:
        async for event in events:
            policy.on_event(event, clock())
    finally:
        tick.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
