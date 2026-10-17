# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""jarvis-agent: Jarvis' analyst.

    jarvis-agent analyze INCIDENT [--gateway URL | --replay events.jsonl [--plans plans.yaml]] [--json]
    jarvis-agent run [--gateway URL] [--config agent.yaml] [--publish ros|stdout]

`analyze` explains one incident and prints the insight. `run` watches the live stream, analyzes
incidents that open at ERROR or worse and recoveries that fail, and publishes the insights (as
INSIGHT events on /jarvis/ingest, where the dashboard shows them). Without a model configured, the
heuristic analyst answers (milestone 4.1); proposals stay advice.
"""

import argparse
import asyncio
import contextlib
import json
import logging
import pathlib
import sys

from jarvis_agent import __version__
from jarvis_agent.config import AgentConfig
from jarvis_agent.core.agent import Agent
from jarvis_agent.core.policy import AutonomyPolicy
from jarvis_agent.core.triggers import TriggerPolicy
from jarvis_agent.core.windows import WindowManager
from jarvis_agent.model import Trigger, TriggerType


def strip_ros_args(argv):
    out, inside = [], False
    for arg in argv:
        if arg == "--ros-args":
            inside = True
        elif inside and arg == "--":
            inside = False
        elif not inside:
            out.append(arg)
    return out


def render(insight):
    """The insight for a terminal."""
    lines = [f"{insight.assessment}  {insight.title}", ""]
    if insight.summary:
        lines += [insight.summary, ""]
    for i, h in enumerate(insight.hypotheses, 1):
        flag = "  (unsupported)" if h.flagged else ""
        lines.append(f"{i}. {h.cause}  [{h.confidence:.2f}, {len(h.evidence)} evidence]{flag}")
    if insight.recommendations:
        lines.append("")
        for r in insight.recommendations:
            args = " ".join(f"{k}={v}" for k, v in (r.args or {}).items())
            plan = f"  → {r.plan_id} {args}".rstrip() if r.plan_id else ""
            lines.append(f"- {r.kind.lower()}: {r.text}{plan}")
    if insight.facts:
        lines += ["", "facts:"] + [f"  {f}" for f in insight.facts]
    if insight.flags:
        lines += ["", "harness:"] + [f"  {f}" for f in insight.flags]
    lines += [
        "",
        f"{insight.analyst} analyst{' (degraded)' if insight.degraded else ''} · {insight.insight_id}",
    ]
    return "\n".join(lines)


def parser():
    p = argparse.ArgumentParser(prog="jarvis-agent", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--config", help="agent.yaml (schema jarvis.agent/v1)")
    p.add_argument("--gateway", help="the gateway's URL (default from the config: http://127.0.0.1:8080)")
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("analyze", help="explain one incident")
    a.add_argument("incident")
    a.add_argument("--replay", help="events as JSONL (ros2 jarvis export), instead of the gateway")
    a.add_argument("--plans", help="with --replay: the plans (a jarvis.plans/v1 file)")
    a.add_argument("--json", action="store_true", help="print the insight as JSON")
    r = sub.add_parser("run", help="watch the live stream and analyze what goes wrong")
    r.add_argument("--publish", choices=["ros", "stdout"], default="ros")
    r.add_argument("--log-level", default="INFO")
    return p


def make_agent(config, query, sink=None):
    return Agent(
        query,
        sink=sink,
        policy=AutonomyPolicy(config.level, config.min_confidence),
        redact=config.redact,
        windows=WindowManager(query, context_s=config.context_s, sliding_s=config.sliding_s),
    )


def cmd_analyze(args, config):
    if args.replay:
        import yaml

        from jarvis_agent.adapters.replay import ReplayQuery, load_jsonl

        plans = []
        if args.plans:
            plans = (yaml.safe_load(pathlib.Path(args.plans).read_text()) or {}).get("plans") or []
        query = ReplayQuery(load_jsonl(args.replay), plans)
    else:
        from jarvis_agent.adapters.gateway import GatewayQuery

        query = GatewayQuery(config.gateway_url, config.token)
    insight = make_agent(config, query).analyze(Trigger(type=TriggerType.ON_DEMAND, ref=args.incident))
    if insight is None:
        print(f"no incident '{args.incident}'", file=sys.stderr)
        return 1
    print(json.dumps(insight.to_json(), indent=2) if args.json else render(insight))
    return 0


def cmd_run(args, config):
    from jarvis_agent.adapters.gateway import GatewayQuery, stream
    from jarvis_agent.app import StdoutSink, run

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # not every request
    if args.publish == "ros":
        from jarvis_agent.adapters.ros2 import RosInsightSink  # rclpy only here

        sink = RosInsightSink()
    else:
        sink = StdoutSink()
    agent = make_agent(config, GatewayQuery(config.gateway_url, config.token), sink)
    policy = TriggerPolicy(config.min_severity, config.settle_s, config.max_per_hour)
    logging.getLogger("jarvis_agent").info(
        "watching %s (autonomy %s, heuristic analyst)", config.gateway_url, config.level
    )
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(agent, policy, stream(config.gateway_url, config.token)))
    return 0


def main(argv=None):
    args = parser().parse_args(strip_ros_args(sys.argv[1:] if argv is None else argv))
    config = AgentConfig.load(args.config, gateway_url=args.gateway)
    return {"analyze": cmd_analyze, "run": cmd_run}[args.command](args, config)


if __name__ == "__main__":
    sys.exit(main())
