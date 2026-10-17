# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The model port: one turn of a chat model that can call tools, whatever serves it
(llm/litellm_client.py for real models, llm/fake.py for tests). Messages and tools are the
OpenAI-style common denominator: role/content messages, function tools with a JSON schema."""

import dataclasses
from typing import Protocol


@dataclasses.dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text, as the model wrote it


@dataclasses.dataclass
class Completion:
    text: str = ""
    tool_calls: list = dataclasses.field(default_factory=list)  # [ToolCall]
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    finish_reason: str = ""


class ModelUnavailable(Exception):  # noqa: N818 - it is not a bug, the model is just not there
    """The model did not answer: offline, timed out, rate limited, refused the key, the context was
    too long. The heuristic analyst answers instead."""


class LLMClient(Protocol):
    provider: str
    name: str

    def complete(self, messages: list[dict], tools: list[dict], tool_choice="auto") -> Completion: ...
