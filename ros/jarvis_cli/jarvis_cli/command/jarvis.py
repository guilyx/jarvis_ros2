# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
from ros2cli.command import CommandExtension, add_subparsers_on_demand


class JarvisCommand(CommandExtension):
    """Operate Jarvis: faults, rules, recovery, approvals, the kill switch, bags and backtests."""

    def add_arguments(self, parser, cli_name):
        self._subparser = parser
        add_subparsers_on_demand(parser, cli_name, "_verb", "jarvis_cli.verb", required=False)

    def main(self, *, parser, args):
        if not hasattr(args, "_verb"):
            self._subparser.print_help()
            return 0
        return args._verb.main(args=args)
