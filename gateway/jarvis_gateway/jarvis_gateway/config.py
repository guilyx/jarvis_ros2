# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Gateway settings: where to listen, who may do what, where notifications go.

    schema: jarvis.gateway/v1
    host: 127.0.0.1                 # LAN only: bind a LAN address to reach it from other machines
    port: 8080
    anonymous: { name: anonymous, role: viewer }   # requests without a token (null: none allowed)
    tokens:                         # static tokens, mainly so writes are attributed in the audit trail
      - { token: change-me, name: alice, role: operator }
    webhooks:                       # where `notify` actions of rules go
      - { name: ops, url: https://hooks.example/..., channels: [operators], format: slack }
    tls: { cert: /etc/jarvis/tls.crt, key: /etc/jarvis/tls.key }   # HTTPS (and wss://)
    proxy_auth:                     # single sign-on through an authenticating proxy (oauth2-proxy)
      trusted: [127.0.0.1]          # the proxy's addresses: its headers are believed from there only
      user_header: X-Forwarded-Email
      groups_header: X-Forwarded-Groups
      roles: { jarvis-admins: admin, jarvis-operators: operator }   # group -> role, highest wins
      default_role: viewer          # signed in, in no listed group (null: refused)
    lint: { plans_dir: ..., catalog: ... }        # what POST /rules:validate checks rules against

Tokens can also come from $JARVIS_GATEWAY_TOKENS ("token:name:role,..."), to keep them out of files.
docs/guides/security.md puts TLS and single sign-on together.
"""

import dataclasses
import os
import pathlib

import yaml

ROLES = {"viewer": 1, "operator": 2, "admin": 3}


@dataclasses.dataclass(frozen=True)
class Principal:
    name: str
    role: str

    def can(self, role):
        return ROLES.get(self.role, 0) >= ROLES[role]


@dataclasses.dataclass
class Webhook:
    name: str
    url: str
    channels: list = dataclasses.field(default_factory=lambda: ["*"])
    format: str = "generic"  # generic | slack
    timeout_s: float = 5.0
    retries: int = 3


@dataclasses.dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8080
    data_dir: str = os.environ.get("JARVIS_DATA", "~/.local/share/jarvis")
    anonymous: Principal | None = dataclasses.field(default_factory=lambda: Principal("anonymous", "viewer"))
    tokens: dict = dataclasses.field(default_factory=dict)  # token -> Principal
    webhooks: list = dataclasses.field(default_factory=list)
    dashboard_dir: str = ""
    recent_capacity: int = 20000  # live events kept in memory until the lake has them
    jarvis_bin: str = os.environ.get("JARVIS_BIN", "jarvis")
    lint_plans_dir: str = ""
    lint_catalog: str = ""
    tls_cert: str = ""
    tls_key: str = ""
    proxy_auth: object = None  # jarvis_gateway.auth.ProxyAuth

    @classmethod
    def load(cls, path=None, **overrides):
        """Settings from a YAML file (optional), $JARVIS_GATEWAY_TOKENS, then explicit overrides."""
        s = cls()
        if path:
            raw = yaml.safe_load(pathlib.Path(path).read_text()) or {}
            if raw.get("schema", "jarvis.gateway/v1") != "jarvis.gateway/v1":
                raise ValueError(f"{path}: expected schema jarvis.gateway/v1")
            for key in ("host", "port", "data_dir", "dashboard_dir", "recent_capacity", "jarvis_bin"):
                if key in raw:
                    setattr(s, key, raw[key])
            if "anonymous" in raw:
                a = raw["anonymous"]
                s.anonymous = _principal(a["name"], a["role"]) if a else None
            for t in raw.get("tokens") or []:
                s.tokens[str(t["token"])] = _principal(t["name"], t["role"])
            s.webhooks = [Webhook(**w) for w in raw.get("webhooks") or []]
            lint = raw.get("lint") or {}
            s.lint_plans_dir = lint.get("plans_dir", "")
            s.lint_catalog = lint.get("catalog", "")
            tls = raw.get("tls") or {}
            s.tls_cert, s.tls_key = str(tls.get("cert", "")), str(tls.get("key", ""))
            if bool(s.tls_cert) != bool(s.tls_key):
                raise ValueError(f"{path}: tls needs both cert and key")
            if raw.get("proxy_auth"):
                from jarvis_gateway.auth import ProxyAuth

                s.proxy_auth = ProxyAuth(**raw["proxy_auth"])
        for item in filter(None, os.environ.get("JARVIS_GATEWAY_TOKENS", "").split(",")):
            token, name, role = item.split(":")
            s.tokens[token] = _principal(name, role)
        for key, value in overrides.items():
            if value is not None:
                setattr(s, key, value)
        return s


def _principal(name, role):
    if role not in ROLES:
        raise ValueError(f"unknown role '{role}' (viewer, operator or admin)")
    return Principal(str(name), role)
