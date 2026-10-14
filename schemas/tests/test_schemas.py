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


def _doc_yaml_blocks(doc: pathlib.Path, marker: str):
    """The complete YAML examples of a design doc that start with `marker` (elided ones skipped)."""
    text = doc.read_text()
    blocks = text.split("```yaml\n")[1:]
    return [b.split("```")[0] for b in blocks if marker in b.split("```")[0] and "…" not in b.split("```")[0]]


RULE_DOC = REPO / "docs" / "design" / "05-rules-and-recovery.md"


@pytest.mark.parametrize("block", _doc_yaml_blocks(RULE_DOC, "schema: jarvis.rules/v1"))
def test_design_doc_rule_examples_validate(block):
    _validator("rules.schema.json").validate(yaml.safe_load(block))


@pytest.mark.parametrize(
    "path", sorted(REPO.glob("ros/*/config/**/rules/*.yaml")), ids=lambda p: str(p.relative_to(REPO))
)
def test_shipped_rules_validate(path):
    _validator("rules.schema.json").validate(yaml.safe_load(path.read_text()) or {})


ABSENT_WITHOUT_FOR = [{"event": {"code": "a"}}, {"absent": {"code": "b"}}]


def _rule(**overrides):
    rule = {"id": "ok", "when": {"event": {"code": "a.b"}}, "then": [{"notify": {"message": "hi"}}]}
    rule.update(overrides)
    return {"schema": "jarvis.rules/v1", "rules": [rule]}


@pytest.mark.parametrize(
    "bad",
    [
        {"id": "Not_Kebab"},
        {"when": {"event": {}}},
        {"when": {"event": {"cod": "a"}}},
        {"when": {"count": {"event": {"code": "a"}, "threshold": 0, "within": "1s"}}},
        {"when": {"event": {"code": "a"}, "count": {"event": {"code": "a"}, "threshold": 1, "within": "1s"}}},
        {"when": {"sequence": {"within": "5s", "steps": ABSENT_WITHOUT_FOR}}},
        {"then": []},
        {"then": [{"notify": {"message": "x"}, "emit": {"kind": "LOG", "code": "a"}}]},
        {"then": [{"emit": {"kind": "FAULT_STATE", "code": "a"}}]},
        {"then": [{"incident": {"open": True, "escalate": True}}]},
        {"policy": {"coldown": "1s"}},
        {"time_basis": "wall"},
    ],
)
def test_rules_schema_rejects_mistakes(bad):
    assert list(_validator("rules.schema.json").iter_errors(_rule(**bad))), f"should reject {bad}"
