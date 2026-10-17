# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Runs the corpus through the agent as it ships: the replay adapter (no gateway, no ROS), the
configured analyst, the harness checks and the autonomy policy, without a recovery client (what it
would propose stays advice, and is graded as such). The clock is the case's `at`.

One budget spans the run: past `max_cost_usd` the analyst wraps up, and the cases left are skipped
(a capped run is not comparable). Its row of evals/results.tsv is how a prompt or model change
shows that the score holds.
"""

import dataclasses
import datetime
import os
import pathlib
import subprocess
import time

from jarvis_agent.adapters.replay import ReplayQuery
from jarvis_agent.core.agent import Agent
from jarvis_agent.core.policy import AutonomyPolicy
from jarvis_agent.core.windows import WindowManager
from jarvis_agent.evals.graders import GRADERS, Grade, grade, score
from jarvis_agent.llm.heuristic import HeuristicAnalyst

DEFAULT_CORPUS = pathlib.Path(__file__).resolve().parents[2] / "evals" / "corpus"
COLUMNS = ("date", "rev", "analyst", "judge", "cases", "score", *GRADERS, "fallbacks", "cost_usd", "note")
SHORT = {
    "root_cause": "cause",
    "evidence": "evid",
    "must_cite": "cite",
    "plans": "plan",
    "escalation": "esc",
    "assessment": "sev",
}


class _Observed:
    """The analyst, watched: what it answered before the harness checks, and the window it had."""

    def __init__(self, inner):
        self.inner = inner
        self.raw = self.window = None

    def analyze(self, window, result, progress=None):
        self.window = window
        self.raw = self.inner.analyze(window, result, progress=progress)
        return self.raw


@dataclasses.dataclass
class Result:
    case: str
    score: float | None = None  # None: skipped
    grades: dict = dataclasses.field(default_factory=dict)  # grader -> Grade
    analyst: str = ""  # who answered: heuristic or llm
    cost_usd: float = 0.0
    latency_s: float = 0.0
    skipped: str = ""
    notes: list = dataclasses.field(default_factory=list)
    insight: dict | None = None

    def to_json(self):
        out = dataclasses.asdict(self)
        out["grades"] = {k: dataclasses.asdict(g) for k, g in self.grades.items()}
        return out


@dataclasses.dataclass
class Report:
    results: list
    analyst: str  # the model, or "heuristic"
    judge: str = ""
    max_cost_usd: float = 0.0

    @property
    def ran(self):
        return [r for r in self.results if not r.skipped]

    @property
    def capped(self):
        return any(r.skipped for r in self.results)

    @property
    def cost_usd(self):
        return round(sum(r.cost_usd for r in self.results), 6)

    @property
    def fallbacks(self):
        """Cases a model run left to the heuristic analyst."""
        return 0 if self.analyst == "heuristic" else sum(1 for r in self.ran if r.analyst != "llm")

    def mean(self, grader=None):
        """The mean score over the cases that ran (of one grader: over the cases it applies to)."""
        if grader is None:
            values = [r.score for r in self.ran]
        else:
            values = [r.grades[grader].score for r in self.ran if r.grades[grader].score is not None]
        return round(sum(values) / len(values), 4) if values else None

    def to_json(self, rev=""):
        return {
            "schema": "jarvis.eval-report/v1",
            "date": _now(),
            "rev": rev,
            "analyst": self.analyst,
            "judge": self.judge,
            "max_cost_usd": self.max_cost_usd,
            "cost_usd": self.cost_usd,
            "score": self.mean(),
            "graders": {g: self.mean(g) for g in GRADERS},
            "cases": [r.to_json() for r in self.results],
        }


def run_case(case, analyst_for, config, judge=None):
    """One case: analyze, judge (optional), grade."""
    query = ReplayQuery(case.events, case.plans)
    observed = _Observed(analyst_for(query) or HeuristicAnalyst())
    agent = Agent(
        query,
        analyst=observed,
        policy=AutonomyPolicy(config.level, config.min_confidence),
        redact=config.redact,
        windows=WindowManager(query, context_s=config.context_s, sliding_s=config.sliding_s),
        clock=lambda: case.at_ns or query.now_ns(),
    )
    started = time.monotonic()
    insight = agent.analyze(case.trigger, question=case.question)
    latency = round(time.monotonic() - started, 3)
    if insight is None:  # the corpus checks make this unlikely: the incident is in the recording
        grades = {g: Grade(0.0, "no insight") for g in GRADERS}
        return Result(case.name, 0.0, grades, latency_s=latency, notes=["no window for the trigger"])
    cost = insight.model.cost_usd if insight.model else 0.0
    notes, judged = [], None
    if judge is not None:
        judged, spent = judge(case, insight)
        cost += spent
        if judged is None:
            notes.append(f"{judge.name} did not grade: the rules graded the root cause")
    grades = grade(case, insight, observed.raw, observed.window.ids, judged)
    return Result(
        case.name,
        score(grades),
        grades,
        analyst=insight.analyst,
        cost_usd=round(cost, 6),
        latency_s=latency,
        notes=notes + [f for f in insight.flags if "heuristic analyst answered" in f],
        insight=insight.to_json(),
    )


def run(cases, analyst_for, config, judge=None, max_cost_usd=2.0, analyst="heuristic", progress=None):
    """Every case, in order, until the spend reaches `max_cost_usd` (0: no cap)."""
    results, spent = [], 0.0
    for case in cases:
        if max_cost_usd and spent >= max_cost_usd:
            results.append(Result(case.name, skipped=f"the cost cap (${max_cost_usd:.2f}) was reached"))
            continue
        r = run_case(case, analyst_for, config, judge)
        spent += r.cost_usd
        results.append(r)
        if progress is not None:
            progress(r)
    return Report(results, analyst, judge.name if judge else "", max_cost_usd)


def render(report):
    """The run as a table, with what each case missed under it."""
    head = f"{'case':<30} {'score':>5}  " + " ".join(f"{SHORT[g]:>5}" for g in GRADERS)
    lines = [head + f"  {'analyst':<9} {'cost':>8}"]
    for r in report.results:
        if r.skipped:
            lines.append(f"{r.case:<30} {'-':>5}  skipped: {r.skipped}")
            continue
        cells = " ".join(_cell(r.grades[g].score) for g in GRADERS)
        lines.append(f"{r.case:<30} {r.score:5.2f}  {cells}  {r.analyst:<9} {_usd(r.cost_usd)}")
        lines += [f"    {SHORT[g]}: {r.grades[g].note}" for g in GRADERS if _missed(r.grades[g])]
        lines += [f"    {n}" for n in r.notes]
    means = " ".join(_cell(report.mean(g)) for g in GRADERS)
    total = report.mean()
    lines.append(
        f"{f'mean ({len(report.ran)} of {len(report.results)})':<30} {_cell(total)}  {means}"
        f"  {'':<9} {_usd(report.cost_usd)}"
    )
    who = report.analyst + (f", judged by {report.judge}" if report.judge else "")
    fallbacks = f", {report.fallbacks} answered by the heuristic analyst" if report.fallbacks else ""
    return "\n".join(lines + [f"analyst {who}{fallbacks}"])


def row(report, rev="", note=""):
    """The run as a row of results.tsv."""
    total = report.mean()
    out = {
        "date": _now(),
        "rev": rev or "-",
        "analyst": report.analyst,
        "judge": report.judge or "-",
        "cases": f"{len(report.ran)}/{len(report.results)}",
        "score": "-" if total is None else f"{total:.3f}",
        "fallbacks": str(report.fallbacks),
        "cost_usd": f"{report.cost_usd:.4f}",
        "note": " ".join(note.split()) or "-",
    }
    for g in GRADERS:
        m = report.mean(g)
        out[g] = "-" if m is None else f"{m:.3f}"
    return out


def append_tsv(path, values):
    path = pathlib.Path(path)
    header = "\t".join(COLUMNS)
    if path.exists() and path.stat().st_size:
        first = path.read_text().splitlines()[0]
        if first != header:
            raise ValueError(f"{path} has other columns than {', '.join(COLUMNS)}")
        prefix = ""
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix = header + "\n"
    with path.open("a") as f:
        f.write(prefix + "\t".join(values[c] for c in COLUMNS) + "\n")


def revision():
    """The commit evaluated (CI's, or the checkout's; -dirty with local changes), or ""."""
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"][:7]
    try:
        out = subprocess.run(
            ["git", "describe", "--always", "--dirty", "--abbrev=7"],
            cwd=pathlib.Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def _usd(value):
    return f"{'$' + format(value, '.4f'):>8}"


def _cell(value):
    return f"{'-':>5}" if value is None else f"{value:5.2f}"


def _missed(g):
    return g.score is not None and g.score < 1 and g.note


def _now():
    return datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%MZ")
