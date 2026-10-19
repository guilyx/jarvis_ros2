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


@pytest.mark.parametrize("path", sorted((SCHEMAS / "examples").glob("insight.*.json")), ids=lambda p: p.name)
def test_insight_examples_validate(path):
    _validator("insight.schema.json").validate(json.loads(path.read_text()))


def test_insight_schema_caps_what_the_analyst_says():
    insight = json.loads((SCHEMAS / "examples" / "insight.lidar_stall.json").read_text())
    hypothesis = insight["hypotheses"][0]
    for bad in (
        {"hypotheses": [hypothesis] * 4},
        {"assessment": "DIRE"},
        {"recommendations": [{"kind": "RUN_SHELL", "text": "rm -rf /"}]},
        {"hypotheses": [dict(hypothesis, confidence=1.5)]},
    ):
        assert list(_validator("insight.schema.json").iter_errors({**insight, **bad})), bad


@pytest.mark.parametrize("path", sorted((SCHEMAS / "examples").glob("fault.*.json")), ids=lambda p: p.name)
def test_fault_examples_validate(path):
    _validator("fault.schema.json").validate(json.loads(path.read_text()))


@pytest.mark.parametrize(
    "path", sorted(REPO.glob("ros/*/config/**/*faults*.yaml")), ids=lambda p: str(p.relative_to(REPO))
)
def test_shipped_fault_catalogs_validate(path):
    _validator("fault_catalog.schema.json").validate(yaml.safe_load(path.read_text()) or {})


def _by_key(items, key):
    return {item[key]: item for item in items}


def test_the_demo_catalog_repeats_the_default_one():
    # Catalogs don't include each other yet: the demo copies the generic faults, and must keep up.
    default = yaml.safe_load((REPO / "ros/jarvis_bringup/config/faults.yaml").read_text())
    demo = yaml.safe_load((REPO / "ros/jarvis_demo/config/demo_faults.yaml").read_text())
    assert demo["defaults"] == default["defaults"]
    faults = _by_key(demo["faults"], "code")
    for fault in default["faults"]:
        assert faults.get(fault["code"]) == fault, fault["code"]
    rules = _by_key(demo.get("correlation", []), "id")
    for rule in default.get("correlation", []):
        assert rules.get(rule["id"]) == rule, rule["id"]


@pytest.mark.parametrize(
    "rules_dir",
    sorted(p for p in (REPO / "ros/jarvis_demo/config").glob("**/rules") if p.is_dir()),
    ids=lambda p: str(p.relative_to(REPO)),
)
def test_the_demo_rules_repeat_the_default_ones(rules_dir):
    default = yaml.safe_load((REPO / "ros/jarvis_bringup/config/rules/core.yaml").read_text())
    demo = {}
    for path in rules_dir.glob("*.yaml"):
        demo.update(_by_key(yaml.safe_load(path.read_text())["rules"], "id"))
    for rule in default["rules"]:
        assert demo.get(rule["id"]) == rule, rule["id"]


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


@pytest.mark.parametrize(
    "path", sorted((SCHEMAS / "examples").glob("execution.*.json")), ids=lambda p: p.name
)
def test_execution_examples_validate(path):
    _validator("execution.schema.json").validate(json.loads(path.read_text()))


@pytest.mark.parametrize("block", _doc_yaml_blocks(RULE_DOC, "schema: jarvis.plans/v1"))
def test_design_doc_plan_examples_validate(block):
    _validator("plans.schema.json").validate(yaml.safe_load(block))


@pytest.mark.parametrize(
    "path", sorted(REPO.glob("ros/*/config/**/plans/*.yaml")), ids=lambda p: str(p.relative_to(REPO))
)
def test_shipped_plans_validate(path):
    _validator("plans.schema.json").validate(yaml.safe_load(path.read_text()) or {})


def _plan(**overrides):
    plan = {"id": "ok", "steps": [{"id": "s", "action": "x.y"}]}
    plan.update(overrides)
    return {"schema": "jarvis.plans/v1", "plans": [plan]}


def test_plans_schema_accepts_every_core_action():
    steps = [
        {
            "id": "a",
            "action": "lifecycle.restart",
            "with": {"node": "{{ params.node }}"},
            "retry": {"attempts": 2},
        },
        {
            "id": "b",
            "action": "wait_for",
            "with": {"fault": {"code": "ros.*", "component": "c"}, "state": "CLEARED"},
        },
        {"id": "c", "action": "wait_for", "with": {"event": {"kind": "STATE", "attrs.value": "ACTIVE"}}},
        {"id": "d", "action": "delay", "with": {"duration": "2s"}, "when": {"faults_inactive": ["safety.*"]}},
        {"id": "e", "action": "check", "with": {"state": {"robot.motion": "STOPPED"}}},
        {"id": "f", "action": "emit", "with": {"code": "a.b", "severity": "WARN", "attrs": {"n": 1}}},
        {
            "id": "g",
            "action": "run",
            "with": {"plan": "other", "args": {"x": "y"}},
            "continue_on_failure": True,
        },
    ]
    plan = _plan(
        params={"node": {"type": "string", "required": True, "pattern": "^/"}},
        locks=["node:{{ params.node }}"],
        on_conflict="queue",
        queue_ttl="10s",
        allowed_requesters=["RULE", "plan"],
        requires_approval=["AGENT"],
        on_failure=[{"run": {"plan": "other", "args": {"x": "{{ params.node }}"}}}, {"escalate": "operator"}],
        steps=steps,
    )
    _validator("plans.schema.json").validate(plan)
    _validator("plans.schema.json").validate(_plan())


@pytest.mark.parametrize(
    "bad",
    [
        {"id": "Not_Kebab"},
        {"steps": []},
        {"colour": "red"},
        {"steps": [{"id": "s", "action": "restart"}]},
        {"steps": [{"id": "s", "action": "x.y", "retry": {"attempts": 0}}]},
        {"steps": [{"id": "s", "action": "x.y", "timeout": "0s"}]},
        {"steps": [{"id": "s", "action": "wait_for", "with": {}}]},
        {"steps": [{"id": "s", "action": "wait_for", "with": {"fault": {"code": "a"}, "state": "GONE"}}]},
        {"steps": [{"id": "s", "action": "wait_for", "with": {"event": {"cod": "a"}}}]},
        {"steps": [{"id": "s", "action": "delay", "with": {"seconds": 2}}]},
        {"steps": [{"id": "s", "action": "run", "with": {"plan": "Bad Plan"}}]},
        {"params": {"n": {"type": "float"}}},
        {"params": {"n": {"required": True, "default": "x"}}},
        {"on_conflict": "wait"},
        {"agent_policy": "yolo"},
        {"allowed_requesters": ["ROBOT"]},
        {"on_failure": [{"run": {"plan": "b"}, "escalate": "operator"}]},
        {"max_attempts": {"count": 0, "per": "1h"}},
    ],
)
def test_plans_schema_rejects_mistakes(bad):
    assert list(_validator("plans.schema.json").iter_errors(_plan(**bad))), f"should reject {bad}"


@pytest.mark.parametrize(
    "bad",
    [
        {"state": "DONE"},
        {"requester": {"type": "ROBOT"}},
        {"gates": [{"gate": "vibes", "passed": True}]},
        {"confidence": 1.5},
        {"surprise": True},
    ],
)
def test_execution_schema_rejects_bad_fields(bad):
    execution = json.loads((SCHEMAS / "examples" / "execution.failed.json").read_text())
    execution.update(bad)
    assert list(_validator("execution.schema.json").iter_errors(execution)), f"should reject {bad}"
