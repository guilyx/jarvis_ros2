# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Autonomy: what the agent may do with a recovery it recommends (docs/design/06-agent.md §8).

    observe      insights only
    advise       recommendations; a human clicks "run" (an OPERATOR request)   (the default)
    supervised   submitted, waiting for one-click approval
    autonomous   runs when the plan allows it, confidence ≥ the threshold and every gate passes

effective = min(deployment level, the plan's agent_policy). The heuristic analyst never goes past
advise, and autonomy below the confidence threshold falls back to supervised.
"""

LEVELS = ("observe", "advise", "supervised", "autonomous")
PLAN_POLICY = {"never": -1, "advise": 1, "supervised": 2, "autonomous": 3}


class AutonomyPolicy:
    def __init__(self, level="advise", min_confidence=0.7):
        if level not in LEVELS:
            raise ValueError(f"unknown autonomy level '{level}' ({', '.join(LEVELS)})")
        self.level = level
        self.min_confidence = min_confidence

    def decide(self, plan, confidence, analyst="llm"):
        """observe (nothing), advise, supervised or autonomous, for one recommended plan."""
        if plan is None or "AGENT" not in (plan.get("allowed_requesters") or []):
            return "observe"
        effective = min(LEVELS.index(self.level), PLAN_POLICY.get(plan.get("agent_policy"), -1))
        if analyst == "heuristic":
            effective = min(effective, LEVELS.index("advise"))
        if effective == LEVELS.index("autonomous") and confidence < self.min_confidence:
            effective = LEVELS.index("supervised")
        return LEVELS[max(effective, 0)]
