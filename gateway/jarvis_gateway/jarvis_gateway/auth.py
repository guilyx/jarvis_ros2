# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Who is calling: a token, else the identity an authenticating proxy vouches for, else anonymous.

Single sign-on (OIDC, SAML, ...) is the proxy's job: oauth2-proxy, Pomerium or any reverse proxy
that signs people in and forwards who they are in headers. The gateway believes those headers only
from the proxy's own addresses (`proxy_auth.trusted`): from anyone else they are ignored, or anyone
could claim to be an admin by sending one. See docs/guides/security.md.
"""

import dataclasses
import ipaddress

from jarvis_gateway.config import ROLES, Principal


@dataclasses.dataclass
class ProxyAuth:
    trusted: list  # addresses or networks of the proxy, e.g. ["127.0.0.1", "10.0.0.0/24"]
    user_header: str = "X-Forwarded-Email"
    groups_header: str = "X-Forwarded-Groups"  # comma-separated
    roles: dict = dataclasses.field(default_factory=dict)  # group -> role; the highest wins
    default_role: str | None = "viewer"  # signed in, in no listed group (None: refused)

    def __post_init__(self):
        self.networks = [ipaddress.ip_network(t, strict=False) for t in self.trusted]
        for role in [*self.roles.values(), self.default_role]:
            if role is not None and role not in ROLES:
                raise ValueError(f"proxy_auth: unknown role '{role}' (viewer, operator or admin)")

    def vouches_for(self, client_host):
        try:
            address = ipaddress.ip_address(client_host)
        except (TypeError, ValueError):
            return False
        return any(address in n for n in self.networks)

    def principal(self, headers):
        """The person the proxy signed in, from its headers. None: no one (or no role for them)."""
        user = (headers.get(self.user_header) or "").strip()
        if not user:
            return None
        groups = [g.strip() for g in (headers.get(self.groups_header) or "").split(",") if g.strip()]
        roles = [self.roles[g] for g in groups if g in self.roles]
        role = max(roles, key=ROLES.get) if roles else self.default_role
        return Principal(user, role) if role else None


class Refused(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status, self.detail = status, detail


def presented(token, authorization):
    """The token a caller presents: `Authorization: Bearer ...` first, else `?token=`."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return token


def identify(settings, token, headers, client_host):
    """The caller's Principal; Refused(401) if they cannot be identified and must be."""
    if token:
        who = settings.tokens.get(token)
        if who is None:
            raise Refused(401, "unknown token")
        return who
    proxy = settings.proxy_auth
    if proxy is not None and proxy.vouches_for(client_host):
        who = proxy.principal(headers)
        if who is not None:
            return who
        if headers.get(proxy.user_header):
            raise Refused(403, "signed in, but in no group that has a role here")
    if settings.anonymous is None:
        raise Refused(401, "a token is required")
    return settings.anonymous
