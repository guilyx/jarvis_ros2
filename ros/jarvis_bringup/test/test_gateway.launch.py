# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The gateway over the real stack: faults, commands with their audit trail, plans, rules, the
recovery kill switch and the live stream, through HTTP only."""

import json
import pathlib
import tempfile
import time
import unittest
import urllib.error
import urllib.request

import launch
import launch_testing
import launch_testing.actions
import pytest
import rclpy
from ament_index_python.packages import get_package_share_directory
from jarvis_interfaces.msg import Event
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from rclpy.qos import QoSProfile, ReliabilityPolicy
from websockets.sync.client import connect

DATA_DIR = tempfile.mkdtemp(prefix="jarvis_gateway_")
PORT = 18080  # packages are tested in parallel: not the default 8080
BASE = f"http://127.0.0.1:{PORT}/api/v1"
CONFIG = pathlib.Path(DATA_DIR) / "gateway.yaml"
CONFIG.write_text(
    "schema: jarvis.gateway/v1\n"
    "tokens:\n"
    "  - { token: op-secret, name: olga, role: operator }\n"
    "  - { token: admin-secret, name: ada, role: admin }\n"
)
AGENT_CONFIG = pathlib.Path(DATA_DIR) / "agent.yaml"
AGENT_CONFIG.write_text("schema: jarvis.agent/v1\ntriggers: { settle_s: 1 }\n")
BRINGUP = pathlib.Path(get_package_share_directory("jarvis_bringup")) / "launch" / "jarvis.launch.py"


@pytest.mark.launch_test
def generate_test_description():
    jarvis = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(str(BRINGUP)),
        launch_arguments={
            "robot_id": "gateway-bot",
            "data_dir": DATA_DIR,
            "bridges": "false",
            "record": "false",
            "blackbox": "false",
            "lake": "false",
            "gateway_port": str(PORT),
            "gateway_config": str(CONFIG),
            "agent_config": str(AGENT_CONFIG),
        }.items(),
    )
    return launch.LaunchDescription([jarvis, launch_testing.actions.ReadyToTest()])


def call(method, path, body=None, token=None):
    """(status, JSON body) of one request."""
    request = urllib.request.Request(BASE + path, method=method)
    if body is not None:
        request.data = json.dumps(body).encode()
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=20) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        text = e.read()
        try:
            return e.code, json.loads(text or b"null")
        except ValueError:
            return e.code, text.decode(errors="replace")


class TestGateway(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.node = rclpy.create_node("gateway_test")
        cls.ingest = cls.node.create_publisher(
            Event, "/jarvis/ingest", QoSProfile(depth=100, reliability=ReliabilityPolicy.RELIABLE)
        )
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                if call("GET", "/health")[1]["status"] == "ok":
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        raise AssertionError("the gateway never became healthy")

    @classmethod
    def tearDownClass(cls):
        cls.node.destroy_node()
        rclpy.shutdown()

    def wait(self, predicate, what, timeout=30.0, step=None):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if step:
                step()
            found = predicate()
            if found:
                return found
            time.sleep(0.2)
        self.fail(f"timed out waiting for {what}")

    def raise_fault(self, code):
        e = Event(kind=Event.KIND_FAULT_RAISE, code=code, severity=Event.SEVERITY_ERROR)
        e.source.node = "/gateway_test"
        e.source.component = "widget"
        e.message = "the widget is broken"
        self.ingest.publish(e)

    def faults(self):
        return call("GET", "/faults")[1]["faults"]

    def test_faults_and_operator_commands(self):
        code = "test.gateway.broken"
        fault = self.wait(
            lambda: next((f for f in self.faults() if f["code"] == code), None),
            "the fault in /faults",
            step=lambda: self.raise_fault(code),
        )
        self.assertEqual(fault["source"]["component"], "widget")
        self.assertEqual(fault["source"]["robot"], "gateway-bot")

        # Anonymous callers read; writes need a token, whose name lands in the audit trail.
        self.assertEqual(call("POST", f"/faults/{fault['fault_id']}:ack", {})[0], 403)
        status, outcome = call("POST", f"/faults/{fault['fault_id']}:ack", {"note": "looking"}, "op-secret")
        self.assertEqual(status, 200, outcome)
        self.wait(
            lambda: any(f["acknowledged_by"] == "olga" for f in self.faults()), "the ack in the snapshot"
        )
        audit = self.wait(
            lambda: [e for e in call("GET", "/audit")[1]["events"] if e["code"] == "fault.acknowledge"],
            "the ack in the audit trail",
        )
        self.assertEqual(audit[0]["attrs"]["user"], "olga")

        status, outcome = call("POST", f"/faults/{fault['fault_id']}:clear", {"reason": "fixed"}, "op-secret")
        self.assertEqual(status, 200, outcome)
        self.wait(lambda: all(f["code"] != code for f in self.faults()), "the fault cleared")
        incident = call("GET", f"/incidents/{fault['incident_id']}")[1]
        self.assertIn(code, [e["code"] for e in incident["timeline"]])
        self.assertEqual(call("POST", "/faults/nope:ack", {}, "op-secret")[0], 409)

    def test_plans_rules_and_the_kill_switch(self):
        plans = {p["id"]: p for p in call("GET", "/plans")[1]["plans"]}
        self.assertTrue(plans["restart-lifecycle-node"]["params"]["node"]["required"])
        rules = {r["id"]: r for r in call("GET", "/rules")[1]["rules"]}
        self.assertIn("escalation-notify", rules)

        status, outcome = call(
            "PATCH", "/rules/escalation-notify", {"dry_run": True, "reason": "test"}, "admin-secret"
        )
        self.assertEqual(status, 200, outcome)
        self.assertTrue(call("GET", "/rules/escalation-notify")[1]["dry_run"])
        self.assertEqual(call("PATCH", "/rules/escalation-notify", {"dry_run": False}, "op-secret")[0], 403)

        status, outcome = call(
            "POST",
            "/recoveries",
            {
                "plan_id": "restart-lifecycle-node",
                "args": {"node": "/jarvis/nothing", "component": "x"},
                "dry_run": True,
                "rationale": "test",
            },
            "op-secret",
        )
        self.assertEqual(status, 202, outcome)
        execution_id = outcome["data"]["execution_id"]
        self.assertTrue(execution_id)
        execution = self.wait(
            lambda: call("GET", f"/recoveries/{execution_id}")[1].get("execution"), "the execution"
        )
        self.assertEqual(execution["requester"], {"type": "OPERATOR", "id": "olga"})

        status, outcome = call(
            "PUT", "/recovery/mode", {"mode": "DISABLED", "reason": "test"}, "admin-secret"
        )
        self.assertEqual(status, 200, outcome)
        self.assertEqual(outcome["data"]["previous_mode"], "ENABLED")
        self.wait(lambda: call("GET", "/recoveries")[1]["mode"] == "DISABLED", "mode DISABLED")
        status, outcome = call("POST", "/recoveries", {"plan_id": "restart-lifecycle-node"}, "op-secret")
        self.assertEqual(status, 409, outcome)
        call("PUT", "/recovery/mode", {"mode": "ENABLED"}, "admin-secret")

    def test_stream_backfills_then_goes_live(self):
        with connect(f"ws://127.0.0.1:{PORT}/api/v1/stream", open_timeout=20) as ws:
            ws.send(json.dumps({"op": "subscribe", "since_seq": "0"}))
            kinds = []
            while True:
                m = json.loads(ws.recv(timeout=20))
                kinds.append(m["type"])
                if m["type"] == "backfill_done":
                    break
            self.assertIn("faults", kinds)
            self.assertIn("event", kinds)  # the hub's startup events, backfilled
            e = Event(kind=Event.KIND_LOG, code="test.gateway.live", severity=Event.SEVERITY_INFO)
            e.source.node = "/gateway_test"
            deadline = time.time() + 20
            while time.time() < deadline:
                self.ingest.publish(e)
                m = json.loads(ws.recv(timeout=20))
                if m["type"] == "event" and m["data"]["code"] == "test.gateway.live":
                    return
            self.fail("the live event never came")

    def test_the_agent_explains_the_incident(self):
        """The fault of test_faults opened an incident at ERROR: the agent analyzed it through the
        gateway and published an insight, which came back through the event hub."""

        def explained():
            for e in call("GET", "/insights")[1]["insights"]:
                if "test.gateway.broken" in e["payload"]["title"]:
                    return e
            return None

        insight = self.wait(explained, "the agent's insight", timeout=60.0)
        self.assertEqual(insight["kind"], "INSIGHT")
        self.assertEqual(insight["payload_schema"], "jarvis.insight/v1")
        self.assertEqual(insight["payload"]["analyst"], "heuristic")
        self.assertTrue(insight["payload"]["hypotheses"][0]["evidence"])
        self.assertEqual(insight["correlation_id"], insight["payload"]["window"]["correlation_id"])

    def test_an_agent_proposal_waits_for_an_operator(self):
        """The agent's node proposes a plan: the orchestrator takes it as an AGENT request, supervised
        (the bringup's agent_autonomy), so it waits for approval; an operator rejects it."""
        from jarvis_agent.adapters.ros2 import RosAgent

        agent = RosAgent(node_name="agent_proposer")
        try:
            got = agent.submit(
                "restart-lifecycle-node",
                {"node": "/jarvis/nothing_here", "component": "test.proposal"},
                rationale="a test proposal (insight t-1)",
                confidence=0.9,
                correlation_id="incident-t-1",
            )
        finally:
            agent.close()
        self.assertEqual(got.state, "PENDING_APPROVAL", got)
        self.assertTrue(got.execution_id)

        def pending():
            found = call("GET", f"/recoveries/{got.execution_id}")[1]
            return found if isinstance(found, dict) and found.get("execution") else None

        x = self.wait(pending, "the proposal in the gateway")["execution"]
        self.assertEqual(x["requester"], {"type": "AGENT", "id": "/jarvis/agent_proposer"})
        self.assertEqual(
            (x["rationale"], x["correlation_id"]), ("a test proposal (insight t-1)", "incident-t-1")
        )
        self.assertAlmostEqual(x["confidence"], 0.9)
        self.assertTrue(x["needs_approval"])
        status, _ = call("POST", f"/recoveries/{got.execution_id}:reject", {"note": "not now"}, "op-secret")
        self.assertEqual(status, 200)

        def rejected():
            found = call("GET", f"/recoveries/{got.execution_id}")[1]
            live = (found or {}).get("execution") or {}
            past = (found or {}).get("history") or {}
            return "REJECTED" in (live.get("state"), past.get("outcome"))

        self.wait(rejected, "the rejection")

    def test_metrics(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/metrics", timeout=10) as r:
            text = r.read().decode()
        self.assertIn("jarvis_events_total", text)
        self.assertIn("jarvis_recovery_enabled", text)


@launch_testing.post_shutdown_test()
class TestShutdown(unittest.TestCase):
    def test_processes_exit_cleanly(self, proc_info):
        launch_testing.asserts.assertExitCodes(proc_info, allowable_exit_codes=[0, -2, -15])
