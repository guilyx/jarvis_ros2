# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Who may call: tokens, single sign-on through an authenticating proxy, TLS (milestone 5.4)."""

import json
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.request

import pytest
from conftest import OPERATOR
from jarvis_gateway.auth import ProxyAuth
from jarvis_gateway.config import Settings
from starlette.websockets import WebSocketDisconnect

PROXY = "127.0.0.1"
STREAM = "/api/v1/stream"
SSO = ProxyAuth(
    trusted=[PROXY, "10.0.0.0/24"],
    roles={"jarvis-admins": "admin", "jarvis-operators": "operator"},
)


def person(email, groups=""):
    return {"X-Forwarded-Email": email, "X-Forwarded-Groups": groups}


def test_the_proxy_signs_people_in_with_the_role_of_their_groups(make_gateway):
    h = make_gateway(proxy_auth=SSO, client_host=PROXY)
    me = h.get("/api/v1/me", headers=person("alice@example.com", "staff, jarvis-operators"))
    assert me == {"name": "alice@example.com", "role": "operator"}
    me = h.get("/api/v1/me", headers=person("ada@example.com", "jarvis-operators,jarvis-admins"))
    assert me["role"] == "admin", "the highest role of their groups"
    assert h.get("/api/v1/me", headers=person("bob@example.com"))["role"] == "viewer", "default_role"
    # The operator can act, and the audit trail names them.
    r = h.client.post(
        "/api/v1/faults/nope:ack", json={}, headers=person("alice@example.com", "jarvis-operators")
    )
    assert r.status_code != 403, r.text


def test_people_in_no_group_are_refused_without_a_default_role(make_gateway):
    sso = ProxyAuth(trusted=[PROXY], roles={"jarvis-operators": "operator"}, default_role=None)
    h = make_gateway(proxy_auth=sso, client_host=PROXY)
    r = h.client.get("/api/v1/me", headers=person("eve@example.com", "staff"))
    assert r.status_code == 403 and "no group" in r.json()["detail"]


def test_identity_headers_from_anyone_but_the_proxy_are_ignored(make_gateway):
    # Someone on the LAN sends the proxy's headers themselves: they are anonymous, nothing more.
    h = make_gateway(proxy_auth=SSO, client_host="192.168.1.66")
    forged = person("root@example.com", "jarvis-admins")
    assert h.get("/api/v1/me", headers=forged) == {"name": "anonymous", "role": "viewer"}
    assert h.client.put("/api/v1/recovery/mode", json={"mode": "DISABLED"}, headers=forged).status_code == 403
    closed = make_gateway(proxy_auth=SSO, client_host="192.168.1.66", anonymous=None)
    assert closed.client.get("/api/v1/me", headers=forged).status_code == 401


def test_tokens_still_work_behind_the_proxy(make_gateway):
    h = make_gateway(proxy_auth=SSO, client_host=PROXY)
    assert (
        h.get("/api/v1/me", headers={**OPERATOR, **person("alice@example.com", "jarvis-admins")})["name"]
        == "olga"
    )


def test_the_live_stream_knows_who_is_connecting(make_gateway):
    h = make_gateway(proxy_auth=SSO, client_host="10.0.0.7", anonymous=None)
    with h.client.websocket_connect(STREAM, headers=person("alice@example.com")) as ws:
        ws.send_text(json.dumps({"subscribe": {}}))
    outsider = make_gateway(proxy_auth=SSO, client_host="10.0.1.7", anonymous=None)
    alice = person("alice@example.com")
    with pytest.raises(WebSocketDisconnect), outsider.client.websocket_connect(STREAM, headers=alice) as ws:
        ws.receive_text()


def test_the_settings_reject_half_configurations(tmp_path):
    bad = tmp_path / "gateway.yaml"
    bad.write_text("schema: jarvis.gateway/v1\ntls: { cert: /x.crt }\n")
    with pytest.raises(ValueError, match="both cert and key"):
        Settings.load(bad)
    bad.write_text("schema: jarvis.gateway/v1\nproxy_auth: { trusted: [127.0.0.1], roles: { a: root } }\n")
    with pytest.raises(ValueError, match="unknown role"):
        Settings.load(bad)
    good = tmp_path / "ok.yaml"
    good.write_text(
        "schema: jarvis.gateway/v1\n"
        "tls: { cert: /etc/jarvis/tls.crt, key: /etc/jarvis/tls.key }\n"
        "proxy_auth: { trusted: [127.0.0.1], roles: { ops: operator } }\n"
    )
    s = Settings.load(good)
    assert (s.tls_cert, s.tls_key) == ("/etc/jarvis/tls.crt", "/etc/jarvis/tls.key")
    assert s.proxy_auth.vouches_for("127.0.0.1") and not s.proxy_auth.vouches_for("127.0.0.2")


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl to make a certificate")
def test_the_gateway_serves_https(tmp_path):
    cert, key = tmp_path / "tls.crt", tmp_path / "tls.key"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
         "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True,
    )  # fmt: skip
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen(
        [sys.executable, "-m", "jarvis_gateway.cli", "--fake", "--port", str(port),
         "--data-dir", str(tmp_path), "--tls-cert", str(cert), "--tls-key", str(key),
         "--log-level", "warning"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )  # fmt: skip
    trust = ssl.create_default_context(cafile=str(cert))
    trust.check_hostname = False  # the certificate names localhost, the URL an address
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(
                    f"https://127.0.0.1:{port}/api/v1/me", context=trust, timeout=2
                ) as r:
                    assert json.load(r)["role"] == "viewer"
                    break
            except OSError:
                if time.monotonic() > deadline or server.poll() is not None:
                    raise
                time.sleep(0.2)
        with pytest.raises(OSError):  # and nothing in clear text
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/me", timeout=2)
    finally:
        server.terminate()
        server.wait(timeout=10)
