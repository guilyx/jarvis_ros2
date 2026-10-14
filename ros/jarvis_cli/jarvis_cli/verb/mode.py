# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
from jarvis_interfaces.msg import ExecutionArray
from jarvis_interfaces.srv import SetRecoveryMode
from ros2cli.verb import VerbExtension

from jarvis_cli import api


class ModeVerb(VerbExtension):
    """Show or set the recovery mode: the kill switch (ENABLED, DRY_RUN, DISABLED)."""

    def add_arguments(self, parser, cli_name):
        parser.add_argument("mode", nargs="?", type=str.upper, choices=["ENABLED", "DRY_RUN", "DISABLED"])
        parser.add_argument("--reason", default="", help="for the audit trail")
        parser.add_argument("--cancel-running", action="store_true", help="DISABLED: also abort what runs")
        parser.add_argument(
            "--operator", default="", help="your name (default: $JARVIS_OPERATOR, then $USER)"
        )

    @api.run
    def main(self, *, args):
        with api.node() as n:
            if not args.mode:
                snapshot = api.latched(n, ExecutionArray, "/jarvis/executions")
                if snapshot is None:
                    raise api.JarvisError("/jarvis/executions has no publisher (is Jarvis running?)")
                print(snapshot.mode)
                return 0
            request = SetRecoveryMode.Request(
                mode=args.mode,
                operator_name=api.operator_name(args.operator),
                reason=args.reason,
                cancel_running=args.cancel_running,
            )
            reply = api.call(n, SetRecoveryMode, "/jarvis/recovery/set_mode", request)
        print(reply.message)
        return 0 if reply.success else 1
