# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Evals (docs/design/06-agent.md §10): the agent on labelled recorded incidents, scored.

    corpus.py    the cases: evals/corpus/<case>/label.yaml (schema jarvis.eval/v1) + the recording
    graders.py   evidence validity, must-cite recall, plans, escalation, assessment, root cause
                 (rules, or an LLM judge with a rubric)
    runner.py    each case through the agent as it ships (replay adapter, no ROS), a cost cap,
                 the table, a JSON report, a row of evals/results.tsv

`jarvis-agent eval` runs them.
"""
