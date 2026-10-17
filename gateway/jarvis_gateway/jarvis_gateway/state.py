# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""What the gateway holds live: the latest fault and execution snapshots, and the recent events the
lake does not have yet (it converts a split only once the recorder closes it).

Listeners (WebSocket clients, the notifier, metrics) are called on the event loop for every new
event and snapshot.
"""

import threading
import time

from jarvis_lake import model

from jarvis_gateway.port import Sink


class RecentStore:
    """Events by seq, bounded: the newest `capacity` are kept. Thread-safe."""

    def __init__(self, capacity=20000):
        self.capacity = capacity
        self._events = {}  # seq -> schema JSON
        self._rows = {}  # seq -> lake row
        self._lock = threading.Lock()
        self.version = 0
        self._table = None
        self._table_version = -1

    def __len__(self):
        return len(self._events)

    def add(self, event):
        """Adds an event; False if it was already there (live and backfill overlap)."""
        seq = int(event.get("seq") or 0)
        if not seq:
            return False
        with self._lock:
            if seq in self._events:
                return False
            self._events[seq] = event
            self._rows[seq] = model.row_from_event(event)
            if len(self._events) > self.capacity:
                for old in sorted(self._events)[: len(self._events) - self.capacity]:
                    del self._events[old]
                    del self._rows[old]
            self.version += 1
            return True

    def trim_below(self, seq):
        """Drops what the lake has (seq <= its newest), keeping the rest."""
        with self._lock:
            old = [s for s in self._events if s <= seq]
            for s in old:
                del self._events[s]
                del self._rows[s]
            if old:
                self.version += 1
            return len(old)

    def table(self):
        """The events as an Arrow table (lake schema), rebuilt only when they changed."""
        with self._lock:
            if self._table_version != self.version:
                self._table = model.table([self._rows[s] for s in sorted(self._rows)])
                self._table_version = self.version
            return self._table

    def max_seq(self):
        with self._lock:
            return max(self._events, default=0)


class LiveState(Sink):
    def __init__(self, capacity=20000):
        self.recent = RecentStore(capacity)
        self.faults = None  # list of faults (latched snapshot), None until it arrives
        self.executions = None  # {"mode", "executions", "recent"}, None until it arrives
        self.last_event_monotonic = None
        self.events_received = 0
        self.robot = ""  # from the events' source
        self._listeners = []

    def listen(self, callback):
        """callback(kind, payload), kind in event | faults | executions. Returns an unlisten()."""
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback) if callback in self._listeners else None

    def _notify(self, kind, payload):
        for callback in list(self._listeners):
            callback(kind, payload)

    # -- the Sink a port delivers to ---------------------------------------------------------------

    def on_event(self, event):
        self.robot = (event.get("source") or {}).get("robot") or self.robot
        if self.recent.add(event):
            self.events_received += 1
            self.last_event_monotonic = time.monotonic()
            self._notify("event", event)

    def on_faults(self, faults):
        self.faults = faults
        self._notify("faults", faults)

    def on_executions(self, snapshot):
        self.executions = snapshot
        self._notify("executions", snapshot)

    # -- reading -----------------------------------------------------------------------------------

    def fault(self, fault_id):
        return next((f for f in self.faults or [] if f["fault_id"] == fault_id), None)

    def execution(self, execution_id):
        snap = self.executions or {}
        for x in (snap.get("executions") or []) + (snap.get("recent") or []):
            if x["execution_id"] == execution_id:
                return x
        return None
