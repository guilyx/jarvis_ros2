# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
from jarvis_interfaces.srv import GetFaults
from ros2cli.verb import VerbExtension

from jarvis_cli import api
from jarvis_cli.convert import FAULT_STATES, SEVERITIES


class FaultsVerb(VerbExtension):
    """List open faults (from /jarvis/fault_manager)."""

    def add_arguments(self, parser, cli_name):
        parser.add_argument("codes", nargs="*", help="code globs to keep, e.g. 'ros.*'")
        parser.add_argument("--all", action="store_true", help="include recently cleared and muted faults")
        parser.add_argument("--component", default="", help="only this component")

    @api.run
    def main(self, *, args):
        with api.node() as n:
            request = GetFaults.Request(
                include_cleared=args.all, include_muted=args.all, codes=args.codes, component=args.component
            )
            faults = api.call(n, GetFaults, "/jarvis/fault_manager/get_faults", request).faults
        if not faults:
            print("no open faults")
            return 0
        rows = [("STATE", "SEVERITY", "CODE", "COMPONENT", "SEEN", "INCIDENT", "MESSAGE")]
        for f in faults:
            state = (
                FAULT_STATES.get(f.state, "?")
                + (" (muted)" if f.muted_by else "")
                + (" ack" if f.acknowledged else "")
            )
            rows.append(
                (
                    state,
                    SEVERITIES.get(f.severity, "?"),
                    f.code,
                    f.source.component,
                    str(f.occurrences),
                    f.incident_id[:8],
                    f.message or f.description,
                )
            )
        _table(rows)
        return 0


def _table(rows):
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]) - 1)]
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=False)) + "  " + r[-1])
