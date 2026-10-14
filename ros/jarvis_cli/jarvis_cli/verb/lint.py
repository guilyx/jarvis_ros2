# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
import subprocess

from ros2cli.verb import VerbExtension

from jarvis_cli import api


class LintVerb(VerbExtension):
    """Check rules, plans and the fault catalog, and how they fit together (`jarvis lint`)."""

    def add_arguments(self, parser, cli_name):
        parser.add_argument("--rules", default="", help="rules directory")
        parser.add_argument("--plans", default="", help="plans directory")
        parser.add_argument("--catalog", default="", help="fault catalog file")
        parser.add_argument(
            "--actuators", default=None, help="comma-separated actions (default: built-in + Nav2)"
        )

    @api.run
    def main(self, *, args):
        command = [api.jarvis_binary(), "lint"]
        for flag, value in (("--rules", args.rules), ("--plans", args.plans), ("--catalog", args.catalog)):
            if value:
                command += [flag, value]
        if args.actuators is not None:
            command += ["--actuators", args.actuators]
        return subprocess.call(command)
