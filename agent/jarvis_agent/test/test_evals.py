# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Evals (milestone 4.5): the corpus, the graders, the judge, the runner, `jarvis-agent eval`; and
the heuristic analyst's baseline, which a change may only hold or improve."""

import json
import pathlib

import pytest
from jarvis_agent import cli
from jarvis_agent.config import AgentConfig
from jarvis_agent.core.analyst import LLMAnalyst
from jarvis_agent.evals import corpus, graders, runner
from jarvis_agent.llm.base import ModelUnavailable
from jarvis_agent.llm.fake import FakeLLM, call, turn
from jarvis_agent.model import Hypothesis, Insight, Recommendation, Trigger, TriggerType, Window

CORPUS = pathlib.Path(__file__).resolve().parents[1] / "evals" / "corpus"
STALL_CASE = "lidar-stall-restart-fails"
FAULT = "01a14a41-3bf2-7236-93f2-a3de1c82e354"  # the stall's sensor.scan.stale ACTIVE
FAILED = "01a14a41-76a6-7921-8765-e90daf855394"  # its restart-lifecycle-node FAILED
WEDGE_FAULT = "01a14a41-27c0-7892-b854-e5393c563854"

# The heuristic analyst on the corpus, case by case: what it scores now. Better is fine (raise the
# numbers); worse fails.
BASELINE = {
    "estop-latched": 0.8947,
    "estop-pressed-and-cleared": 1.0,
    "lidar-stall-restart-fails": 1.0,
    "motor-overcurrent": 0.9079,
    "node-logs-errors": 0.9079,
    "relay-wedge-again": 0.9417,
    "relay-wedge-restart-fixes": 0.9125,
    "what-happened-to-the-lidar": 0.6689,
    "why-did-it-stop": 0.5132,
}


@pytest.fixture(scope="module")
def cases():
    return {c.name: c for c in corpus.load(CORPUS)}


def insight(hypotheses=(), recommendations=(), assessment="ERROR", **kw):
    return Insight(
        trigger=Trigger(type=TriggerType.RECOVERY_FAILED, ref="x"),
        window=Window(**{"from": "2026-10-17T14:25:51.000000000Z"}, to="2026-10-17T14:26:11Z", event_count=1),
        title=kw.pop("title", "sensor.scan.stale on lidar"),
        assessment=assessment,
        hypotheses=[Hypothesis(**h) for h in hypotheses],
        recommendations=[Recommendation(**r) for r in recommendations],
        **kw,
    )


# -- the corpus ------------------------------------------------------------------------------------


def test_the_corpus_names_only_what_its_recordings_hold(cases):
    assert sorted(cases) == sorted(BASELINE)
    for case in cases.values():
        assert corpus.check(case) == []
        assert case.label.must_cite and case.label.root_cause.mentions
        assert not [e for e in case.events if e["code"].startswith("demo.tour.")]  # the narration
        assert case.plans and case.plans[0]["id"] == "restart-lifecycle-node"


def test_a_case_is_cut_where_the_live_agent_would_analyze(cases):
    from jarvis_agent.events import ns

    stall = cases[STALL_CASE]
    assert stall.trigger == Trigger(type=TriggerType.RECOVERY_FAILED, ref=stall.label.trigger.incident)
    assert max(ns(e["stamp"]) for e in stall.events) <= stall.at_ns
    later = cases["estop-latched"].label.trigger.incident
    assert not [e for e in stall.events if e.get("correlation_id") == later]  # its future
    assert cases["why-did-it-stop"].question == "Why did the robot stop?"


def test_a_label_must_match_its_recording(tmp_path, cases):
    stall = cases[STALL_CASE]
    folder = tmp_path / "bad"
    folder.mkdir()
    (folder / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in stall.events))
    (folder / "plans.json").write_text(json.dumps({"plans": stall.plans}))
    label = {
        "schema": "jarvis.eval/v1",
        "trigger": {"type": "RECOVERY_FAILED"},
        "root_cause": {"code": "power.*", "text": "the battery"},
        "must_cite": [["nope"], []],
        "expect": {"plans": {"forbidden": ["reboot"], "required": True}},
    }
    (folder / "label.yaml").write_text(json.dumps(label))  # JSON is YAML
    with pytest.raises(corpus.CorpusError) as e:
        corpus.load_case(folder)
    for problem in (
        "a RECOVERY_FAILED trigger needs an incident",
        "must_cite: no event nope",
        "must_cite: an empty group",
        "expect.plans: no plan reboot in the catalog",
        "required without an acceptable plan",
        "root_cause.code: no power.* event",
    ):
        assert problem in str(e.value)

    (folder / "label.yaml").write_text(json.dumps({**label, "schema": "jarvis.eval/v0"}))
    with pytest.raises(corpus.CorpusError, match="not jarvis.eval/v1"):
        corpus.load_case(folder)
    (folder / "label.yaml").write_text(json.dumps({**label, "surprise": 1}))
    with pytest.raises(corpus.CorpusError, match="surprise"):
        corpus.load_case(folder)
    with pytest.raises(corpus.CorpusError, match="no case nope"):
        corpus.load(CORPUS, ["nope"])
    with pytest.raises(corpus.CorpusError, match="no cases in"):
        corpus.load(tmp_path / "empty")


# -- the graders -----------------------------------------------------------------------------------


def test_evidence_counts_what_the_analyst_was_never_shown(cases):
    case = cases[STALL_CASE]
    seen = {FAULT, FAILED}
    raw = insight([{"cause": "x", "confidence": 0.5, "evidence": [FAULT, WEDGE_FAULT, "made-up", FAILED]}])
    g = graders.evidence(case, raw, seen)
    assert g.score == 0.5
    assert g.note == "2 of 4 cited id(s) it was never shown (1 not events at all)"
    assert graders.evidence(case, insight(), seen) == graders.Grade(0.0, "cites nothing")


def test_must_cite_wants_one_event_of_each_group(cases):
    case = cases[STALL_CASE]
    step_failed = case.label.must_cite[1][0]
    one = insight([{"cause": "x", "confidence": 0.5, "evidence": [FAULT]}])
    both = insight(
        [{"cause": "x", "confidence": 0.5, "evidence": [FAULT], "counter_evidence": [step_failed]}]
    )
    assert graders.must_cite(case, one).score == 0.5
    assert graders.must_cite(case, one).note == f"missed 1 of 2: {step_failed}"
    assert graders.must_cite(case, both).score == 1.0


def test_plans_escalation_and_assessment(cases):
    stall, wedge = cases[STALL_CASE], cases["relay-wedge-again"]
    restart = {"kind": "RECOVER", "text": "restart it", "plan_id": "restart-lifecycle-node"}
    escalate = {"kind": "ESCALATE", "text": "call someone"}
    assert graders.plans(stall, insight(recommendations=[restart])) == graders.Grade(
        0.0, "recommends restart-lifecycle-node, which is wrong here"
    )
    assert graders.plans(stall, insight()).score == 1.0
    assert graders.plans(wedge, insight(recommendations=[restart])).score == 1.0
    wedge_required = wedge.label.model_copy(deep=True)
    wedge_required.expect.plans.required = True
    required = corpus.Case(wedge.name, wedge_required, wedge.events, wedge.plans, wedge.at_ns)
    assert graders.plans(required, insight()).note == "recommends none of restart-lifecycle-node"
    other = cases["what-happened-to-the-lidar"]  # nothing to run: a recovery is unexpected, not wrong
    assert graders.plans(other, insight(recommendations=[restart])).score == 0.5

    assert graders.escalation(stall, insight(recommendations=[escalate])).score == 1.0
    assert graders.escalation(stall, insight()) == graders.Grade(0.0, "does not escalate")
    assert graders.escalation(wedge, insight(recommendations=[escalate])) == graders.Grade(0.0, "escalates")
    assert graders.escalation(cases["motor-overcurrent"], insight()).score is None

    assert graders.assessment(stall, insight(assessment="CRITICAL")).score == 1.0
    warn = graders.assessment(stall, insight(assessment="WARN"))
    assert warn.note == "assesses WARN, not ERROR or CRITICAL"


def test_the_root_cause_is_where_the_top_hypothesis_points_and_what_it_says(cases):
    case = cases[STALL_CASE]
    right = {"cause": "The lidar itself stopped, upstream of the relay", "confidence": 0.7}
    right["evidence"] = [FAULT]
    wrong = {"cause": "The relay hangs", "confidence": 0.4, "evidence": [FAILED]}
    g = graders.root_cause(case, insight([right, wrong], [{"kind": "ESCALATE", "text": "operator"}]))
    assert (g.score, g.note) == (1.0, "points at sensor.scan.stale on lidar")

    g = graders.root_cause(case, insight([{**right, "evidence": [FAILED]}]))  # right words, wrong event
    assert g.score == 0.25
    assert "its top hypothesis cites no sensor.scan.stale on lidar event" in g.note
    assert "never says escalat|operator|human" in g.note

    g = graders.root_cause(case, insight([right]), judged=(0.5, "vague on the mechanism"))
    assert g.score == 0.75
    assert g.note == "points at sensor.scan.stale on lidar; judge 0.5: vague on the mechanism"


def test_the_score_weighs_what_applies():
    grades = {k: graders.Grade(None) for k in graders.GRADERS}
    assert graders.score(grades) == 0.0
    grades["plans"] = graders.Grade(1.0)
    grades["evidence"] = graders.Grade(0.0)
    assert graders.score(grades) == pytest.approx(0.2 / 0.35, abs=1e-4)
    assert sum(graders.WEIGHTS.values()) == pytest.approx(1.0)


# -- the judge -------------------------------------------------------------------------------------


def test_the_judge_grades_with_a_rubric(cases):
    case = cases[STALL_CASE]
    answer = insight([{"cause": "The lidar stopped", "confidence": 0.7, "evidence": [FAULT]}])
    llm = FakeLLM([turn(call("grade", score=2, reason="same cause"), cost_usd=0.002)], name="judge")
    assert graders.Judge(llm)(case, answer) == ((1.0, "same cause"), 0.002)
    (sent,) = llm.calls
    assert sent["tool_choice"] == graders.FORCE_GRADE and sent["tools"] == [graders.GRADE]
    assert sent["messages"][0]["content"] == graders.JUDGE
    user = sent["messages"][1]["content"]
    assert case.label.root_cause.text in user and "1. [0.70] The lidar stopped" in user

    for step in (turn(call("grade", score=3, reason="?")), turn(call("grade", reason="?")), turn(text="2")):
        assert graders.Judge(FakeLLM([step]))(case, answer)[0] is None
    assert graders.Judge(FakeLLM([ModelUnavailable("down")]))(case, answer) == (None, 0.0)


# -- the runner ------------------------------------------------------------------------------------


def test_the_heuristic_baseline_holds(cases):
    report = runner.run(list(cases.values()), lambda query: None, AgentConfig())
    scores = {r.case: r.score for r in report.results}
    worse = {k: (scores[k], v) for k, v in BASELINE.items() if scores[k] < v - 1e-4}
    assert not worse, f"the heuristic analyst got worse on {worse}"
    assert report.mean("evidence") == 1.0  # it only cites what it was shown
    assert report.mean() >= 0.86 and report.cost_usd == 0 and report.fallbacks == 0
    assert all(r.analyst == "heuristic" and r.insight["analyst"] == "heuristic" for r in report.results)


def test_a_model_run_is_graded_on_what_the_model_said_and_capped(cases):
    stall, question = cases[STALL_CASE], cases["why-did-it-stop"]
    submit = call(
        "submit_insight",
        title="The relay hangs",
        summary="Restart it",
        assessment="ERROR",
        hypotheses=[{"cause": "The relay hangs", "confidence": 0.6, "evidence": [FAULT, "made-up"]}],
        recommendations=[{"kind": "RECOVER", "text": "Restart it", "plan_id": "restart-lifecycle-node"}],
    )
    judge = graders.Judge(FakeLLM([turn(call("grade", score=0, reason="wrong cause"), cost_usd=0.001)]))
    llms = []

    def analyst_for(query):
        llms.append(FakeLLM([turn(submit, cost_usd=0.01)]))
        return LLMAnalyst(llms[-1], query)

    report = runner.run(
        [stall, question], analyst_for, AgentConfig(), judge=judge, max_cost_usd=0.005, analyst="fake"
    )
    first, second = report.results
    assert first.analyst == "llm" and first.cost_usd == pytest.approx(0.011)
    assert first.grades["evidence"].score == 0.5  # "made-up" is dropped by the checks, not here
    assert first.grades["plans"].score == 0.0
    assert first.grades["root_cause"].score == 0.5  # points at the fault, the judge says 0
    assert first.grades["escalation"].note == "does not escalate"
    assert second.skipped == "the cost cap ($0.01) was reached" and second.score is None
    assert report.capped and len(llms) == 1 and report.mean() == first.score

    text = runner.render(report)
    assert "plan: recommends restart-lifecycle-node, which is wrong here" in text
    assert "skipped: the cost cap" in text and "judged by fake" in text


def test_a_judge_that_cannot_grade_leaves_it_to_the_rules(cases):
    judge = graders.Judge(FakeLLM([ModelUnavailable("down")], name="judge"))
    r = runner.run_case(cases["estop-latched"], lambda query: None, AgentConfig(), judge)
    assert r.notes == ["judge did not grade: the rules graded the root cause"]
    assert r.grades["root_cause"].score == 1.0


def test_results_tsv_rows(tmp_path, cases):
    report = runner.run([cases["estop-latched"]], lambda query: None, AgentConfig())
    tsv = tmp_path / "results.tsv"
    runner.append_tsv(tsv, runner.row(report, "abc1234", "the\tbaseline\n"))
    runner.append_tsv(tsv, runner.row(report))
    header, first, second = [line.split("\t") for line in tsv.read_text().splitlines()]
    assert tuple(header) == runner.COLUMNS
    row = dict(zip(header, first, strict=True))
    assert (row["rev"], row["analyst"], row["judge"], row["cases"]) == ("abc1234", "heuristic", "-", "1/1")
    assert (row["score"], row["escalation"], row["note"]) == ("0.895", "-", "the baseline")
    assert dict(zip(header, second, strict=True))["rev"] == "-"
    tsv.write_text("other\tcolumns\n")
    with pytest.raises(ValueError, match="other columns"):
        runner.append_tsv(tsv, runner.row(report))


def test_the_cli(tmp_path, capsys):
    assert runner.DEFAULT_CORPUS == CORPUS or not runner.DEFAULT_CORPUS.exists()  # an installed copy
    tsv, report = tmp_path / "results.tsv", tmp_path / "report.json"
    args = [
        "--heuristic",
        "eval",
        "--corpus",
        str(CORPUS),
        "--case",
        "estop-latched",
        "--case",
        "why-did-it-stop",
    ]
    assert cli.main([*args, "--results", str(tsv), "--report", str(report), "--note", "x"]) == 0
    out = capsys.readouterr()
    assert "estop-latched" in out.out and "mean (2 of 2)" in out.out
    assert "why-did-it-stop: 0.51 (heuristic, $0.0000)" in out.err
    assert len(tsv.read_text().splitlines()) == 2
    written = json.loads(report.read_text())
    assert written["schema"] == "jarvis.eval-report/v1" and written["analyst"] == "heuristic"
    assert [c["case"] for c in written["cases"]] == ["estop-latched", "why-did-it-stop"]
    assert written["cases"][0]["insight"]["analyst"] == "heuristic"

    assert cli.main([*args, "--min-score", "0.99"]) == 1
    assert "is below 0.990" in capsys.readouterr().err
    assert cli.main(["--heuristic", "eval", "--corpus", str(tmp_path)]) == 2
    assert "no cases in" in capsys.readouterr().err
