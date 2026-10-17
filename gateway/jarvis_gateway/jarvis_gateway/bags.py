# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Recordings on disk: the continuous recorder's splits and the black box's snapshots, with their
time range (from the MCAP summary), whether the lake converted them, and the incidents they cover.
"""

from jarvis_lake import storage
from jarvis_lake.model import iso8601, parse_iso8601
from mcap.reader import make_reader

KINDS = {"recording": "bags", "blackbox": "blackbox"}


def _time_range(path, cache):
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in cache:
        start = end = None
        if storage.is_finalized(path):
            try:
                with open(path, "rb") as f:
                    stats = make_reader(f).get_summary().statistics
                if stats and stats.message_count:
                    start, end = stats.message_start_time, stats.message_end_time
            except Exception:  # noqa: BLE001 - an unreadable file is listed, without a range
                pass
        cache[key] = (start, end)
    return cache[key]


def listing(layout, ledger, incidents=(), cache=None):
    """Every split, newest first: {kind, bag, file, size, start, end, open, converted, incidents}."""
    cache = {} if cache is None else cache
    out = []
    for kind, folder in KINDS.items():
        root = getattr(layout, folder)
        if not root.is_dir():
            continue
        for bag in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
            for split in sorted(bag.glob("*.mcap"), key=storage.split_index, reverse=True):
                start, end = _time_range(split, cache)
                entry = {
                    "kind": kind,
                    "bag": bag.name,
                    "file": split.name,
                    "size": split.stat().st_size,
                    "start": _iso(start),
                    "end": _iso(end),
                    "open": not storage.is_finalized(split),
                    "converted": kind == "recording" and ledger.converted(split),
                    "incidents": [],
                }
                if start is not None:
                    for inc in incidents:
                        opened, last = (
                            _ns(inc.get("opened_at")),
                            _ns(inc.get("closed_at") or inc.get("resolved_at")),
                        )
                        if opened is not None and opened <= end and (last is None or last >= start):
                            entry["incidents"].append(inc["incident_id"])
                out.append(entry)
    return out


def resolve(layout, kind, bag, file):
    """The path of one split, or None: no way out of the recording folders."""
    folder = KINDS.get(kind)
    if not folder or "/" in bag or "/" in file or bag.startswith(".") or not file.endswith(".mcap"):
        return None
    root = getattr(layout, folder).resolve()
    path = (root / bag / file).resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


def _iso(ns):
    return iso8601(ns) if ns else None


def _ns(text):
    return parse_iso8601(text) if isinstance(text, str) and text else None
