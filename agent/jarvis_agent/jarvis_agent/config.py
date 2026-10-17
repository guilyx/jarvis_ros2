# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Agent settings (jarvis-agent --config agent.yaml):

schema: jarvis.agent/v1
gateway: { url: http://127.0.0.1:8080 }   # token: $JARVIS_AGENT_TOKEN (never in this file)
autonomy: { level: advise, min_confidence: 0.7 }
triggers: { min_severity: ERROR, settle_s: 20, max_per_hour: 20, catch_up_s: 120 }
windows: { context_s: 300, sliding_s: 600 }
redact: ["attrs.gps.*", "@identities"]   # what a model never sees (core/redaction.py)
scrub_emails: true                        # e-mail addresses too, in any text sent to a model
model:                                    # none: the heuristic analyst answers
  name: openrouter/<vendor>/<model>       # or $JARVIS_AGENT_MODEL; any LiteLLM model string
  api_base: ""                            # a LiteLLM proxy or a local server (Ollama, vLLM)
  api_key_env: ""                         # the variable with the key (default: the provider's own,
                                          # e.g. $OPENROUTER_API_KEY), or a file: $JARVIS_AGENT_LLM_KEY_FILE
  per_trigger: { RECOVERY_FAILED: ... }   # another model for some triggers
  timeout_s: 60
  max_output_tokens: 2048
  max_tool_calls: 12
  context_tokens: 30000
  skip_when_conclusive: true              # one fault fixed by a plan: no model call
  assume_tools: false                     # start even if LiteLLM does not know the model calls tools
budget: { usd_per_hour: 1.0, usd_per_day: 10.0, tokens_per_analysis: 250000 }
traces_dir: ""                            # default $JARVIS_DATA/agent_traces
"""

import dataclasses
import os
import pathlib

import yaml

from jarvis_agent.core.policy import LEVELS
from jarvis_agent.core.redaction import check_patterns
from jarvis_agent.model import TriggerType


@dataclasses.dataclass
class AgentConfig:
    gateway_url: str = "http://127.0.0.1:8080"
    token: str = ""
    level: str = "advise"
    min_confidence: float = 0.7
    min_severity: str = "ERROR"
    settle_s: float = 20.0
    max_per_hour: int = 20
    catch_up_s: float = 120.0  # at start, what happened this long before still triggers
    context_s: float = 300.0
    sliding_s: float = 600.0
    redact: list = dataclasses.field(default_factory=list)
    scrub_emails: bool = True
    model: str = ""
    api_base: str = ""
    api_key_env: str = ""
    per_trigger: dict = dataclasses.field(default_factory=dict)
    timeout_s: float = 60.0
    max_output_tokens: int = 2048
    max_tool_calls: int = 12
    context_tokens: int = 30_000
    skip_when_conclusive: bool = True
    assume_tools: bool = False
    usd_per_hour: float = 1.0
    usd_per_day: float = 10.0
    tokens_per_analysis: int = 250_000
    traces_dir: str = ""

    @classmethod
    def load(cls, path=None, **overrides):
        c = cls()
        if path:
            raw = yaml.safe_load(pathlib.Path(path).read_text()) or {}
            if raw.get("schema", "jarvis.agent/v1") != "jarvis.agent/v1":
                raise ValueError(f"{path}: expected schema jarvis.agent/v1")
            if "token" in (raw.get("gateway") or {}):
                raise ValueError(f"{path}: put the gateway token in $JARVIS_AGENT_TOKEN, not in the file")
            if "api_key" in (raw.get("model") or {}):
                raise ValueError(
                    f"{path}: put the model's key in the environment (model.api_key_env) or in a file "
                    "($JARVIS_AGENT_LLM_KEY_FILE), not in this file"
                )
            sections = {
                "gateway": {"url": "gateway_url"},
                "autonomy": {"level": "level", "min_confidence": "min_confidence"},
                "triggers": {
                    "min_severity": "min_severity",
                    "settle_s": "settle_s",
                    "max_per_hour": "max_per_hour",
                    "catch_up_s": "catch_up_s",
                },
                "windows": {"context_s": "context_s", "sliding_s": "sliding_s"},
                "model": {
                    "name": "model",
                    "api_base": "api_base",
                    "api_key_env": "api_key_env",
                    "per_trigger": "per_trigger",
                    "timeout_s": "timeout_s",
                    "max_output_tokens": "max_output_tokens",
                    "max_tool_calls": "max_tool_calls",
                    "context_tokens": "context_tokens",
                    "skip_when_conclusive": "skip_when_conclusive",
                    "assume_tools": "assume_tools",
                },
                "budget": {
                    "usd_per_hour": "usd_per_hour",
                    "usd_per_day": "usd_per_day",
                    "tokens_per_analysis": "tokens_per_analysis",
                },
            }
            for section, keys in sections.items():
                for key, field in keys.items():
                    if key in (raw.get(section) or {}):
                        setattr(c, field, raw[section][key])
            c.redact = check_patterns(raw.get("redact") or [])
            c.scrub_emails = bool(raw.get("scrub_emails", True))
            c.traces_dir = raw.get("traces_dir") or ""
        # Set but empty means unset: compose passes JARVIS_AGENT_MODEL through even when nobody set it.
        c.token = os.environ.get("JARVIS_AGENT_TOKEN") or c.token
        c.model = os.environ.get("JARVIS_AGENT_MODEL") or c.model
        for key, value in overrides.items():
            if value is not None:
                setattr(c, key, value)
        if not c.traces_dir and os.environ.get("JARVIS_DATA"):
            c.traces_dir = str(pathlib.Path(os.environ["JARVIS_DATA"]) / "agent_traces")
        if c.level not in LEVELS:
            raise ValueError(f"unknown autonomy level '{c.level}' ({', '.join(LEVELS)})")
        c.per_trigger = dict(c.per_trigger or {})
        unknown = [t for t in c.per_trigger if t not in TriggerType.__members__]
        if unknown:
            raise ValueError(f"model.per_trigger: unknown trigger(s) {unknown} ({', '.join(TriggerType)})")
        return c

    def api_key(self):
        """The model's key: from the file $JARVIS_AGENT_LLM_KEY_FILE names, else from the variable
        model.api_key_env names, else "" (LiteLLM then reads the provider's usual variable)."""
        path = os.environ.get("JARVIS_AGENT_LLM_KEY_FILE")
        if path:
            return pathlib.Path(path).read_text().strip()
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "")
        return ""
