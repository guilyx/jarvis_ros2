# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The fault catalog as people read it: what each code means, and what to do about it.

The catalog is the fault manager's (`catalog:=` launch argument, docs/design/03-data-model.md §5).
A runbook is a Markdown file: the steps to check and fix a fault, shown on the fault in the
dashboard and offered to the agent. The runbook of a code is, in order:
- the file its catalog entry names (`runbook: runbooks/sensor.lidar.stale.md`), relative to the
  catalog's directory;
- `runbooks/<code>.md` next to the catalog;
- `<code>.md` in one of the `runbooks` directories (the launch file passes the ones Jarvis ships,
  for the codes its bridges raise), so a catalog of your own gets those without naming them.
Files are read when asked for: edit a runbook and the next look shows it, no restart.
"""

import pathlib
import re

import yaml

MAX_RUNBOOK_BYTES = 256 * 1024
CODE = re.compile(r"[a-z0-9_]+(\.[a-z0-9_]+)*")


class Catalog:
    def __init__(self, path="", runbooks=()):
        self.path = pathlib.Path(path).expanduser() if path else None
        self.dirs = [pathlib.Path(d).expanduser() for d in runbooks if d]
        if self.path is not None:
            self.dirs.insert(0, self.path.parent / "runbooks")
        self._stamp = None
        self._entries = {}
        self._defaults = {}

    def _load(self):
        if self.path is None:
            return
        try:
            stamp = self.path.stat().st_mtime_ns
        except OSError:
            self._stamp, self._entries, self._defaults = None, {}, {}
            return
        if stamp == self._stamp:
            return
        try:
            doc = yaml.safe_load(self.path.read_text()) or {}
        except (OSError, yaml.YAMLError):
            doc = {}
        self._defaults = doc.get("defaults") or {}
        self._entries = {
            str(f["code"]): f for f in doc.get("faults") or [] if isinstance(f, dict) and f.get("code")
        }
        self._stamp = stamp

    def faults(self):
        """The cataloged codes, for people: code, description, severity, latch, has a runbook."""
        self._load()
        default = self._defaults.get("severity", "ERROR")
        return [
            {
                "code": code,
                "description": " ".join(str(f.get("description") or "").split()),
                "severity": f.get("severity", default),
                "latch": bool(f.get("latch", False)),
                "runbook": self.runbook_path(code) is not None,
            }
            for code, f in sorted(self._entries.items())
        ]

    def runbook_path(self, code):
        """Where the runbook of `code` is, or None. A runbook the catalog names is returned even
        if the file is missing (reading it says so); a code is a file name only if it is a code."""
        self._load()
        name = (self._entries.get(code) or {}).get("runbook")
        if name:
            path = pathlib.Path(str(name)).expanduser()
            return path if path.is_absolute() else self.path.parent / path
        if not CODE.fullmatch(code):
            return None
        return next((d / f"{code}.md" for d in self.dirs if (d / f"{code}.md").is_file()), None)

    def runbook(self, code):
        """The runbook of `code` as Markdown, or None. A runbook that is named but cannot be read
        raises LookupError, with what is wrong: the catalog points at a file that is not there."""
        path = self.runbook_path(code)
        if path is None:
            return None
        if path.suffix.lower() != ".md":
            raise LookupError(f"{code}: its runbook {path.name} is not a Markdown (.md) file")
        try:
            with path.open("rb") as f:
                data = f.read(MAX_RUNBOOK_BYTES + 1)
        except OSError as e:
            raise LookupError(f"{code}: its runbook {path} cannot be read ({e.strerror})") from None
        if len(data) > MAX_RUNBOOK_BYTES:
            raise LookupError(f"{code}: its runbook is over {MAX_RUNBOOK_BYTES // 1024} KiB")
        return data.decode("utf-8", errors="replace")
