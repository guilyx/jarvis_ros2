# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
for package in ("agent/jarvis_agent", "gateway/jarvis_gateway", "lake/jarvis_lake"):
    if str(ROOT / package) not in sys.path:
        sys.path.append(str(ROOT / package))

from jarvis_agent.adapters.replay import ReplayQuery, load_jsonl  # noqa: E402

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

# The demo's guided tour, recorded through the gateway (ros2 launch jarvis_demo demo.launch.py, then
# inject tour): the lidar relay wedges and a restart fixes it; the lidar stalls, the restart does not
# help and Jarvis escalates; the e-stop is pressed and stays latched.
WEDGE = "01a14a41-27c0-73bf-bd09-b93746be9cb3"
STALL = "01a14a41-3bf2-78ab-85ae-c2345420f217"
ESTOP = "01a14a41-d01a-711a-91f5-d875392dcdfd"


@pytest.fixture
def events():
    return load_jsonl(FIXTURES / "demo_tour.jsonl")


@pytest.fixture
def plans():
    return json.loads((FIXTURES / "plans.json").read_text())


@pytest.fixture
def query(events, plans):
    return ReplayQuery(events, plans)
