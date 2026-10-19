# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""SROS2 (milestone 5.4, NFR-13): the whole stack with DDS security enforced, from a keystore made
by `jarvis_keystore` out of security/policy.xml. Every Jarvis process runs in its own enclave; the
test plays a robot node (the producer enclave) and a person (the operator enclave):

- what is allowed works end to end: a producer's fault reaches the fault manager, an operator
  sees it, and so does the gateway (its enclave reads the stream);
- what is not is refused by DDS: a producer cannot publish the stream (/jarvis/event_batches,
  /jarvis/events) nor read it, and a participant whose enclave is not in the keystore cannot join at all;
- and, without DDS, the gateway's enclave allows every service and action the gateway calls.
"""

import fnmatch
import json
import os
import pathlib
import signal
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

import pytest

PARTICIPANT = pathlib.Path(__file__).with_name("security_participant.py")
POLICY = pathlib.Path(__file__).parents[1] / "security" / "policy.xml"
PORT = 8094
CODE = "test.security.fault"
SECURITY_ENV = ("ROS_SECURITY_KEYSTORE", "ROS_SECURITY_ENABLE", "ROS_SECURITY_STRATEGY")


@pytest.fixture(scope="module")
def keystore(tmp_path_factory):
    path = tmp_path_factory.mktemp("keystore")
    run = subprocess.run(
        ["ros2", "run", "jarvis_bringup", "jarvis_keystore", str(path)], capture_output=True, text=True
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "export ROS_SECURITY_KEYSTORE" in run.stdout
    return path


@pytest.fixture(scope="module")
def secure(keystore):
    """Security on for the processes started from here: the stack and the participants."""
    saved = {k: os.environ.get(k) for k in SECURITY_ENV}
    os.environ.update(
        ROS_SECURITY_KEYSTORE=str(keystore), ROS_SECURITY_ENABLE="true", ROS_SECURITY_STRATEGY="Enforce"
    )
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(scope="module")
def stack(secure, tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("data")
    log = open(data_dir / "launch.log", "w")  # noqa: SIM115 - closed below
    proc = subprocess.Popen(
        [
            "ros2", "launch", "jarvis_bringup", "jarvis.launch.py", f"data_dir:={data_dir}",
            "robot_id:=secure-bot", "bridges:=false", f"gateway_port:={PORT}",
        ],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        env={**os.environ, "ROS_SECURITY_ENCLAVE_OVERRIDE": "/jarvis/launch"},
    )  # fmt: skip
    yield data_dir
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGINT)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
    log.close()


def participant(enclave, *args):
    """A robot node or a person: a process of its own, in its enclave."""
    run = subprocess.run(
        [sys.executable, str(PARTICIPANT), enclave, *args], capture_output=True, text=True, timeout=90
    )
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    assert lines, run.stdout + run.stderr
    return json.loads(lines[-1])


def eventually(predicate, what, timeout=90.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(1.0)
    pytest.fail(f"timed out waiting for {what}")


def in_gateway():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/v1/faults", timeout=2) as r:
            return any(f["code"] == CODE for f in json.load(r)["faults"])
    except OSError:
        return False


def test_what_the_policy_allows_works_end_to_end(stack):
    eventually(
        lambda: participant("/jarvis/operator", "faults", CODE).get("faults") is not None,
        "the fault manager, for an operator",
        120,
    )
    raised = participant("/jarvis/producer", "raise", CODE)
    assert raised == {"join": "allowed", "matched": True, "acked": True}, raised
    eventually(lambda: participant("/jarvis/operator", "faults", CODE)["faults"] == [CODE], "the fault")
    # The gateway's enclave reads the stream too: the fault is in its API.
    eventually(in_gateway, "the fault in the gateway", 60)


def test_what_it_does_not_allow_is_refused(stack):
    # Only the core publishes the stream: a producer cannot forge events on it, nor read it.
    forged = participant("/jarvis/producer", "forge")
    assert forged["join"] == "allowed"
    assert forged["publish_events"].startswith("denied"), forged
    assert forged["read_events"].startswith("denied"), forged
    assert forged["publish_batches"].startswith("denied"), forged
    assert forged["read_batches"].startswith("denied"), forged
    # An enclave the keystore does not have: no certificate, no participant.
    assert participant("/jarvis/intruder", "join")["join"].startswith("denied")


def test_the_gateway_enclave_allows_what_the_gateway_calls():
    # The gateway makes a client for every service it may call when it starts: under Enforce, one
    # the policy does not allow fails the start, and the gateway shows nothing. Checked without DDS.
    from jarvis_gateway.adapters.ros2 import SERVICES

    enclave = ET.parse(POLICY).find(".//enclave[@path='/jarvis/gateway']")
    allowed = [e.text for e in enclave.findall(".//services[@request='ALLOW']/service")]
    calls = [e.text for e in enclave.findall(".//actions[@call='ALLOW']/action")]
    missing = [
        f"/jarvis/{name}"
        for _, name in SERVICES.values()
        if not any(fnmatch.fnmatchcase(f"/jarvis/{name}", a) for a in allowed)
    ]
    assert not missing, f"add to the gateway's enclave in {POLICY.name}: {missing}"
    for action in ("/jarvis/recovery/execute", "/jarvis/agent/analyze"):
        assert any(fnmatch.fnmatchcase(action, a) for a in calls), action
