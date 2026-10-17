# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The fault catalog for people (milestone 5.5): what codes mean and their runbooks, as the
dashboard and the agent read them; and the runbooks the shipped catalogs point at."""

import os
import pathlib

import pytest
import yaml
from jarvis_gateway.catalog import MAX_RUNBOOK_BYTES, Catalog

ROOT = pathlib.Path(__file__).resolve().parents[3]
SHIPPED = [
    ROOT / "ros" / "jarvis_bringup" / "config" / "faults.yaml",
    ROOT / "ros" / "jarvis_demo" / "config" / "demo_faults.yaml",
]
RUNBOOKS = ROOT / "ros" / "jarvis_bringup" / "config" / "runbooks"  # what the launch file passes


@pytest.fixture
def catalog(tmp_path):
    (tmp_path / "runbooks").mkdir()
    (tmp_path / "runbooks" / "lidar.md").write_text("# sensor.lidar.stale\n\nCheck the cable.\n")
    path = tmp_path / "faults.yaml"
    path.write_text(
        "schema: jarvis.faults/v1\n"
        "defaults: { severity: WARN }\n"
        "faults:\n"
        "  - { code: sensor.lidar.stale, description: The lidar went  quiet., runbook: runbooks/lidar.md }\n"
        "  - { code: safety.estop.engaged, severity: CRITICAL, latch: true }\n"
        "  - { code: broken.link, runbook: runbooks/missing.md }\n"
    )
    return path


def test_people_read_what_codes_mean_and_their_runbooks(make_gateway, catalog):
    h = make_gateway(catalog=str(catalog))
    faults = {f["code"]: f for f in h.get("/api/v1/catalog")["faults"]}
    assert faults["sensor.lidar.stale"] == {
        "code": "sensor.lidar.stale",
        "description": "The lidar went quiet.",
        "severity": "WARN",
        "latch": False,
        "runbook": True,
    }
    assert faults["safety.estop.engaged"]["latch"] and not faults["safety.estop.engaged"]["runbook"]
    book = h.get("/api/v1/runbooks/sensor.lidar.stale")
    assert book == {"code": "sensor.lidar.stale", "markdown": "# sensor.lidar.stale\n\nCheck the cable.\n"}
    assert h.client.get("/api/v1/runbooks/safety.estop.engaged").status_code == 404
    assert h.client.get("/api/v1/runbooks/not.cataloged").status_code == 404
    broken = h.client.get("/api/v1/runbooks/broken.link")
    assert broken.status_code == 500 and "missing.md cannot be read" in broken.json()["detail"]


def test_an_edited_catalog_is_read_again(make_gateway, catalog):
    h = make_gateway(catalog=str(catalog))
    assert len(h.get("/api/v1/catalog")["faults"]) == 3
    text = catalog.read_text().replace("  - { code: broken.link, runbook: runbooks/missing.md }\n", "")
    catalog.write_text(text)
    os.utime(catalog, ns=(1, 1))  # a new mtime, however coarse the file system's clock
    assert len(h.get("/api/v1/catalog")["faults"]) == 2


def test_without_a_catalog_there_is_nothing_to_read(make_gateway):
    h = make_gateway()
    assert h.get("/api/v1/catalog") == {"faults": []}
    assert h.client.get("/api/v1/runbooks/ros.topic.stale").status_code == 404


def test_runbooks_are_found_by_code_next_to_the_catalog_then_in_the_shipped_ones(
    make_gateway, catalog, tmp_path
):
    (catalog.parent / "runbooks" / "safety.estop.engaged.md").write_text("# safety.estop.engaged\n")
    shipped = tmp_path / "shipped"
    shipped.mkdir()
    (shipped / "ros.topic.stale.md").write_text("# ros.topic.stale\n")
    (shipped / "safety.estop.engaged.md").write_text("# not this one: the catalog's own comes first\n")
    h = make_gateway(catalog=str(catalog), runbooks=[str(shipped)])
    assert h.get("/api/v1/runbooks/safety.estop.engaged")["markdown"] == "# safety.estop.engaged\n"
    assert h.get("/api/v1/runbooks/ros.topic.stale")["markdown"] == "# ros.topic.stale\n", "uncataloged too"
    faults = {f["code"]: f for f in h.get("/api/v1/catalog")["faults"]}
    assert faults["safety.estop.engaged"]["runbook"]
    for bad in ("..%2Fshipped%2Fros.topic.stale", "ROS.TOPIC.STALE", "a..b"):
        assert h.client.get(f"/api/v1/runbooks/{bad}").status_code == 404, bad


def test_runbooks_are_markdown_files_of_a_bounded_size(tmp_path):
    (tmp_path / "big.md").write_text("x" * (MAX_RUNBOOK_BYTES + 1))
    (tmp_path / "notes.txt").write_text("hi")
    path = tmp_path / "faults.yaml"
    path.write_text(
        "schema: jarvis.faults/v1\nfaults:\n"
        f"  - {{ code: a.big, runbook: {tmp_path / 'big.md'} }}\n"
        "  - { code: a.text, runbook: notes.txt }\n"
    )
    c = Catalog(path)
    with pytest.raises(LookupError, match="KiB"):
        c.runbook("a.big")
    with pytest.raises(LookupError, match="Markdown"):
        c.runbook("a.text")


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_every_shipped_fault_has_a_runbook_that_reads(path):
    c = Catalog(path, [RUNBOOKS])
    codes = [f["code"] for f in (yaml.safe_load(path.read_text()) or {})["faults"]]
    missing = [code for code in codes if c.runbook_path(code) is None]
    assert not missing, f"{path.name}: no runbook for {missing}"
    for code in codes:
        text = c.runbook(code)
        assert text.startswith(f"# {code}"), f"{c.runbook_path(code)} should open with '# {code}'"
