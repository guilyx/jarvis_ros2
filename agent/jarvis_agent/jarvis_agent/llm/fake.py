# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""A scripted model, for tests: each turn answers with the next step of its script.

A step is a Completion, a function (messages, tools) -> Completion (to answer from what the agent
sent), or an exception to raise. `calls` keeps what the agent sent each turn."""

import copy
import itertools
import json

from jarvis_agent.llm.base import Completion, ModelUnavailable, ToolCall

_ids = itertools.count(1)


def call(name, **arguments):
    return ToolCall(id=f"call_{next(_ids)}", name=name, arguments=json.dumps(arguments))


def turn(*calls, text="", input_tokens=1000, output_tokens=100, cost_usd=0.001):
    return Completion(
        text=text,
        tool_calls=list(calls),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        model="fake",
        finish_reason="tool_calls" if calls else "stop",
    )


class FakeLLM:
    provider = "fake"

    def __init__(self, script, name="fake"):
        self.script = list(script)
        self.name = name
        self.calls = []  # [{"messages", "tools", "tool_choice"}]

    def complete(self, messages, tools, tool_choice="auto"):
        self.calls.append({"messages": copy.deepcopy(messages), "tools": tools, "tool_choice": tool_choice})
        if not self.script:
            raise ModelUnavailable("the fake model's script ran out")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(messages, tools) if callable(step) else step
