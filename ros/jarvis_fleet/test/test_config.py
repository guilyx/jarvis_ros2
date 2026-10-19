# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Rules and plans from the fleet, without ROS: the source, the robot's versions, and each way a
new revision goes (settles, lint, the nodes' own check, the load, a rollback).

With JARVIS_TEST_S3 set to an S3 URI (CI starts moto's S3 server), the source is read from it too."""

import contextlib
import os
import pathlib
import shutil
import uuid

import pyarrow.fs as pafs
import pytest
from jarvis_fleet import config

RULE = """schema: jarvis.rules/v1
rules:
  - id: {id}
    when:
      event: {{ kind: FAULT_STATE, code: sensor.scan.stale, attrs.state: ACTIVE }}
    then:
      - recover: {{ plan: {plan} }}
"""
PLAN = """schema: jarvis.plans/v1
plans:
  - id: {id}
    steps:
      - {{ id: pause, action: delay, with: {{ duration: 1s }} }}
"""


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def robot(tmp_path):
    """The robot's own rules and plans (its launch's), seeded."""
    own = tmp_path / "own"
    write(own / "rules" / "default.yaml", RULE.format(id="own-rule", plan="own-plan"))
    write(own / "plans" / "default.yaml", PLAN.format(id="own-plan"))
    store = config.Store(tmp_path / "data" / "config")
    store.seed(own / "rules", own / "plans")
    return store


@pytest.fixture
def fleet(tmp_path):
    return tmp_path / "fleet"


class Nodes:
    """The rule engine and the recovery orchestrator: what they were asked, what they answer."""

    def __init__(self, store):
        self.store, self.calls, self.loaded = store, [], store.current()
        self.check_errors, self.load_errors, self.unavailable = [], [], False

    def __call__(self, validate_only):
        self.calls.append((validate_only, self.store.current()))
        if self.unavailable:
            raise config.Unavailable("/jarvis/recovery/reload is not answering")
        if validate_only:
            return list(self.check_errors)
        if self.load_errors and self.store.current() != self.loaded:
            return list(self.load_errors)
        self.loaded = self.store.current()
        return []


def passes(directory):
    errors = [
        f"{p.relative_to(directory)}: broken"
        for p in sorted(directory.glob("*/*.yaml"))
        if "broken" in p.read_text()
    ]
    return errors, []


def syncer(store, fleet, nodes, validate=passes, robot="amr-7"):
    return config.Syncer(store, config.Source(f"file://{fleet}", robot), validate, nodes, settle_s=0)


def apply(s):
    """Runs until the revision settled."""
    result = s.run()
    return s.run() if result.state == "settling" else result


def test_a_revision_is_its_files_and_their_content():
    a = {"rules/a.yaml": b"x", "plans/p.yaml": b"y"}
    assert config.revision(a) == config.revision(dict(reversed(a.items())))
    assert config.revision(a) != config.revision({**a, "rules/a.yaml": b"x "})
    assert config.revision(a) != config.revision({"rules/b.yaml": b"x", "plans/p.yaml": b"y"})
    assert len(config.revision(a)) == 12


def test_the_source_has_shared_files_and_the_robot_s_own_over_them(fleet):
    write(fleet / "rules" / "common.yaml", "shared")
    write(fleet / "rules" / "lidar.yaml", "shared lidar")
    write(fleet / "robots" / "amr-7" / "rules" / "lidar.yaml", "amr-7's lidar")
    write(fleet / "robots" / "amr-8" / "rules" / "lidar.yaml", "amr-8's lidar")
    write(fleet / "robots" / "amr-7" / "plans" / "dock.yml", "amr-7's plan")
    write(fleet / "rules" / ".hidden.yaml", "skipped")
    write(fleet / "rules" / "README.md", "skipped")
    write(fleet / "rules" / "with space.yaml", "ignored, said")
    fetched, ignored = config.Source(f"file://{fleet}", "amr-7").read()
    assert fetched == {
        "rules": {"common.yaml": b"shared", "lidar.yaml": b"amr-7's lidar"},
        "plans": {"dock.yml": b"amr-7's plan"},
    }
    assert [pathlib.Path(p).name for p in ignored] == ["with space.yaml"]  # a README is no rule file
    # A robot without its own directory, an empty source.
    assert config.Source(f"file://{fleet}", "amr-9").read()[0]["rules"]["lidar.yaml"] == b"shared lidar"
    assert config.Source(f"file://{fleet}/nothing", "amr-7").read() == ({}, [])


def test_the_source_refuses_what_is_no_rule_file(fleet):
    write(fleet / "rules" / "huge.yaml", "x" * (config.MAX_FILE_BYTES + 1))
    with pytest.raises(ValueError, match="more than a rule file"):
        config.Source(f"file://{fleet}", "amr-7").read()


def test_seeding_makes_the_robot_s_own_the_first_version_and_keeps_a_later_one(tmp_path, robot):
    first = robot.current()
    assert first and (robot.active / "rules" / "default.yaml").is_file()
    assert os.readlink(robot.active) == f"versions/{first}"  # relative: the data dir can move
    assert robot.files(first) == {"rules": ["default.yaml"], "plans": ["default.yaml"]}
    # A version the fleet gave stays active across launches; the base follows the launch.
    other = robot.install({"rules/fleet.yaml": b"r", "plans/fleet.yaml": b"p"})
    robot.switch(other)
    write(tmp_path / "own" / "rules" / "new.yaml", "new")
    robot.seed(tmp_path / "own" / "rules", tmp_path / "own" / "plans")
    assert robot.current() == other
    assert sorted(robot.own()["rules"]) == ["default.yaml", "new.yaml"]


def test_a_new_revision_settles_then_is_checked_then_loaded(robot, fleet):
    nodes = Nodes(robot)
    s = syncer(robot, fleet, nodes)
    assert s.run().state == "empty"  # nothing at the source yet: the robot keeps its own
    first = robot.current()

    write(fleet / "rules" / "lidar.yaml", RULE.format(id="fleet-rule", plan="own-plan"))
    result = s.run()
    assert result.state == "settling" and robot.current() == first and not nodes.calls
    result = s.run()
    assert result.state == "applied", result
    rev = robot.current()
    assert rev != first and f"was {first}" in result.message
    # Checked, then loaded, from the new version: the fleet's file over the robot's own.
    assert nodes.calls == [(True, rev), (False, rev)]
    assert sorted(os.listdir(robot.active / "rules")) == ["default.yaml", "lidar.yaml"]
    assert sorted(os.listdir(robot.active / "plans")) == ["default.yaml"]
    status = s.status()
    assert status["revision"] == rev and status["from_fleet"] == {"rules": ["lidar.yaml"], "plans": []}
    assert status["files"] == {"rules": ["default.yaml", "lidar.yaml"], "plans": ["default.yaml"]}
    assert status["source"] == f"file://{fleet}" and status["state"] == "applied"

    assert s.run().state == "in-sync"
    assert len(nodes.calls) == 2  # nothing changed: nothing reloaded

    # A file of the robot's own name replaces it: an empty one takes it away.
    write(fleet / "rules" / "default.yaml", "schema: jarvis.rules/v1\nrules: []\n")
    assert apply(s).state == "applied"
    assert (robot.active / "rules" / "default.yaml").read_text().endswith("rules: []\n")


def test_a_revision_lint_refuses_is_not_loaded_nor_tried_again(robot, fleet):
    nodes = Nodes(robot)
    s = syncer(robot, fleet, nodes)
    write(fleet / "rules" / "lidar.yaml", RULE.format(id="good", plan="own-plan"))
    assert apply(s).state == "applied"
    good, calls = robot.current(), len(nodes.calls)

    write(fleet / "rules" / "lidar.yaml", "broken")
    result = apply(s)
    assert result.state == "rejected"
    assert result.message.startswith("rules and plans ")
    assert "rules/lidar.yaml: broken" in result.message and result.message.endswith(f"still on {good}")
    assert robot.current() == good and len(nodes.calls) == calls  # the nodes never saw it
    rejected = s.status()["rejected"]
    assert rejected["errors"] == ["rules/lidar.yaml: broken"]

    # The same revision again: refused without a second look, even by a new process.
    again = syncer(robot, fleet, nodes, validate=lambda d: pytest.fail("linted twice"))
    assert again.run().state == "rejected"

    # Fixed at the source: loaded, the rejection forgotten.
    write(fleet / "rules" / "lidar.yaml", RULE.format(id="fixed", plan="own-plan"))
    assert apply(s).state == "applied"
    assert s.status()["rejected"] is None and robot.current() != good


def test_reverting_the_source_to_what_runs_ends_the_rejection(robot, fleet):
    s = syncer(robot, fleet, Nodes(robot))
    write(fleet / "rules" / "lidar.yaml", RULE.format(id="good", plan="own-plan"))
    assert apply(s).state == "applied"
    write(fleet / "rules" / "lidar.yaml", "broken")
    assert apply(s).state == "rejected"
    write(fleet / "rules" / "lidar.yaml", RULE.format(id="good", plan="own-plan"))
    assert s.run().state == "in-sync" and s.status()["rejected"] is None


def test_what_the_nodes_refuse_switches_back_before_anything_loads(robot, fleet):
    nodes = Nodes(robot)
    nodes.check_errors = ["plans: rollout.yaml: plan 'p' step 'r' (container.restart): 'x' not allowed"]
    s = syncer(robot, fleet, nodes)
    first = robot.current()
    write(fleet / "plans" / "rollout.yaml", PLAN.format(id="p"))
    result = apply(s)
    assert result.state == "rejected" and "not allowed" in result.message
    assert robot.current() == first
    assert [validate_only for validate_only, _ in nodes.calls] == [True]


def test_a_failed_load_goes_back_and_loads_the_one_before_again(robot, fleet):
    nodes = Nodes(robot)
    nodes.load_errors = ["rules: rules/lidar.yaml: something only loading finds"]
    s = syncer(robot, fleet, nodes)
    first = robot.current()
    write(fleet / "rules" / "lidar.yaml", RULE.format(id="r", plan="own-plan"))
    result = apply(s)
    assert result.state == "rejected" and robot.current() == first
    new = s.status()["rejected"]["revision"]
    assert nodes.calls == [(True, new), (False, new), (False, first)]
    assert nodes.loaded == first and not s.last_error


def test_nodes_not_answering_keep_the_revision_for_later(robot, fleet):
    nodes = Nodes(robot)
    nodes.unavailable = True
    s = syncer(robot, fleet, nodes)
    first = robot.current()
    write(fleet / "rules" / "lidar.yaml", RULE.format(id="r", plan="own-plan"))
    result = apply(s)
    assert result.state == "waiting" and "not answering" in result.message
    assert robot.current() == first and s.status()["rejected"] is None
    nodes.unavailable = False
    assert apply(s).state == "applied"


def test_offline_the_robot_keeps_what_it_runs(robot, fleet):
    class Offline(config.Source):
        def read(self):
            raise OSError("When reading information for key 'config/rules' in bucket 'fleet': timeout")

    s = config.Syncer(robot, Offline(f"file://{fleet}", "amr-7"), passes, Nodes(robot), settle_s=0)
    first = robot.current()
    result = s.run()
    assert result.state == "offline" and "timeout" in result.message
    assert robot.current() == first and "timeout" in s.status()["last_error"]


def test_a_source_that_changes_while_read_is_not_applied_half_way(robot, fleet):
    nodes = Nodes(robot)
    s = syncer(robot, fleet, nodes)
    write(fleet / "rules" / "lidar.yaml", RULE.format(id="r", plan="new-plan"))
    assert s.run().state == "settling"
    write(fleet / "plans" / "new.yaml", PLAN.format(id="new-plan"))  # the upload goes on
    assert s.run().state == "settling" and not nodes.calls
    assert s.run().state == "applied"
    assert sorted(os.listdir(robot.active / "plans")) == ["default.yaml", "new.yaml"]


def test_old_versions_are_pruned_but_the_active_one_stays(robot, fleet):
    s = syncer(robot, fleet, Nodes(robot))
    for i in range(config.KEEP_VERSIONS + 3):
        write(fleet / "rules" / "lidar.yaml", RULE.format(id=f"r{i}", plan="own-plan"))
        assert apply(s).state == "applied"
    versions = [p.name for p in robot.versions.iterdir() if not p.name.startswith(".")]
    assert len(versions) == config.KEEP_VERSIONS and robot.current() in versions


@pytest.mark.skipif(not shutil.which("jarvis"), reason="the jarvis tool (jarvis_core) is not on PATH")
def test_lint_reports_without_the_version_s_path(robot, tmp_path):
    validate = config.lint(shutil.which("jarvis"), "")
    rev = robot.current()
    errors, warnings = validate(robot.versions / rev)
    assert errors == [] and warnings == [
        "plans/default.yaml: plan 'own-plan' never verifies its effect (no wait_for or check step)"
    ]
    bad = robot.install({"rules/bad.yaml": RULE.format(id="bad", plan="no-such-plan").encode()})
    errors, _ = validate(robot.versions / bad)
    assert errors and errors[0].startswith("rules/bad.yaml: rule 'bad'"), errors
    assert "no-such-plan" in errors[0]


@pytest.mark.skipif(not os.environ.get("JARVIS_TEST_S3"), reason="no S3 (JARVIS_TEST_S3=s3://bucket?...)")
def test_s3(robot):
    """The source in an S3 API (moto's server in CI), credentials in the environment."""
    base = os.environ["JARVIS_TEST_S3"]
    s3, bucket = pafs.FileSystem.from_uri(base)
    with contextlib.suppress(OSError):
        s3.create_dir(bucket.split("/")[0])
    path, _, query = base.partition("?")
    source = f"{path.rstrip('/')}/{uuid.uuid4().hex}?{query}"
    fs, prefix = pafs.FileSystem.from_uri(source)
    for key, text in (
        ("rules/lidar.yaml", RULE.format(id="shared", plan="own-plan")),
        ("robots/amr-7/rules/lidar.yaml", RULE.format(id="amr-7", plan="own-plan")),
        ("robots/amr-7/plans/dock.yaml", PLAN.format(id="dock")),
    ):
        with fs.open_output_stream(f"{prefix}/{key}") as f:
            f.write(text.encode())
    fetched, ignored = config.Source(source, "amr-7").read()
    assert sorted(fetched["rules"]) == ["lidar.yaml"] and b"amr-7" in fetched["rules"]["lidar.yaml"]
    assert sorted(fetched["plans"]) == ["dock.yaml"] and ignored == []
    nodes = Nodes(robot)
    s = config.Syncer(robot, config.Source(source, "amr-7"), passes, nodes, settle_s=0)
    assert apply(s).state == "applied"
    status = s.status()
    assert "secret" not in status["source"]
    assert status["from_fleet"] == {"rules": ["lidar.yaml"], "plans": ["dock.yaml"]}
