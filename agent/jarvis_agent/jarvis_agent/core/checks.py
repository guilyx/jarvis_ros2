# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Harness checks: what an analyst says is checked before anyone sees it (docs/design/06-agent.md §7).

- Every evidence id must be an event the analyst saw (the window, or tool results). Others are
  dropped; a hypothesis left without evidence is capped at 0.3 and flagged.
- A recommended plan must exist and the arguments it gives must validate against the plan's
  parameters (advice may leave some for the operator; a proposal may not).
- At most 3 hypotheses and 3 recovery recommendations, the most confident first.
"""

import re

MAX_HYPOTHESES = 3
MAX_RECOVERIES = 3
UNSUPPORTED_CAP = 0.3


def validate_args(plan, args, partial=False):
    """Problems with `args` for `plan` (empty: they would pass the plan's own validation). With
    `partial`, missing required ones are fine: advice may leave them to the operator."""
    problems = []
    params = plan.get("params") or {}
    for name, value in (args or {}).items():
        spec = params.get(name)
        if spec is None:
            problems.append(f"unknown parameter '{name}'")
            continue
        kind = spec.get("type", "string")
        if kind == "int" and not re.fullmatch(r"-?\d+", value):
            problems.append(f"{name}: not an integer")
        elif kind == "number" and not re.fullmatch(r"-?\d+(\.\d+)?", value):
            problems.append(f"{name}: not a number")
        elif kind == "bool" and value not in ("true", "false"):
            problems.append(f"{name}: not true or false")
        if spec.get("pattern") and not re.search(spec["pattern"], value):
            problems.append(f"{name}: does not match {spec['pattern']}")
        if spec.get("one_of") and value not in spec["one_of"]:
            problems.append(f"{name}: not one of {spec['one_of']}")
    for name, spec in params.items():
        if partial:
            break
        if spec.get("required") and name not in (args or {}) and "default" not in spec:
            problems.append(f"missing required parameter '{name}'")
    return problems


def check(insight, known_ids, plans):
    """A checked copy of the insight; what changed is in its `flags`."""
    insight = insight.model_copy(deep=True)
    flags = list(insight.flags)
    by_id = {p["id"]: p for p in plans}

    hypotheses = sorted(insight.hypotheses, key=lambda h: h.confidence, reverse=True)
    if len(hypotheses) > MAX_HYPOTHESES:
        flags.append(f"kept the {MAX_HYPOTHESES} most confident of {len(hypotheses)} hypotheses")
        hypotheses = hypotheses[:MAX_HYPOTHESES]
    for h in hypotheses:
        unknown = [i for i in h.evidence + h.counter_evidence if i not in known_ids]
        if unknown:
            flags.append(f"dropped {len(unknown)} evidence id(s) the analyst never saw")
            h.evidence = [i for i in h.evidence if i in known_ids]
            h.counter_evidence = [i for i in h.counter_evidence if i in known_ids]
        if not h.evidence and h.confidence > UNSUPPORTED_CAP:
            h.confidence = UNSUPPORTED_CAP
            h.flagged = True
            flags.append(f"capped '{h.cause[:60]}' at {UNSUPPORTED_CAP}: no evidence")
    insight.hypotheses = hypotheses

    kept, recoveries = [], 0
    for r in insight.recommendations:
        if r.kind == "RECOVER":
            plan = by_id.get(r.plan_id or "")
            if plan is None:
                flags.append(f"dropped a recommendation of unknown plan '{r.plan_id}'")
                continue
            problems = validate_args(plan, r.args or {}, partial=True)
            if problems:
                flags.append(f"dropped {r.plan_id}: {'; '.join(problems)}")
                continue
            if recoveries == MAX_RECOVERIES:
                flags.append(f"kept {MAX_RECOVERIES} recovery recommendations")
                continue
            recoveries += 1
        kept.append(r)
    insight.recommendations = kept
    insight.flags = list(dict.fromkeys(flags))  # in order, once each
    return insight
