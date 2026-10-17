# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""jarvis-agent: Jarvis' analyst.

    jarvis-agent analyze INCIDENT [--gateway URL | --replay events.jsonl [--plans plans.yaml]] [--json]
    jarvis-agent run [--gateway URL] [--config agent.yaml] [--publish ros|stdout]

`analyze` explains one incident and prints the insight. `run` watches the live stream, analyzes
incidents that open at ERROR or worse and recoveries that fail, and publishes the insights (as
INSIGHT events on /jarvis/ingest, where the dashboard shows them). With a model configured
(model.name, $JARVIS_AGENT_MODEL or --model), it investigates with read-only tools; without one, or
when the model cannot answer, the heuristic analyst does. At autonomy supervised or autonomous,
`run` sends the model's proposals to the recovery gates as requester AGENT; `analyze` only advises.
"""

import argparse
import asyncio
import contextlib
import json
import logging
import pathlib
import sys
import time

from jarvis_agent import __version__
from jarvis_agent.config import AgentConfig
from jarvis_agent.core.agent import Agent
from jarvis_agent.core.policy import AutonomyPolicy
from jarvis_agent.core.triggers import TriggerPolicy
from jarvis_agent.core.windows import WindowManager
from jarvis_agent.events import iso_ns
from jarvis_agent.model import Trigger, TriggerType

LLM_INSTALL = "pip install --require-hashes -r requirements-llm.txt (jarvis_agent's, hash-locked)"


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
    footer = f"{insight.analyst} analyst{' (degraded)' if insight.degraded else ''}"
    if insight.model:
        m = insight.model
        footer += f" · {m.name} · {m.input_tokens} in, {m.output_tokens} out"
        if m.cost_usd:
            footer += f" · ${m.cost_usd:.4f}"
    lines += ["", f"{footer} · {insight.insight_id}"]
    return "\n".join(lines)


def parser():
    p = argparse.ArgumentParser(prog="jarvis-agent", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--config", help="agent.yaml (schema jarvis.agent/v1)")
    p.add_argument("--gateway", help="the gateway's URL (default from the config: http://127.0.0.1:8080)")
    p.add_argument("--model", help="a LiteLLM model string (default from the config or $JARVIS_AGENT_MODEL)")
    p.add_argument("--heuristic", action="store_true", help="no model: the heuristic analyst only")
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


def make_analyst(config, query, proposals=False):
    """The model analyst for the configured model, or None (the heuristic analyst). Exits when the
    model cannot be used: litellm missing, or a model that does not call tools."""
    if not config.model:
        return None
    from jarvis_agent.core.analyst import Budget, LLMAnalyst

    def client(name):
        try:
            from jarvis_agent.llm.litellm_client import LiteLLMClient

            c = LiteLLMClient(
                name,
                api_base=config.api_base,
                api_key=config.api_key(),
                timeout_s=config.timeout_s,
                max_output_tokens=config.max_output_tokens,
            )
        except ImportError:
            raise SystemExit(f"model {name}: litellm is not installed: {LLM_INSTALL}") from None
        if not config.assume_tools and not c.supports_tools():
            raise SystemExit(
                f"model {name}: LiteLLM does not know it to call tools. Pick another, or set "
                "model.assume_tools: true if it does"
            )
        return c

    return LLMAnalyst(
        client(config.model),
        query,
        budget=Budget(config.usd_per_hour, config.usd_per_day),
        max_tool_calls=config.max_tool_calls,
        tokens_per_analysis=config.tokens_per_analysis,
        context_tokens=config.context_tokens,
        redact=config.redact,
        traces_dir=config.traces_dir,
        skip_when_conclusive=config.skip_when_conclusive,
        clients={TriggerType(t): client(name) for t, name in config.per_trigger.items()},
        proposals=proposals,
    )


def make_agent(config, query, sink=None, recovery=None):
    proposals = recovery is not None and config.level in ("supervised", "autonomous")
    return Agent(
        query,
        sink=sink,
        recovery=recovery,
        analyst=make_analyst(config, query, proposals),
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
    recovery = None
    if args.publish == "ros":
        from jarvis_agent.adapters.ros2 import RosAgent  # rclpy only here

        sink = recovery = RosAgent()
    else:
        sink = StdoutSink()
    query = GatewayQuery(config.gateway_url, config.token)
    agent = make_agent(config, query, sink, recovery)
    if recovery is not None:  # questions over ROS (the gateway's /agent/ask)
        recovery.serve(lambda incident_id, question, progress: agent.ask(incident_id, question, progress))
    policy = TriggerPolicy(config.min_severity, config.settle_s, config.max_per_hour)
    logging.getLogger("jarvis_agent").info(
        "watching %s (autonomy %s, %s)",
        config.gateway_url,
        config.level,
        f"model {config.model}" if config.model else "heuristic analyst",
    )
    with contextlib.suppress(KeyboardInterrupt):
        started = time.time_ns()

        def catch_up():  # what happened while the agent (or the gateway) was starting
            return query.seq_before(iso_ns(started - int(config.catch_up_s * 1e9)))

        asyncio.run(run(agent, policy, stream(config.gateway_url, config.token, catch_up=catch_up)))
    return 0


def main(argv=None):
    args = parser().parse_args(strip_ros_args(sys.argv[1:] if argv is None else argv))
    config = AgentConfig.load(args.config, gateway_url=args.gateway, model=args.model)
    if args.heuristic:
        config.model = ""
    return {"analyze": cmd_analyze, "run": cmd_run}[args.command](args, config)


if __name__ == "__main__":
    sys.exit(main())
