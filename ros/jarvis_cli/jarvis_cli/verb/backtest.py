# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
import pathlib
import subprocess
import tempfile

from ros2cli.verb import VerbExtension

from jarvis_cli import api
from jarvis_cli.verb.export import export


class BacktestVerb(VerbExtension):
    """Replay a recorded bag (or JSON Lines) through rules and, with --plans, plans in dry run."""

    def add_arguments(self, parser, cli_name):
        parser.add_argument("source", help="a bag directory, or an events .jsonl file")
        parser.add_argument("--rules", required=True, help="rules directory to replay")
        parser.add_argument("--plans", default="", help="plans directory: simulate recoveries in dry run")
        parser.add_argument("--robot", default="", help="robot id")
        parser.add_argument("--json", action="store_true", help="machine-readable report")
        parser.add_argument(
            "--expect-same", action="store_true", help="exit 1 if decisions differ from the live run"
        )

    @api.run
    def main(self, *, args):
        command = [api.jarvis_binary(), "backtest", "--rules", args.rules]
        for flag, value in (("--plans", args.plans), ("--robot", args.robot)):
            if value:
                command += [flag, value]
        command += ["--json"] if args.json else []
        command += ["--expect-same"] if args.expect_same else []
        source = pathlib.Path(args.source)
        if source.is_file():
            return subprocess.call([*command, str(source)])
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl") as events:
            export(source, events)
            events.flush()
            return subprocess.call([*command, events.name])
