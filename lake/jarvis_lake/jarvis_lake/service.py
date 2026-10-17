# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The lake service: converts splits as the recorder closes them, enforces retention, reports.

Retention runs every `retention_period`, and at every pass while the disk is low on free space,
before conversion: converting needs room too.

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
        self.disk_short_bytes = 0  # what the last retention could not free (0: the floor holds)

    def poke(self, closed_file=""):
        """Something closed a split (thread-safe): convert it soon."""
        self._wake.set()

    def run_once(self, recover=False):
        """One pass: retention when due or while the disk is low (first: converting needs room too),
        then conversion, then status. Each step runs even if the one before failed. Never raises."""
        errors = []
        now = self.clock()
        due = self._last_retention is None or now - self._last_retention >= self.retention_period
        low = self._step(errors, "disk check", self.lake.disk_low)
        if low is False:
            self.disk_short_bytes = 0  # room again: what the last retention could not free is moot
        if due or low:
            self._last_retention = now
            self._step(errors, "retention", self._retention)
        failed = self._step(errors, "conversion", lambda: self._convert(recover)) or []
        self.last_error = "; ".join(failed + errors)
        return self.write_status()

    def _step(self, errors, what, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - the service outlives one bad split or a full disk
            errors.append(f"{what} failed: {type(e).__name__}: {e}")
            self.log(f"lake {what} failed: {type(e).__name__}: {e}\n{traceback.format_exc()}")
            return None

    def _retention(self):
        report = self.lake.enforce_retention()
        self.disk_short_bytes = report.disk_short_bytes
        deleted = report.deleted_splits + report.deleted_partitions + report.deleted_blackbox
        if deleted:
            self.log(f"retention: deleted {len(deleted)} item(s), {report.freed_bytes} bytes")
        if report.disk_short_bytes:
            self.log(
                f"disk low: {report.disk_free_bytes >> 20} MiB free, "
                f"{report.disk_short_bytes >> 20} MiB short, and nothing else may go"
            )

    def _convert(self, recover):
        """Converts what is pending. Returns the splits that failed, as "name: error"."""
        failed = []
        for result in self.lake.sync(recover=recover):
            if result.failed:
                again = " (will try again)" if result.transient else ""
                failed.append(f"{result.split.name}: {result.error}{again}")
                self.log(f"{result.split.name}: cannot convert it: {result.error}{again}")
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
        return failed

    def write_status(self):
        status = {
            "schema": "jarvis.lake.status/v1",
            "updated_at": self.clock(),
            "converted_total": self.converted_total,
            "last_conversion": self.last_conversion,
            "last_error": self.last_error,
            "disk_short_bytes": self.disk_short_bytes,
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
