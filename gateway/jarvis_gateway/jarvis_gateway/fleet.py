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
at once, within `timeout_s`; a robot that doesn't answer is shown offline, with why. A robot that
takes its rules and plans from the fleet (config_from, roadmap 9.4) says which revision it runs and
which it refused: the dashboard shows a rollout across the robots (roadmap 9.5). A token (a
viewer's is enough) comes from the environment variable `token_env`, so it stays out of the file.
This gateway's own robot comes first, from its own state.

The fleet's history (roadmap 9.3): where the robots upload theirs (jarvis_lake.upload), read in
place with the lake's views, for the dashboard's Fleet page:

    fleet:
      robots: [...]
      history: s3://acme-robots/jarvis          # credentials: the gateway's environment (AWS_...)

    GET /api/v1/fleet/history?hours=168  ->  {robots: [...], top_codes: [...], trend: [...]}

It reads the uploaded Parquet at most once a minute per window (`History`), off the event loop.

Watching the fleet (roadmap 9.6): nobody keeps the Fleet page open all day. With `watch`, the
gateway looks at the robots every `period_s` by itself and alerts the webhooks of `channel` when a
robot has been unreachable for `offline_after_s` (a blip is not offline), when it is back, when it
refuses the fleet's rules and plans (config_from) and when it takes them again:

    fleet:
      robots: [...]
      watch: { period_s: 30, offline_after_s: 120, channel: fleet }    # or watch: true

    webhooks:
      - { name: robots, url: https://hooks.slack.com/..., channels: [fleet], format: slack }

    GET /api/v1/fleet/alerts  ->  {watching, alerts: [the last ones, newest first]}
"""

import asyncio
import collections
import dataclasses
import datetime
import os
import time

import httpx
import pyarrow
import pyarrow.fs as pafs
from jarvis_lake import fleet as lake_fleet
from jarvis_lake import upload

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
class Watch:
    period_s: float = 30.0  # how often the robots are looked at
    offline_after_s: float = 120.0  # unreachable this long is offline: a blip is not
    channel: str = "fleet"  # the webhooks' channel the alerts go to

    @classmethod
    def load(cls, raw):
        w = (
            cls()
            if raw is True
            else cls(**{k: raw[k] for k in ("period_s", "offline_after_s", "channel") if k in raw})
        )
        w.period_s, w.offline_after_s, w.channel = float(w.period_s), float(w.offline_after_s), str(w.channel)
        if w.period_s <= 0 or w.offline_after_s < 0:
            raise ValueError("fleet.watch: period_s must be positive, offline_after_s not negative")
        return w


@dataclasses.dataclass
class Settings:
    robots: list = dataclasses.field(default_factory=list)  # [Robot]
    timeout_s: float = 3.0
    history: str = ""  # where the robots upload their history (jarvis_lake.upload), if they do
    watch: Watch | None = None  # look at the robots without anyone on the page, and alert

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
        history = str(raw.get("history") or "")
        if history:
            try:
                pafs.FileSystem.from_uri(history)
            except (ValueError, pyarrow.ArrowException) as e:
                raise ValueError(f"fleet.history: {upload.redact(history)}: {e}") from e
        watch = Watch.load(raw["watch"]) if raw.get("watch") else None
        return cls(robots=robots, timeout_s=float(raw.get("timeout_s", 3.0)), history=history, watch=watch)


class History:
    """The fleet's uploaded history, summarized per window and kept for `ttl_s`: the Fleet page
    refreshes, the bucket is read once a minute."""

    def __init__(self, target, ttl_s=60.0, clock=time.monotonic):
        self.target = target
        self.ttl_s = ttl_s
        self.clock = clock
        self._cache = {}  # hours -> (when, summary)
        self._lock = asyncio.Lock()

    async def summary(self, hours):
        async with self._lock:
            cached = self._cache.get(hours)
            if cached and self.clock() - cached[0] < self.ttl_s:
                return cached[1]
            summary = await asyncio.to_thread(lake_fleet.summary, self.target, hours)
            summary["target"] = upload.redact(self.target)
            summary["updated_at"] = time.time()
            self._cache[hours] = (self.clock(), summary)
            return summary


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


def _duration(seconds):
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


class Watcher:
    """What changed from one look at the fleet to the next, as alerts: offline (after the grace),
    back online, rules and plans refused, taken again. The gateway looks and sends them."""

    def __init__(self, watch, clock=time.time, keep=200):
        self.watch, self.clock = watch, clock
        self.alerts = collections.deque(maxlen=keep)  # newest last
        self._down = {}  # robot -> since when it doesn't answer
        self._offline = {}  # robot -> since when, once alerted
        self._refused = {}  # robot -> the revision it refused, once alerted
        self._n = 0

    def observe(self, robots):
        """One look (fleet.robots()): returns the alerts it raises. This gateway's own robot
        alerts by its own rules."""
        now, out = self.clock(), []
        for r in robots:
            if r.get("local"):
                continue
            name = r["name"]
            if not r.get("online"):
                since = self._down.setdefault(name, now)
                if name not in self._offline and now - since >= self.watch.offline_after_s:
                    self._offline[name] = since
                    why = r.get("error") or "no answer"
                    out.append(
                        self._alert(
                            r, "robot_offline", "ERROR", f"offline: {why} for {_duration(now - since)}"
                        )
                    )
                continue
            self._down.pop(name, None)
            if name in self._offline:
                since = self._offline.pop(name)
                out.append(
                    self._alert(r, "robot_online", "INFO", f"back online after {_duration(now - since)}")
                )
            config = r.get("config") or {}
            refused = config.get("rejected")
            if refused and self._refused.get(name) != refused:
                self._refused[name] = refused
                message = f"refused rules and plans {refused}: {config.get('error') or '?'}"
                out.append(
                    self._alert(r, "config_rejected", "WARN", f"{message}; still on {config.get('revision')}")
                )
            elif not refused and name in self._refused:
                before = self._refused.pop(name)
                message = f"runs rules and plans {config.get('revision')} (refused {before} before)"
                out.append(self._alert(r, "config_accepted", "INFO", message))
        return out

    def _alert(self, robot, what, severity, message):
        """An alert as an event of the stream: what webhooks are sent."""
        self._n += 1
        at = self.clock()
        stamp = datetime.datetime.fromtimestamp(at, tz=datetime.UTC).isoformat().replace("+00:00", "Z")
        name = robot["name"]
        event = {
            "id": f"fleet-{int(at * 1000)}-{self._n}",
            "stamp": stamp,
            "kind": "SYSTEM",
            "code": f"jarvis.fleet.{what}",
            "severity": severity,
            "message": message,
            "source": {"robot": name, "component": "fleet", "node": "", "host": ""},
            "attrs": {"channel": self.watch.channel, "robot": name, "url": robot.get("url", "")},
        }
        self.alerts.append(event)
        return event


def config_summary(status):
    """A robot's rules and plans at a glance, from config_sync's status (None without config_from):
    the revision it runs, the one it refused and why."""
    if not status:
        return None
    rejected = status.get("rejected") or {}
    errors = rejected.get("errors") or []
    return {
        "revision": status.get("revision"),
        "state": status.get("state", ""),
        "source": status.get("source", ""),
        "since": status.get("applied_at"),
        "rejected": rejected.get("revision") if rejected else None,
        "error": errors[0] if errors else "",
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
            "config": config_summary(h.get("config")),
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
