# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Jarvis's open faults as the errors of a VDA 5050 state: which faults, their type and level, the
references and the hint, and the endpoint a robot's VDA 5050 adapter reads."""

import pathlib

import pytest
from jarvis_gateway.config import Settings as GatewaySettings
from jarvis_gateway.vda5050 import Settings, errors, hint, level_of

RUNBOOKS = pathlib.Path(__file__).resolve().parents[3] / "ros" / "jarvis_bringup" / "config" / "runbooks"


def fault(code, severity="ERROR", state="ACTIVE", latch=False, muted_by="", component="lidar", message=""):
    return {
        "fault_id": f"f-{code}",
        "code": code,
        "source": {"component": component, "node": "/fake"},
        "state": state,
        "severity": severity,
        "latch": latch,
        "muted_by": muted_by,
        "incident_id": "i-1",
        "message": message or f"{code} on {component}",
    }


def test_levels_follow_the_severity():
    assert level_of(fault("x", "WARN")) == "WARNING"
    assert level_of(fault("x", "INFO")) == "WARNING"
    assert level_of(fault("x", "ERROR")) == "URGENT"
    assert level_of(fault("x", "CRITICAL")) == "CRITICAL"
    # A person must clear it: the robot can't take new orders until then.
    assert level_of(fault("x", "CRITICAL", latch=True)) == "FATAL"
    # VDA 5050 2.x has WARNING and FATAL only.
    assert level_of(fault("x", "ERROR"), "2.1") == "WARNING"
    assert level_of(fault("x", "CRITICAL"), "2.1") == "FATAL"
    assert level_of(fault("x", "WARN"), "2.0", mapped="CRITICAL") == "FATAL"


def test_open_root_causes_only_most_severe_first():
    faults = [
        fault("ros.topic.slow", "WARN"),
        fault("sensor.scan.stale", "ERROR"),
        fault("safety.estop.engaged", "CRITICAL", state="LATCHED", latch=True, component="base"),
        fault("nav.costmap.blind", "ERROR", muted_by="f-sensor.scan.stale"),  # a symptom
        fault("ros.node.missing", "ERROR", state="PENDING"),  # not confirmed yet
        fault("ros.log.error", "ERROR", state="CLEARED"),
    ]
    out = errors(faults, Settings())
    assert [(e["errorType"], e["errorLevel"]) for e in out] == [
        ("safety.estop.engaged", "FATAL"),
        ("sensor.scan.stale", "URGENT"),
        ("ros.topic.slow", "WARNING"),
    ]
    estop = out[0]
    assert estop["errorDescription"] == "safety.estop.engaged on base"
    assert estop["errorReferences"] == [
        {"referenceKey": "component", "referenceValue": "base"},
        {"referenceKey": "faultId", "referenceValue": "f-safety.estop.engaged"},
        {"referenceKey": "incidentId", "referenceValue": "i-1"},
    ]


def test_mapped_types_and_levels():
    settings = Settings.load(
        {
            "version": "3.0",
            "errors": [
                {"code": "nav.localization.*", "type": "LOCALIZATION_ERROR", "level": "FATAL"},
                {"code": "medkit.*", "level": "URGENT"},
            ],
        }
    )
    out = errors(
        [fault("nav.localization.lost", "ERROR"), fault("medkit.motor_overheat", "CRITICAL")], settings
    )
    assert out[0]["errorType"] == "LOCALIZATION_ERROR" and out[0]["errorLevel"] == "FATAL"
    # The Jarvis code stays findable when the type is mapped.
    assert {"referenceKey": "jarvisCode", "referenceValue": "nav.localization.lost"} in out[0][
        "errorReferences"
    ]
    assert out[1]["errorType"] == "medkit.motor_overheat" and out[1]["errorLevel"] == "URGENT"


def test_settings_are_checked():
    with pytest.raises(ValueError, match="version"):
        Settings.load({"version": "1.1"})
    with pytest.raises(ValueError, match="level"):
        Settings.load({"errors": [{"code": "x.*", "level": "SEVERE"}]})


def test_the_hint_is_the_runbooks_first_fix():
    runbook = (RUNBOOKS / "ros.topic.stale.md").read_text()
    assert (
        hint(runbook) == "A managed driver: restart it. ros2 jarvis recover restart-lifecycle-node -a "
        "node=/<driver> -a component=<component> -a fault=ros.topic.stale"
    )
    assert hint("# x\n\n## Check\n\n1. look\n") == ""
    assert hint(None) == ""
    long = "## Fix\n\n- " + "word " * 200
    assert len(hint(long)) == 300 and hint(long).endswith("…")


def test_hints_by_version():
    runbooks = {"ros.topic.stale": (RUNBOOKS / "ros.topic.stale.md").read_text()}
    f = [fault("ros.topic.stale")]
    assert "errorHint" in errors(f, Settings(version="2.1"), runbooks.get)[0]
    assert "errorHint" not in errors(f, Settings(version="2.0"), runbooks.get)[0]  # 2.0 has no hint


def test_the_endpoint_a_vda5050_adapter_reads(make_gateway, tmp_path):
    config = tmp_path / "gateway.yaml"
    config.write_text(
        "schema: jarvis.gateway/v1\n"
        "vda5050:\n  version: '3.0'\n  errors:\n    - { code: safety.*, type: EMERGENCY_STOP }\n"
    )
    gw = make_gateway(vda5050=GatewaySettings.load(str(config)).vda5050, runbooks=[str(RUNBOOKS)])
    assert gw.get("/api/v1/vda5050/errors") == {"version": "3.0", "errors": []}

    gw.do(gw.fake.raise_fault, "ros.topic.stale", "lidar", message="/scan: no message for 1.0 s")
    gw.do(gw.fake.raise_fault, "safety.estop.engaged", "base", severity="CRITICAL", latch=True)
    out = gw.wait(
        lambda: len(gw.get("/api/v1/vda5050/errors")["errors"]) == 2 and gw.get("/api/v1/vda5050/errors")
    )
    estop, stale = out["errors"]
    assert (estop["errorType"], estop["errorLevel"]) == ("EMERGENCY_STOP", "FATAL")
    assert (stale["errorType"], stale["errorLevel"]) == ("ros.topic.stale", "URGENT")
    assert stale["errorDescription"] == "/scan: no message for 1.0 s"
    assert stale["errorHint"].startswith("A managed driver: restart it.")
    # A viewer may read it (the adapter can use the anonymous role, a viewer by default).
    assert gw.client.get("/api/v1/vda5050/errors").status_code == 200
