# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""One rosbag2 MCAP split → Parquet files, one per UTC day. No ROS needed: MCAP embeds the schemas.

A split that was never closed (the recorder died with it) has no footer and no index. It is read
as a stream instead, record after record, until the data stops making sense: everything up to the
last complete record (or chunk, if the recorder chunks) is kept, which is what `mcap recover` would
keep.
"""

import collections
import dataclasses
import pathlib

import pyarrow.parquet as pq
from mcap.reader import make_reader
from mcap_ros2.decoder import DecoderFactory

from jarvis_lake import model, storage


@dataclasses.dataclass
class SplitResult:
    split: pathlib.Path
    rows: int = 0
    files: list = dataclasses.field(default_factory=list)
    finalized: bool = True
    truncated: bool = False  # the stream ended in the middle of a record (crash)
    skipped: int = 0  # messages that did not decode as an Event
    error: str = ""  # why the read stopped early (torn split) or failed
    failed: bool = False  # nothing could be read: the split is recorded as failed


class _Stream:
    """A file the MCAP reader must read front to back (no footer to seek to)."""

    def __init__(self, f):
        self._f = f

    def read(self, n=-1):
        return self._f.read(n)

    def seekable(self):
        return False


def read_split(path):
    """The Event rows of one split, and how the read went."""
    path = pathlib.Path(path)
    result = SplitResult(split=path, finalized=storage.is_finalized(path))
    rows = []
    name = storage.split_id(path)
    with open(path, "rb") as f:
        source = f if result.finalized else _Stream(f)
        reader = make_reader(source, decoder_factories=[DecoderFactory()])
        # File order: sorting by log time would buffer everything, and a torn end would lose it all.
        messages = reader.iter_decoded_messages(topics=[model.EVENTS_TOPIC], log_time_order=False)
        while True:
            try:
                schema, _, _, msg = next(messages)
            except StopIteration:
                break
            except Exception as e:  # noqa: BLE001 - a torn file fails in many ways
                if result.finalized:
                    raise
                result.truncated = True
                result.error = f"{type(e).__name__}: {e}"
                break
            if schema is None or schema.name != model.EVENT_TYPE:
                result.skipped += 1
                continue
            try:
                rows.append(model.row(msg, split=name))
            except (AttributeError, KeyError, TypeError, ValueError):
                result.skipped += 1
    result.rows = len(rows)
    return rows, result


def write_partitions(layout, split, rows):
    """Writes the rows of one split as events/date=D/<split>.parquet, replacing earlier versions."""
    name = storage.split_id(split)
    for _, directory in layout.partitions():
        (directory / f"{name}.parquet").unlink(missing_ok=True)
    by_day = collections.defaultdict(list)
    for r in rows:
        by_day[model.utc_date(r["ts"])].append(r)
    files = []
    for day, day_rows in sorted(by_day.items()):
        day_rows.sort(key=lambda r: (r["seq"], r["ts"]))
        target = layout.events / f"date={day}" / f"{name}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{name}.parquet.tmp")
        pq.write_table(model.table(day_rows), tmp, compression="zstd")
        with open(tmp, "rb+") as f:
            f.flush()
            storage.os.fsync(f.fileno())
        tmp.replace(target)
        files.append(target)
    return files


def convert(layout, split):
    """Reads one split and writes its partitions. Returns the SplitResult."""
    rows, result = read_split(split)
    result.files = write_partitions(layout, split, rows)
    return result
