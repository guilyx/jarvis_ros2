# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The lake service: converts splits as the recorder closes them, enforces retention, reports.

It wakes up when poked (the ROS adapter pokes it on `events/write_split`) and every `scan_period`
anyway, so it also works without ROS and catches splits it was not told about (a bag closed at
shutdown, splits left by a crash). Each pass writes lake/status.json for the gateway.
"""

import threading
import time
import traceback

from jarvis_lake import storage


class LakeService:
    def __init__(self, lake, scan_period=30.0, retention_period=600.0, log=print, clock=time.time):
        self.lake = lake
        self.scan_period = scan_period
        self.retention_period = retention_period
        self.log = log
        self.clock = clock
        self._wake = threading.Event()
        self._last_retention = None
        self.last_conversion = None
        self.last_error = ""
        self.converted_total = 0

    def poke(self, closed_file=""):
        """Something closed a split (thread-safe): convert it soon."""
        self._wake.set()

    def run_once(self, recover=False):
        """One pass: convert what is pending, retention when due, status. Never raises."""
        try:
            failed = []
            for result in self.lake.sync(recover=recover):
                if result.failed:
                    failed.append(f"{result.split.name}: {result.error}")
                    self.log(f"{result.split.name}: cannot convert it: {result.error}")
                    continue
                self.converted_total += 1
                self.last_conversion = {
                    "split": str(result.split),
                    "rows": result.rows,
                    "finalized": result.finalized,
                    "truncated": result.truncated,
                    "at": self.clock(),
                }
                how = "recovered from a torn split" if not result.finalized else "converted"
                self.log(f"{result.split.name}: {result.rows} events {how}")
            now = self.clock()
            if self._last_retention is None or now - self._last_retention >= self.retention_period:
                self._last_retention = now
                report = self.lake.enforce_retention()
                deleted = report.deleted_splits + report.deleted_partitions + report.deleted_blackbox
                if deleted:
                    self.log(f"retention: deleted {len(deleted)} item(s), {report.freed_bytes} bytes")
            self.last_error = "; ".join(failed)
        except Exception as e:  # noqa: BLE001 - the service outlives one bad split
            self.last_error = f"{type(e).__name__}: {e}"
            self.log(f"lake pass failed: {self.last_error}\n{traceback.format_exc()}")
        return self.write_status()

    def write_status(self):
        status = {
            "schema": "jarvis.lake.status/v1",
            "updated_at": self.clock(),
            "converted_total": self.converted_total,
            "last_conversion": self.last_conversion,
            "last_error": self.last_error,
        }
        try:
            status.update(self.lake.status())
            storage.write_json(self.lake.layout.status, status)
        except OSError as e:
            self.log(f"cannot write {self.lake.layout.status}: {e}")
        return status

    def run(self, stop):
        """Runs passes until `stop` (a threading.Event) is set."""
        while not stop.is_set():
            self.run_once()
            self._wake.wait(self.scan_period)
            self._wake.clear()
