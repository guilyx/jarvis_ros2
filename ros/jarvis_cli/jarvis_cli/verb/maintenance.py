# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
import re
from datetime import UTC, datetime

from jarvis_interfaces.msg import ExecutionArray
from jarvis_interfaces.srv import SetMaintenance
from ros2cli.verb import VerbExtension

from jarvis_cli import api

UNITS = {"s": 1, "m": 60, "min": 60, "h": 3600, "d": 86400}


def seconds(text):
    """'30m', '2h', '90s', '1d' (or plain seconds) -> seconds."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(s|m|min|h|d)?", text.strip())
    if not m:
        raise api.JarvisError(f"duration '{text}': a number and s, m, h or d, e.g. 30m")
    return float(m.group(1)) * UNITS[m.group(2) or "s"]


def clock(t):
    return datetime.fromtimestamp(t.sec + t.nanosec / 1e9, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


class MaintenanceVerb(VerbExtension):
    """Put a component under maintenance (rules and the agent keep off it), end it, or list them."""

    def add_arguments(self, parser, cli_name):
        parser.add_argument("component", nargs="?", help="the component, e.g. lidar")
        parser.add_argument("duration", nargs="?", help="how long: 30m, 2h, 1d (at most a week)")
        parser.add_argument("--end", action="store_true", help="end the component's maintenance now")
        parser.add_argument("--reason", default="", help="for the audit trail and the dashboard")
        parser.add_argument(
            "--operator", default="", help="your name (default: $JARVIS_OPERATOR, then $USER)"
        )

    @api.run
    def main(self, *, args):
        with api.node() as n:
            if not args.component:
                snapshot = api.latched(n, ExecutionArray, "/jarvis/executions")
                if snapshot is None:
                    raise api.JarvisError("/jarvis/executions has no publisher (is Jarvis running?)")
                if not snapshot.maintenance:
                    print("nothing under maintenance")
                for m in snapshot.maintenance:
                    why = f": {m.reason}" if m.reason else ""
                    print(f"{m.component:<20} until {clock(m.until)}  {m.operator_name}{why}")
                return 0
            if not args.end and not args.duration:
                raise api.JarvisError("how long? e.g. ros2 jarvis maintenance lidar 30m (or --end)")
            request = SetMaintenance.Request(
                component=args.component,
                duration_s=0.0 if args.end else seconds(args.duration),
                operator_name=api.operator_name(args.operator),
                reason=args.reason,
            )
            reply = api.call(n, SetMaintenance, "/jarvis/recovery/set_maintenance", request)
        print(reply.message)
        return 0 if reply.success else 1
