# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The fleet view (roadmap 7.4): one dashboard over several robots' gateways, read-only.

Every robot keeps its own Jarvis and gateway. One of them (or a gateway on an ops machine) lists the
others, and its dashboard shows them side by side; a click opens the robot's own dashboard:

    fleet:
      - { name: amr-1, url: http://amr-1.local:8080 }
      - { name: amr-2, url: https://amr-2.local:8443, token_env: JARVIS_AMR2_TOKEN }

    GET /api/v1/fleet  ->  {"robots": [{name, url, online, robot, status, mode, faults, worst, ...}]}

The gateway asks each robot's gateway (its health, open faults and recovery mode) when asked, all
at once, within `timeout_s`; a robot that doesn't answer is shown offline, with why. A token (a
viewer's is enough) comes from the environment variable `token_env`, so it stays out of the file.
This gateway's own robot comes first, from its own state.
"""

import asyncio
import dataclasses
import os
import time

import httpx

SEVERITY_ORDER = ("CRITICAL", "ERROR", "WARN", "INFO", "DEBUG")
OPEN = ("PENDING", "ACTIVE", "LATCHED")


@dataclasses.dataclass
class Robot:
    name: str
    url: str
    token_env: str = ""

    @property
    def token(self):
        return os.environ.get(self.token_env, "") if self.token_env else ""


@dataclasses.dataclass
class Settings:
    robots: list = dataclasses.field(default_factory=list)  # [Robot]
    timeout_s: float = 3.0

    @classmethod
    def load(cls, raw):
        if isinstance(raw, list):  # the short form: just the robots
            raw = {"robots": raw}
        robots = []
        for entry in raw.get("robots") or []:
            url = str(entry["url"]).rstrip("/")
            if not url.startswith(("http://", "https://")):
                raise ValueError(f"fleet: {entry.get('name', url)}: url must start with http:// or https://")
            robots.append(
                Robot(name=str(entry.get("name") or url), url=url, token_env=str(entry.get("token_env", "")))
            )
        names = [r.name for r in robots]
        if len(set(names)) != len(names):
            raise ValueError("fleet: robot names must be unique")
        return cls(robots=robots, timeout_s=float(raw.get("timeout_s", 3.0)))


def summarize(faults, mode="UNKNOWN"):
    """A robot at a glance: open faults by severity, the worst one, incidents, the recovery mode."""
    open_faults = [f for f in faults or [] if f.get("state") in OPEN and not f.get("muted_by")]
    counts = {s: 0 for s in SEVERITY_ORDER[:3]}
    for f in open_faults:
        if f.get("severity") in counts:
            counts[f["severity"]] += 1
    rank = {s: i for i, s in enumerate(SEVERITY_ORDER)}
    worst = min(
        open_faults,
        key=lambda f: (
            rank.get(f.get("severity"), len(rank)),
            f.get("state") != "LATCHED",
            f.get("code", ""),
        ),
        default=None,
    )
    return {
        "mode": mode,
        "faults": {**counts, "total": len(open_faults)},
        "worst": (
            {
                "code": worst.get("code", ""),
                "severity": worst.get("severity", ""),
                "state": worst.get("state", ""),
                "component": (worst.get("source") or {}).get("component", ""),
                "message": worst.get("message", ""),
                "fault_id": worst.get("fault_id", ""),
            }
            if worst
            else None
        ),
        "incidents": len({f["incident_id"] for f in open_faults if f.get("incident_id")}),
        "latched": sum(1 for f in open_faults if f.get("state") == "LATCHED"),
    }


async def _robot(client, robot, timeout):
    """One robot, from its gateway."""
    headers = {"Authorization": f"Bearer {robot.token}"} if robot.token else {}
    started = time.monotonic()
    try:
        health, faults, recoveries = await asyncio.gather(
            *(
                client.get(f"{robot.url}/api/v1/{path}", headers=headers, timeout=timeout)
                for path in ("health", "faults", "recoveries?limit=1")
            )
        )
        for r in (health, faults, recoveries):
            r.raise_for_status()
        h = health.json()
        out = {
            "name": robot.name,
            "url": robot.url,
            "online": True,
            "robot": h.get("robot", ""),
            "status": h.get("status", ""),
            "connected": bool(h.get("connected")),
            "latency_ms": round((time.monotonic() - started) * 1000),
            **summarize(faults.json().get("faults"), recoveries.json().get("mode", "UNKNOWN")),
        }
    except httpx.HTTPStatusError as e:
        why = (
            "refused the token" if e.response.status_code in (401, 403) else f"HTTP {e.response.status_code}"
        )
        out = _offline(robot, why)
    except (httpx.HTTPError, ValueError) as e:
        out = _offline(robot, _why(e))
    return out


def _why(error):
    if isinstance(error, httpx.TimeoutException):
        return "no answer"
    if isinstance(error, httpx.ConnectError):
        return "unreachable"
    return type(error).__name__


def _offline(robot, why):
    return {"name": robot.name, "url": robot.url, "online": False, "error": why, **summarize([])}


async def robots(settings, own=None, client=None):
    """The fleet: this robot first (`own`, already summarized), then each listed robot's gateway."""
    owned = client is None
    client = client or httpx.AsyncClient(follow_redirects=False)
    try:
        others = await asyncio.gather(*(_robot(client, r, settings.timeout_s) for r in settings.robots))
    finally:
        if owned:
            await client.aclose()
    return ([own] if own else []) + list(others)
