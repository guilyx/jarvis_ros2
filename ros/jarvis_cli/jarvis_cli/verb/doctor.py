# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""ros2 jarvis doctor: is Jarvis ready on this robot? Before handing a robot over, or when something
looks off: Jarvis's nodes, the recovery mode, plans and rules, open faults, the recordings and the
disk they fill, the clock, the gateway. Each check says what to do when it fails. Exit code 1 when
something must be fixed (warnings don't count)."""

import dataclasses
import json
import os
import platform
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import rclpy
from jarvis_interfaces.msg import ExecutionArray, Fault, FaultArray
from jarvis_interfaces.srv import ListPlans, ListRules
from lifecycle_msgs.srv import GetState
from rcl_interfaces.srv import GetParameters
from ros2cli.verb import VerbExtension

from jarvis_cli import api

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
MARK = {OK: "✓", WARN: "!", FAIL: "✗", INFO: "·"}
CORE = [
    ("event hub", "/jarvis/event_hub"),
    ("fault manager", "/jarvis/fault_manager"),
    ("rule engine", "/jarvis/rule_engine"),
    ("recovery", "/jarvis/recovery"),
]
RECORDER = "/jarvis/recorder"
EXPECTED = [node for _, node in CORE] + [RECORDER]
GIB = 1024**3


@dataclasses.dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""


# -- what each finding means (no ROS: tested on their own) -------------------------------------------


def lifecycle_check(name, node, state):
    """`state`: the node's lifecycle state label, or None when the node isn't on the graph."""
    if state is None:
        return Check(
            name,
            FAIL,
            f"{node} is not running",
            "ros2 launch jarvis_bringup jarvis.launch.py, or see its log",
        )
    if state != "active":
        return Check(name, FAIL, f"{node} is {state}", f"ros2 lifecycle set {node} activate")
    return Check(name, OK, "active")


def mode_check(mode, maintenance=()):
    under = ", ".join(m.component for m in maintenance)
    extra = f"; under maintenance: {under}" if under else ""
    if mode == "ENABLED":
        return Check("recovery mode", OK, "ENABLED" + extra)
    if mode == "DRY_RUN":
        return Check(
            "recovery mode",
            WARN,
            "DRY_RUN: Jarvis decides, nothing moves" + extra,
            'ros2 jarvis mode ENABLED --reason "..." once you trust it',
        )
    if mode == "DISABLED":
        return Check(
            "recovery mode",
            WARN,
            "DISABLED: the kill switch is on" + extra,
            'ros2 jarvis mode ENABLED --reason "..."',
        )
    return Check("recovery mode", FAIL, "unknown: no /jarvis/executions", "is the recovery node running?")


def plans_check(count):
    if not count:
        return Check(
            "plans",
            WARN,
            "none loaded: nothing can be recovered",
            "plans:= a directory of jarvis.plans/v1 files",
        )
    return Check("plans", OK, f"{count} loaded")


def rules_check(rules):
    if not rules:
        return Check(
            "rules",
            WARN,
            "none loaded: nothing happens by itself",
            "rules:= a directory of jarvis.rules/v1 files",
        )
    shadow = sum(1 for r in rules if r.enabled and r.dry_run)
    off = sum(1 for r in rules if not r.enabled)
    notes = [f"{shadow} in shadow mode"] if shadow else []
    notes += [f"{off} off"] if off else []
    return Check("rules", OK, f"{len(rules)} loaded" + (f" ({', '.join(notes)})" if notes else ""))


def faults_check(faults):
    open_ = [f for f in faults if f.state in (Fault.STATE_ACTIVE, Fault.STATE_LATCHED)]
    storage = [f for f in open_ if f.code == "jarvis.storage.low"]
    if storage:
        return Check(
            "faults",
            FAIL,
            "jarvis.storage.low: the disk is nearly full, the lake deletes the oldest data",
            "free space under the data directory, or move it to a bigger disk (data_dir:=)",
        )
    latched = [f.code for f in open_ if f.state == Fault.STATE_LATCHED]
    if latched:
        return Check(
            "faults",
            WARN,
            f"{len(open_)} open, latched: {', '.join(sorted(set(latched)))}",
            "a latched fault stays until a human clears it (dashboard: Faults)",
        )
    if not open_:
        return Check("faults", OK, "none open")
    codes = sorted({f.code for f in open_})
    more = f" and {len(codes) - 3} more" if len(codes) > 3 else ""
    return Check("faults", OK, f"{len(open_)} open: {', '.join(codes[:3])}{more}")


def disk_check(data_dir, usage, warn_gib=5.0, fail_gib=1.0):
    """`usage`: shutil.disk_usage of the data directory, or None when it isn't on this machine."""
    if usage is None:
        return Check("data", INFO, f"{data_dir}: not on this machine (run the doctor on the robot)")
    free = usage.free / GIB
    detail = f"{data_dir}: {free:.1f} GiB free of {usage.total / GIB:.0f} GiB"
    if free < fail_gib:
        return Check(
            "data", FAIL, detail, "the lake is deleting the oldest recordings: free space, or a bigger disk"
        )
    if free < warn_gib:
        return Check(
            "data", WARN, detail, f"keep at least {warn_gib:g} GiB free for recordings and black boxes"
        )
    return Check("data", OK, detail)


def recording_check(running, bags):
    """`bags`: how many recordings are in the data directory (None: not on this machine)."""
    if not running:
        return Check(
            "recordings", FAIL, "the recorder is not running", "see jarvis.launch.py's log (/jarvis/recorder)"
        )
    return Check("recordings", OK, "recording" + (f", {bags} recording(s) kept" if bags is not None else ""))


def clock_check(timedatectl=None, chrony=None):
    """`timedatectl`: `timedatectl show -p NTPSynchronized --value`; `chrony`: `chronyc tracking`."""
    fix = "install chrony (or systemd-timesyncd) and point it at your time source"
    if timedatectl is not None:
        if timedatectl.strip() == "yes":
            return Check("clock", OK, "synchronized (NTP)")
        return Check(
            "clock", WARN, "not synchronized: event times drift, fleet history interleaves wrong", fix
        )
    if chrony is not None:
        if "Leap status     : Normal" in chrony:
            return Check("clock", OK, "synchronized (chrony)")
        return Check("clock", WARN, "chrony is not synchronized", fix)
    return Check("clock", INFO, "unknown: neither timedatectl nor chronyc here")


def gateway_check(url, health=None, error=None):
    if error is not None:
        return Check(
            "gateway", WARN, f"{url}: {error}", "gateway:=true when you want the dashboard and the API"
        )
    status = health.get("status", "?")
    if status == "ok":
        return Check("gateway", OK, f"{url}: ok")
    return Check("gateway", WARN, f"{url}: {status}", "see the System page, or GET /api/v1/health")


def unknown(name, node):
    """A check that needs a node which isn't running: no verdict, the node's own check is the problem."""
    return Check(name, INFO, f"unknown: {node} is not running")


def render(header, checks):
    lines = [header, ""]
    for c in checks:
        lines.append(f" {MARK[c.status]} {c.name:<14} {c.detail}")
        if c.fix and c.status in (WARN, FAIL):
            lines.append(f"   {'':<14} → {c.fix}")
    problems = sum(1 for c in checks if c.status == FAIL)
    warnings = sum(1 for c in checks if c.status == WARN)
    lines.append("")
    if not problems and not warnings:
        lines.append("All good.")
    else:
        lines.append(f"{problems} problem(s), {warnings} warning(s).")
    return "\n".join(lines)


# -- what the robot says ------------------------------------------------------------------------------


def _quiet_call(n, srv_type, name, request, timeout=3.0):
    try:
        return api.call(n, srv_type, name, request, timeout=timeout)
    except api.JarvisError:
        return None


def _parameter(n, node, name):
    reply = _quiet_call(n, GetParameters, f"{node}/get_parameters", GetParameters.Request(names=[name]))
    if reply is None or not reply.values:
        return None
    value = reply.values[0]
    return value.string_value or None


def _run(cmd):
    if not shutil.which(cmd[0]):
        return None
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _gateway(url):
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/v1/health", timeout=3) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except (OSError, ValueError) as e:
        return None, f"not reachable ({getattr(e, 'reason', e)})"


def _graph(n, wait=5.0):
    """The nodes on the graph, once Jarvis's are all there or after `wait`. Discovery usually takes
    well under a second, but a lost announcement waits for the next one (every 3 s with Fast DDS):
    a shorter wait would call a running node missing."""
    deadline = time.monotonic() + wait
    names = set()
    while time.monotonic() < deadline:
        rclpy.spin_once(n, timeout_sec=0.1)
        names = {f"{ns.rstrip('/')}/{name}" for name, ns in n.get_node_names_and_namespaces()}
        if all(node in names for node in EXPECTED):
            break
    return names


class DoctorVerb(VerbExtension):
    """Check that Jarvis is ready on this robot: nodes, recovery, rules, faults, disk, clock, gateway."""

    def add_arguments(self, parser, cli_name):
        parser.add_argument("--gateway", default="http://127.0.0.1:8080", help="the gateway's address")
        parser.add_argument("--no-gateway", action="store_true", help="this robot runs no gateway")
        parser.add_argument(
            "--data-dir", default="", help="Jarvis's data directory (default: as the nodes say)"
        )
        parser.add_argument("--json", action="store_true", help="machine-readable")

    @api.run
    def main(self, *, args):
        checks = []
        with api.node() as n:
            graph = _graph(n)
            up = {}
            for label, node in CORE:
                up[node] = node in graph
                reply = (
                    _quiet_call(n, GetState, f"{node}/get_state", GetState.Request()) if up[node] else None
                )
                checks.append(lifecycle_check(label, node, reply.current_state.label if reply else None))
            robot = (up["/jarvis/event_hub"] and _parameter(n, "/jarvis/event_hub", "robot_id")) or "?"
            if up["/jarvis/recovery"]:
                executions = api.latched(n, ExecutionArray, "/jarvis/executions", timeout=2.0)
                checks.append(
                    mode_check(
                        executions.mode if executions else "", executions.maintenance if executions else ()
                    )
                )
                plans = _quiet_call(n, ListPlans, "/jarvis/recovery/list_plans", ListPlans.Request())
                checks.append(plans_check(len(plans.plans) if plans else 0))
            else:
                checks += [unknown("recovery mode", "/jarvis/recovery"), unknown("plans", "/jarvis/recovery")]
            if up["/jarvis/rule_engine"]:
                rules = _quiet_call(n, ListRules, "/jarvis/rule_engine/list_rules", ListRules.Request())
                checks.append(rules_check(rules.rules if rules else []))
            else:
                checks.append(unknown("rules", "/jarvis/rule_engine"))
            state_dir = None
            if up["/jarvis/fault_manager"]:
                faults = api.latched(n, FaultArray, "/jarvis/faults", timeout=2.0)
                checks.append(faults_check(faults.faults if faults else []))
                state_dir = _parameter(n, "/jarvis/fault_manager", "state_dir")
            else:
                checks.append(unknown("faults", "/jarvis/fault_manager"))
        if args.data_dir or state_dir:
            data_dir = Path(args.data_dir or Path(state_dir).parent).expanduser()
            local = data_dir.is_dir()
            checks.append(disk_check(data_dir, shutil.disk_usage(data_dir) if local else None))
        else:
            local, data_dir = False, None
            checks.append(Check("data", INFO, "unknown: pass --data-dir (the fault manager says where)"))
        bags = (
            len([p for p in (data_dir / "bags").iterdir() if p.is_dir()])
            if local and (data_dir / "bags").is_dir()
            else None
        )
        checks.append(recording_check(RECORDER in graph, bags))
        timedatectl = _run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
        checks.append(
            clock_check(timedatectl or None, None if timedatectl else _run(["chronyc", "tracking"]))
        )
        if not args.no_gateway:
            health, error = _gateway(args.gateway)
            checks.append(gateway_check(args.gateway, health, error))

        rmw = os.environ.get("RMW_IMPLEMENTATION") or rclpy.get_rmw_implementation_identifier()
        distro = os.environ.get("ROS_DISTRO", "?")
        domain = os.environ.get("ROS_DOMAIN_ID", "0")
        header = (
            f"Jarvis doctor · {robot} · {platform.system()} {platform.machine()} · "
            f"ROS 2 {distro} · {rmw} · domain {domain}"
        )
        if args.json:
            print(
                json.dumps(
                    {
                        "robot": robot,
                        "arch": platform.machine(),
                        "checks": [dataclasses.asdict(c) for c in checks],
                        "problems": sum(1 for c in checks if c.status == FAIL),
                        "warnings": sum(1 for c in checks if c.status == WARN),
                    },
                    indent=2,
                )
            )
        else:
            print(render(header, checks))
        return 1 if any(c.status == FAIL for c in checks) else 0
