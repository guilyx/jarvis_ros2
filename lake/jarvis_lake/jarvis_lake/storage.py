# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The lake's files: where splits and partitions live, and the ledger of converted splits.

    $JARVIS_DATA/bags/<bag>/<bag>_<n>.mcap         rosbag2 splits (written by the recorder)
    $JARVIS_DATA/lake/events/date=YYYY-MM-DD/<bag>_<n>.parquet
    $JARVIS_DATA/lake/ledger.json                  which splits are converted, and into what
    $JARVIS_DATA/lake/status.json                  what the service did last (for the gateway)

Every file the lake writes goes through a temporary file, fsync and rename: a crash leaves the old
file or the new one, never half of one.
"""

import json
import os
import pathlib
import tempfile

MCAP_MAGIC = b"\x89MCAP0\r\n"


def write_atomic(path, data):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def write_json(path, value):
    write_atomic(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def read_json(path, default):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        return default  # unreadable: start over (conversion is idempotent)


def is_finalized(path):
    """True if the MCAP file was closed properly: it ends with the magic bytes after its footer."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() < 2 * len(MCAP_MAGIC):
                return False
            f.seek(-len(MCAP_MAGIC), os.SEEK_END)
            return f.read() == MCAP_MAGIC
    except OSError:
        return False


def split_id(path):
    """The name a split's Parquet files take: its file name without .mcap."""
    return pathlib.Path(path).stem


class Layout:
    """Paths under one data directory."""

    def __init__(self, data_dir, lake_dir=None):
        self.data = pathlib.Path(data_dir).expanduser()
        self.bags = self.data / "bags"
        self.blackbox = self.data / "blackbox"
        self.lake = pathlib.Path(lake_dir).expanduser() if lake_dir else self.data / "lake"
        self.events = self.lake / "events"
        self.ledger = self.lake / "ledger.json"
        self.status = self.lake / "status.json"

    def splits(self):
        """Every MCAP split of the continuous recorder, grouped by bag, oldest bag first."""
        if not self.bags.is_dir():
            return []
        bags = sorted(p for p in self.bags.iterdir() if p.is_dir())
        return [(bag, sorted(bag.glob("*.mcap"), key=split_index)) for bag in bags]

    def partitions(self):
        """Event partitions, oldest day first: (date, directory)."""
        if not self.events.is_dir():
            return []
        out = []
        for p in self.events.iterdir():
            if p.is_dir() and p.name.startswith("date="):
                out.append((p.name[len("date=") :], p))
        return sorted(out)


def split_index(path):
    """rosbag2 names splits <bag>_<n>.mcap: order them by n, not as text (10 after 9)."""
    stem = pathlib.Path(path).stem
    _, _, n = stem.rpartition("_")
    return (int(n) if n.isdigit() else -1, stem)


class Ledger:
    """Converted splits: {split path: {size, mtime_ns, rows, files, finalized[, error]}}.

    A split is done once it is in here with its current size and mtime, converted or failed.
    """

    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.entries = read_json(self.path, {}).get("splits", {})

    def converted(self, split):
        entry = self.entries.get(str(split))
        if not entry:
            return False
        try:
            st = pathlib.Path(split).stat()
        except FileNotFoundError:
            return True  # converted, then deleted by retention
        return entry.get("size") == st.st_size and entry.get("mtime_ns") == st.st_mtime_ns

    def record(self, split, rows, files, finalized, error=""):
        st = pathlib.Path(split).stat()
        self.entries[str(split)] = {
            "size": st.st_size,
            "mtime_ns": st.st_mtime_ns,
            "rows": rows,
            "files": [str(f) for f in files],
            "finalized": finalized,
        }
        if error:
            self.entries[str(split)]["error"] = error

    def forget_missing(self):
        """Drops entries of splits whose files are gone (deleted after conversion)."""
        for split in [s for s in self.entries if not pathlib.Path(s).exists()]:
            del self.entries[split]

    def save(self):
        write_json(self.path, {"schema": "jarvis.lake.ledger/v1", "splits": self.entries})
