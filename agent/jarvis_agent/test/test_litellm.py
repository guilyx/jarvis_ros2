# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The LiteLLM client for real: over HTTP to a local OpenAI-compatible server (what Ollama, vLLM or
a LiteLLM proxy serve), so the requests litellm sends and the answers it parses are the real ones.
Skipped without litellm (pip install --require-hashes -r requirements-llm.txt)."""

import json
import socket
import threading
import time

import pytest
from conftest import FIXTURES, STALL
from jarvis_agent.core.agent import Agent
from jarvis_agent.core.analyst import LLMAnalyst
from jarvis_agent.llm.base import ModelUnavailable
from jarvis_agent.llm.litellm_client import LiteLLMClient  # imports litellm when used
from jarvis_agent.model import Trigger, TriggerType


@pytest.fixture(autouse=True)
def _litellm():
    pytest.importorskip("litellm")  # here, not at module level: that would skip the whole directory


class Stub:
    """POST /v1/chat/completions answers with the next step: tool calls, text, or an HTTP error."""

    def __init__(self):
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse

        self.script, self.requests = [], []
        self.app = FastAPI()

        @self.app.post("/v1/chat/completions")
        async def chat(request: Request):
            body = await request.json()
            self.requests.append({"headers": dict(request.headers), "body": body})
            step = self.script.pop(0) if self.script else {"text": "(out of script)"}
            if "status" in step:
                return JSONResponse({"error": {"message": step["status_text"], "type": "x"}}, step["status"])
            calls = [
                {
                    "id": f"call_{len(self.requests)}_{i}",
                    "type": "function",
                    "function": {"name": n, "arguments": json.dumps(a)},
                }
                for i, (n, a) in enumerate(step.get("calls", []))
            ]
            message = {"role": "assistant", "content": step.get("text")}
            if calls:
                message["tool_calls"] = calls
            return {
                "id": f"chatcmpl-{len(self.requests)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body["model"],
                "choices": [
                    {"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}
                ],
                "usage": {"prompt_tokens": 1200, "completion_tokens": 80, "total_tokens": 1280},
            }


@pytest.fixture
def stub():
    import uvicorn

    s = Stub()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(s.app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    for _ in range(500):
        if server.started:
            break
        time.sleep(0.01)
    s.url = f"http://127.0.0.1:{sock.getsockname()[1]}/v1"
    yield s
    server.should_exit = True
    thread.join(5)


def insight_body(query):
    return {
        "title": "The lidar stopped publishing; a restart did not help",
        "summary": "sensor.scan.stale stayed after restart-lifecycle-node.",
        "assessment": "ERROR",
        "hypotheses": [
            {
                "cause": "The lidar itself",
                "confidence": 0.6,
                "evidence": [query.incident(STALL)["timeline"][1]["id"]],
            }
        ],
        "recommendations": [{"kind": "INVESTIGATE", "text": "Check its power and cable"}],
    }


def test_the_loop_over_http(stub, query):
    stub.script = [
        {"calls": [("query_events", {"correlation_id": STALL, "kinds": ["FAULT_STATE"]})]},
        {"calls": [("submit_insight", insight_body(query))]},
    ]
    client = LiteLLMClient("openai/jarvis-test", api_base=stub.url, api_key="sk-test-key", max_retries=0)
    analyst = LLMAnalyst(client, query)
    insight = Agent(query, analyst=analyst).analyze(Trigger(type=TriggerType.RECOVERY_FAILED, ref=STALL))

    assert insight.analyst == "llm", insight.flags
    assert insight.hypotheses[0].evidence and not insight.flags
    assert (insight.model.provider, insight.model.name) == ("openai", "openai/jarvis-test")
    assert (insight.model.input_tokens, insight.model.output_tokens) == (2400, 160)

    first, second = (r["body"] for r in stub.requests)
    assert stub.requests[0]["headers"]["authorization"] == "Bearer sk-test-key"
    assert first["model"] == "jarvis-test" and first["tool_choice"] == "auto" and first["max_tokens"] == 2048
    assert "submit_insight" in [t["function"]["name"] for t in first["tools"]]
    assert first["messages"][0]["role"] == "system"
    tool = second["messages"][-1]
    assert tool["role"] == "tool" and tool["tool_call_id"] == "call_1_0"
    assert json.loads(tool["content"])["events"]
    assert second["messages"][-2]["tool_calls"][0]["function"]["name"] == "query_events"


def test_http_errors_are_an_unavailable_model(stub, query):
    stub.script = [
        {"status": 429, "status_text": "slow down"},
        {"status": 401, "status_text": "bad key sk-secret"},
    ]
    client = LiteLLMClient("openai/jarvis-test", api_base=stub.url, api_key="sk-secret", max_retries=0)
    with pytest.raises(ModelUnavailable, match="RateLimitError"):
        client.complete([{"role": "user", "content": "hi"}], [])
    with pytest.raises(ModelUnavailable) as e:
        client.complete([{"role": "user", "content": "hi"}], [])
    assert "AuthenticationError" in str(e.value) and "sk-secret" not in str(e.value)

    closed = LiteLLMClient(
        "openai/jarvis-test", api_base="http://127.0.0.1:9/v1", api_key="k", max_retries=0, timeout_s=3
    )
    with pytest.raises(ModelUnavailable):
        closed.complete([{"role": "user", "content": "hi"}], [])

    insight = Agent(query, analyst=LLMAnalyst(closed, query)).analyze(
        Trigger(type=TriggerType.RECOVERY_FAILED, ref=STALL)
    )
    assert insight.analyst == "heuristic"
    assert any(f.startswith("openai/jarvis-test: model unavailable") for f in insight.flags)


def test_completions_are_parsed():
    client = LiteLLMClient(
        "openai/gpt-4o-mini",
        api_key="k",
        extra={
            "mock_tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "get_active_faults", "arguments": "{}"}}
            ]
        },
    )
    c = client.complete([{"role": "user", "content": "hi"}], [])
    assert [(t.id, t.name, t.arguments) for t in c.tool_calls] == [("c1", "get_active_faults", "{}")]
    assert c.input_tokens > 0 and c.cost_usd > 0  # priced from the bundled cost map
    assert client.supports_tools()
    assert not LiteLLMClient("openai/jarvis-test").supports_tools()  # unknown to litellm


def test_the_stable_prefix_is_marked_cacheable_where_it_helps():
    messages = [{"role": "system", "content": "stable"}, {"role": "user", "content": "window"}]
    sent = LiteLLMClient("anthropic/claude-sonnet-4-5")._messages(messages)
    assert sent[0]["content"] == [{"type": "text", "text": "stable", "cache_control": {"type": "ephemeral"}}]
    assert sent[1] == messages[1]
    assert LiteLLMClient("openai/jarvis-test")._messages(messages) == messages


def test_the_cli_with_a_model(stub, query, tmp_path, monkeypatch, capsys):
    from jarvis_agent import cli

    plans = tmp_path / "plans.yaml"
    plans.write_text(json.dumps({"plans": json.loads((FIXTURES / "demo_tour.plans.json").read_text())}))
    config = tmp_path / "agent.yaml"
    config.write_text(
        "schema: jarvis.agent/v1\n"
        f"model: {{ name: openai/jarvis-test, api_base: '{stub.url}', api_key_env: JARVIS_TEST_KEY }}\n"
    )
    monkeypatch.setenv("JARVIS_TEST_KEY", "sk-from-env")
    monkeypatch.delenv("JARVIS_AGENT_MODEL", raising=False)
    argv = [
        "--config",
        str(config),
        "analyze",
        STALL,
        "--replay",
        str(FIXTURES / "demo_tour.jsonl"),
        "--plans",
        str(plans),
    ]
    with pytest.raises(SystemExit, match="does not know it to call tools"):
        cli.main(argv)

    config.write_text(
        config.read_text().replace("JARVIS_TEST_KEY }", "JARVIS_TEST_KEY, assume_tools: true }")
    )
    stub.script = [{"calls": [("submit_insight", insight_body(query))]}]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "The lidar stopped publishing" in out
    assert "llm analyst · openai/jarvis-test · 1200 in, 80 out" in out
    assert stub.requests[-1]["headers"]["authorization"] == "Bearer sk-from-env"

    assert cli.main(["--heuristic", *argv]) == 0  # no model: no request
    assert "heuristic analyst (degraded)" in capsys.readouterr().out
    assert len(stub.requests) == 1
