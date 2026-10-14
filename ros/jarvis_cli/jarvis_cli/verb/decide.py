# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
from jarvis_interfaces.srv import CancelExecution, DecideApproval
from ros2cli.verb import VerbExtension

from jarvis_cli import api


class _Decide(VerbExtension):
    approve = True

    def add_arguments(self, parser, cli_name):
        parser.add_argument("execution_id", help="from `ros2 jarvis executions`")
        parser.add_argument("--note", default="", help="for the audit trail")
        parser.add_argument(
            "--operator", default="", help="your name (default: $JARVIS_OPERATOR, then $USER)"
        )

    @api.run
    def main(self, *, args):
        request = DecideApproval.Request(
            execution_id=args.execution_id,
            approve=self.approve,
            operator_name=api.operator_name(args.operator),
            note=args.note,
        )
        with api.node() as n:
            reply = api.call(n, DecideApproval, "/jarvis/recovery/decide", request)
        print(reply.message)
        return 0 if reply.success else 1


class ApproveVerb(_Decide):
    """Approve an execution waiting in PENDING_APPROVAL."""

    approve = True


class RejectVerb(_Decide):
    """Reject an execution waiting in PENDING_APPROVAL."""

    approve = False


class CancelVerb(VerbExtension):
    """Abort a waiting or running execution, or stop the escalation ladder of a failed one."""

    def add_arguments(self, parser, cli_name):
        parser.add_argument("execution_id", help="from `ros2 jarvis executions`")
        parser.add_argument("--reason", default="", help="for the audit trail")
        parser.add_argument(
            "--operator", default="", help="your name (default: $JARVIS_OPERATOR, then $USER)"
        )

    @api.run
    def main(self, *, args):
        request = CancelExecution.Request(
            execution_id=args.execution_id, operator_name=api.operator_name(args.operator), reason=args.reason
        )
        with api.node() as n:
            reply = api.call(n, CancelExecution, "/jarvis/recovery/cancel", request)
        print(reply.message)
        return 0 if reply.success else 1
