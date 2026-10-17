# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Prometheus metrics (/metrics): what flows through the gateway, and Jarvis' state as it sees it."""

from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

SEVERITIES = ("DEBUG", "INFO", "WARN", "ERROR", "CRITICAL")


class Metrics:
    content_type = CONTENT_TYPE_LATEST

    def __init__(self):
        self.registry = CollectorRegistry()
        r = self.registry
        self.events = Counter("jarvis_events", "Events received", ["kind", "severity"], registry=r)
        self.faults = Gauge("jarvis_faults_open", "Open faults (not muted)", ["severity"], registry=r)
        self.muted = Gauge("jarvis_faults_muted", "Faults muted as symptoms", registry=r)
        self.executions = Gauge(
            "jarvis_executions_live", "Executions running or waiting", ["state"], registry=r
        )
        self.mode = Gauge("jarvis_recovery_enabled", "1 when recovery is ENABLED", registry=r)
        self.clients = Gauge("jarvis_gateway_stream_clients", "WebSocket clients", registry=r)
        self.requests = Counter(
            "jarvis_gateway_commands", "Commands sent to Jarvis", ["command", "outcome"], registry=r
        )
        self.notifications = Counter(
            "jarvis_notifications", "Webhook deliveries", ["webhook", "outcome"], registry=r
        )
        self.lake_rows = Gauge("jarvis_lake_rows", "Events in the lake", registry=r)
        self.lake_pending = Gauge("jarvis_lake_splits_pending", "Splits not converted yet", registry=r)

    def on_state(self, kind, payload):
        """A listener for LiveState."""
        if kind == "event":
            self.events.labels(payload.get("kind", "?"), payload.get("severity", "?")).inc()
        elif kind == "faults":
            counts = dict.fromkeys(SEVERITIES, 0)
            muted = 0
            for f in payload:
                if f.get("muted_by"):
                    muted += 1
                else:
                    counts[f.get("severity", "INFO")] = counts.get(f.get("severity", "INFO"), 0) + 1
            for severity, n in counts.items():
                self.faults.labels(severity).set(n)
            self.muted.set(muted)
        elif kind == "executions":
            states = {}
            for x in payload.get("executions") or []:
                states[x["state"]] = states.get(x["state"], 0) + 1
            for state in ("PENDING_APPROVAL", "QUEUED", "RUNNING", "FAILED"):
                self.executions.labels(state).set(states.get(state, 0))
            self.mode.set(1 if payload.get("mode") == "ENABLED" else 0)

    def on_delivery(self, record):
        self.notifications.labels(record["webhook"], "ok" if record.get("ok") else "failed").inc()

    def render(self, lake_status=None):
        if lake_status:
            self.lake_rows.set(lake_status.get("rows") or 0)
            self.lake_pending.set(lake_status.get("splits_pending") or 0)
        return generate_latest(self.registry)
