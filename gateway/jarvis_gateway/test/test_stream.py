# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The live stream: snapshots, live events, gap-free resume and filters."""

import pytest
from starlette.websockets import WebSocketDisconnect

STREAM = "/api/v1/stream"


def _until(ws, kind):
    """Messages up to and including the first of `kind`."""
    out = []
    while True:
        m = ws.receive_json()
        out.append(m)
        if m["type"] == kind:
            return out


def _events(messages):
    return [m["data"] for m in messages if m["type"] == "event"]


def test_snapshots_then_live_events(gw):
    gw.do(gw.fake.raise_fault, "ros.topic.stale", "lidar")
    with gw.client.websocket_connect(STREAM) as ws:
        ws.send_json({"op": "subscribe"})
        first = _until(ws, "backfill_done")
        assert [m["type"] for m in first] == ["faults", "executions", "backfill_done"]
        assert first[0]["data"]["faults"][0]["code"] == "ros.topic.stale"
        assert first[-1]["seq"] == "3"  # no since_seq: from now on, no backfill
        gw.do(gw.fake.emit, "LOG", "ros.log.info", "INFO", "base", "live")
        assert ws.receive_json() == {"type": "event", "data": gw.fake.events[-1]}


def test_resume_since_a_seq_without_gaps_or_repeats(gw):
    for i in range(5):
        gw.do(gw.fake.emit, "LOG", "ros.log.info", "INFO", "base", f"e{i}")
    with gw.client.websocket_connect(STREAM) as ws:
        ws.send_json({"op": "subscribe", "since_seq": "2"})
        messages = _until(ws, "backfill_done")
        assert [e["seq"] for e in _events(messages)] == ["3", "4", "5"]
        assert messages[-1] == {"type": "backfill_done", "seq": "5", "truncated": False}
        gw.do(gw.fake.emit, "LOG", "ros.log.info", "INFO", "base", "e5")
        live = ws.receive_json()
        assert live["data"]["seq"] == "6"


def test_events_received_during_backfill_are_not_repeated(gw):
    """Live events are buffered from the subscription on, and those the backfill sent are skipped."""
    for i in range(3):
        gw.do(gw.fake.emit, "LOG", "ros.log.info", "INFO", "base", f"e{i}")
    history = gw.gateway.history
    real = history.events

    def slow_events(**kwargs):
        rows = real(**kwargs)
        gw.client.portal.start_task_soon(lambda: _emit_later(gw))  # arrives while backfilling
        return rows

    history.events = slow_events
    with gw.client.websocket_connect(STREAM) as ws:
        ws.send_json({"op": "subscribe", "since_seq": "0"})
        messages = _until(ws, "backfill_done")
        seqs = [e["seq"] for e in _events(messages)]
        seqs.append(ws.receive_json()["data"]["seq"])
        assert seqs == ["1", "2", "3", "4"]
    history.events = real


async def _emit_later(gw):
    gw.fake.emit("LOG", "ros.log.info", "INFO", "base", "during backfill")


def test_filters_apply_to_backfill_and_live(gw):
    gw.do(gw.fake.emit, "LOG", "ros.log.info", "INFO", "base", "quiet")
    gw.do(gw.fake.emit, "FAULT_RAISE", "ros.topic.stale", "ERROR", "lidar", "loud")
    with gw.client.websocket_connect(STREAM) as ws:
        ws.send_json({"op": "subscribe", "since_seq": "0", "filter": {"min_severity": "WARN"}})
        assert [e["message"] for e in _events(_until(ws, "backfill_done"))] == ["loud"]
        gw.do(gw.fake.emit, "LOG", "ros.log.info", "INFO", "base", "quiet again")
        gw.do(gw.fake.emit, "LOG", "ros.log.warn", "WARN", "lidar", "loud again")
        assert ws.receive_json()["data"]["message"] == "loud again"


def test_fault_and_execution_updates_are_pushed(gw):
    with gw.client.websocket_connect(STREAM) as ws:
        ws.send_json({"op": "subscribe"})
        _until(ws, "backfill_done")
        gw.do(gw.fake.raise_fault, "ros.topic.stale", "lidar")
        faults = _until(ws, "faults")[-1]
        assert faults["data"]["faults"][0]["code"] == "ros.topic.stale"
        gw.client.post(
            "/api/v1/recoveries", json={"plan_id": "safe-stop"}, headers={"Authorization": "Bearer o-token"}
        )
        executions = _until(ws, "executions")[-1]
        assert executions["data"]["executions"][0]["plan_id"] == "safe-stop"


def test_ping(gw):
    with gw.client.websocket_connect(STREAM) as ws:
        ws.send_json({"op": "subscribe"})
        _until(ws, "backfill_done")
        ws.send_json({"op": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_tokens_and_protocol_errors(make_gateway):
    h = make_gateway(anonymous=None)
    with pytest.raises(WebSocketDisconnect) as closed, h.client.websocket_connect(STREAM) as ws:
        ws.receive_json()
    assert closed.value.code == 1008
    with h.client.websocket_connect(f"{STREAM}?token=v-token") as ws:
        ws.send_json({"op": "hello"})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1003


def test_clients_are_counted(gw):
    with gw.client.websocket_connect(STREAM) as ws:
        ws.send_json({"op": "subscribe"})
        _until(ws, "backfill_done")
        assert gw.get("/api/v1/system")["stream_clients"] == 1
    gw.wait(lambda: gw.get("/api/v1/system")["stream_clients"] == 0)
