# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Rules and plans from the fleet (roadmap 9.4): every robot takes them from one place, the fleet's
storage, checks them, loads what passes and keeps what it had when they don't.

    <source>/rules/*.yaml                    every robot's
    <source>/plans/*.yaml
    <source>/robots/<robot>/rules/*.yaml     one robot's
    <source>/robots/<robot>/plans/*.yaml

The source is a URI, as pyarrow reads it (the history's target, docs/guides/fleet.md):
s3://bucket/prefix, s3://bucket/prefix?endpoint_override=minio:9000&scheme=http, file:///mnt/fleet.
The files are layered by name: the robot's own (its launch `rules` and `plans`), the shared ones
over them, the robot's directory over those. A file replaces the one of the same name below it
(`rules: []` takes one away); the others add to it.

On the robot, under <data_dir>/config:

    base/rules, base/plans          the robot's own, copied at launch: the bottom layer
    versions/<revision>/rules|plans one directory per set of rules and plans seen, never changed
    active -> versions/<revision>   what the rule engine and the recovery orchestrator load
    status.json                     what the dashboard's System page shows

A new revision is applied in steps, and any failing keeps the one before:

1. it settles: read again a few seconds later, the same (a fleet operator half-way through
   uploading a rule and its plan is not a revision);
2. `jarvis lint` passes it: the rules, the plans and the fault catalog fit together;
3. `active` points at it, and the recovery orchestrator and the rule engine check it with what
   only they know (the actuators, their allowlists): `validate_only`;
4. they load it, plans first (new rules may use new plans). If one fails, `active` points back
   and both load the old one again.

A rejected revision is not tried again (until the source changes) and raises
`jarvis.config.rejected`; the robot keeps running what it had. Offline, it keeps it too.
"""

import dataclasses
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import time

import pyarrow
import pyarrow.fs as pafs
from jarvis_lake import storage, upload

KINDS = ("rules", "plans")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.ya?ml$")
MAX_FILE_BYTES = 1 << 20  # a rule file is a few KiB
MAX_FILES = 500
SETTLE_S = 5.0
KEEP_VERSIONS = 5
OPERATOR = "fleet-config"  # who the rule engine and the recovery orchestrator say reloaded


class Unavailable(Exception):
    """The rule engine or the recovery orchestrator does not answer: nothing is wrong with the
    revision, it is tried again."""


def revision(files):
    """The revision of a set of rules and plans: {"rules/a.yaml": bytes, ...} -> 12 hex digits."""
    h = hashlib.sha256()
    for path in sorted(files):
        h.update(path.encode() + b"\0" + files[path] + b"\0")
    return h.hexdigest()[:12]


def _flat(by_kind):
    return {f"{kind}/{name}": data for kind, names in by_kind.items() for name, data in names.items()}


class Source:
    """The fleet's rules and plans, where `config_from` says."""

    def __init__(self, uri, robot):
        self.uri = uri
        self.robot = upload.robot_name(robot)
        self.fs, base = pafs.FileSystem.from_uri(uri)  # a bad URI fails here, at start
        self.base = base.rstrip("/")

    def read(self):
        """{kind: {file name: bytes}} for the kinds the source has: the shared files, then the
        robot's own over them. Raises OSError (or an Arrow error) when the source is unreachable,
        ValueError when it holds what is no rule or plan file."""
        out, ignored = {}, []
        for under in (self.base, f"{self.base}/robots/{self.robot}"):
            for kind in KINDS:
                selector = pafs.FileSelector(f"{under}/{kind}", allow_not_found=True)
                for info in sorted(self.fs.get_file_info(selector), key=lambda i: i.path):
                    name = info.base_name
                    if not info.is_file or name.startswith(".") or not name.endswith((".yaml", ".yml")):
                        continue  # a README, a temporary file
                    if not NAME.match(name):
                        ignored.append(info.path)  # no file name the robot writes
                        continue
                    if info.size > MAX_FILE_BYTES:
                        raise ValueError(f"{info.path}: {info.size} bytes, more than a rule file has")
                    with self.fs.open_input_stream(info.path) as f:
                        out.setdefault(kind, {})[name] = f.read()
        if sum(len(names) for names in out.values()) > MAX_FILES:
            raise ValueError(f"more than {MAX_FILES} files")
        return out, ignored


class Store:
    """The robot's versions of its rules and plans, under <data_dir>/config."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.base = self.root / "base"
        self.versions = self.root / "versions"
        self.active = self.root / "active"
        self.status_path = self.root / "status.json"

    def seed(self, rules, plans):
        """At launch: the robot's own rules and plans become the base, the layer the fleet's go
        over. With no version active yet (a first start), they are it: the rule engine loads
        something from the start, and the fleet's come once fetched. Returns `active`."""
        fresh = self.root / "base.new"
        shutil.rmtree(fresh, ignore_errors=True)
        for kind, src in (("rules", rules), ("plans", plans)):
            (fresh / kind).mkdir(parents=True)
            for path in sorted(pathlib.Path(src).glob("*.y*ml")) if src else []:
                if NAME.match(path.name):
                    shutil.copyfile(path, fresh / kind / path.name)
        shutil.rmtree(self.base, ignore_errors=True)
        fresh.rename(self.base)
        if self.current() is None:
            self.switch(self.install(self.merged({})))
        return self.active

    def own(self):
        """The robot's own rules and plans (the base): {kind: {name: bytes}}."""
        out = {}
        for kind in KINDS:
            out[kind] = {p.name: p.read_bytes() for p in sorted((self.base / kind).glob("*.y*ml"))}
        return out

    def merged(self, fetched):
        """What the robot runs: the fleet's files over the robot's own, by name."""
        own = self.own()
        return _flat({kind: {**own.get(kind, {}), **fetched.get(kind, {})} for kind in KINDS})

    def install(self, files):
        """Writes a version (if new) and returns its revision. Written whole, then renamed."""
        rev = revision(files)
        final = self.versions / rev
        if not final.is_dir():
            temporary = self.versions / f".{rev}.new"
            shutil.rmtree(temporary, ignore_errors=True)
            for kind in KINDS:
                (temporary / kind).mkdir(parents=True)
            for path, data in files.items():
                (temporary / path).write_bytes(data)
            temporary.rename(final)
        return rev

    def current(self):
        """The active revision, or None."""
        try:
            rev = os.readlink(self.active)
        except OSError:
            return None
        rev = pathlib.Path(rev).name
        return rev if (self.versions / rev).is_dir() else None

    def switch(self, rev):
        """Points `active` at a version, at once: a new link, renamed over the old one."""
        link = self.root / ".active.new"
        link.unlink(missing_ok=True)
        link.symlink_to(pathlib.Path("versions") / rev)
        os.replace(link, self.active)

    def files(self, rev):
        return {kind: sorted(p.name for p in (self.versions / rev / kind).glob("*.y*ml")) for kind in KINDS}

    def prune(self, keep=KEEP_VERSIONS):
        """Keeps the active version and the last few others (newest first): to go back to by hand."""
        current = self.current()
        others = sorted(
            (p for p in self.versions.glob("*") if p.is_dir() and p.name != current),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for path in others[max(0, keep - 1) :]:
            shutil.rmtree(path, ignore_errors=True)


def lint(jarvis, catalog):
    """A validator: `jarvis lint` on a version's directory. Returns (errors, warnings), each line
    without the version's path (rules/x.yaml: ...)."""

    def validate(directory):
        command = [jarvis, "lint", "--rules", str(directory / "rules"), "--plans", str(directory / "plans")]
        if catalog:
            command += ["--catalog", str(catalog)]
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Unavailable(f"jarvis lint: {e}") from e
        prefix = f"{directory}/"
        errors, warnings = [], []
        for line in done.stdout.splitlines():
            for mark, out in (("error: ", errors), ("warning: ", warnings)):
                if line.startswith(mark):
                    out.append(line[len(mark) :].replace(prefix, ""))
        if done.returncode and not errors:
            why = (done.stderr or done.stdout).strip().replace(prefix, "")
            errors.append(why or f"jarvis lint: exit {done.returncode}")
        return errors, warnings

    return validate


@dataclasses.dataclass
class Result:
    state: str  # in-sync, settling, applied, rejected, offline, empty, waiting
    message: str = ""


class Syncer:
    """Takes the source's rules and plans when they change, as the module says.

    `validate(directory) -> (errors, warnings)`: lint. `reload(validate_only) -> errors`: the rule
    engine and the recovery orchestrator, from `active`; raises Unavailable if one does not answer."""

    def __init__(self, store, source, validate, reload, clock=time.time, settle_s=SETTLE_S):
        self.store, self.source = store, source
        self.validate, self.reload, self.clock, self.settle_s = validate, reload, clock, settle_s
        saved = storage.read_json(store.status_path, {})
        self.rejected = saved.get("rejected")  # {revision, at, errors}
        self.applied_at = saved.get("applied_at")
        self.warnings = saved.get("warnings", [])
        self.ignored = []
        self.candidate = None  # a new revision seen once: applied if seen again, settled
        self.state, self.message = "starting", ""
        self.last_check_at = None
        self.last_error = ""
        self.from_fleet = {}

    def run(self):
        """One look at the source. Returns a Result; the status is written."""
        result = self._run()
        self.state, self.message = result.state, result.message
        self.last_check_at = self.clock()
        self.write_status()
        return result

    def _run(self):
        try:
            fetched, self.ignored = self.source.read()
        except (OSError, pyarrow.ArrowException) as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return Result("offline", f"the source is unreachable: {self.last_error}")
        except ValueError as e:
            return self._reject(None, [str(e)])
        self.last_error = ""
        current = self.store.current()
        if not any(fetched.values()):
            # A wrong prefix, or a wiped one: not a reason to drop what the fleet gave.
            return Result("empty", f"the source has no rules or plans; still on {current}")
        self.from_fleet = {kind: sorted(fetched.get(kind, {})) for kind in KINDS}
        files = self.store.merged(fetched)
        rev = revision(files)
        if rev == current:
            self.candidate, self.rejected = None, None
            return Result("in-sync", f"rules and plans {rev}")
        if self.rejected and self.rejected["revision"] == rev:
            return Result("rejected", self._rejected_message())
        if self.candidate != rev:
            self.candidate = rev
            return Result("settling", f"{rev} found, checking it again in {self.settle_s:.0f} s")
        self.candidate = None
        self.store.install(files)
        try:
            errors, warnings = self.validate(self.store.versions / rev)
        except Unavailable as e:
            self.last_error = str(e)
            return Result("waiting", f"{rev} not checked: {e}")
        if errors:
            return self._reject(rev, errors)
        try:
            errors = self._load(rev, current)
        except Unavailable as e:
            self.last_error = str(e)
            return Result("waiting", f"{rev} not loaded: {e}")
        if errors:
            return self._reject(rev, errors)
        self.rejected, self.warnings, self.applied_at = None, warnings, self.clock()
        self.store.prune()
        return Result("applied", f"rules and plans {rev} loaded" + (f", was {current}" if current else ""))

    def _load(self, rev, previous):
        """Switches to `rev` and has it loaded; on any failure, back to `previous`."""
        self.store.switch(rev)
        loading = False
        try:
            errors = self.reload(True)
            if not errors:
                loading = True
                errors = self.reload(False)
        except Unavailable:
            self._back(previous, loading)
            raise
        if errors:
            self._back(previous, loading)
        # The nodes name files as they read them, through `active`: as at the source instead.
        active = f"{self.store.active}/"
        return [e.replace(active, "") for e in errors]

    def _back(self, previous, reload):
        """`active` back to `previous`, loaded again if the new one was (in part: plans first)."""
        self.store.switch(previous)
        if not reload:
            return
        try:
            restored = self.reload(False)
        except Unavailable as e:
            restored = [str(e)]
        if restored:
            self.last_error = f"{previous} could not be loaded again: {restored[0]}"

    def _reject(self, rev, errors):
        self.rejected = {"revision": rev, "at": self.clock(), "errors": errors[:20]}
        self.store.prune()
        return Result("rejected", self._rejected_message())

    def _rejected_message(self):
        r = self.rejected
        more = f" (and {len(r['errors']) - 1} more)" if len(r["errors"]) > 1 else ""
        what = f"rules and plans {r['revision']}" if r["revision"] else "the source"
        return f"{what} rejected: {r['errors'][0]}{more}; still on {self.store.current()}"

    def status(self):
        current = self.store.current()
        return {
            "schema": "jarvis.config.status/v1",
            "source": upload.redact(self.source.uri),
            "robot": self.source.robot,
            "state": self.state,
            "message": self.message,
            "revision": current,
            "files": self.store.files(current) if current else {},
            "from_fleet": self.from_fleet,
            "applied_at": self.applied_at,
            "last_check_at": self.last_check_at,
            "last_error": self.last_error,
            "rejected": self.rejected,
            "warnings": self.warnings[:20],
            "ignored": self.ignored[:20],
        }

    def write_status(self):
        storage.write_json(self.store.status_path, self.status())
