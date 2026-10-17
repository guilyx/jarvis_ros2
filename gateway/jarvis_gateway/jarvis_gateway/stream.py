# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The live stream (/api/v1/stream): gap-free resume across the lake, recent events and live ones.

    client → { "op": "subscribe", "since_seq": "9000", "filter": { "min_severity": "WARN",
               "kinds": ["FAULT_STATE"], "codes": ["ros.*"], "component": "", "correlation_id": "" } }
    server → { "type": "event", "data": Event }               backfill, then live, in seq order
             { "type": "faults", "data": { "last_seq": "...", "faults": [...] } }
             { "type": "executions", "data": { "mode": ..., "executions": [...], "recent": [...] } }
             { "type": "backfill_done", "seq": "9131", "truncated": false }
             { "type": "heartbeat", "seq": "9140" }

Live events are buffered from the moment the client subscribes, before the backfill is read, then
sent above the backfill's last seq: nothing is lost or repeated. A client that falls too far behind
is disconnected (close code 1013) and resumes with since_seq.
"""

import asyncio
import contextlib
import fnmatch

from starlette.websockets import WebSocket, WebSocketDisconnect

SEVERITY_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40, "CRITICAL": 50}
BACKFILL_LIMIT = 5000
QUEUE_LIMIT = 10000
HEARTBEAT_S = 15.0


class Filter:
    def __init__(self, spec=None):
        spec = spec or {}
        self.min_level = SEVERITY_LEVELS.get(spec.get("min_severity") or "", 0)
        self.kinds = set(spec.get("kinds") or [])
        self.codes = list(spec.get("codes") or [])
        self.component = spec.get("component") or ""
        self.correlation_id = spec.get("correlation_id") or ""

    def matches(self, e):
        if SEVERITY_LEVELS.get(e.get("severity"), 20) < self.min_level:
            return False
        if self.kinds and e.get("kind") not in self.kinds:
            return False
        if self.codes and not any(fnmatch.fnmatchcase(e.get("code", ""), c) for c in self.codes):
            return False
        if self.component and (e.get("source") or {}).get("component") != self.component:
            return False
        return not self.correlation_id or e.get("correlation_id") == self.correlation_id

    def history_args(self):
        return {
            "min_severity": next((k for k, v in SEVERITY_LEVELS.items() if v == self.min_level), None),
            "kinds": sorted(self.kinds),
            "codes": self.codes,
            "component": self.component,
            "correlation_id": self.correlation_id,
        }


async def serve(ws: WebSocket, state, history, on_clients=lambda n: None, clients=None):
    """Runs one client connection until it closes."""
    queue = asyncio.Queue(maxsize=QUEUE_LIMIT)
    overflow = asyncio.Event()

    def listener(kind, payload):
        try:
            queue.put_nowait((kind, payload))
        except asyncio.QueueFull:
            overflow.set()

    try:
        request = await asyncio.wait_for(ws.receive_json(), timeout=30.0)
        since = request.get("since_seq")
        last = int(since) if since not in (None, "") else None
        if request.get("op") != "subscribe":
            raise ValueError("expected {op: subscribe}")
        flt = Filter(request.get("filter"))
    except TimeoutError:
        await ws.close(code=1008, reason="expected {op: subscribe} within 30 s")
        return
    except (ValueError, AttributeError) as e:
        await ws.close(code=1003, reason=str(e)[:120])
        return
    except WebSocketDisconnect:
        return
    unlisten = state.listen(listener)
    clients = clients if clients is not None else set()
    clients.add(ws)
    on_clients(len(clients))
    reader = getter = None
    try:
        truncated = False
        if last is not None:
            backfill = await asyncio.to_thread(
                history.events,
                after_seq=last,
                limit=BACKFILL_LIMIT + 1,
                newest_first=False,
                **flt.history_args(),
            )
            truncated = len(backfill) > BACKFILL_LIMIT
            for e in backfill[:BACKFILL_LIMIT]:
                await ws.send_json({"type": "event", "data": e})
                last = int(e["seq"])
        else:
            last = state.recent.max_seq()
        await _snapshots(ws, state, last)
        await ws.send_json({"type": "backfill_done", "seq": str(last), "truncated": truncated})

        reader = asyncio.create_task(_read(ws))
        while True:
            if overflow.is_set():
                await ws.close(code=1013, reason="too far behind: resume with since_seq")
                return
            getter = getter or asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                {getter, reader}, timeout=HEARTBEAT_S, return_when=asyncio.FIRST_COMPLETED
            )
            if reader in done:
                return  # the client left
            if getter not in done:
                await ws.send_json({"type": "heartbeat", "seq": str(last)})
                continue
            kind, payload = getter.result()
            getter = None
            if kind == "event":
                seq = int(payload["seq"])
                if seq <= last:
                    continue  # already sent by the backfill
                last = seq
                if flt.matches(payload):
                    await ws.send_json({"type": "event", "data": payload})
            elif kind == "faults":
                await ws.send_json({"type": "faults", "data": {"last_seq": str(last), "faults": payload}})
            elif kind == "executions":
                await ws.send_json({"type": "executions", "data": payload})
    except WebSocketDisconnect:
        pass
    finally:
        for task in (reader, getter):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        unlisten()
        clients.discard(ws)
        on_clients(len(clients))


async def _snapshots(ws, state, last):
    if state.faults is not None:
        await ws.send_json({"type": "faults", "data": {"last_seq": str(last), "faults": state.faults}})
    if state.executions is not None:
        await ws.send_json({"type": "executions", "data": state.executions})


async def _read(ws):
    """Reads (and ignores, apart from pings) what the client sends, to notice when it leaves."""
    while True:
        message = await ws.receive_json()
        if message.get("op") == "ping":
            await ws.send_json({"type": "pong"})
