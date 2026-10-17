# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The lake as a whole: which splits to convert, converting them, retention, and status.

Which splits are converted:
- a split that was closed properly (it has its MCAP footer): always;
- a split that was never closed: only once the recorder has moved on (a newer split or a newer
  bag exists), because until then it is the file being written. After a crash or a power cut the
  recorder starts a new bag when Jarvis comes back, and the torn split is recovered then.
  `sync(recover=True)` converts it at once (`jarvis-lake sync --recover`).

Retention never deletes a split before it is converted. It keeps the policy's ages and budgets,
and a floor of free disk space: below it, more goes, whatever its age, in the order it costs least
(converted splits, whose events are in Parquet; black-box snapshots; whole days of Parquet, never
the newest). When that is not enough, the disk is reported low (status, diagnostics, a fault).
"""

import dataclasses
import datetime
import itertools
import pathlib
import shutil
import time

import pyarrow.parquet as pq

from jarvis_lake import convert, storage, views

GIB = 1 << 30


@dataclasses.dataclass
class Retention:
    bags_max_age_days: float = 7.0
    bags_max_bytes: int = 10 * GIB
    lake_max_age_days: float = 180.0
    lake_max_bytes: int = 5 * GIB
    blackbox_max_bytes: int = 2 * GIB
    min_free_bytes: int = 1 * GIB  # free disk under the data directory, whatever the budgets


@dataclasses.dataclass
class RetentionReport:
    deleted_splits: list = dataclasses.field(default_factory=list)
    deleted_partitions: list = dataclasses.field(default_factory=list)
    deleted_blackbox: list = dataclasses.field(default_factory=list)
    freed_bytes: int = 0
    disk_free_bytes: int = 0  # after retention (estimated, on a dry run)
    disk_short_bytes: int = 0  # still missing for the floor: what is left may not be deleted


def _size(path):
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _delete(path, deleted, report, dry_run, name=None):
    """Deletes one split, snapshot or partition, and accounts for it. Returns its size."""
    size = _size(path)
    deleted.append(name or str(path))
    report.freed_bytes += size
    if not dry_run:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    return size


class Lake:
    def __init__(self, data_dir, lake_dir=None, retention=None, clock=time.time, disk_free=None):
        self.layout = storage.Layout(data_dir, lake_dir)
        self.retention_policy = retention or Retention()
        self.clock = clock
        self.disk_free = disk_free or self._disk_free
        self.ledger = storage.Ledger(self.layout.ledger)

    def _disk_free(self):
        """Free bytes on the file system of the data directory (or of its nearest parent)."""
        path = self.layout.data
        while not path.exists() and path != path.parent:
            path = path.parent
        return shutil.disk_usage(path).free

    def disk_low(self):
        return self.disk_free() < self.retention_policy.min_free_bytes

    # -- conversion ----------------------------------------------------------------------------

    def pending(self, recover=False):
        """Splits to convert now, oldest first."""
        out = []
        bags = self.layout.splits()
        for b, (_, splits) in enumerate(bags):
            newest_bag = b == len(bags) - 1
            for i, split in enumerate(splits):
                if self.ledger.converted(split):
                    continue
                last = i == len(splits) - 1
                if storage.is_finalized(split) or recover or not (newest_bag and last):
                    out.append(split)
        return out

    def convert(self, split):
        """Converts one split (again, if it already was) and records it in the ledger.

        A split that cannot be read is recorded as failed, with the error, and is not tried again
        until the file changes: one bad file must not hold back the ones after it.
        """
        try:
            result = convert.convert(self.layout, split)
        except Exception as e:  # noqa: BLE001 - whatever is wrong with the file, report and move on
            result = convert.SplitResult(split=split, error=f"{type(e).__name__}: {e}", failed=True)
            result.finalized = storage.is_finalized(split)
            self.ledger.record(split, 0, [], result.finalized, error=result.error)
        else:
            self.ledger.record(split, result.rows, result.files, result.finalized)
        self.ledger.save()
        return result

    def sync(self, recover=False):
        """Converts every pending split. Returns their SplitResults (failed ones have `error`)."""
        return [self.convert(split) for split in self.pending(recover)]

    # -- queries -------------------------------------------------------------------------------

    def connect(self, recent=None):
        """DuckDB with the lake's views (see jarvis_lake.views)."""
        return views.connect(self.layout, recent)

    # -- retention -----------------------------------------------------------------------------

    def enforce_retention(self, dry_run=False):
        """Deletes what the policy says, oldest first, then more while the disk is short of free
        space. Returns what was (or would be) deleted."""
        policy, report = self.retention_policy, RetentionReport()
        now = self.clock()

        # Recorded splits: only converted ones may go.
        bags = self.layout.splits()
        converted = [s for _, splits in bags for s in splits if self.ledger.converted(s)]
        total = sum(_size(bag) for bag, _ in bags)
        for split in converted:
            too_old = now - split.stat().st_mtime > policy.bags_max_age_days * 86400
            if too_old or total > policy.bags_max_bytes:
                total -= _delete(split, report.deleted_splits, report, dry_run)

        # Parquet partitions: whole days, never the newest one.
        partitions = self.layout.partitions()
        total = sum(_size(d) for _, d in partitions)
        partitions = partitions[:-1]
        today = datetime.datetime.fromtimestamp(now, tz=datetime.UTC).date()
        for day, directory in partitions:
            age_days = (today - datetime.date.fromisoformat(day)).days
            if age_days > policy.lake_max_age_days or total > policy.lake_max_bytes:
                total -= _delete(directory, report.deleted_partitions, report, dry_run, day)

        # Black-box snapshots: oldest files first, down to the budget.
        snapshots = []
        if self.layout.blackbox.is_dir():
            snapshots = sorted(self.layout.blackbox.rglob("*.mcap"), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in snapshots)
        for f in snapshots:
            if total <= policy.blackbox_max_bytes:
                break
            total -= _delete(f, report.deleted_blackbox, report, dry_run)

        # Disk pressure: more of the same, whatever its age, cheapest loss first.
        free = self.disk_free() + (report.freed_bytes if dry_run else 0)
        candidates = itertools.chain(
            ((s, report.deleted_splits, str(s)) for s in converted),
            ((f, report.deleted_blackbox, str(f)) for f in snapshots),
            ((d, report.deleted_partitions, day) for day, d in partitions),
        )
        for path, deleted, name in candidates:
            if free >= policy.min_free_bytes:
                break
            if name not in deleted:
                free += _delete(path, deleted, report, dry_run, name)
        report.disk_free_bytes = free
        report.disk_short_bytes = max(0, policy.min_free_bytes - free)

        if not dry_run:
            # The metadata.yaml of a bag whose splits are all gone.
            for bag in {pathlib.Path(s).parent for s in report.deleted_splits}:
                if bag.is_dir() and not any(bag.glob("*.mcap")):
                    shutil.rmtree(bag)
            self.ledger.forget_missing()
            self.ledger.save()
        return report

    # -- status --------------------------------------------------------------------------------

    def status(self):
        """Counts and sizes, for `jarvis-lake status`, diagnostics and the gateway."""
        bags = self.layout.splits()
        splits = [s for _, ss in bags for s in ss]
        partitions = self.layout.partitions()
        free = self.disk_free()
        return {
            "splits": len(splits),
            "splits_converted": sum(1 for s in splits if self.ledger.converted(s)),
            "splits_pending": len(self.pending()),
            "splits_failed": {s: e["error"] for s, e in self.ledger.entries.items() if e.get("error")},
            "bags_bytes": sum(_size(b) for b, _ in bags),
            "lake_bytes": sum(_size(d) for _, d in partitions),
            "blackbox_bytes": _size(self.layout.blackbox) if self.layout.blackbox.is_dir() else 0,
            "disk_free_bytes": free,
            "disk_min_free_bytes": self.retention_policy.min_free_bytes,
            "disk_low": free < self.retention_policy.min_free_bytes,
            "partitions": [day for day, _ in partitions],
            "rows": sum(
                pq.ParquetFile(f).metadata.num_rows for _, d in partitions for f in d.glob("*.parquet")
            ),
        }
