# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""jarvis-gateway: the HTTP API, live stream, metrics and dashboard of one robot's Jarvis.

    jarvis-gateway [--config gateway.yaml] [--host H] [--port P] [--data-dir D] [--dashboard DIR]
    jarvis-gateway --fake [--scenario]      an in-memory Jarvis: no ROS, for dashboard work

Settings come from --config (schema jarvis.gateway/v1), then $JARVIS_GATEWAY_TOKENS, then the
flags. The API is documented at /docs once it runs.
"""

import argparse
import copy
import logging
import os
import pathlib
import re
import sys

from jarvis_gateway import __version__
from jarvis_gateway.config import Principal, Settings


def strip_ros_args(argv):
    """argv without `--ros-args ... [--]`, which ros2 launch appends."""
    out, inside = [], False
    for arg in argv:
        if arg == "--ros-args":
            inside = True
        elif inside and arg == "--":
            inside = False
        elif not inside:
            out.append(arg)
    return out


def parser():
    p = argparse.ArgumentParser(prog="jarvis-gateway", description=__doc__.splitlines()[0])
    p.add_argument("--config", help="gateway.yaml (schema jarvis.gateway/v1)")
    p.add_argument("--host", help="address to bind (default 127.0.0.1: this machine only)")
    p.add_argument("--port", type=int, help="port (default 8080)")
    p.add_argument("--data-dir", help="Jarvis data directory (default $JARVIS_DATA)")
    p.add_argument(
        "--dashboard", dest="dashboard_dir", help="the dashboard's built files (default: the installed ones)"
    )
    p.add_argument("--catalog", help="the fault catalog (jarvis.faults/v1): descriptions and runbooks")
    p.add_argument(
        "--runbooks", action="append", metavar="DIR", help="a directory of <code>.md runbooks (repeatable)"
    )
    p.add_argument("--namespace", default="/jarvis", help="Jarvis' ROS namespace")
    p.add_argument("--fake", action="store_true", help="an in-memory Jarvis instead of ROS")
    p.add_argument("--scenario", action="store_true", help="with --fake: make things happen")
    p.add_argument(
        "--anonymous-role",
        choices=["none", "viewer", "operator", "admin"],
        help="what requests without a token may do (default from the config: viewer)",
    )
    p.add_argument("--tls-cert", help="serve HTTPS with this certificate (PEM); needs --tls-key")
    p.add_argument("--tls-key", help="the certificate's private key (PEM)")
    p.add_argument("--log-level", default="info")
    p.add_argument("--version", action="version", version=__version__)
    return p


def installed_dashboard():
    """The dashboard bundle: $JARVIS_DASHBOARD, else share/jarvis_gateway/dashboard (the Docker image
    puts the built dashboard there), else none (the gateway then serves the API only)."""
    found = os.environ.get("JARVIS_DASHBOARD")
    if found:
        return found
    try:
        from ament_index_python.packages import get_package_share_directory

        share = pathlib.Path(get_package_share_directory("jarvis_gateway")) / "dashboard"
    except (ImportError, LookupError, ValueError):
        return ""
    return str(share) if (share / "index.html").is_file() else ""


def settings_from(args):
    s = Settings.load(
        args.config,
        host=args.host,
        port=args.port,
        data_dir=args.data_dir,
        dashboard_dir=args.dashboard_dir,
        tls_cert=args.tls_cert,
        tls_key=args.tls_key,
        catalog=args.catalog,
        runbooks=args.runbooks,
    )
    if bool(s.tls_cert) != bool(s.tls_key):
        raise SystemExit("jarvis-gateway: TLS needs both a certificate and its key")
    s.dashboard_dir = s.dashboard_dir or installed_dashboard()
    if args.anonymous_role:
        s.anonymous = None if args.anonymous_role == "none" else Principal("anonymous", args.anonymous_role)
    return s


TOKEN_IN_URL = re.compile(r"(?i)([?&]token=)[^&\s\"]+")


class MaskTokens(logging.Filter):
    """A `?token=...` in a logged URL (the dashboard's live stream: browsers cannot send headers on
    a WebSocket) reads `token=***`: logs are not where tokens belong."""

    def filter(self, record):
        if isinstance(record.msg, str):
            record.msg = TOKEN_IN_URL.sub(r"\1***", record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                TOKEN_IN_URL.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args
            )
        return True


def log_config():
    """uvicorn's logging, with tokens masked on every handler."""
    from uvicorn.config import LOGGING_CONFIG

    config = copy.deepcopy(LOGGING_CONFIG)
    config["filters"] = {"tokens": {"()": MaskTokens}}
    for handler in config["handlers"].values():
        handler["filters"] = ["tokens"]
    return config


def main(argv=None):
    args = parser().parse_args(strip_ros_args(sys.argv[1:] if argv is None else argv))
    import uvicorn

    from jarvis_gateway.app import create_app

    settings = settings_from(args)
    if args.fake:
        from jarvis_gateway.adapters.fake import FakeJarvis

        port = FakeJarvis(scenario=args.scenario)
    else:
        from jarvis_gateway.adapters.ros2 import RosPort  # rclpy only here

        port = RosPort(namespace=args.namespace)
    app = create_app(settings, port)
    tls = {"ssl_certfile": settings.tls_cert, "ssl_keyfile": settings.tls_key} if settings.tls_cert else {}
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=args.log_level,
        log_config=log_config(),
        loop="asyncio",
        **tls,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
