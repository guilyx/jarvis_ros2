# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""jarvis-mcp against a real gateway (its in-memory Jarvis, with a scenario): the protocol over
stdio, every tool, roles, and an interop check with the official MCP client when it is installed."""

import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

REPO = pathlib.Path(__file__).absolute().parents[3]
TOKENS = "v-tok:victor:viewer,o-tok:olga:operator,a-tok:ada:admin"
PYTHONPATH = os.pathsep.join(
    str(REPO / p) for p in ("agent/jarvis_mcp", "gateway/jarvis_gateway", "lake/jarvis_lake")
)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    port = _free_port()
    env = dict(os.environ, PYTHONPATH=PYTHONPATH, JARVIS_GATEWAY_TOKENS=TOKENS)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "jarvis_gateway.cli",
            "--fake",
            "--scenario",
            "--port",
            str(port),
            "--data-dir",
            str(tmp_path_factory.mktemp("data")),
            "--catalog",
            str(REPO / "ros/jarvis_demo/config/demo_faults.yaml"),
            "--runbooks",
            str(REPO / "ros/jarvis_bringup/config/runbooks"),
            "--log-level",
            "warning",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url + "/api/v1/health", timeout=1).read()
            break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("the gateway did not start: " + proc.stderr.read().decode()[-2000:])
    # The scenario raises the latched e-stop at once; wait for it, so every test sees a fault.
    while time.monotonic() < deadline:
        faults = json.loads(
            urllib.request.urlopen(
                urllib.request.Request(url + "/api/v1/faults", headers={"Authorization": "Bearer v-tok"})
            ).read()
        )
        if faults["faults"]:
            break
        time.sleep(0.2)
    yield url
    proc.terminate()
    proc.wait(10)


class Session:
    """jarvis-mcp as an assistant runs it: a child process, JSON-RPC lines on its stdin/stdout."""

    def __init__(self, url, token, *flags):
        env = dict(os.environ, PYTHONPATH=PYTHONPATH, JARVIS_URL=url, JARVIS_TOKEN=token)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "jarvis_mcp.cli", *flags],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.next_id = 0

    def send(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params=None):
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}})
        reply = json.loads(self.proc.stdout.readline())
        assert reply["id"] == self.next_id
        return reply

    def call(self, tool, **arguments):
        reply = self.request("tools/call", {"name": tool, "arguments": arguments})
        return reply["result"]

    def close(self):
        self.proc.stdin.close()
        assert self.proc.wait(10) == 0
        return self.proc.stderr.read()


@pytest.fixture
def viewer(gateway):
    s = Session(gateway, "v-tok")
    s.request(
        "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test"}}
    )
    s.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    yield s
    s.close()


def _json(result):
    assert not result["isError"], result["content"][0]["text"]
    return json.loads(result["content"][0]["text"])


def test_initialize_negotiates_the_version(gateway):
    s = Session(gateway, "v-tok")
    r = s.request(
        "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t"}}
    )["result"]
    assert r["protocolVersion"] == "2025-03-26"
    assert r["capabilities"] == {"tools": {"listChanged": False}}
    assert r["serverInfo"]["name"] == "jarvis" and "jarvis_status" in r["instructions"]
    newer = s.request("initialize", {"protocolVersion": "2099-01-01"})["result"]
    assert newer["protocolVersion"] == "2025-06-18", "an unknown version gets our newest"
    assert s.request("ping")["result"] == {}
    s.close()


def test_tools_are_read_only_unless_recovery_is_allowed(gateway, viewer):
    tools = viewer.request("tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"jarvis_status", "list_faults", "get_runbook", "search_events", "ask_agent"} <= names
    assert "request_recovery" not in names
    for t in tools:
        assert t["inputSchema"]["type"] == "object" and t["description"]
        assert t["annotations"]["readOnlyHint"] and not t["annotations"]["destructiveHint"]
    s = Session(gateway, "o-tok", "--allow-recovery")
    s.request("initialize", {"protocolVersion": "2025-06-18"})
    recover = next(t for t in s.request("tools/list")["result"]["tools"] if t["name"] == "request_recovery")
    assert recover["annotations"]["destructiveHint"] and recover["inputSchema"]["required"] == [
        "plan_id",
        "rationale",
    ]
    s.close()


def test_status_faults_runbook_and_events(viewer):
    status = _json(viewer.call("jarvis_status"))
    assert status["health"]["status"] == "ok" and status["recovery_mode"] == "ENABLED"
    assert status["open_faults"]["by_severity"].get("CRITICAL") == 1
    faults = _json(viewer.call("list_faults"))["faults"]
    estop = next(f for f in faults if f["code"] == "safety.estop.engaged")
    assert estop["state"] == "LATCHED"
    detail = _json(viewer.call("get_fault", fault_id=estop["fault_id"]))
    assert detail["fault"]["fault_id"] == estop["fault_id"]
    runbook = viewer.call("get_runbook", code="safety.estop.engaged")
    assert not runbook["isError"] and runbook["content"][0]["text"].startswith("#")
    missing = viewer.call("get_runbook", code="no.such.code")
    assert not missing["isError"] and "No runbook" in missing["content"][0]["text"]
    events = _json(viewer.call("search_events", code="safety.*", limit=5))["events"]
    assert events and all(e["code"].startswith("safety.") for e in events)
    assert _json(viewer.call("list_plans"))["plans"]
    assert _json(viewer.call("analytics", name="top_codes", hours=1))["name"] == "top_codes"
    assert "live" in _json(viewer.call("list_recoveries"))
    assert "incidents" in _json(viewer.call("list_incidents", limit=5))


def test_errors_are_tool_results_or_protocol_errors(viewer):
    unknown = viewer.request("tools/call", {"name": "rm_rf", "arguments": {}})
    assert unknown["error"]["code"] == -32602
    assert viewer.request("no/such/method")["error"]["code"] == -32601
    bad = viewer.call("get_fault", nope=1)
    assert bad["isError"] and "bad arguments" in bad["content"][0]["text"]
    gone = viewer.call("get_fault", fault_id="no-such-fault")
    assert gone["isError"] and "404" in gone["content"][0]["text"]
    # A viewer may read, not ask the agent: the gateway refuses, and the model is told why.
    refused = viewer.call("ask_agent", question="what broke?")
    assert (
        refused["isError"]
        and "403" in refused["content"][0]["text"]
        and "role" in refused["content"][0]["text"]
    )
    # Not listed without --allow-recovery, so not callable either.
    sneaky = viewer.request("tools/call", {"name": "request_recovery", "arguments": {"plan_id": "safe-stop"}})
    assert sneaky["error"]["code"] == -32602


def test_an_operator_asks_the_agent_and_requests_a_dry_run(gateway):
    s = Session(gateway, "o-tok", "--allow-recovery")
    s.request("initialize", {"protocolVersion": "2025-06-18"})
    answer = _json(s.call("ask_agent", question="what is wrong with the robot?"))
    assert answer["stages"][-1] == "done" and answer["insight"]["title"]
    run = _json(s.call("request_recovery", plan_id="safe-stop", rationale="test", args={}))
    assert run["success"] and run["data"]["execution_id"]
    detail = _json(s.call("list_recoveries"))
    mine = [x for x in detail["live"] + detail["recent"] if x["execution_id"] == run["data"]["execution_id"]]
    assert mine and mine[0]["requester"] == {"type": "OPERATOR", "id": "olga"}, (
        "the token's name, as anyone's"
    )
    assert mine[0]["dry_run"], "a dry run unless asked otherwise"
    assert "o-tok" not in s.close(), "the token never reaches the logs"


def test_stdio_framing(gateway):
    s = Session(gateway, "v-tok")
    s.proc.stdin.write("this is not json\n\n")
    s.proc.stdin.flush()
    assert json.loads(s.proc.stdout.readline())["error"]["code"] == -32700
    s.send([{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/x"}])
    assert json.loads(s.proc.stdout.readline()) == [{"jsonrpc": "2.0", "id": 1, "result": {}}]
    s.close()


def test_check(gateway):
    env = dict(os.environ, PYTHONPATH=PYTHONPATH, JARVIS_URL=gateway, JARVIS_TOKEN="o-tok")
    ok = subprocess.run(
        [sys.executable, "-m", "jarvis_mcp.cli", "--check"], env=env, capture_output=True, text=True
    )
    assert ok.returncode == 0 and "signed in as olga (operator)" in ok.stdout
    env["JARVIS_TOKEN"] = "wrong"
    bad = subprocess.run(
        [sys.executable, "-m", "jarvis_mcp.cli", "--check"], env=env, capture_output=True, text=True
    )
    assert bad.returncode == 1 and "401" in bad.stderr


def test_the_official_mcp_client_talks_to_it(gateway):
    """Interop: the reference SDK's stdio client initializes, lists and calls tools."""
    mcp = pytest.importorskip("mcp")
    import anyio
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "jarvis_mcp.cli"],
        env=dict(os.environ, PYTHONPATH=PYTHONPATH, JARVIS_URL=gateway, JARVIS_TOKEN="v-tok"),
    )

    def attr(obj, snake, camel):  # SDK 1.x names fields like the wire, 2.x in snake_case
        return getattr(obj, snake) if hasattr(obj, snake) else getattr(obj, camel)

    async def session():
        async with stdio_client(params) as (read, write), mcp.ClientSession(read, write) as client:
            init = await client.initialize()
            assert attr(init, "server_info", "serverInfo").name == "jarvis"
            tools = await client.list_tools()
            assert "list_faults" in {t.name for t in tools.tools}
            result = await client.call_tool("list_faults", {})
            assert not attr(result, "is_error", "isError")
            assert "safety.estop.engaged" in result.content[0].text
            assert attr(result, "structured_content", "structuredContent")["faults"]

    anyio.run(session)
