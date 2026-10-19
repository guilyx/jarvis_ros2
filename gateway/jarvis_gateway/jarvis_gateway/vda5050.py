# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Jarvis's open faults as the `errors` of a VDA 5050 state (roadmap 7.3).

A fleet manager drives a robot over VDA 5050: the robot's adapter publishes its state on
`uagv/v<major>/<manufacturer>/<serialNumber>/state`, and the state's `errors` are every active error
of the robot. The adapter owns that message; the gateway serves Jarvis's part of it, ready to merge:

    GET /api/v1/vda5050/errors   ->  {"version": "3.0", "errors": [{errorType, errorLevel, ...}]}

One error per open root cause (ACTIVE or LATCHED; a muted symptom is left out, the fleet manager acts
on causes):
- errorType: the Jarvis code, or the type `errors` maps it to (LOCALIZATION_ERROR, ...);
- errorLevel: from the severity, or as mapped. VDA 5050 3.0: WARN and below WARNING, ERROR URGENT,
  CRITICAL CRITICAL, a CRITICAL fault that latches (a person must clear it) FATAL. 2.x has WARNING
  and FATAL only: CRITICAL and FATAL are FATAL;
- errorReferences: the component, the fault, its incident (and the Jarvis code when the type is
  mapped);
- errorDescription: the fault's message; errorHint (2.1 and later): the first step of its runbook's
  "Fix" section.

    vda5050:
      version: "3.0"               # or "2.1", "2.0"
      errors:                      # first match by code (glob): the type and/or the level
        - { code: nav.localization.*, type: LOCALIZATION_ERROR, level: FATAL }
        - { code: "medkit.*", level: URGENT }
"""

import dataclasses
import fnmatch
import re

LEVELS = ("WARNING", "URGENT", "CRITICAL", "FATAL")
VERSIONS = ("2.0", "2.1", "3.0")
OPEN = ("ACTIVE", "LATCHED")
MAX_HINT = 300


@dataclasses.dataclass
class Mapping:
    code: str
    type: str = ""
    level: str = ""


@dataclasses.dataclass
class Settings:
    version: str = "3.0"
    errors: list = dataclasses.field(default_factory=list)  # [Mapping]

    @classmethod
    def load(cls, raw):
        raw = raw or {}
        version = str(raw.get("version", "3.0"))
        if version not in VERSIONS:
            raise ValueError(f"vda5050.version: {version} is not one of {', '.join(VERSIONS)}")
        errors = []
        for entry in raw.get("errors") or []:
            m = Mapping(
                code=str(entry["code"]), type=str(entry.get("type", "")), level=str(entry.get("level", ""))
            )
            if m.level and m.level not in LEVELS:
                raise ValueError(
                    f"vda5050.errors: level {m.level} of {m.code} is not one of {', '.join(LEVELS)}"
                )
            errors.append(m)
        return cls(version=version, errors=errors)


def level_of(fault, version="3.0", mapped=""):
    """The VDA 5050 errorLevel of a fault."""
    level = mapped
    if not level:
        severity = fault.get("severity", "ERROR")
        if severity == "CRITICAL":
            level = "FATAL" if fault.get("latch") else "CRITICAL"
        elif severity == "ERROR":
            level = "URGENT"
        else:
            level = "WARNING"
    if version.startswith("2"):
        return "FATAL" if level in ("CRITICAL", "FATAL") else "WARNING"
    return level


def hint(runbook):
    """The first step of a runbook's "Fix" section, as one line of plain text (or "")."""
    if not runbook:
        return ""
    lines = runbook.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if re.match(r"^#+\s*Fix\b", line.strip()))
    except StopIteration:
        return ""
    step = []
    for line in lines[start + 1 :]:
        if line.startswith("#"):
            break
        item = re.match(r"^\s*(?:[-*]|\d+\.)\s+(.*)", line)
        if item and step:
            break
        if item:
            step.append(item.group(1))
        elif step and line.strip():
            step.append(line.strip())
        elif step:
            break
    text = re.sub(r"\*\*|__|`", "", " ".join(step))
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", " ".join(text.split()))
    return text if len(text) <= MAX_HINT else text[: MAX_HINT - 1].rstrip() + "…"


def errors(faults, settings, runbook=lambda code: None):
    """The VDA 5050 error objects of Jarvis's open root-cause faults. `runbook(code)` gives the
    Markdown of a code's runbook (or None)."""
    out = []
    for f in faults or []:
        if f.get("state") not in OPEN or f.get("muted_by"):
            continue
        code = f.get("code", "")
        mapping = next((m for m in settings.errors if fnmatch.fnmatchcase(code, m.code)), None)
        component = (f.get("source") or {}).get("component", "")
        refs = [{"referenceKey": "component", "referenceValue": component}] if component else []
        if mapping and mapping.type:
            refs.append({"referenceKey": "jarvisCode", "referenceValue": code})
        refs.append({"referenceKey": "faultId", "referenceValue": f.get("fault_id", "")})
        if f.get("incident_id"):
            refs.append({"referenceKey": "incidentId", "referenceValue": f["incident_id"]})
        error = {
            "errorType": (mapping.type if mapping and mapping.type else code),
            "errorLevel": level_of(f, settings.version, mapping.level if mapping else ""),
            "errorReferences": refs,
            "errorDescription": f.get("message") or f.get("description") or code,
        }
        if settings.version != "2.0":
            try:
                text = hint(runbook(code))
            except LookupError:
                text = ""
            if text:
                error["errorHint"] = text
        out.append(error)
    # Most severe first: what the fleet manager acts on.
    out.sort(key=lambda e: -LEVELS.index(e["errorLevel"]))
    return out
