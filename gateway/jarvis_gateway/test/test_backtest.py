# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The rule lab: POST /api/v1/rules:backtest lints a draft, replays the history through it with the
robot's other rules, and answers about the draft's rules only. Against a stand-in for `jarvis` (what
it was given, what it said), and against the real one when it is installed."""

import json
import shutil
import stat

import pytest
import yaml
from conftest import ADMIN, OPERATOR
from jarvis_gateway.backtest import _others, rule_ids

DRAFT = """schema: jarvis.rules/v1
rules:
  - id: stale-twice
    description: The same topic went stale twice within a minute.
    partition_by: [source.component]
    when:
      count: { event: { kind: FAULT_RAISE, code: ros.topic.stale }, threshold: 2, within: 1m }
    then:
      - notify: { channel: operators, message: "{{ event.source.component }} keeps going stale" }
"""


def fake_jarvis(tmp, report, lint="0 error(s), 0 warning(s)", lint_code=0):
    """A `jarvis` that records what it was given and answers `report` to backtest."""
    (tmp / "report.json").write_text(json.dumps(report))
    script = tmp / "jarvis"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" > {tmp}/args-$1\n'
        'if [ "$1" = lint ]; then\n'
        f"  printf '{lint}\\n'; exit {lint_code}\n"
        "fi\n"
        'for a in "$@"; do last="$a"; done\n'
        f'cp "$last" {tmp}/events.jsonl\n'
        f'rules=$(echo "$@" | sed "s/.*--rules \\([^ ]*\\).*/\\1/"); cp -r "$rules" {tmp}/given\n'
        f"cat {tmp}/report.json\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_rule_ids_and_the_robots_other_rules(tmp_path):
    assert rule_ids(DRAFT) == ["stale-twice"]
    assert rule_ids("rules: [") == [] and rule_ids("just text") == []
    robot = tmp_path / "robot"
    robot.mkdir()
    (robot / "a.yaml").write_text(
        yaml.safe_dump({"schema": "jarvis.rules/v1", "rules": [{"id": "stale-twice"}, {"id": "keep-me"}]})
    )
    (robot / "b.yaml").write_text(
        yaml.safe_dump({"schema": "jarvis.rules/v1", "rules": [{"id": "stale-twice"}]})
    )
    out = tmp_path / "out"
    out.mkdir()
    assert _others(robot, {"stale-twice", "new-one"}, out) == {"stale-twice"}
    assert sorted(p.name for p in out.iterdir()) == ["robot-a.yaml"], "b.yaml had only the redefined rule"
    assert yaml.safe_load((out / "robot-a.yaml").read_text())["rules"] == [{"id": "keep-me"}]


def test_a_draft_is_replayed_with_the_robots_rules(make_gateway, tmp_path):
    robot = tmp_path / "rules"
    robot.mkdir()
    (robot / "core.yaml").write_text(
        yaml.safe_dump(
            {"schema": "jarvis.rules/v1", "rules": [{"id": "stale-twice"}, {"id": "escalation-notify"}]}
        )
    )
    report = {
        "events": 3,
        "from": "2026-10-19T01:00:00Z",
        "to": "2026-10-19T01:30:00Z",
        "matches": [
            {"at": "2026-10-19T01:10:00Z", "rule": "stale-twice", "match_id": "m1", "cause_id": "CAUSE"},
            {"at": "2026-10-19T01:11:00Z", "rule": "escalation-notify", "match_id": "m2", "cause_id": "x"},
        ],
        "recoveries": [],
        "rules": [
            {
                "rule": "stale-twice",
                "fires": 1,
                "dry_fires": 0,
                "suppressed": 0,
                "cooled_down": 2,
                "guard_blocked": 0,
            },
            {
                "rule": "escalation-notify",
                "fires": 1,
                "dry_fires": 0,
                "suppressed": 0,
                "cooled_down": 0,
                "guard_blocked": 0,
            },
        ],
        "comparison": {
            "recorded": 4,
            "reproduces": False,
            "missing": ["stale-twice <- CAUSE", "stale-twice <- chained", "lidar-stale-restart <- e1"],
            "extra": [],
        },
    }
    h = make_gateway(
        jarvis_bin=fake_jarvis(tmp_path, report), lint_rules_dir=str(robot), lint_plans_dir="/plans"
    )
    raised = h.do(h.fake.raise_fault, "ros.topic.stale", "lidar")
    cause = h.wait(
        lambda: next(
            (
                e
                for e in h.get("/api/v1/events", limit=50)["events"]
                if e["code"] == "ros.topic.stale" and e["kind"] == "FAULT_RAISE"
            ),
            None,
        )
    )
    report["matches"][0]["cause_id"] = cause["id"]
    report["comparison"]["missing"][0] = f"stale-twice <- {cause['id']}"
    (tmp_path / "report.json").write_text(json.dumps(report))
    assert raised

    r = h.client.post("/api/v1/rules:backtest", json={"yaml": DRAFT, "hours": 2}, headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] and body["events"] == 3 and body["from"] == "2026-10-19T01:00:00Z"
    # Only the draft's rules: the robot's others ran too, but are not what was asked about.
    assert [m["rule"] for m in body["matches"]] == ["stale-twice"] and body["match_count"] == 1
    assert body["matches"][0]["cause"]["code"] == "ros.topic.stale"
    assert body["matches"][0]["cause"]["component"] == "lidar"
    assert [s["rule"] for s in body["rules"]] == ["stale-twice"] and body["rules"][0]["cooled_down"] == 2
    # Compared with what the robot's own stale-twice decided live.
    assert body["replaces"] == ["stale-twice"]
    assert [(d["rule"], d["cause_id"]) for d in body["missing"]] == [
        ("stale-twice", cause["id"]),
        ("stale-twice", ""),
    ]
    assert body["missing"][0]["cause"]["component"] == "lidar" and body["missing"][1]["cause"] is None
    assert body["extra"] == []

    # What jarvis was given: the history, the draft, and the robot's other rules without the redefined one.
    args = (tmp_path / "args-backtest").read_text()
    assert "--plans /plans" in args and "--json" in args
    given = sorted((tmp_path / "given").iterdir())
    assert [p.name for p in given] == ["draft.yaml", "robot-core.yaml"]
    ids = [r["id"] for p in given for r in yaml.safe_load(p.read_text())["rules"]]
    assert sorted(ids) == ["escalation-notify", "stale-twice"], "one stale-twice: the draft's"
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert any(json.loads(line)["id"] == cause["id"] for line in lines)


def test_a_draft_with_errors_is_not_replayed(make_gateway, tmp_path):
    lint = "error: candidate.yaml: rule stale-twice: unknown plan nope\\n1 error(s), 0 warning(s)"
    h = make_gateway(jarvis_bin=fake_jarvis(tmp_path, {}, lint=lint, lint_code=1))
    body = h.client.post("/api/v1/rules:backtest", json={"yaml": DRAFT}, headers=ADMIN).json()
    assert body["success"] is False and body["message"] == "1 error(s), 0 warning(s)"
    assert body["findings"][0]["level"] == "error" and body["matches"] == []
    assert not (tmp_path / "args-backtest").exists()


def test_who_may_backtest_and_limits(make_gateway, tmp_path):
    h = make_gateway(jarvis_bin=fake_jarvis(tmp_path, {}))
    assert h.client.post("/api/v1/rules:backtest", json={"yaml": DRAFT}, headers=OPERATOR).status_code == 403
    assert (
        h.client.post("/api/v1/rules:backtest", json={"yaml": DRAFT, "hours": 500}, headers=ADMIN).status_code
        == 422
    )
    broken = make_gateway(jarvis_bin=fake_jarvis(tmp_path, {}))
    (tmp_path / "report.json").write_text("not json")
    r = broken.client.post("/api/v1/rules:backtest", json={"yaml": DRAFT}, headers=ADMIN)
    assert r.status_code == 503
    assert (
        make_gateway(jarvis_bin="/nonexistent/jarvis")
        .client.post("/api/v1/rules:backtest", json={"yaml": DRAFT}, headers=ADMIN)
        .status_code
        == 503
    )


JARVIS = shutil.which("jarvis")


@pytest.mark.skipif(not JARVIS, reason="the jarvis tool is built with jarvis_core (the ROS job has it)")
def test_the_real_backtest_over_the_gateways_history(make_gateway):
    h = make_gateway(jarvis_bin=JARVIS)
    for _ in range(3):
        f = h.do(h.fake.raise_fault, "ros.topic.stale", "lidar")
        h.do(h.fake.heal, f["fault_id"])
    h.do(h.fake.raise_fault, "ros.topic.stale", "camera")
    h.wait(
        lambda: (
            len(
                [
                    e
                    for e in h.get("/api/v1/events", limit=100)["events"]
                    if e["code"] == "ros.topic.stale" and e["kind"] == "FAULT_RAISE"
                ]
            )
            == 4
        )
    )
    body = h.client.post("/api/v1/rules:backtest", json={"yaml": DRAFT, "hours": 1}, headers=ADMIN).json()
    assert body["success"], body
    assert body["events"] > 0
    # The lidar went stale three times: the second raise completes the count (threshold 2); the
    # camera once: not enough.
    assert body["match_count"] >= 1
    assert {m["cause"]["component"] for m in body["matches"]} == {"lidar"}
    # A new rule: nothing live to compare with.
    assert body["replaces"] == [] and body["extra"] == [] and body["missing"] == []
