# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The window as text for a model: stable first (what does not change between analyses, so
providers can cache it), the window last, bounded by a token budget (about 4 characters a token).
Rows that do not fit are dropped by rank, never transitions; the model can fetch more with tools."""

import json

from jarvis_agent.events import clock, component

CHARS_PER_TOKEN = 4


def plan_catalog(plans):
    lines = []
    for p in sorted(plans, key=lambda p: p["id"]):
        params = ", ".join(
            f"{n}{'' if s.get('required') else '?'}" for n, s in sorted((p.get("params") or {}).items())
        )
        lines.append(
            f"- {p['id']}({params}) [agent: {p.get('agent_policy', 'never')}]: {p.get('description', '')}"
        )
    return "\n".join(lines) or "(no plans)"


def row_line(row):
    e = row.first
    attrs = e.get("attrs") or {}
    keys = ("state", "phase", "plan", "rule", "fault_id", "reason", "value")
    shown = {k: attrs[k] for k in keys if k in attrs}
    text = f"{clock(e['stamp'])} {e.get('kind', ''):<11} {e.get('severity', ''):<8} {e['code']}"
    if component(e):
        text += f" [{component(e)}]"
    if e.get("message"):
        text += f" {e['message'][:200]}"
    if shown:
        text += " " + json.dumps(shown, separators=(",", ":"))
    if row.count > 1:
        text += f" ×{row.count} until {clock(row.last['stamp'])}"
    return f"{text}  id={e['id']}" + (f" last={row.last['id']}" if row.count > 1 else "")


def render(window, result, budget_tokens=30_000):
    """(stable prefix, window text). The window keeps every transition and the best-ranked repeats."""
    stable = "## Recovery plans\n" + plan_catalog(window.plans)
    head = [f"## Trigger\n{window.trigger.type.value} {window.trigger.ref}"]
    if window.incident:
        i = window.incident
        head.append(f"## Incident {i['incident_id']}\n{i.get('title', '')} · {i.get('state', '')}")
    if window.question:
        head.append(f"## Question\n{window.question}")
    if result.facts:
        head.append("## Facts (computed)\n" + "\n".join(f"- {f}" for f in result.facts))
    if window.active_faults:
        head.append(
            "## Active faults\n"
            + "\n".join(
                f"- {f['code']} [{f['source']['component']}] {f['state']}" for f in window.active_faults
            )
        )
    budget = budget_tokens * CHARS_PER_TOKEN - len(stable) - sum(len(h) for h in head)
    keep, used = set(), 0
    must = [r for r in result.rows if r.count == 1 and r.first.get("kind") not in ("LOG", "STATE")]
    for row in must + [r for r in result.ranked if r not in must]:
        size = len(row_line(row)) + 1
        if used + size > budget:
            continue
        keep.add(id(row))
        used += size
    lines = [row_line(r) for r in result.rows if id(r) in keep]
    dropped = len(result.rows) - len(lines)
    events = "## Events (oldest first; repeats collapsed)\n" + "\n".join(lines)
    if dropped:
        events += f"\n({dropped} lower-ranked rows left out: query_events has them)"
    return stable, "\n\n".join([*head, events])
