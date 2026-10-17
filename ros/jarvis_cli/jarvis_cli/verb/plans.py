# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
import json

from jarvis_interfaces.srv import ListPlans, ReloadConfig
from ros2cli.verb import VerbExtension

from jarvis_cli import api
from jarvis_cli.verb.rules import _report


class PlansVerb(VerbExtension):
    """List recovery plans, or reload them (`reload --check` validates them against the actuators)."""

    def add_arguments(self, parser, cli_name):
        parser.add_argument("action", nargs="?", default="list", choices=["list", "reload"])
        parser.add_argument("ids", nargs="*", help="list: plan ids (globs)")
        parser.add_argument("--check", action="store_true", help="reload: validate only, change nothing")
        parser.add_argument("--operator", default="", help="your name (default: $JARVIS_OPERATOR, $USER)")

    @api.run
    def main(self, *, args):
        with api.node() as n:
            if args.action == "list":
                reply = api.call(n, ListPlans, "/jarvis/recovery/list_plans", ListPlans.Request(ids=args.ids))
                print(f"recovery mode: {reply.mode}")
                for p in reply.plans:
                    params = json.loads(p.params_json or "{}")
                    shown = ", ".join(f"{k}{'' if v.get('required') else '?'}" for k, v in params.items())
                    print(f"{p.id}({shown})  steps: {' -> '.join(p.steps)}  timeout {p.timeout_s:g} s")
                    if p.description:
                        print(f"    {p.description}")
                if not reply.plans:
                    print("no plans")
                return 0
            request = ReloadConfig.Request(
                validate_only=args.check, operator_name=api.operator_name(args.operator)
            )
            return _report(api.call(n, ReloadConfig, "/jarvis/recovery/reload", request))
