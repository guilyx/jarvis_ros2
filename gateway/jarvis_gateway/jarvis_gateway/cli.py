# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""jarvis-gateway: the HTTP API, live stream, metrics and dashboard of one robot's Jarvis.

    jarvis-gateway [--config gateway.yaml] [--host H] [--port P] [--data-dir D] [--dashboard DIR]
    jarvis-gateway --fake [--scenario]      an in-memory Jarvis: no ROS, for dashboard work

Settings come from --config (schema jarvis.gateway/v1), then $JARVIS_GATEWAY_TOKENS, then the
flags. The API is documented at /docs once it runs.
"""

import argparse
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
    p.add_argument("--dashboard", dest="dashboard_dir", help="the dashboard's built files")
    p.add_argument("--namespace", default="/jarvis", help="Jarvis' ROS namespace")
    p.add_argument("--fake", action="store_true", help="an in-memory Jarvis instead of ROS")
    p.add_argument("--scenario", action="store_true", help="with --fake: make things happen")
    p.add_argument(
        "--anonymous-role",
        choices=["none", "viewer", "operator", "admin"],
        help="what requests without a token may do (default from the config: viewer)",
    )
    p.add_argument("--log-level", default="info")
    p.add_argument("--version", action="version", version=__version__)
    return p


def settings_from(args):
    s = Settings.load(
        args.config, host=args.host, port=args.port, data_dir=args.data_dir, dashboard_dir=args.dashboard_dir
    )
    if args.anonymous_role:
        s.anonymous = None if args.anonymous_role == "none" else Principal("anonymous", args.anonymous_role)
    return s


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
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=args.log_level, loop="asyncio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
