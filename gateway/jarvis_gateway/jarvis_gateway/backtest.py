# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Rule lab (roadmap 8.3): what a draft rule would have done over the robot's recorded history.

    POST /api/v1/rules:backtest   {"yaml": "<rules>", "hours": 24}

The draft is linted first, like POST /rules:validate. Then the events of the last `hours` (the lake,
then what the gateway received since) are replayed through `jarvis backtest`: the draft together with
the robot's other rules (a draft rule replaces the robot's rule of the same id), and the plans in
dry run, so a rule that reacts to another's events or to a recovery's outcome behaves as it would
live. The answer is about the draft's rules: when each would have fired, on which event, which
recoveries it would have asked for and how the gates would have answered, and where it differs from
what the robot's rule of the same id decided live.
"""

import datetime
import json
import pathlib
import subprocess
import tempfile

import yaml

MAX_EVENTS = 200_000
MAX_MATCHES = 500
TIMEOUT_S = 120


class BacktestError(Exception):
    """The backtest could not run (the tool is missing, timed out, or failed)."""


def rule_ids(text):
    """The ids of the rules in a rules document (best effort: lint has the last word)."""
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return []
    rules = doc.get("rules") if isinstance(doc, dict) else None
    return [r["id"] for r in rules or [] if isinstance(r, dict) and isinstance(r.get("id"), str)]


def _others(rules_dir, drafted, out):
    """The robot's rules, without those the draft redefines, into `out`. Returns the redefined ids."""
    replaced = set()
    for path in sorted(pathlib.Path(rules_dir).glob("*.y*ml")):
        doc = yaml.safe_load(path.read_text()) or {}
        if not isinstance(doc, dict):
            continue
        rules = [r for r in doc.get("rules") or [] if isinstance(r, dict)]
        replaced |= {r.get("id") for r in rules} & drafted
        kept = [r for r in rules if r.get("id") not in drafted]
        if kept:
            (out / f"robot-{path.stem}.yaml").write_text(
                yaml.safe_dump({**doc, "rules": kept}, sort_keys=False)
            )
    return replaced


def _cause(event):
    if not event:
        return None
    source = event.get("source") or {}
    return {
        "id": event.get("id", ""),
        "kind": event.get("kind", ""),
        "code": event.get("code", ""),
        "component": source.get("component", ""),
        "severity": event.get("severity", ""),
        "message": event.get("message", ""),
        "at": event.get("stamp", ""),
    }


def run(settings, history, text, hours, robot="robot", now=None):
    """Backtests a draft over the last `hours`. Returns the report the API serves."""
    drafted = set(rule_ids(text))
    now = now or datetime.datetime.now(datetime.UTC)
    events = history.events(
        since=now - datetime.timedelta(hours=hours), newest_first=False, limit=MAX_EVENTS + 1
    )
    truncated = len(events) > MAX_EVENTS
    events = events[:MAX_EVENTS]
    by_id = {e.get("id"): e for e in events}
    with tempfile.TemporaryDirectory(prefix="jarvis_backtest_") as tmp:
        rules = pathlib.Path(tmp) / "rules"
        rules.mkdir()
        replaced = _others(settings.lint_rules_dir, drafted, rules) if settings.lint_rules_dir else set()
        (rules / "draft.yaml").write_text(text)
        stream = pathlib.Path(tmp) / "events.jsonl"
        stream.write_text("".join(json.dumps(e, separators=(",", ":")) + "\n" for e in events))
        cmd = [settings.jarvis_bin, "backtest", "--rules", str(rules), "--robot", robot or "robot", "--json"]
        if settings.lint_plans_dir:
            cmd += ["--plans", settings.lint_plans_dir]
        cmd.append(str(stream))
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
        except FileNotFoundError as e:
            raise BacktestError(f"`{settings.jarvis_bin}` is not installed here") from e
        except subprocess.TimeoutExpired as e:
            raise BacktestError(f"the backtest took longer than {TIMEOUT_S} s: try fewer hours") from e
    try:
        report = json.loads(out.stdout)
    except ValueError as e:
        why = (out.stderr or out.stdout).strip().splitlines()
        raise BacktestError(why[-1] if why else f"jarvis backtest exited {out.returncode}") from e

    mine = lambda rule: rule in drafted  # noqa: E731
    matches = [m for m in report.get("matches", []) if mine(m.get("rule"))]
    # Against what the robot decided live: only for a rule it already has (a new rule is all "extra").
    # A decision is "rule <- cause id", or "rule <- chained" when the cause was another rule's event.
    comparison = report.get("comparison") or {}

    def differs(keys):
        out = []
        for key in keys or []:
            rule, _, cause = key.partition(" <- ")
            if rule in replaced:
                cause = "" if cause == "chained" else cause
                out.append({"rule": rule, "cause_id": cause, "cause": _cause(by_id.get(cause))})
        return out[:MAX_MATCHES]

    return {
        "events": report.get("events", 0),
        "from": report.get("from"),
        "to": report.get("to"),
        "hours": hours,
        "truncated": truncated,
        "replaces": sorted(replaced),
        "rules": [s for s in report.get("rules", []) if mine(s.get("rule"))],
        "matches": [{**m, "cause": _cause(by_id.get(m.get("cause_id")))} for m in matches[:MAX_MATCHES]],
        "match_count": len(matches),
        "recoveries": [r for r in report.get("recoveries", []) if mine(r.get("rule"))][:MAX_MATCHES],
        "missing": differs(comparison.get("missing")),
        "extra": differs(comparison.get("extra")),
    }
