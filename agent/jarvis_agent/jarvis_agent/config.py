# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Agent settings (jarvis-agent --config agent.yaml):

schema: jarvis.agent/v1
gateway: { url: http://127.0.0.1:8080 }   # token: $JARVIS_AGENT_TOKEN (never in this file)
autonomy: { level: advise, min_confidence: 0.7 }
triggers: { min_severity: ERROR, settle_s: 20, max_per_hour: 20 }
windows: { context_s: 300, sliding_s: 600 }
redact: ["attrs.gps.*"]                   # attributes the analyst never sees
"""

import dataclasses
import os
import pathlib

import yaml

from jarvis_agent.core.policy import LEVELS


@dataclasses.dataclass
class AgentConfig:
    gateway_url: str = "http://127.0.0.1:8080"
    token: str = ""
    level: str = "advise"
    min_confidence: float = 0.7
    min_severity: str = "ERROR"
    settle_s: float = 20.0
    max_per_hour: int = 20
    context_s: float = 300.0
    sliding_s: float = 600.0
    redact: list = dataclasses.field(default_factory=list)

    @classmethod
    def load(cls, path=None, **overrides):
        c = cls()
        if path:
            raw = yaml.safe_load(pathlib.Path(path).read_text()) or {}
            if raw.get("schema", "jarvis.agent/v1") != "jarvis.agent/v1":
                raise ValueError(f"{path}: expected schema jarvis.agent/v1")
            if "token" in (raw.get("gateway") or {}):
                raise ValueError(f"{path}: put the gateway token in $JARVIS_AGENT_TOKEN, not in the file")
            sections = {
                "gateway": {"url": "gateway_url"},
                "autonomy": {"level": "level", "min_confidence": "min_confidence"},
                "triggers": {
                    "min_severity": "min_severity",
                    "settle_s": "settle_s",
                    "max_per_hour": "max_per_hour",
                },
                "windows": {"context_s": "context_s", "sliding_s": "sliding_s"},
            }
            for section, keys in sections.items():
                for key, field in keys.items():
                    if key in (raw.get(section) or {}):
                        setattr(c, field, raw[section][key])
            c.redact = list(raw.get("redact") or [])
        c.token = os.environ.get("JARVIS_AGENT_TOKEN", c.token)
        for key, value in overrides.items():
            if value is not None:
                setattr(c, key, value)
        if c.level not in LEVELS:
            raise ValueError(f"unknown autonomy level '{c.level}' ({', '.join(LEVELS)})")
        return c
