# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""A made-up history for robots that don't exist, written where robots upload theirs: to try the
fleet's history (the dashboard's Fleet page, `jarvis-lake fleet`) without waiting a week.

    jarvis-lake demo-history s3://bucket/prefix --robots amr-7,amr-8,amr-9 --days 7

Each robot gets its own habits (one's lidar drops out more, another hits its e-stop), so the robots
differ the way real ones do. The events are what Jarvis records: fault transitions, incidents,
recoveries (a rule restarting what hangs, some failing and escalating), operators clearing latched
faults; one file per robot and day (`demo_history_<day>.parquet`), next to real uploads. Deterministic
for a seed. Never point it at a real fleet's storage: it is made up.
"""

import dataclasses
import datetime
import random

import pyarrow.fs as pafs
import pyarrow.parquet as pq

from jarvis_lake import model, upload


@dataclasses.dataclass
class Kind:
    code: str
    component: str
    severity: str
    per_day: float  # episodes a day, before the robot's own factor
    mttr_s: float  # typical time to clear
    plan: str = ""  # the rule's recovery, if any
    success: float = 0.0  # how often the recovery works
    latched: bool = False  # cleared by an operator


KINDS = [
    Kind("sensor.scan.stale", "lidar", "ERROR", 2.0, 8, "restart-lifecycle-node", 0.9),
    Kind("diag.scan_relay.output_rate", "lidar", "WARN", 3.0, 20),
    Kind("ros.action.navigate_to_pose.aborted", "nav", "ERROR", 1.5, 40, "clear-costmaps", 0.8),
    Kind("diag.base.motor_driver", "base", "ERROR", 0.5, 120, "restart-lifecycle-node", 0.5),
    Kind("safety.estop.engaged", "base", "CRITICAL", 0.4, 300, latched=True),
    Kind("ros.log.error", "planner", "ERROR", 1.0, 60),
]

# Each robot's habits: a factor per code (default 1), so robots differ.
HABITS = [
    {},
    {"sensor.scan.stale": 3.0, "diag.scan_relay.output_rate": 2.0},  # a flaky lidar cable
    {"safety.estop.engaged": 3.0, "ros.action.navigate_to_pose.aborted": 2.0},  # a busy aisle
    {"diag.base.motor_driver": 4.0},  # a tired motor
]


class _Robot:
    def __init__(self, name, rng):
        self.name, self.rng, self.seq, self.n = name, rng, 0, 0

    def event(self, t, kind, code, severity, component, message="", correlation="", **attrs):
        self.seq += 1
        return {
            "id": f"{self.name}-demo-{self.seq}",
            "seq": self.seq,
            "stamp": t.isoformat().replace("+00:00", "Z"),
            "recv_stamp": t.isoformat().replace("+00:00", "Z"),
            "source": {"robot": self.name, "node": f"/{component}", "component": component, "host": ""},
            "kind": kind,
            "code": code,
            "severity": severity,
            "message": message,
            "attrs": attrs,
            "correlation_id": correlation,
        }

    def uid(self, prefix):
        self.n += 1
        return f"{prefix}-{self.name}-{self.n:05d}"

    def episode(self, t, k):
        """One fault episode: confirmed, maybe an incident and a recovery, cleared."""
        rng, out = self.rng, []
        fault, incident = self.uid("f"), self.uid("i") if k.severity in ("ERROR", "CRITICAL") else ""
        lasts = max(1.0, rng.lognormvariate(0, 0.6) * k.mttr_s)

        def state(at, new, prev):
            return self.event(
                at,
                "FAULT_STATE",
                k.code,
                "INFO" if new == "CLEARED" else k.severity,
                k.component,
                f"{k.code} {new}",
                incident,
                fault_id=fault,
                state=new,
                prev_state=prev,
                occurrences=1,
            )

        def incident_event(at, what, new):
            return self.event(
                at,
                "SYSTEM",
                f"jarvis.incident.{what}",
                k.severity,
                k.component,
                f"{k.code} on {k.component}",
                incident,
                incident_id=incident,
                state=new,
            )

        out.append(self.event(t, "FAULT_RAISE", k.code, k.severity, k.component, f"{k.code} raised"))
        out.append(state(t, "PENDING", ""))
        confirmed = t + datetime.timedelta(seconds=1)
        out.append(state(confirmed, "LATCHED" if k.latched else "ACTIVE", "PENDING"))
        if incident:
            out.append(incident_event(confirmed, "opened", "OPEN"))
        cleared = confirmed + datetime.timedelta(seconds=lasts)
        if k.plan:
            execution, ok = self.uid("x"), rng.random() < k.success
            started = confirmed + datetime.timedelta(seconds=1)
            done = started + datetime.timedelta(seconds=min(lasts, 30))
            common = {"execution_id": execution, "plan": k.plan, "requester": "RULE", "dry_run": False}
            out.append(
                self.event(
                    started,
                    "RECOVERY",
                    "jarvis.recovery.started",
                    "INFO",
                    k.component,
                    f"started {k.plan}",
                    incident,
                    phase="STARTED",
                    **common,
                )
            )
            out.append(
                self.event(
                    done,
                    "RECOVERY",
                    f"jarvis.recovery.{'succeeded' if ok else 'failed'}",
                    "INFO" if ok else "ERROR",
                    k.component,
                    k.plan,
                    incident,
                    phase="SUCCEEDED" if ok else "FAILED",
                    **common,
                )
            )
            if not ok:  # escalated: a person fixes it, later
                out.append(incident_event(done, "escalated", "ESCALATED"))
                cleared = done + datetime.timedelta(seconds=rng.uniform(300, 1800))
        if k.latched:
            out.append(
                self.event(
                    cleared,
                    "OPERATOR",
                    "jarvis.operator.clear",
                    "INFO",
                    k.component,
                    f"cleared {k.code}",
                    incident,
                    operator="olga",
                )
            )
        out.append(state(cleared, "CLEARED", "LATCHED" if k.latched else "ACTIVE"))
        if incident:
            out.append(incident_event(cleared, "resolved", "RESOLVED"))
        return out


def events(robots, days, until=None, seed=7):
    """{(robot, day): [events]} over the `days` before `until`."""
    until = until or datetime.datetime.now(tz=datetime.UTC)
    start = (until - datetime.timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    out = {}
    for i, name in enumerate(robots):
        rng = random.Random(f"{seed}-{name}")
        robot, habits = _Robot(name, rng), HABITS[i % len(HABITS)]
        episodes = []
        for k in KINDS:
            rate = k.per_day * habits.get(k.code, 1.0)
            t = start + datetime.timedelta(seconds=rng.expovariate(rate / 86400))
            while t < until:
                episodes.append((t, k))
                t += datetime.timedelta(seconds=rng.expovariate(rate / 86400))
        for t, k in sorted(episodes, key=lambda e: e[0]):
            for e in robot.episode(t, k):
                if datetime.datetime.fromisoformat(e["stamp"].replace("Z", "+00:00")) < until:
                    out.setdefault((name, e["stamp"][:10]), []).append(e)
    return out


def write(target, robots, days=7, until=None, seed=7):
    """Writes the made-up history where the robots upload. Returns the files written."""
    fs, base = pafs.FileSystem.from_uri(target)
    base = base.rstrip("/")
    written = []
    for (robot, day), evs in sorted(events(robots, days, until, seed).items()):
        path = f"{base}/robots/{upload.robot_name(robot)}/events/date={day}/demo_history_{day}.parquet"
        if isinstance(fs, pafs.LocalFileSystem):
            fs.create_dir(path.rsplit("/", 1)[0], recursive=True)
        pq.write_table(
            model.table([model.row_from_event(e, "demo-history") for e in evs]), path, filesystem=fs
        )
        written.append(path)
    return written
