# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
from jarvis_interfaces.msg import ExecutionArray
from ros2cli.verb import VerbExtension

from jarvis_cli import api
from jarvis_cli.convert import EXECUTION_STATES, REQUESTERS, STEP_STATES


class ExecutionsVerb(VerbExtension):
    """Show recovery executions: running, waiting for approval or locks, and recently finished."""

    def add_arguments(self, parser, cli_name):
        parser.add_argument("--recent", type=int, default=5, help="how many finished ones to show")
        parser.add_argument("--steps", action="store_true", help="show each step")

    @api.run
    def main(self, *, args):
        with api.node() as n:
            snapshot = api.latched(n, ExecutionArray, "/jarvis/executions")
        if snapshot is None:
            raise api.JarvisError("/jarvis/executions has no publisher (is Jarvis running?)")
        print(f"mode {snapshot.mode}")
        print(f"\nin play ({len(snapshot.executions)})")
        for x in snapshot.executions:
            _show(x, args.steps)
        recent = list(snapshot.recent)[-args.recent :] if args.recent > 0 else []
        print(f"\nrecent ({len(recent)} of {len(snapshot.recent)})")
        for x in recent:
            _show(x, args.steps)
        return 0


def _show(x, steps):
    flags = " (dry run)" if x.dry_run else ""
    flags += " ladder running" if x.ladder_running else ""
    flags += " escalated" if x.escalated else ""
    who = f"{REQUESTERS.get(x.requester_type, '?')} {x.requester_id}".strip()
    print(f"  {x.execution_id}  {x.plan_id}  {EXECUTION_STATES.get(x.state, '?')}{flags}  by {who}")
    if x.reason:
        print(f"      {x.reason}")
    if steps:
        for s in x.steps:
            print(f"      - {s.id} ({s.action}) {STEP_STATES.get(s.state, '?')} x{s.attempts}  {s.message}")
