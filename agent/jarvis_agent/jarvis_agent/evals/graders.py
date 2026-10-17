# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Graders: one insight against its case's label, each a score in [0, 1] (None: not asked).

    root_cause   its most confident hypothesis cites an event of the labelled fault, and says
                 what the label says: the label's `mentions` (rules), or an LLM judge (rubric)
    evidence     what the analyst cited, before the harness checks, it had been shown
    must_cite    the evidence a good answer rests on: one event of each group
    plans        no forbidden plan recommended; an acceptable one when one is required
    escalation   an ESCALATE recommendation when, and only when, the label wants one
    assessment   the severity it gives is one the label accepts

The score of a case is the weighted mean of the graders that apply.
"""

import dataclasses
import fnmatch
import json

from jarvis_agent.events import component
from jarvis_agent.llm.base import ModelUnavailable

WEIGHTS = {
    "root_cause": 0.35,
    "evidence": 0.15,
    "must_cite": 0.2,
    "plans": 0.2,
    "escalation": 0.05,
    "assessment": 0.05,
}
GRADERS = tuple(WEIGHTS)


@dataclasses.dataclass
class Grade:
    score: float | None  # None: the label does not ask
    note: str = ""


def grade(case, insight, raw, seen, judged=None):
    """Every grade of `insight` (as checked) for `case`. `raw` is the analyst's own insight, before
    the harness checks; `seen` the event ids it was shown; `judged` the judge's (score, reason)."""
    return {
        "root_cause": root_cause(case, insight, judged),
        "evidence": evidence(case, raw, seen),
        "must_cite": must_cite(case, insight),
        "plans": plans(case, insight),
        "escalation": escalation(case, insight),
        "assessment": assessment(case, insight),
    }


def score(grades):
    """The weighted mean of the grades that apply."""
    applied = {k: g.score for k, g in grades.items() if g.score is not None}
    weight = sum(WEIGHTS[k] for k in applied)
    return round(sum(WEIGHTS[k] * s for k, s in applied.items()) / weight, 4) if weight else 0.0


def answer_text(insight):
    """What the insight says, for the mentions: title, summary, causes, recommendations."""
    parts = [insight.title, insight.summary]
    parts += [h.cause for h in insight.hypotheses] + [r.text for r in insight.recommendations]
    return "\n".join(parts).lower()


def root_cause(case, insight, judged=None):
    rc = case.label.root_cause
    by_id = {e["id"]: e for e in case.events}
    top = insight.hypotheses[0] if insight.hypotheses else None
    where = f"{rc.code}{' on ' + rc.component if rc.component else ''}"
    points = top is not None and any(_is(by_id.get(i), rc) for i in top.evidence)
    parts = [1.0 if points else 0.0]
    notes = [f"points at {where}" if points else f"its top hypothesis cites no {where} event"]
    if judged is not None:
        parts.append(judged[0])
        notes.append(f"judge {judged[0]:.1f}: {judged[1]}")
    elif rc.mentions:
        text = answer_text(insight)
        missing = [g for g in rc.mentions if not any(w.lower() in text for w in g)]
        parts.append(1 - len(missing) / len(rc.mentions))
        if missing:
            notes.append("never says " + " / ".join("|".join(g) for g in missing))
    return Grade(round(sum(parts) / len(parts), 4), "; ".join(notes))


def evidence(case, raw, seen):
    cited = [i for h in raw.hypotheses for i in h.evidence + h.counter_evidence]
    if not cited:
        return Grade(0.0, "cites nothing")
    unseen = [i for i in cited if i not in seen]
    if not unseen:
        return Grade(1.0)
    recorded = {e["id"] for e in case.events}
    invented = sum(1 for i in unseen if i not in recorded)
    note = f"{len(unseen)} of {len(cited)} cited id(s) it was never shown ({invented} not events at all)"
    return Grade(round(1 - len(unseen) / len(cited), 4), note)


def must_cite(case, insight):
    groups = case.label.must_cite
    if not groups:
        return Grade(None)
    cited = {i for h in insight.hypotheses for i in h.evidence + h.counter_evidence}
    missed = [g for g in groups if not cited & set(g)]
    note = f"missed {len(missed)} of {len(groups)}: {', '.join(g[0] for g in missed)}" if missed else ""
    return Grade(round(1 - len(missed) / len(groups), 4), note)


def plans(case, insight):
    want = case.label.expect.plans
    recommended = [r.plan_id for r in insight.recommendations if r.kind == "RECOVER" and r.plan_id]
    if bad := [p for p in recommended if p in want.forbidden]:
        return Grade(0.0, f"recommends {bad[0]}, which is wrong here")
    if want.required and not any(p in want.acceptable for p in recommended):
        return Grade(0.0, f"recommends none of {', '.join(want.acceptable)}")
    if other := [p for p in recommended if p not in want.acceptable]:
        return Grade(0.5, f"recommends {other[0]}, which the label does not expect")
    return Grade(1.0)


def escalation(case, insight):
    want = case.label.expect.escalate
    if want is None:
        return Grade(None)
    got = any(r.kind == "ESCALATE" for r in insight.recommendations)
    if got == want:
        return Grade(1.0)
    return Grade(0.0, "escalates" if got else "does not escalate")


def assessment(case, insight):
    want = case.label.expect.assessment
    if not want:
        return Grade(None)
    if insight.assessment in want:
        return Grade(1.0)
    return Grade(0.0, f"assesses {insight.assessment}, not {' or '.join(want)}")


def _is(event, rc):
    return (
        event is not None
        and fnmatch.fnmatchcase(event.get("code", ""), rc.code)
        and (not rc.component or component(event) == rc.component)
    )


# -- the judge -------------------------------------------------------------------------------------

JUDGE = """\
You grade an analysis of a robot incident against its known root cause.

2: it names the same root cause: the same component and the same mechanism (wording may differ).
1: it points at the right component or fault, but the mechanism is vague, partly wrong or missing,
   or the right cause is there but ranked below a wrong one.
0: it names another cause, or only restates the symptom.

Judge the cause, not the style or the recommendations. Call grade once."""

GRADE = {
    "type": "function",
    "function": {
        "name": "grade",
        "description": "Your grade of the analysis.",
        "parameters": {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "enum": [0, 1, 2]},
                "reason": {"type": "string", "description": "one sentence"},
            },
            "required": ["score", "reason"],
        },
    },
}
FORCE_GRADE = {"type": "function", "function": {"name": "grade"}}


def describe(insight):
    """The insight as the judge reads it."""
    lines = [f"Title: {insight.title}", f"Assessment: {insight.assessment}"]
    if insight.summary:
        lines.append(f"Summary: {insight.summary}")
    lines.append("Hypotheses (most confident first):")
    lines += [f"{i}. [{h.confidence:.2f}] {h.cause}" for i, h in enumerate(insight.hypotheses, 1)]
    if insight.recommendations:
        lines.append("Recommendations:")
        for r in insight.recommendations:
            lines.append(f"- {r.kind}: {r.text}" + (f" (plan {r.plan_id})" if r.plan_id else ""))
    return "\n".join(lines)


class Judge:
    """An LLM (any LLMClient) that grades the root cause in words, with the rubric above."""

    def __init__(self, client):
        self.client = client
        self.name = client.name

    def __call__(self, case, insight):
        """(score in [0, 1], reason) or None when it could not grade; and what it cost."""
        rc = case.label.root_cause
        known = f"## What happened\n{case.label.description}\n\n" if case.label.description else ""
        known += f"## The known root cause\n{rc.text}\n\n"
        messages = [
            {"role": "system", "content": JUDGE},
            {"role": "user", "content": f"{known}## The analysis\n{describe(insight)}"},
        ]
        try:
            c = self.client.complete(messages, [GRADE], FORCE_GRADE)
        except ModelUnavailable:
            return None, 0.0
        for t in c.tool_calls:
            if t.name != "grade":
                continue
            try:
                args = json.loads(t.arguments)
                value = int(args["score"])
            except (ValueError, TypeError, KeyError):
                break
            if value in (0, 1, 2):
                return (value / 2, str(args.get("reason", ""))[:300]), c.cost_usd
        return None, c.cost_usd
