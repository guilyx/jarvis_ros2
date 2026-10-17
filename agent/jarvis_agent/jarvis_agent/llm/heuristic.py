# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The heuristic analyst: an insight without a model (offline, over budget, or triage was enough).

It says what the event stream shows, nothing more (docs/design/06-agent.md §9):
- the root cause candidate: the first confirmed fault not muted as a symptom of another;
- what preceded it on the same component, and whether the code keeps coming back;
- how the incident's recoveries went, and which plan fixed this code before, how often.
It recommends plans by their record and never runs anything itself (autonomy capped at advise).
"""

from jarvis_agent.events import attr, clock, component, level, ns, seconds
from jarvis_agent.model import SEVERITIES, Hypothesis, Insight, Recommendation


def _event(window, test):
    return next((e for e in window.events if test(e)), None)


def _confirmation(window, episode):
    return _event(
        window,
        lambda e: (
            e.get("kind") == "FAULT_STATE"
            and attr(e, "fault_id") == episode["fault_id"]
            and attr(e, "state") in ("ACTIVE", "LATCHED")
        ),
    )


def _outcome_event(window, execution):
    return _event(
        window,
        lambda e: (
            e.get("kind") == "RECOVERY"
            and attr(e, "execution_id") == execution["execution_id"]
            and attr(e, "phase") in ("FAILED", "SUCCEEDED", "ABORTED", "REJECTED", "EXPIRED")
        ),
    )


def _args_for(plan, episode):
    """What the fault tells about a plan's arguments: its component and code. The rest is left to
    whoever runs it (a node to restart is not the node that reported the fault)."""
    params = plan.get("params") or {}
    known = {"component": episode["component"], "fault": episode["code"]}
    return {k: v for k, v in known.items() if k in params}


class HeuristicAnalyst:
    name = "heuristic"

    def analyze(self, window, result):
        if result.root is None:
            return self._without_faults(window, result)
        root = result.root
        where = f"{root['code']} on {root['component']}"
        confirmation = _confirmation(window, root)
        evidence = [confirmation["id"]] if confirmation else []
        hypotheses, recommendations = [], []
        fixed = [x for x in result.succeeded if not x.get("dry_run")]
        failed = [x for x in result.failed if not x.get("dry_run") and x.get("outcome") == "FAILED"]

        if failed and not fixed:
            plan = failed[-1]["plan"]
            outcome = _outcome_event(window, failed[-1])
            hypotheses.append(
                Hypothesis(
                    cause=f"The cause is beyond what {plan} acts on: {root['code']} stayed after it ran",
                    confidence=0.55,
                    evidence=evidence + ([outcome["id"]] if outcome else []),
                )
            )
            recommendations.append(
                Recommendation(
                    kind="INVESTIGATE",
                    text=f"Look at {root['component']} itself: {plan} did not clear {root['code']}",
                )
            )
        elif fixed:
            plan = fixed[-1]
            outcome = _outcome_event(window, plan)
            took = ""
            if plan.get("started_at") and root.get("cleared_at"):
                took = f" {seconds(ns(root['cleared_at']) - ns(plan['started_at']))} after it started"
            hypotheses.append(
                Hypothesis(
                    cause=f"What {plan['plan']} acts on was the problem: {root['code']} cleared{took}",
                    confidence=0.6,
                    evidence=evidence + ([outcome["id"]] if outcome else []),
                )
            )
        else:
            muted = sorted({s["code"] for s in result.symptoms})
            mutes = [
                e["id"]
                for e in result.own
                if e["code"] == "jarvis.fault.muted"
                or (e.get("kind") == "FAULT_STATE" and attr(e, "muted_by"))
            ]
            cause = f"{where} is the root cause"
            if muted:
                cause += f"; {', '.join(muted)} followed as its symptom(s)"
            hypotheses.append(
                Hypothesis(cause=cause, confidence=0.6 if muted else 0.5, evidence=evidence + mutes[:5])
            )

        if result.precursors:
            p = result.precursors[-1]
            gap = seconds(ns(root.get("first_seen") or root["confirmed_at"]) - ns(p["stamp"]))
            hypotheses.append(
                Hypothesis(
                    cause=f"{p['code']} on {component(p)} led to it ({gap} before)",
                    confidence=0.35,
                    evidence=[p["id"]] + evidence,
                )
            )

        earlier = result.earlier.get(root["code"], [])
        if len(earlier) >= 2:
            hypotheses.append(
                Hypothesis(
                    cause=f"{root['code']} keeps coming back on {root['component']} "
                    f"({len(earlier)} earlier episodes): something lasting causes it",
                    confidence=0.4,
                    evidence=evidence,
                )
            )

        plans = {p["id"]: p for p in window.plans}
        tried = {x["plan"] for x in failed}
        record = sorted(
            (
                (ok / runs, runs, plan)
                for plan, codes in result.plan_record.items()
                for code, (ok, runs) in codes.items()
                if code == root["code"] and plan in plans and plan not in tried and runs
            ),
            reverse=True,
        )
        if not fixed:
            for rate, runs, plan in record[:2]:
                ok = round(rate * runs)
                recommendations.append(
                    Recommendation(
                        kind="RECOVER",
                        text=f"{plan} fixed {root['code']} {ok} of {runs} time(s)",
                        plan_id=plan,
                        args=_args_for(plans[plan], root) or None,
                        confidence=round(rate, 2),
                    )
                )
            if root.get("last_state") == "LATCHED":
                recommendations.append(
                    Recommendation(
                        kind="INVESTIGATE",
                        text=f"{root['code']} is latched: it stays until an operator clears it, "
                        "once the cause is gone",
                    )
                )
            elif not record and not failed:
                recommendations.append(
                    Recommendation(
                        kind="INVESTIGATE",
                        text=f"No plan has fixed {root['code']} before: look at {root['component']}",
                    )
                )
        if (window.incident or {}).get("escalated"):
            recommendations.append(
                Recommendation(
                    kind="ESCALATE", text="Jarvis gave up and asked for a human: someone should go and look"
                )
            )

        title = where
        if fixed:
            title += f": fixed by {fixed[-1]['plan']}"
        elif failed:
            title += f": {failed[-1]['plan']} did not fix it"
        elif result.symptoms:
            title += f", {len(result.symptoms)} symptom(s) muted"
        return Insight(
            trigger=window.trigger,
            window=window.info(),
            title=title[:200],
            summary=self._summary(window, result, root)[:4000],
            assessment=self._assessment(window),
            hypotheses=hypotheses[:3],
            recommendations=recommendations,
            degraded=True,
            analyst="heuristic",
            facts=result.facts,
        )

    def _without_faults(self, window, result):
        warnings = [e for e in window.events if level(e) >= 30]
        codes = {}
        for e in warnings:
            codes.setdefault(e["code"], []).append(e)
        top = sorted(codes.items(), key=lambda kv: len(kv[1]), reverse=True)
        hypotheses = [
            Hypothesis(
                cause=f"{code} is the most frequent warning "
                f"({len(events)}×, first {clock(events[0]['stamp'])})",
                confidence=0.3,
                evidence=[events[0]["id"]],
            )
            for code, events in top[:2]
        ]
        title = (window.incident or {}).get("title") or (
            f"{len(warnings)} warning(s) in the window" if warnings else "Nothing went wrong in the window"
        )
        return Insight(
            trigger=window.trigger,
            window=window.info(),
            title=title[:200],
            summary="; ".join(result.facts[:6]) or "No fault was confirmed.",
            assessment=self._assessment(window),
            hypotheses=hypotheses,
            degraded=True,
            analyst="heuristic",
            facts=result.facts,
        )

    @staticmethod
    def _summary(window, result, root):
        parts = [
            f"{root['code']} on {root['component']} confirmed at {clock(root['confirmed_at'])}",
            *result.facts[1:5],
        ]
        return ". ".join(parts) + "."

    @staticmethod
    def _assessment(window):
        worst = max(
            (level(e) for e in window.events if e.get("kind") in ("FAULT_STATE", "FAULT_RAISE")), default=20
        )
        if window.incident and window.incident.get("severity_level"):
            worst = max(worst, window.incident["severity_level"])
        return SEVERITIES[min(max(worst // 10 - 1, 0), 4)]
