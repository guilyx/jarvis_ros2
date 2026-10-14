# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests: examples and shipped configs must validate against the canonical schemas."""

import json
import pathlib

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMAS = pathlib.Path(__file__).resolve().parents[1]
REPO = SCHEMAS.parent


def _registry() -> Registry:
    resources = []
    for path in SCHEMAS.glob("*.schema.json"):
        schema = json.loads(path.read_text())
        resource = Resource.from_contents(schema)
        resources.append((schema["$id"], resource))
        resources.append((path.name, resource))
    return Registry().with_resources(resources)


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMAS / name).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=_registry())


@pytest.mark.parametrize("path", sorted((SCHEMAS / "examples").glob("event.*.json")), ids=lambda p: p.name)
def test_event_examples_validate(path):
    _validator("event.schema.json").validate(json.loads(path.read_text()))


@pytest.mark.parametrize("path", sorted((SCHEMAS / "examples").glob("fault.*.json")), ids=lambda p: p.name)
def test_fault_examples_validate(path):
    _validator("fault.schema.json").validate(json.loads(path.read_text()))


@pytest.mark.parametrize(
    "path", sorted(REPO.glob("ros/*/config/**/*faults*.yaml")), ids=lambda p: str(p.relative_to(REPO))
)
def test_shipped_fault_catalogs_validate(path):
    _validator("fault_catalog.schema.json").validate(yaml.safe_load(path.read_text()) or {})


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "GOSSIP"},
        {"code": "Not.Lowercase"},
        {"severity": "LOUD"},
        {"stamp": "yesterday"},
        {"seq": 12},
        {"attrs": {"nested": {"no": "objects"}}},
    ],
)
def test_event_schema_rejects_bad_fields(bad):
    event = json.loads((SCHEMAS / "examples" / "event.minimal.json").read_text())
    event.update(bad)
    assert list(_validator("event.schema.json").iter_errors(event)), f"should reject {bad}"


def test_catalog_schema_rejects_unknown_keys():
    catalog = {"faults": [{"code": "a.b", "lacth": True}]}
    assert list(_validator("fault_catalog.schema.json").iter_errors(catalog))
