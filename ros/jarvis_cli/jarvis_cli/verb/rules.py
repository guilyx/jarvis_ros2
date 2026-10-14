# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
from jarvis_interfaces.srv import ListRules, ReloadConfig, SetRuleState
from ros2cli.verb import VerbExtension

from jarvis_cli import api

SERVICE = "/jarvis/rule_engine"


class RulesVerb(VerbExtension):
    """List and operate rules: list, enable, disable, dry-run, reload."""

    def add_arguments(self, parser, cli_name):
        parser.add_argument(
            "action", nargs="?", default="list", choices=["list", "enable", "disable", "dry-run", "reload"]
        )
        parser.add_argument("ids", nargs="*", help="rule ids (globs for list)")
        parser.add_argument("--off", action="store_true", help="dry-run: leave shadow mode")
        parser.add_argument("--check", action="store_true", help="reload: validate only, change nothing")

    @api.run
    def main(self, *, args):
        with api.node() as n:
            if args.action == "list":
                rules = api.call(n, ListRules, f"{SERVICE}/list_rules", ListRules.Request(ids=args.ids)).rules
                for r in rules:
                    mode = ("on" if r.enabled else "OFF") + (", dry run" if r.dry_run else "")
                    print(
                        f"{r.id}  [{mode}]  fired {r.fires}, dry {r.dry_fires}, suppressed {r.suppressed}, "
                        f"cooled down {r.cooled_down}, guard blocked {r.guard_blocked}"
                    )
                    if r.description:
                        print(f"    {r.description}")
                if not rules:
                    print("no rules")
                return 0
            if args.action == "reload":
                reply = api.call(
                    n, ReloadConfig, f"{SERVICE}/reload", ReloadConfig.Request(validate_only=args.check)
                )
                return _report(reply)
            if not args.ids:
                raise ValueError(f"{args.action} needs at least one rule id")
            K, T, F = SetRuleState.Request.KEEP, SetRuleState.Request.SET_TRUE, SetRuleState.Request.SET_FALSE
            enabled, dry = {"enable": (T, K), "disable": (F, K), "dry-run": (K, F if args.off else T)}[
                args.action
            ]
            failed = 0
            for rule_id in args.ids:
                req = SetRuleState.Request(rule_id=rule_id, enabled=enabled, dry_run=dry)
                reply = api.call(n, SetRuleState, f"{SERVICE}/set_rule_state", req)
                print(f"{rule_id}: {reply.message}")
                failed += 0 if reply.success else 1
            return 1 if failed else 0


def _report(reply):
    for e in reply.errors:
        print(f"error: {e}")
    for w in reply.warnings:
        print(f"warning: {w}")
    print(f"{reply.message} (revision {reply.revision})")
    return 0 if reply.success else 1
