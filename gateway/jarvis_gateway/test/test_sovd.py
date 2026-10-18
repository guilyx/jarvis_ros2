# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The SOVD-style surface (/sovd/api/v1): what the ros2_medkit web UI and its Foxglove panel read."""

import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request

from conftest import OPERATOR, VIEWER
from jarvis_gateway import sovd

API = "/sovd/api/v1"
REPO = pathlib.Path(__file__).absolute().parents[3]
SOURCES = ("gateway/jarvis_gateway", "lake/jarvis_lake")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_discovery_finds_the_robot_and_its_apps(gw):
    gw.do(gw.fake.raise_fault, "sensor.scan.stale", "lidar_front", message="no scan for 2 s")
    assert gw.client.get("/sovd/version-info").json()["sovd_info"][0]["base_uri"] == API
    info = gw.get(f"{API}/version-info")["items"][0]
    assert info["vendor_info"]["name"] == "jarvis"
    assert gw.client.get(f"{API}/health").json()["status"] == "healthy"
    assert gw.get(f"{API}/areas") == {"items": []}
    (robot,) = gw.get(f"{API}/components")["items"]
    assert robot["id"] == "fake-bot"
    apps = gw.get(f"{API}/components/fake-bot/hosts")["items"]
    assert [a["id"] for a in apps] == ["lidar-front"], "an id the clients keep as is"
    assert gw.get(f"{API}/apps/lidar-front")["name"] == "lidar_front"
    assert gw.client.get(f"{API}/apps/nope", headers=VIEWER).json()["error_code"] == "resource-not-found"
    # Jarvis serves faults only: what the clients ask for besides is there, and empty.
    assert gw.get(f"{API}/apps/lidar-front/data") == {"items": []}
    assert gw.get(f"{API}/components/fake-bot/operations") == {"items": []}
    assert gw.client.get(f"{API}/apps/lidar-front/logs", headers=VIEWER).status_code == 404


def test_faults_are_listed_as_medkit_lists_them(gw):
    f = gw.do(gw.fake.raise_fault, "sensor.scan.stale", "lidar", severity="ERROR", message="no scan")
    gw.do(gw.fake.raise_fault, "battery.low", "power", severity="WARN", latch=True)
    page = gw.get(f"{API}/faults")
    assert page["x-medkit"]["count"] == 2
    item = next(i for i in page["items"] if i["fault_code"] == "sensor.scan.stale")
    assert item["severity"] == 2 and item["severity_label"] == "ERROR"
    assert item["status"] == "CONFIRMED" and item["description"] == "no scan"
    assert item["reporting_sources"] == ["/fake-bot/lidar"]
    assert item["first_occurred"] > 1e9 and item["occurrence_count"] == 1
    assert item["x-jarvis"]["fault_id"] == f["fault_id"]
    latched = next(i for i in page["items"] if i["fault_code"] == "battery.low")
    assert latched["status"] == "CONFIRMED" and latched["severity_label"] == "WARN"
    assert [i["fault_code"] for i in gw.get(f"{API}/apps/power/faults")["items"]] == ["battery.low"]
    assert len(gw.get(f"{API}/components/fake-bot/faults")["items"]) == 2
    assert gw.get(f"{API}/faults", status="pending")["items"] == []
    assert gw.client.get(f"{API}/faults?status=bogus", headers=VIEWER).status_code == 400


def test_a_fault_in_detail_has_its_status_bits_and_runbook_link(gw):
    gw.do(gw.fake.raise_fault, "sensor.scan.stale", "lidar", latch=True)
    d = gw.get(f"{API}/apps/lidar/faults/sensor.scan.stale")
    assert d["item"]["code"] == "sensor.scan.stale"
    assert d["item"]["status"] == {
        "aggregatedStatus": "active",
        "testFailed": "0",
        "confirmedDTC": "1",
        "pendingDTC": "0",
    }
    assert d["environment_data"]["extended_data_records"]["first_occurrence"]
    assert d["x-medkit"]["occurrence_count"] == 1 and d["x-jarvis"]["state"] == "LATCHED"
    missing = gw.client.get(f"{API}/apps/lidar/faults/nope", headers=VIEWER)
    assert missing.status_code == 404 and missing.json()["parameters"]["code"] == "nope"


def test_clearing_needs_an_operator_and_goes_through_jarvis(gw):
    gw.do(gw.fake.raise_fault, "sensor.scan.stale", "lidar")
    gw.do(gw.fake.raise_fault, "sensor.imu.stale", "imu")
    path = f"{API}/apps/lidar/faults/sensor.scan.stale"
    assert gw.client.delete(path, headers=VIEWER).status_code == 403
    assert gw.client.delete(path, headers=OPERATOR).status_code == 204
    assert [i["fault_code"] for i in gw.get(f"{API}/faults")["items"]] == ["sensor.imu.stale"]
    audit = gw.wait(lambda: [e for e in gw.get("/api/v1/audit")["events"] if e["code"] == "fault.clear"])
    assert audit[0]["attrs"]["user"] == "olga", "attributed to the token's owner"
    assert gw.client.delete(path, headers=OPERATOR).status_code == 404
    assert gw.client.delete(f"{API}/apps/imu/faults", headers=OPERATOR).status_code == 204
    assert gw.get(f"{API}/faults")["items"] == []


def test_muted_symptoms_are_left_out_unless_asked(gw):
    root = gw.do(gw.fake.raise_fault, "sensor.scan.stale", "lidar")
    symptom = gw.do(gw.fake.raise_fault, "nav.goal.aborted", "nav")
    symptom["muted_by"] = root["fault_id"]
    gw.do(gw.fake._publish_faults)
    assert [i["fault_code"] for i in gw.get(f"{API}/faults")["items"]] == ["sensor.scan.stale"]
    assert len(gw.get(f"{API}/faults", include_muted="true")["items"]) == 2


def test_what_the_stream_reports_between_two_snapshots():
    def fault(fid, state, occurrences=1, last_seen="2026-10-18T10:00:00Z"):
        return {"fault_id": fid, "state": state, "occurrences": occurrences, "last_seen": last_seen}

    before = {"a": fault("a", "PENDING"), "b": fault("b", "ACTIVE"), "c": fault("c", "LATCHED")}
    after = {"a": fault("a", "ACTIVE"), "b": fault("b", "ACTIVE", 2), "d": fault("d", "PENDING")}
    seen = [(kind, f["fault_id"], f["state"]) for kind, f in sovd.fault_changes(before, after)]
    assert seen == [
        ("fault_confirmed", "a", "ACTIVE"),
        ("fault_updated", "b", "ACTIVE"),
        ("fault_cleared", "c", "CLEARED"),
    ], "d is pending: not reported, as medkit does"


def test_the_live_stream_on_a_running_gateway(tmp_path):
    """Over HTTP (the test client buffers whole responses): the fake's scenario raises a stale
    lidar a few seconds in, then heals it."""
    port = _free_port()
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(str(REPO / p) for p in SOURCES))
    proc = subprocess.Popen(
        [sys.executable, "-m", "jarvis_gateway.cli", "--fake", "--scenario", "--port", str(port)]
        + ["--data-dir", str(tmp_path), "--log-level", "warning"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}{API}/faults/stream"
        deadline = time.monotonic() + 30
        while True:
            try:
                response = urllib.request.urlopen(url, timeout=30)
                break
            except OSError:
                assert time.monotonic() < deadline, "the gateway did not start"
                time.sleep(0.2)
        events = []
        with response:
            for raw in response:
                line = raw.decode().strip()
                if line.startswith("data: "):
                    e = json.loads(line[6:])
                    if e["fault"]["fault_code"] == "ros.topic.stale":
                        events.append(e)
                if len(events) == 2:
                    break
        assert [e["event_type"] for e in events] == ["fault_confirmed", "fault_cleared"]
        assert events[0]["fault"]["reporting_sources"] == ["/fake-bot/lidar"]
        assert events[1]["fault"]["status"] == "CLEARED"
    finally:
        proc.terminate()
        proc.wait(10)


def test_cors_is_off_unless_origins_are_set(make_gateway):
    origin = {"Origin": "http://ui.local:3000", "Access-Control-Request-Method": "GET"}
    plain = make_gateway()
    assert "access-control-allow-origin" not in plain.client.options(f"{API}/faults", headers=origin).headers
    allowed = make_gateway(cors_origins=["http://ui.local:3000"])
    r = allowed.client.options(f"{API}/faults", headers=origin)
    assert r.headers["access-control-allow-origin"] == "http://ui.local:3000"
