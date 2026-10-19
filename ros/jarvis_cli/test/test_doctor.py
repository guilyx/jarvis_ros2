# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""ros2 jarvis doctor: what each finding means, and how the report reads."""

import collections
from types import SimpleNamespace

from jarvis_cli.verb.doctor import (
    FAIL,
    INFO,
    OK,
    WARN,
    Check,
    clock_check,
    disk_check,
    faults_check,
    fleet_config_check,
    gateway_check,
    lifecycle_check,
    mode_check,
    plans_check,
    recording_check,
    render,
    rules_check,
    unknown,
)
from jarvis_interfaces.msg import Fault

Usage = collections.namedtuple("Usage", "total used free")
GIB = 1024**3


def fault(code, state):
    return SimpleNamespace(code=code, state=state)


def test_nodes():
    assert lifecycle_check("recovery", "/jarvis/recovery", "active").status == OK
    missing = lifecycle_check("recovery", "/jarvis/recovery", None)
    assert missing.status == FAIL and "not running" in missing.detail
    inactive = lifecycle_check("recovery", "/jarvis/recovery", "inactive")
    assert inactive.status == FAIL and inactive.fix == "ros2 lifecycle set /jarvis/recovery activate"

    # What needs a missing node has no verdict: the node's own check is the problem.
    assert unknown("plans", "/jarvis/recovery") == Check(
        "plans", INFO, "unknown: /jarvis/recovery is not running"
    )


def test_recovery_mode():
    assert mode_check("ENABLED").status == OK
    assert mode_check("DRY_RUN").status == WARN
    off = mode_check("DISABLED", [SimpleNamespace(component="lidar")])
    assert off.status == WARN and off.detail == "DISABLED: the kill switch is on; under maintenance: lidar"
    assert mode_check("").status == FAIL


def test_plans_and_rules():
    assert plans_check(0).status == WARN
    assert plans_check(3).detail == "3 loaded"
    assert rules_check([]).status == WARN
    rules = [
        SimpleNamespace(enabled=True, dry_run=False),
        SimpleNamespace(enabled=True, dry_run=True),
        SimpleNamespace(enabled=False, dry_run=False),
    ]
    assert rules_check(rules).detail == "3 loaded (1 in shadow mode, 1 off)"


def test_faults():
    assert faults_check([]).detail == "none open"
    assert faults_check(
        [fault("sensor.scan.stale", Fault.STATE_ACTIVE), fault("x", Fault.STATE_PENDING)]
    ) == Check("faults", OK, "1 open: sensor.scan.stale")
    many = [fault(f"code.{i}", Fault.STATE_ACTIVE) for i in range(5)]
    assert faults_check(many).detail == "5 open: code.0, code.1, code.2 and 2 more"
    latched = faults_check([fault("safety.estop.engaged", Fault.STATE_LATCHED)])
    assert latched.status == WARN and "latched: safety.estop.engaged" in latched.detail
    assert faults_check([fault("jarvis.storage.low", Fault.STATE_ACTIVE)]).status == FAIL


def test_disk():
    assert disk_check("/data", None).status == INFO
    assert disk_check("/data", Usage(100 * GIB, 0, 40 * GIB)).detail == "/data: 40.0 GiB free of 100 GiB"
    assert disk_check("/data", Usage(100 * GIB, 0, 3 * GIB)).status == WARN
    assert disk_check("/data", Usage(100 * GIB, 0, GIB // 2)).status == FAIL


def test_recordings_clock_gateway():
    assert recording_check(False, None).status == FAIL
    assert recording_check(True, 4).detail == "recording, 4 recording(s) kept"
    assert clock_check("yes\n").status == OK
    assert clock_check("no\n").status == WARN
    assert clock_check(None, "Leap status     : Normal\n").status == OK
    assert clock_check(None, "Leap status     : Not synchronised\n").status == WARN
    assert clock_check().status == INFO
    assert gateway_check("http://x", {"status": "ok"}).status == OK
    assert gateway_check("http://x", {"status": "degraded"}).status == WARN
    assert gateway_check("http://x", error="not reachable").status == WARN


def test_report():
    text = render(
        "Jarvis doctor · amr-7",
        [
            Check("event hub", OK, "active"),
            Check("recordings", FAIL, "the recorder is not running", "see its log"),
            Check("clock", WARN, "not synchronized", "install chrony"),
        ],
    )
    assert text.splitlines() == [
        "Jarvis doctor · amr-7",
        "",
        " ✓ event hub      active",
        " ✗ recordings     the recorder is not running",
        "                  → see its log",
        " ! clock          not synchronized",
        "                  → install chrony",
        "",
        "1 problem(s), 1 warning(s).",
    ]
    assert render("h", [Check("event hub", OK, "active")]).endswith("All good.")


def test_fleet_config():
    status = {"source": "s3://fleet/config", "revision": "4f1c2a9e0b7d", "state": "in-sync", "message": ""}
    ok = fleet_config_check(status, running=True)
    assert ok.status == OK and ok.detail == "rules and plans 4f1c2a9e0b7d from s3://fleet/config"
    refused = fleet_config_check(
        {**status, "state": "rejected", "message": "rules and plans 9b2e rejected: ...; still on 4f1c"}, True
    )
    assert refused.status == WARN and "rejected" in refused.detail and "jarvis.config.rejected" in refused.fix
    assert "jarvis.env" in fleet_config_check({**status, "state": "offline"}, True).fix
    stopped = fleet_config_check(status, running=False)
    assert stopped.status == WARN and "not running" in stopped.detail
