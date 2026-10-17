# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
import pathlib
import sys
import time

import pytest

try:
    import jarvis_lake  # noqa: F401
except ImportError:  # running from the source tree, without the workspace sourced
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "lake" / "jarvis_lake"))

from fastapi.testclient import TestClient  # noqa: E402
from jarvis_gateway.adapters.fake import FakeJarvis  # noqa: E402
from jarvis_gateway.app import create_app  # noqa: E402
from jarvis_gateway.config import Principal, Settings  # noqa: E402

TOKENS = {
    "v-token": Principal("victor", "viewer"),
    "o-token": Principal("olga", "operator"),
    "a-token": Principal("ada", "admin"),
}
VIEWER = {"Authorization": "Bearer v-token"}
OPERATOR = {"Authorization": "Bearer o-token"}
ADMIN = {"Authorization": "Bearer a-token"}


class Harness:
    """A gateway over a FakeJarvis. `do(fn, ...)` runs fn on the gateway's event loop (the fake's
    helpers must run there, as the ROS adapter's callbacks do)."""

    def __init__(self, client, fake):
        self.client = client
        self.fake = fake
        self.gateway = client.app.state.gateway

    def do(self, fn, *args, **kwargs):
        return self.client.portal.call(lambda: fn(*args, **kwargs))

    def get(self, path, headers=VIEWER, **params):
        r = self.client.get(path, headers=headers, params=params)
        assert r.status_code == 200, r.text
        return r.json()

    def wait(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.02)
        raise AssertionError("timed out waiting")


@pytest.fixture
def make_gateway(tmp_path):
    started = []

    def make(fake=None, notifier=None, **settings):
        fake = fake or FakeJarvis(step_s=0.01)
        s = Settings(data_dir=str(tmp_path / "data"), tokens=dict(TOKENS), **settings)
        client = TestClient(create_app(s, fake, notifier))
        client.__enter__()
        started.append(client)
        return Harness(client, fake)

    yield make
    for client in started:
        client.__exit__(None, None, None)


@pytest.fixture
def gw(make_gateway):
    return make_gateway()
