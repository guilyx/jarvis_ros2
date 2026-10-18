# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""jarvis-mcp: serve Jarvis to an AI assistant (Claude Code, Claude Desktop, Cursor...) over MCP.

    jarvis-mcp --url http://robot:8080 --token $JARVIS_TOKEN
    claude mcp add jarvis -e JARVIS_URL=http://robot:8080 -e JARVIS_TOKEN=... -- jarvis-mcp

The assistant gets read-only tools (faults, incidents, runbooks, events, recoveries, analytics,
the agent). --allow-recovery adds request_recovery, which goes through every gate like anyone's.
Guide: docs/guides/ai-assistants.md.
"""

import argparse
import logging
import os
import sys

from jarvis_mcp import __version__
from jarvis_mcp.gateway import Gateway, GatewayError
from jarvis_mcp.server import Server
from jarvis_mcp.tools import INSTRUCTIONS, Toolbox


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="jarvis-mcp", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--url",
        default=os.environ.get("JARVIS_URL", "http://127.0.0.1:8080"),
        help="the gateway ($JARVIS_URL)",
    )
    p.add_argument(
        "--token-env",
        default="JARVIS_TOKEN",
        help="environment variable holding the token (default JARVIS_TOKEN)",
    )
    p.add_argument(
        "--ca-file", default=os.environ.get("JARVIS_CA_FILE"), help="CA bundle for an https gateway"
    )
    p.add_argument(
        "--allow-recovery", action="store_true", help="add request_recovery (needs an operator token)"
    )
    p.add_argument("--check", action="store_true", help="check the gateway and the token, then exit")
    p.add_argument("--version", action="version", version=f"jarvis-mcp {__version__}")
    args = p.parse_args(argv)
    # stdout is the protocol's: logs go to stderr, and never carry the token.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="jarvis-mcp: %(message)s")
    gateway = Gateway(args.url, os.environ.get(args.token_env, ""), ca_file=args.ca_file)
    if args.check:
        try:
            me = gateway.get("/me")
        except GatewayError as e:
            print(f"jarvis-mcp: {e}", file=sys.stderr)
            return 1
        print(f"{args.url}: signed in as {me['name']} ({me['role']})")
        return 0
    logging.getLogger("jarvis_mcp").info(
        "serving %s over stdio%s", args.url, ", recovery requests allowed" if args.allow_recovery else ""
    )
    Server(Toolbox(gateway, allow_recovery=args.allow_recovery), INSTRUCTIONS).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
