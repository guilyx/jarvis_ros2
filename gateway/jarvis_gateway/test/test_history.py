# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""One timeline over the lake and the gateway's recent events; recordings on disk."""

import datetime
import time

from conftest import VIEWER
from jarvis_gateway.adapters.fake import FakeJarvis
from jarvis_lake import model, storage
from mcap.writer import Writer


def _lake_rows(n, start_ns):
    rows = []
    for i in range(1, n + 1):
        e = {
            "id": f"lake-{i}",
            "seq": str(i),
            "stamp": model.iso8601(start_ns + i * 1_000_000),
            "source": {"robot": "bot", "node": "/n", "component": "base", "host": ""},
            "kind": "LOG",
            "code": "ros.log.info",
            "severity": "INFO",
            "message": f"from the lake {i}",
        }
        rows.append(model.row_from_event(e, split="bags/b/b_0.mcap"))
    return rows


def _write_lake(data_dir, n=5):
    import pyarrow.parquet as pq

    layout = storage.Layout(data_dir)
    now = time.time_ns() - 60 * 1_000_000_000
    day = datetime.datetime.fromtimestamp(now / 1e9, tz=datetime.UTC).strftime("%Y-%m-%d")
    folder = layout.events / f"date={day}"
    folder.mkdir(parents=True)
    pq.write_table(model.table(_lake_rows(n, now)), folder / "b_0.parquet")
    return layout


def test_lake_and_recent_events_are_one_timeline(make_gateway, tmp_path):
    _write_lake(tmp_path / "data")
    fake = FakeJarvis(step_s=0.01)
    fake.seq = 3  # the hub still has 4 and 5 (also in the lake), then new ones
    for _ in range(4):
        fake.emit("LOG", "ros.log.info", "INFO", "base", "from the hub")
    h = make_gateway(fake=fake)
    h.wait(lambda: h.gateway.backfilled)
    seqs = [e["seq"] for e in h.get("/api/v1/events")["events"]]
    assert seqs == ["7", "6", "5", "4", "3", "2", "1"]
    events = {e["seq"]: e for e in h.get("/api/v1/events")["events"]}
    assert events["5"]["message"] == "from the lake 5"  # the lake wins where both have it
    assert events["6"]["message"] == "from the hub"
    assert h.gateway.history.lake_max_seq(0.0) == 5
    assert len(h.gateway.state.recent) == 2  # the backfill starts above the lake's newest seq
    assert h.gateway.state.recent.trim_below(5) == 0  # nothing the lake has is held twice
    assert [e["seq"] for e in h.get("/api/v1/events")["events"]] == seqs


def test_stream_resume_reaches_into_the_lake(make_gateway, tmp_path):
    _write_lake(tmp_path / "data")
    fake = FakeJarvis(step_s=0.01)
    fake.seq = 5
    h = make_gateway(fake=fake)
    h.do(fake.emit, "LOG", "ros.log.info", "INFO", "base", "live")
    with h.client.websocket_connect("/api/v1/stream") as ws:
        ws.send_json({"op": "subscribe", "since_seq": "3"})
        seqs = []
        while (m := ws.receive_json())["type"] != "backfill_done":
            if m["type"] == "event":
                seqs.append(m["data"]["seq"])
    assert seqs == ["4", "5", "6"]


def _mcap(path, stamps):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        w = Writer(f)
        w.start()
        schema = w.register_schema("jarvis_interfaces/msg/Event", "ros2msg", b"")
        channel = w.register_channel("/jarvis/events", "cdr", schema)
        for t in stamps:
            w.add_message(channel, log_time=t, data=b"x", publish_time=t)
        w.finish()


def test_bags_are_listed_with_their_time_range_and_incidents(make_gateway, tmp_path):
    data = tmp_path / "data"
    layout = storage.Layout(data)
    t0 = time.time_ns() - 3_600_000_000_000
    _mcap(layout.bags / "jarvis_20261017" / "jarvis_20261017_0.mcap", [t0, t0 + 5_000_000_000])
    open_split = layout.bags / "jarvis_20261017" / "jarvis_20261017_1.mcap"
    open_split.write_bytes(b"\x89MCAP0\r\n partial")
    _mcap(layout.blackbox / "snapshot_1" / "snapshot_1_0.mcap", [t0 + 1_000_000_000])
    h = make_gateway()
    listing = h.get("/api/v1/bags")["bags"]
    by_file = {b["file"]: b for b in listing}
    first = by_file["jarvis_20261017_0.mcap"]
    assert first["kind"] == "recording" and not first["open"] and not first["converted"]
    assert first["start"] == model.iso8601(t0)
    assert by_file["jarvis_20261017_1.mcap"]["open"] and by_file["jarvis_20261017_1.mcap"]["start"] is None
    assert by_file["snapshot_1_0.mcap"]["kind"] == "blackbox"
    r = h.client.get("/api/v1/bags/recording/jarvis_20261017/jarvis_20261017_0.mcap", headers=VIEWER)
    assert r.status_code == 200 and r.content.startswith(b"\x89MCAP")
    for bad in (
        "recording/..%2F..%2Fetc/passwd.mcap",
        "nope/x/y.mcap",
        "recording/.x/y.mcap",
        "recording/b/y.txt",
    ):
        assert h.client.get(f"/api/v1/bags/{bad}").status_code == 404
