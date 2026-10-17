# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The lake as a whole: which splits to convert, converting them, retention, and status.

Which splits are converted:
- a split that was closed properly (it has its MCAP footer): always;
- a split that was never closed: only once the recorder has moved on (a newer split or a newer
  bag exists), because until then it is the file being written. After a crash or a power cut the
  recorder starts a new bag when Jarvis comes back, and the torn split is recovered then.
  `sync(recover=True)` converts it at once (`jarvis-lake sync --recover`).

Retention never deletes a split before it is converted.
"""

import dataclasses
import datetime
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


@dataclasses.dataclass
class RetentionReport:
    deleted_splits: list = dataclasses.field(default_factory=list)
    deleted_partitions: list = dataclasses.field(default_factory=list)
    deleted_blackbox: list = dataclasses.field(default_factory=list)
    freed_bytes: int = 0


def _size(path):
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


class Lake:
    def __init__(self, data_dir, lake_dir=None, retention=None, clock=time.time):
        self.layout = storage.Layout(data_dir, lake_dir)
        self.retention_policy = retention or Retention()
        self.clock = clock
        self.ledger = storage.Ledger(self.layout.ledger)

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
        """Deletes what the policy says, oldest first. Returns what was (or would be) deleted."""
        policy, report = self.retention_policy, RetentionReport()
        now = self.clock()

        # Recorded splits: only converted ones may go.
        bags = self.layout.splits()
        total = sum(_size(bag) for bag, _ in bags)
        for bag, splits in bags:
            for split in splits:
                if not self.ledger.converted(split):
                    continue
                too_old = now - split.stat().st_mtime > policy.bags_max_age_days * 86400
                if too_old or total > policy.bags_max_bytes:
                    size = split.stat().st_size
                    report.deleted_splits.append(str(split))
                    report.freed_bytes += size
                    total -= size
                    if not dry_run:
                        split.unlink()
            if not dry_run and bag.is_dir() and not any(bag.glob("*.mcap")):
                shutil.rmtree(bag)  # metadata.yaml of a bag whose splits are all gone
        if not dry_run:
            self.ledger.forget_missing()
            self.ledger.save()

        # Parquet partitions: whole days, never the newest one.
        partitions = self.layout.partitions()
        today = datetime.datetime.fromtimestamp(now, tz=datetime.UTC).date()
        total = sum(_size(d) for _, d in partitions)
        for i, (day, directory) in enumerate(partitions):
            if i == len(partitions) - 1:
                break
            age_days = (today - datetime.date.fromisoformat(day)).days
            if age_days > policy.lake_max_age_days or total > policy.lake_max_bytes:
                size = _size(directory)
                report.deleted_partitions.append(day)
                report.freed_bytes += size
                total -= size
                if not dry_run:
                    shutil.rmtree(directory)

        # Black-box snapshots: oldest files first, down to the budget.
        if self.layout.blackbox.is_dir():
            files = sorted(self.layout.blackbox.rglob("*.mcap"), key=lambda f: f.stat().st_mtime)
            total = sum(f.stat().st_size for f in files)
            for f in files:
                if total <= policy.blackbox_max_bytes:
                    break
                size = f.stat().st_size
                report.deleted_blackbox.append(str(f))
                report.freed_bytes += size
                total -= size
                if not dry_run:
                    f.unlink()
        return report

    # -- status --------------------------------------------------------------------------------

    def status(self):
        """Counts and sizes, for `jarvis-lake status`, diagnostics and the gateway."""
        bags = self.layout.splits()
        splits = [s for _, ss in bags for s in ss]
        partitions = self.layout.partitions()
        return {
            "splits": len(splits),
            "splits_converted": sum(1 for s in splits if self.ledger.converted(s)),
            "splits_pending": len(self.pending()),
            "splits_failed": {s: e["error"] for s, e in self.ledger.entries.items() if e.get("error")},
            "bags_bytes": sum(_size(b) for b, _ in bags),
            "lake_bytes": sum(_size(d) for _, d in partitions),
            "blackbox_bytes": _size(self.layout.blackbox) if self.layout.blackbox.is_dir() else 0,
            "partitions": [day for day, _ in partitions],
            "rows": sum(
                pq.ParquetFile(f).metadata.num_rows for _, d in partitions for f in d.glob("*.parquet")
            ),
        }
