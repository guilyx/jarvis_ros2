# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
from jarvis_interfaces.srv import ReloadConfig
from ros2cli.verb import VerbExtension

from jarvis_cli import api
from jarvis_cli.verb.rules import _report


class PlansVerb(VerbExtension):
    """Reload recovery plans (`reload --check` validates them against the loaded actuators)."""

    def add_arguments(self, parser, cli_name):
        parser.add_argument("action", choices=["reload"])
        parser.add_argument("--check", action="store_true", help="validate only, change nothing")

    @api.run
    def main(self, *, args):
        with api.node() as n:
            request = ReloadConfig.Request(validate_only=args.check)
            return _report(api.call(n, ReloadConfig, "/jarvis/recovery/reload", request))
