# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Rule suggestions: GET /api/v1/rules:suggest finds the plan operators keep running by hand after
the same fault, ties each run to the fault it answered, and drafts the rule that would do it."""

import datetime
import pathlib
import shutil
import subprocess

import pytest
import yaml
from conftest import VIEWER
from jarvis_gateway.suggest import covered, suggest, template

T0 = datetime.datetime(2026, 10, 19, 1, 0, tzinfo=datetime.UTC)
REPO = pathlib.Path(__file__).resolve().parents[3]


def at(s):
    return (T0 + datetime.timedelta(seconds=s)).isoformat().replace("+00:00", "Z")


def fault(s, state, fault_id, component, node, incident=""):
    e = {
        "id": f"{fault_id}-{state}",
        "stamp": at(s),
        "kind": "FAULT_STATE",
        "code": "sensor.scan.stale",
        "source": {"component": component},
        "attrs": {"fault_id": fault_id, "state": state, "node": node},
    }
    return {**e, "correlation_id": incident} if incident else e


def recovery(s, phase, execution, operator="alice", args=None, incident="", requester="OPERATOR", **attrs):
    e = {
        "id": f"{execution}-{phase}",
        "stamp": at(s),
        "kind": "RECOVERY",
        "code": f"jarvis.recovery.{phase.lower()}",
        "attrs": {
            "phase": phase,
            "plan": attrs.pop("plan", "restart-lifecycle-node"),
            "execution_id": execution,
            "requester": requester,
            "requester_id": operator,
            "dry_run": attrs.pop("dry_run", False),
            **{f"arg.{k}": v for k, v in (args or {}).items()},
        },
    }
    return {**e, "correlation_id": incident} if incident else e


LIDAR = {"node": "/demo/scan_relay", "component": "lidar", "fault": "sensor.scan.stale"}
CAMERA = {"node": "/demo/camera_relay", "component": "camera", "fault": "sensor.scan.stale"}
EVENTS = [
    # alice, from the fault's page (its incident)
    fault(0, "ACTIVE", "f1", "lidar", "/demo/scan_relay", "inc1"),
    recovery(40, "STARTED", "x1", args=LIDAR, incident="inc1"),
    recovery(45, "SUCCEEDED", "x1"),
    fault(46, "CLEARED", "f1", "lidar", "/demo/scan_relay", "inc1"),
    # bob, from the command line: no incident, the args name the fault's node
    fault(100, "ACTIVE", "f2", "camera", "/demo/camera_relay", "inc2"),
    recovery(160, "STARTED", "x2", operator="bob", args=CAMERA),
    recovery(166, "SUCCEEDED", "x2"),
    fault(167, "CLEARED", "f2", "camera", "/demo/camera_relay", "inc2"),
    # alice again
    fault(200, "ACTIVE", "f3", "lidar", "/demo/scan_relay", "inc3"),
    recovery(220, "STARTED", "x3", args=LIDAR, incident="inc3"),
    recovery(226, "FAILED", "x3"),
    # not operators' doing, or not a recovery of anything: none of these count
    recovery(230, "STARTED", "x4", operator="lidar-stale-restart", args=LIDAR, requester="RULE"),
    recovery(231, "STARTED", "x5", args=LIDAR, dry_run=True),
    fault(240, "CLEARED", "f3", "lidar", "/demo/scan_relay", "inc3"),
    recovery(300, "STARTED", "x6", plan="safe-stop"),
    recovery(301, "STARTED", "x7", plan="safe-stop"),
]


class History:
    def __init__(self, events):
        self.list = events
        self.asked = {}

    def events(self, **kwargs):
        self.asked = kwargs
        return [e for e in self.list if e["kind"] in kwargs["kinds"]]


def test_args_from_the_fault_become_templates():
    f = fault(0, "ACTIVE", "f1", "lidar", "/demo/scan_relay")
    assert template({"node": "/demo/scan_relay", "component": "lidar", "fault": "x.y", "mode": ""}, f) == {
        "component": "{{ event.source.component }}",
        "fault": "x.y",
        "mode": "",
        "node": "{{ event.attrs.node }}",
    }
    # Another attribute that holds the same value
    assert template({"target": "/demo/scan_relay"}, f) == {"target": "{{ event.attrs.node }}"}


def test_what_operators_keep_doing_becomes_a_rule():
    history = History(EVENTS)
    report = suggest(history, 24, now=T0 + datetime.timedelta(hours=1))
    assert history.asked["since"] == T0 + datetime.timedelta(hours=1) - datetime.timedelta(hours=24)
    [s] = report["suggestions"]
    assert (s["code"], s["plan"], s["runs"], s["succeeded"]) == (
        "sensor.scan.stale",
        "restart-lifecycle-node",
        3,
        2,
    )
    assert s["args"] == {
        "component": "{{ event.source.component }}",
        "fault": "sensor.scan.stale",
        "node": "{{ event.attrs.node }}",
    }
    assert s["operators"] == ["alice", "bob"] and s["components"] == ["camera", "lidar"]
    assert [r["execution_id"] for r in s["evidence"]] == ["x1", "x2", "x3"]
    assert [r["after_s"] for r in s["evidence"]] == [40, 60, 20] and s["median_after_s"] == 40
    assert s["evidence"][1]["fault_event_id"] == "f2-ACTIVE", "bob's run, tied by its args"
    assert s["id"] == "sensor-scan-stale-restart-lifecycle-node"

    [rule] = yaml.safe_load(s["yaml"])["rules"]
    assert rule["when"] == {
        "event": {"kind": "FAULT_STATE", "code": "sensor.scan.stale", "attrs.state": "ACTIVE"}
    }
    assert rule["then"] == [{"recover": {"plan": "restart-lifecycle-node", "args": s["args"]}}]
    assert rule["partition_by"] == ["source.component"] and rule["policy"]["max_fires"]["count"] == 3
    assert "3 times" in rule["description"]

    assert suggest(History(EVENTS), 24, min_runs=4, now=T0)["suggestions"] == []


def test_what_a_rule_already_does_is_not_suggested(tmp_path):
    (tmp_path / "robot.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": "jarvis.rules/v1",
                "rules": [
                    {
                        "id": "lidar-stale-restart",
                        "when": {"event": {"kind": "FAULT_STATE", "code": "sensor.scan.stale"}},
                        "then": [{"recover": {"plan": "restart-lifecycle-node"}}],
                    },
                    {"id": "just-notify", "when": {"event": {"code": "a.b"}}, "then": [{"notify": {}}]},
                ],
            }
        )
    )
    (tmp_path / "broken.yaml").write_text("rules: [")
    assert covered(tmp_path) == {("sensor.scan.stale", "restart-lifecycle-node")}
    assert suggest(History(EVENTS), 24, rules_dir=str(tmp_path), now=T0)["suggestions"] == []


def test_the_api_over_the_fake(make_gateway):
    h = make_gateway()
    for _ in range(2):
        f = h.do(h.fake.raise_fault, "ros.topic.stale", "lidar")
        args = {"node": "/lidar_driver", "component": "lidar"}
        r = h.do(h.fake.recover, "restart-lifecycle-node", args, "alice", correlation_id=f["incident_id"])
        assert r.success, r.message
        h.do(h.fake.heal, f["fault_id"])
    body = h.wait(lambda: (b := h.get("/api/v1/rules:suggest", hours=1)) and b["suggestions"] and b)
    [s] = body["suggestions"]
    assert s["runs"] == 2 and s["operators"] == ["alice"]
    assert s["args"] == {"component": "{{ event.source.component }}", "node": "/lidar_driver"}
    assert h.client.get("/api/v1/rules:suggest", params={"min_runs": 1}, headers=VIEWER).status_code == 422


JARVIS = shutil.which("jarvis")


@pytest.mark.skipif(not JARVIS, reason="the jarvis tool is built with jarvis_core (the ROS job has it)")
def test_the_draft_lints_clean_against_the_shipped_plans(tmp_path):
    [s] = suggest(History(EVENTS), 24, now=T0)["suggestions"]
    (tmp_path / "draft.yaml").write_text(s["yaml"])
    plans = REPO / "ros" / "jarvis_bringup" / "config" / "plans"
    out = subprocess.run(
        [JARVIS, "lint", "--rules", str(tmp_path), "--plans", str(plans)], capture_output=True, text=True
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert "0 error(s), 0 warning(s)" in out.stdout
