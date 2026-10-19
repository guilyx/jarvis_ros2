#!/usr/bin/env python3
# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""One version for every package of the repository (RELEASING.md).

    scripts/version.py              print it (and fail if the packages disagree)
    scripts/version.py --check      fail if they disagree (CI)
    scripts/version.py 1.2.0        set it everywhere, and date the changelogs' Forthcoming sections

Where it lives: every package.xml, the Python packages' setup.py and __version__, the core's
CMake project(), and the package.json (and lockfile) of the dashboard and the Foxglove panel.
"""

import argparse
import datetime
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP = {"build", "install", "log", "node_modules", ".git"}
SEMVER = re.compile(r"\d+\.\d+\.\d+")
NPM = ("dashboard", "foxglove/jarvis-panel")


def files(pattern):
    return sorted(p for p in ROOT.rglob(pattern) if not SKIP & set(p.relative_to(ROOT).parts))


# (file, regex whose group 1 is the version)
def places():
    out = [(p, r"<version>([^<]+)</version>") for p in files("package.xml")]
    for p in files("setup.py"):
        if "version=" in p.read_text():
            out.append((p, r"version=\"([^\"]+)\""))
    for p in files("__init__.py"):
        if "__version__ = " in p.read_text():
            out.append((p, r"__version__ = \"([^\"]+)\""))
    out.append((ROOT / "core/jarvis_core/CMakeLists.txt", r"project\(jarvis_core VERSION (\S+) "))
    return out


def current():
    found = {}
    for path, pattern in places():
        m = re.search(pattern, path.read_text())
        found[path.relative_to(ROOT)] = m.group(1) if m else None
    for package in NPM:
        for name in ("package.json", "package-lock.json"):
            path = pathlib.Path(package, name)
            found[path] = json.loads((ROOT / path).read_text())["version"]
    return found


def set_version(version):
    for path, pattern in places():
        text = path.read_text()
        m = re.search(pattern, text)
        path.write_text(text[: m.start(1)] + version + text[m.end(1) :])
    for package in NPM:
        for name in ("package.json", "package-lock.json"):
            path = ROOT / package / name
            text = path.read_text()
            # The package's own version: the first "version" key (and, in the lockfile, the root
            # package's under "packages"), never a dependency's.
            count = 2 if name == "package-lock.json" else 1
            text = re.sub(r'("version": ")[^"]+(")', rf"\g<1>{version}\g<2>", text, count=count)
            path.write_text(text)
    today = datetime.date.today().isoformat()
    for path in [*files("CHANGELOG.rst"), ROOT / "CHANGELOG.md", ROOT / "foxglove/jarvis-panel/CHANGELOG.md"]:
        text = path.read_text()
        if path.suffix == ".rst":
            heading = f"{version} ({today})"
            text = re.sub(r"^Forthcoming\n-+$", f"{heading}\n{'-' * len(heading)}", text, count=1, flags=re.M)
        else:
            text = re.sub(r"^## Forthcoming$", f"## {version} ({today})", text, count=1, flags=re.M)
        path.write_text(text)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("version", nargs="?", help="X.Y.Z to set everywhere")
    p.add_argument("--check", action="store_true", help="fail if the packages disagree")
    args = p.parse_args(argv)
    if args.version:
        if not SEMVER.fullmatch(args.version):
            p.error(f"{args.version}: not X.Y.Z")
        set_version(args.version)
    found = current()
    versions = set(found.values())
    if len(versions) != 1 or None in versions:
        for path, v in sorted(found.items()):
            print(f"{v or 'missing':>10}  {path}", file=sys.stderr)
        print("version.py: the packages disagree (scripts/version.py X.Y.Z sets them all)", file=sys.stderr)
        return 1
    if not args.check:
        print(versions.pop())
    return 0


if __name__ == "__main__":
    sys.exit(main())
