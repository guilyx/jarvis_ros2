# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""History off the robot: the lake's Parquet (and, if asked, its black-box snapshots) copied to object
storage or a mounted share, for the fleet's history and for the day a robot's disk is lost.

    <target>/robots/<robot>/events/date=YYYY-MM-DD/<bag>_<n>.parquet
    <target>/robots/<robot>/blackbox/<bag>/<bag>_<n>.mcap

The target is a URI, as pyarrow reads it:

    s3://bucket/prefix                                       AWS S3
    s3://bucket/prefix?endpoint_override=minio:9000&scheme=http   MinIO, Ceph, any S3 API
    file:///mnt/fleet                                        a mounted share (NFS, SMB)

Credentials stay out of the URI: the AWS SDK's usual chain (AWS_ACCESS_KEY_ID and
AWS_SECRET_ACCESS_KEY, the AWS config file, an instance role).

A file goes up once it is complete (Parquet is written whole, a black-box snapshot once closed),
and again if it changes (a split converted again after a crash). The upload ledger
(lake/uploads.json) has what went where, with the size and mtime it had. Each pass uploads
oldest first, for at most `budget_s`, so a long backlog never holds conversion back. A failure
(the robot is offline) backs off: 30 s, doubling up to 10 min, and back to the start after a
success. Retention keeps what is waiting, whatever its age and budget, unless the disk runs short:
then the robot comes first, and what goes before it was uploaded is counted (`lost`).
"""

import dataclasses
import pathlib
import re
import time
import urllib.parse

import pyarrow
import pyarrow.fs as pafs

from jarvis_lake import storage

BACKOFF_MIN_S = 30.0
BACKOFF_MAX_S = 600.0
LOCAL = pafs.LocalFileSystem()


@dataclasses.dataclass
class Item:
    path: pathlib.Path
    key: str  # where it goes, under the target
    size: int
    mtime_ns: int


def redact(uri):
    """The target as status and logs show it: no user, password or key, whatever the URI holds."""
    p = urllib.parse.urlsplit(uri)
    host = p.hostname or ""
    if p.port:
        host += f":{p.port}"
    query = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not re.search("key|secret|token", k, re.I)]
    return urllib.parse.urlunsplit((p.scheme, host, p.path, urllib.parse.urlencode(query), ""))


def behind_message(status):
    """What `jarvis.upload.behind` says, from an uploader's status: None while it keeps up."""
    if not status or not status.get("behind"):
        return None
    error = status.get("last_error") or ""
    if len(error) > 120:
        error = "…" + error[-119:]  # the end of an S3 error says what happened
    return (
        f"history is not leaving the robot: {status['pending_files']} file(s), "
        f"{_size(status['pending_bytes'])}, the oldest waiting {_age(status['oldest_pending_s'])}"
        + (f"; last error: {error}" if error else "")
    )


def _size(n):
    for unit in ("bytes", "KiB", "MiB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GiB"


def _age(seconds):
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def robot_name(robot):
    """The robot's id as one path segment."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", robot or "robot").strip(".") or "robot"


class Uploader:
    def __init__(
        self, lake, target, robot, blackbox=False, budget_s=60.0, max_lag_s=86400.0, clock=time.time
    ):
        """`max_lag_s`: past this, a file still waiting makes the upload `behind` (a fault on ROS)."""
        self.lake = lake
        self.target = target
        self.robot = robot_name(robot)
        self.blackbox = blackbox
        self.budget_s = budget_s
        self.max_lag_s = max_lag_s
        self.clock = clock
        self.path = lake.layout.lake / "uploads.json"
        saved = storage.read_json(self.path, {})
        self.entries = saved.get("files", {})
        self.lost = saved.get("lost", 0)  # files deleted for room before they were uploaded
        self.fs, self.base = pafs.FileSystem.from_uri(target)  # a bad URI fails here, at start
        self.base = self.base.rstrip("/")
        self.uploaded_total = 0  # since the service started
        self.uploaded_bytes_total = 0
        self.last_upload_at = None
        self.last_error = ""
        self.retry_at = 0.0
        self._backoff = 0.0

    # -- what goes where ----------------------------------------------------------------------

    def key(self, path):
        path = pathlib.Path(path)
        under = f"{self.base}/robots/{self.robot}"
        try:
            return f"{under}/events/{path.relative_to(self.lake.layout.events).as_posix()}"
        except ValueError:
            return f"{under}/blackbox/{path.relative_to(self.lake.layout.blackbox).as_posix()}"

    def files(self):
        """What goes up when complete: every Parquet file, and the black box's closed snapshots."""
        out = []
        if self.lake.layout.events.is_dir():
            out += [p for p in self.lake.layout.events.glob("date=*/*.parquet") if not p.name.startswith(".")]
        if self.blackbox:
            out += self.lake.snapshots()
        return out

    def uploaded(self, path):
        entry = self.entries.get(str(path))
        if not entry:
            return False
        try:
            st = pathlib.Path(path).stat()
        except FileNotFoundError:
            return True
        return entry["size"] == st.st_size and entry["mtime_ns"] == st.st_mtime_ns

    def waiting(self, path):
        """Retention asks: would deleting `path` (a file, or a day of Parquet) lose what is not
        uploaded yet? Recordings never go up (their events are in Parquet), snapshots if asked."""
        path = pathlib.Path(path)
        if path.is_dir():
            return any(not self.uploaded(p) for p in path.glob("*.parquet") if not p.name.startswith("."))
        if path.suffix == ".parquet" or (self.blackbox and path.is_relative_to(self.lake.layout.blackbox)):
            return not self.uploaded(path)
        return False

    def pending(self):
        """Files to upload, oldest first."""
        items = []
        for path in self.files():
            if self.uploaded(path):
                continue
            try:
                st = path.stat()
            except FileNotFoundError:
                continue  # deleted since the listing
            items.append(Item(path, self.key(path), st.st_size, st.st_mtime_ns))
        return sorted(items, key=lambda i: i.mtime_ns)

    # -- uploading ----------------------------------------------------------------------------

    def run(self):
        """Uploads what is pending, oldest first, until the budget is spent or one fails (then backs
        off). Returns how many went up."""
        now = self.clock()
        if now < self.retry_at:
            return 0
        start, done, error = time.monotonic(), 0, ""
        for item in self.pending():
            if done and time.monotonic() - start > self.budget_s:
                break  # at least one per pass, whatever the budget
            try:
                self._put(item)
            except (OSError, pyarrow.ArrowException) as e:
                if not item.path.exists():
                    continue  # retention or a new conversion took it meanwhile
                error = f"{item.path.name}: {type(e).__name__}: {e}"
                break
            self.entries[str(item.path)] = {
                "size": item.size,
                "mtime_ns": item.mtime_ns,
                "key": item.key,
                "at": self.clock(),
            }
            self.uploaded_total += 1
            self.uploaded_bytes_total += item.size
            self.last_upload_at = self.clock()
            done += 1
        if error:
            self.last_error = error
            self._backoff = min(BACKOFF_MAX_S, max(BACKOFF_MIN_S, self._backoff * 2))
            self.retry_at = self.clock() + self._backoff
        else:
            self.last_error, self.retry_at, self._backoff = "", 0.0, 0.0
        if done:
            self.save()
        return done

    def _put(self, item):
        """One file to its key. Object stores make a key appear whole; on a share, a temporary name
        and a rename do. Then the size is checked."""
        if isinstance(self.fs, pafs.LocalFileSystem):
            parent, name = item.key.rsplit("/", 1)
            self.fs.create_dir(parent, recursive=True)
            temporary = f"{parent}/.{name}.uploading"
            pafs.copy_files(str(item.path), temporary, LOCAL, self.fs)
            self.fs.move(temporary, item.key)
        else:
            pafs.copy_files(str(item.path), item.key, LOCAL, self.fs)
        info = self.fs.get_file_info(item.key)
        if info.size != item.size:
            raise OSError(f"{item.key} has {info.size} bytes, not {item.size}")

    def deleted_waiting(self, paths):
        """Retention deleted these for room before they were uploaded."""
        if paths:
            self.lost += len(paths)
            self.save()

    def save(self):
        self.entries = {p: e for p, e in self.entries.items() if pathlib.Path(p).exists()}
        storage.write_json(
            self.path, {"schema": "jarvis.lake.uploads/v1", "files": self.entries, "lost": self.lost}
        )

    # -- status -------------------------------------------------------------------------------

    def status(self):
        pending = self.pending()
        now = self.clock()
        oldest = (now - pending[0].mtime_ns / 1e9) if pending else 0.0
        return {
            "target": redact(self.target),
            "robot": self.robot,
            "blackbox": self.blackbox,
            "pending_files": len(pending),
            "pending_bytes": sum(i.size for i in pending),
            "oldest_pending_s": round(max(0.0, oldest), 1),
            "behind": oldest > self.max_lag_s,
            "max_lag_s": self.max_lag_s,
            "uploaded_total": self.uploaded_total,
            "uploaded_bytes_total": self.uploaded_bytes_total,
            "last_upload_at": self.last_upload_at,
            "last_error": self.last_error,
            "retry_at": self.retry_at or None,
            "lost": self.lost,
        }
