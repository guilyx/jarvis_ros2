# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
import math

import rclpy
from jarvis_interfaces.action import ExecuteRecovery
from rclpy.action import ActionClient
from ros2cli.verb import VerbExtension

from jarvis_cli import api
from jarvis_cli.convert import make_attrs, parse_assignments

OUTCOMES = {
    v: k[len("OUTCOME_") :] for k, v in vars(ExecuteRecovery.Result).items() if k.startswith("OUTCOME_")
}
STATES = {v: k[len("STATE_") :] for k, v in vars(ExecuteRecovery.Feedback).items() if k.startswith("STATE_")}


class RecoverVerb(VerbExtension):
    """Run a recovery plan through the gates, as an operator (ExecuteRecovery)."""

    def add_arguments(self, parser, cli_name):
        parser.add_argument("plan", help="plan id, e.g. restart-lifecycle-node")
        parser.add_argument(
            "-a", "--arg", action="append", default=[], metavar="KEY=VALUE", help="plan argument"
        )
        parser.add_argument("--dry-run", action="store_true", help="run every gate, simulate the steps")
        parser.add_argument("--rationale", default="", help="why, for the audit trail")
        parser.add_argument(
            "--operator", default="", help="your name (default: $JARVIS_OPERATOR, then $USER)"
        )
        parser.add_argument("--correlation", default="", help="the incident this is about")
        parser.add_argument(
            "--timeout", type=float, default=600.0, help="give up waiting after this many seconds"
        )

    @api.run
    def main(self, *, args):
        goal = ExecuteRecovery.Goal(
            plan_id=args.plan,
            args=make_attrs(parse_assignments(args.arg)),
            requester_type=ExecuteRecovery.Goal.REQUESTER_OPERATOR,
            requester_id=api.operator_name(args.operator),
            correlation_id=args.correlation,
            rationale=args.rationale,
            confidence=math.nan,
            dry_run=args.dry_run,
        )
        with api.node() as n:
            client = ActionClient(n, ExecuteRecovery, "/jarvis/recovery/execute")
            if not client.wait_for_server(timeout_sec=10.0):
                raise api.JarvisError("/jarvis/recovery/execute is not available (is Jarvis running?)")
            last = []

            def on_feedback(msg):
                f = msg.feedback
                line = f"{STATES.get(f.state, '?')}"
                if f.step_id:
                    line += f"  step {f.step_index + 1}/{f.step_count} {f.step_id}"
                if f.message:
                    line += f"  {f.message}"
                if line != (last[-1] if last else None):
                    print(f"  {line}")
                    last.append(line)

            sent = client.send_goal_async(goal, feedback_callback=on_feedback)
            rclpy.spin_until_future_complete(n, sent, timeout_sec=10.0)
            handle = sent.result()
            if handle is None or not handle.accepted:
                raise api.JarvisError("the recovery server refused the goal (not active)")
            result = handle.get_result_async()
            rclpy.spin_until_future_complete(n, result, timeout_sec=args.timeout)
            if not result.done():
                raise api.JarvisError(f"no result within {args.timeout:.0f} s (the execution goes on)")
            r = result.result().result
        outcome = OUTCOMES.get(r.outcome, "?")
        print(f"{outcome}: {r.reason}" + (f" (step {r.failed_step})" if r.failed_step else ""))
        print(f"execution {r.execution_id}")
        return 0 if r.outcome == ExecuteRecovery.Result.OUTCOME_SUCCEEDED else 1
