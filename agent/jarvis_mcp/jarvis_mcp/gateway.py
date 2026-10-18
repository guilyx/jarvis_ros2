# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""A small client of the Jarvis gateway's REST API (/api/v1), on the standard library.

The assistant acts as the token's owner: the gateway checks its role and records what it does in
the audit trail under that name, exactly as for a person.
"""

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request


class GatewayError(Exception):
    def __init__(self, status, detail):
        super().__init__(f"{status}: {detail}" if status else detail)
        self.status, self.detail = status, detail


class Gateway:
    def __init__(self, url, token="", timeout_s=30.0, ca_file=None):
        self.base = url.rstrip("/") + "/api/v1"
        self.token = token
        self.timeout_s = timeout_s
        self._tls = ssl.create_default_context(cafile=ca_file) if ca_file else None

    def __repr__(self):  # never the token
        return f"Gateway({self.base!r})"

    def _request(self, method, path, query=None, body=None, accept="application/json"):
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v not in (None, "")})
        headers = {"Accept": accept, "User-Agent": "jarvis-mcp"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            return urllib.request.urlopen(req, timeout=self.timeout_s, context=self._tls)
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read() or b"{}").get("detail", e.reason)
            except ValueError:
                detail = e.reason
            raise GatewayError(e.code, detail if isinstance(detail, str) else json.dumps(detail)) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise GatewayError(0, f"the gateway at {self.base} is not reachable: {e}") from None

    def get(self, path, **query):
        with self._request("GET", path, query) as r:
            return json.loads(r.read() or b"null")

    def post(self, path, body=None):
        with self._request("POST", path, body=body if body is not None else {}) as r:
            return json.loads(r.read() or b"null")

    def stream(self, path, body):
        """POSTs and reads a server-sent event stream: yields (event, data) as they arrive."""
        with self._request("POST", path, body=body, accept="text/event-stream") as r:
            event, data = "message", []
            for raw in r:
                line = raw.decode().rstrip("\r\n")
                if not line:
                    if data:
                        yield event, json.loads("\n".join(data))
                    event, data = "message", []
                elif line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
