# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Jarvis's faults through a SOVD-style API (Service-Oriented Vehicle Diagnostics, ISO 17978, as
ros2_medkit serves it), so that SOVD clients (the ros2_medkit web UI, its Foxglove panel) list
Jarvis's faults, follow them live and clear them, with the gateway's tokens and roles:

    GET    /sovd/version-info                                   the API versions served
    GET    /sovd/api/v1/  /health  /version-info
    GET    /sovd/api/v1/{areas,functions,components,apps}[/{id}]  the entity tree
    GET    /sovd/api/v1/faults[?status=...&include_muted=...]   every fault, as medkit lists them
    GET    /sovd/api/v1/faults/stream                           live: confirmed, updated, cleared
    GET    /sovd/api/v1/{components,apps}/{id}/faults[/{code}]  an entity's faults, one in detail
    DELETE /sovd/api/v1/{components,apps}/{id}/faults[/{code}]  clears them (operator)

The robot is the one component; each component of Jarvis's faults (`source.component`) is an app on
it, its id made of lowercase letters, digits and dashes. A fault's code is Jarvis's code. Severities
map to SOVD's 0-3 (DEBUG and INFO are 0), states to medkit's statuses: PENDING is PREFAILED, ACTIVE
and LATCHED are CONFIRMED, CLEARED is CLEARED. Muted symptoms are left out unless include_muted=true.
Clients point at http://<gateway>/sovd (they add /api/v1).
"""

import asyncio
import json
import re
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
from jarvis_lake import model

from jarvis_gateway import __version__

BASE = "/sovd/api/v1"
SEVERITY = {"DEBUG": 0, "INFO": 0, "WARN": 1, "ERROR": 2, "CRITICAL": 3}
LABEL = {0: "INFO", 1: "WARN", 2: "ERROR", 3: "CRITICAL"}
STATUS = {"PENDING": "PREFAILED", "ACTIVE": "CONFIRMED", "LATCHED": "CONFIRMED", "CLEARED": "CLEARED"}
CONFIRMED = ("ACTIVE", "LATCHED")
# The fault's status bits (UDS DTC status, as SOVD reports them).
BITS = {
    "PENDING": ("passive", "1", "0", "1"),
    "ACTIVE": ("active", "1", "1", "0"),
    "LATCHED": ("active", "0", "1", "0"),
    "CLEARED": ("cleared", "0", "0", "0"),
}
FILTERS = {
    "pending": {"PREFAILED"},
    "confirmed": {"CONFIRMED"},
    "cleared": {"CLEARED"},
    "healed": {"CLEARED"},  # Jarvis does not tell a healed fault from a cleared one
    "all": {"PREFAILED", "CONFIRMED", "CLEARED"},
}
KEEPALIVE_S = 15.0


def slug(text):
    """An entity id: lowercase letters, digits and dashes (the SOVD clients turn _ into -)."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "jarvis"


def _epoch(iso):
    return round(model.parse_iso8601(iso) / 1e9, 3) if iso else 0.0


def fault_changes(before, after):
    """What the stream reports between two fault snapshots (fault_id -> fault), as medkit does:
    a fault confirmed, a confirmed one seen again, one gone. Pending faults are not reported."""
    for fault_id, f in after.items():
        prev = before.get(fault_id)
        if f["state"] in CONFIRMED and (prev is None or prev["state"] not in CONFIRMED):
            yield "fault_confirmed", f
        elif f["state"] in CONFIRMED and (
            prev["occurrences"] != f["occurrences"] or prev.get("last_seen") != f.get("last_seen")
        ):
            yield "fault_updated", f
    for fault_id, f in before.items():
        if fault_id not in after and f["state"] in CONFIRMED:
            yield "fault_cleared", {**f, "state": "CLEARED"}


def _error(status, error_code, message, **parameters):
    return JSONResponse({"error_code": error_code, "message": message, "parameters": parameters}, status)


def router(gw, viewer, operator):
    """The SOVD routes over a Gateway, with its role checks: `api` at /sovd/api/v1, `root` at
    /sovd."""
    api = APIRouter(prefix=BASE, tags=["sovd"])
    root = APIRouter(prefix="/sovd", tags=["sovd"])

    def robot():
        return gw.state.robot or "robot"

    def version_info():
        return {
            "version": "1.0.0",
            "base_uri": BASE,
            "vendor_info": {"name": "jarvis", "version": __version__},
        }

    def open_faults(include_muted=False):
        return [f for f in gw.state.faults or [] if include_muted or not f.get("muted_by")]

    def app_of(fault):
        return slug(fault["source"].get("component"))

    def item(fault):
        """A fault as medkit lists them, Jarvis's own fields under x-jarvis."""
        severity = SEVERITY.get(fault["severity"], 2)
        return {
            "fault_code": fault["code"],
            "severity": severity,
            "severity_label": LABEL[severity],
            "description": fault.get("message") or fault.get("description") or fault["code"],
            "first_occurred": _epoch(fault.get("first_seen")),
            "last_occurred": _epoch(fault.get("last_seen")),
            "occurrence_count": fault.get("occurrences", 1),
            "status": STATUS.get(fault["state"], "PREFAILED"),
            "reporting_sources": [f"/{slug(robot())}/{app_of(fault)}"],
            "x-jarvis": {
                "fault_id": fault["fault_id"],
                "state": fault["state"],
                "component": fault["source"].get("component", ""),
                "muted_by": fault.get("muted_by", ""),
                "incident_id": fault.get("incident_id", ""),
                "acknowledged": fault.get("acknowledged", False),
            },
        }

    def cleared(limit=200):
        """Cleared faults, from the history (newest first), in the open faults' shape."""
        out = []
        for e in gw.history.fault_episodes(limit=limit):
            if e.get("last_state") != "CLEARED":
                continue
            out.append(
                {
                    "fault_id": e["fault_id"],
                    "code": e["code"],
                    "source": {"component": e.get("component") or ""},
                    "severity": model.SEVERITIES.get(e.get("severity_level"), "ERROR"),
                    "state": "CLEARED",
                    "occurrences": e.get("occurrences") or 1,
                    "first_seen": e.get("first_seen"),
                    "last_seen": e.get("cleared_at") or e.get("first_seen"),
                    "muted_by": "",
                    "incident_id": e.get("incident_id") or "",
                    "message": "",
                }
            )
        return out

    def faults(status, include_muted, entity=None):
        wanted = set()
        for s in (status or "pending,confirmed").lower().split(","):
            if s not in FILTERS:
                raise HTTPException(400, f"status: one of {', '.join(FILTERS)}")
            wanted |= FILTERS[s]
        found = open_faults(include_muted)
        if "CLEARED" in wanted:
            found += cleared()
        if entity:
            found = [f for f in found if entity(f)]
        return [item(f) for f in found if STATUS.get(f["state"]) in wanted]

    def entity_filter(collection, entity_id):
        """Which faults belong to an entity, or an error response if there is no such entity."""
        if collection == "components":
            if entity_id != slug(robot()):
                return None
            return lambda f: True
        return lambda f: app_of(f) == entity_id

    def apps():
        names = {}
        for f in open_faults(include_muted=True):
            names.setdefault(app_of(f), f["source"].get("component") or "jarvis")
        return [
            {"id": a, "name": n, "href": f"{BASE}/apps/{a}", "x-jarvis": {"component": n}}
            for a, n in sorted(names.items())
        ]

    def component():
        r = slug(robot())
        return {"id": r, "name": robot(), "href": f"{BASE}/components/{r}"}

    # -- discovery ---------------------------------------------------------------------------------

    @root.get("/version-info")
    def sovd_info():
        """The SOVD versions served (the standard's discovery, outside the versioned API)."""
        return {"sovd_info": [version_info()]}

    @api.get("/")
    def capabilities(who=Depends(viewer)):
        return {
            "name": "Jarvis",
            "version": __version__,
            "api_base": BASE,
            "capabilities": {"faults": True, "fault_stream": True, "clear_faults": True},
        }

    @api.get("/health")
    def health():
        return {"status": "healthy" if gw.port.connected else "degraded", "timestamp": time.time()}

    @api.get("/version-info")
    def versions():
        return {"items": [version_info()]}

    @api.get("/areas")
    def areas(who=Depends(viewer)):
        return {"items": []}

    @api.get("/functions")
    def functions(who=Depends(viewer)):
        return {"items": []}

    @api.get("/components")
    def components(who=Depends(viewer)):
        return {"items": [component()]}

    @api.get("/components/{entity_id}")
    def one_component(entity_id: str, who=Depends(viewer)):
        if entity_id != slug(robot()):
            return _error(404, "resource-not-found", f"no component '{entity_id}'", id=entity_id)
        return component()

    @api.get("/components/{entity_id}/subcomponents")
    def subcomponents(entity_id: str, who=Depends(viewer)):
        return {"items": []}

    @api.get("/components/{entity_id}/hosts")
    def hosts(entity_id: str, who=Depends(viewer)):
        return {"items": apps() if entity_id == slug(robot()) else []}

    @api.get("/apps")
    def all_apps(who=Depends(viewer)):
        return {"items": apps()}

    @api.get("/apps/{entity_id}")
    def one_app(entity_id: str, who=Depends(viewer)):
        found = next((a for a in apps() if a["id"] == entity_id), None)
        if found is None:
            return _error(404, "resource-not-found", f"no app '{entity_id}' has faults", id=entity_id)
        return found

    # -- faults ------------------------------------------------------------------------------------

    @api.get("/faults")
    def all_faults(who=Depends(viewer), status: str = "", include_muted: bool = False):
        items = faults(status, include_muted)
        return {"items": items, "x-medkit": {"count": len(items)}}

    @api.get("/faults/stream")
    async def stream(who=Depends(viewer), include_muted: bool = False):
        """Server-sent events, as medkit sends them: fault_confirmed, fault_updated, fault_cleared."""
        queue = asyncio.Queue()
        unlisten = gw.state.listen(lambda kind, payload: kind == "faults" and queue.put_nowait(payload))

        async def frames():
            before = {f["fault_id"]: f for f in open_faults(include_muted)}
            n = 0
            try:
                while True:
                    try:
                        snapshot = await asyncio.wait_for(queue.get(), KEEPALIVE_S)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    after = {f["fault_id"]: f for f in snapshot if include_muted or not f.get("muted_by")}
                    for kind, f in fault_changes(before, after):
                        n += 1
                        data = {"event_type": kind, "fault": item(f), "timestamp": time.time()}
                        yield f"id: {n}\nevent: {kind}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
                    before = after
            finally:
                unlisten()

        return StreamingResponse(
            frames(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @api.get("/{collection}/{entity_id}/faults")
    def entity_faults(
        collection: str, entity_id: str, who=Depends(viewer), status: str = "", include_muted: bool = False
    ):
        belongs = _entity(collection, entity_id)
        if isinstance(belongs, Response):
            return belongs
        items = faults(status, include_muted, belongs)
        return {"items": items, "x-medkit": {"count": len(items)}}

    @api.get("/{collection}/{entity_id}/faults/{code}")
    def entity_fault(collection: str, entity_id: str, code: str, who=Depends(viewer)):
        belongs = _entity(collection, entity_id)
        if isinstance(belongs, Response):
            return belongs
        found = [f for f in open_faults(True) + cleared() if f["code"] == code and belongs(f)]
        if not found:
            return _error(
                404, "resource-not-found", f"no fault '{code}' on {entity_id}", code=code, id=entity_id
            )
        f = found[0]  # the open one if there is one, else the newest cleared
        listed = item(f)
        aggregated, failed, confirmed, pending = BITS.get(f["state"], BITS["ACTIVE"])
        runbook = gw.catalog.runbook_path(code) is not None
        freeze = f.get("freeze_frame") or {}
        return {
            "item": {
                "code": code,
                "fault_name": listed["description"],
                "severity": listed["severity"],
                "status": {
                    "aggregatedStatus": aggregated,
                    "testFailed": failed,
                    "confirmedDTC": confirmed,
                    "pendingDTC": pending,
                },
            },
            "environment_data": {
                "extended_data_records": {
                    "first_occurrence": f.get("first_seen"),
                    "last_occurrence": f.get("last_seen"),
                },
                "snapshots": [{"type": "freeze_frame", "name": code, "data": freeze}] if freeze else [],
            },
            "x-medkit": {
                "occurrence_count": listed["occurrence_count"],
                "reporting_sources": listed["reporting_sources"],
                "severity_label": listed["severity_label"],
            },
            "x-jarvis": {**listed["x-jarvis"], "runbook": f"/api/v1/runbooks/{code}" if runbook else None},
        }

    # After the fault routes: the first route that matches a path serves it.
    @api.get("/{collection}/{entity_id}/{resource}")
    def nothing_else(collection: str, entity_id: str, resource: str, who=Depends(viewer)):
        """Data, operations and configurations: Jarvis serves faults only, so these are empty."""
        if resource not in ("data", "operations", "configurations") or collection not in (
            "components",
            "apps",
        ):
            return _error(404, "resource-not-found", f"no {resource} here", resource=resource)
        return {"items": []}

    async def clear(faults_, who):
        for f in faults_:
            await gw.command(
                "clear", gw.port.clear_fault(f["fault_id"], who.name, "cleared from a SOVD client", False)
            )
        return Response(status_code=204)

    @api.delete("/{collection}/{entity_id}/faults")
    async def clear_entity(collection: str, entity_id: str, who=Depends(operator)):
        belongs = _entity(collection, entity_id)
        if isinstance(belongs, Response):
            return belongs
        return await clear([f for f in open_faults(True) if belongs(f)], who)

    @api.delete("/{collection}/{entity_id}/faults/{code}")
    async def clear_one(collection: str, entity_id: str, code: str, who=Depends(operator)):
        belongs = _entity(collection, entity_id)
        if isinstance(belongs, Response):
            return belongs
        found = [f for f in open_faults(True) if f["code"] == code and belongs(f)]
        if not found:
            return _error(404, "resource-not-found", f"no open fault '{code}' on {entity_id}", code=code)
        return await clear(found, who)

    def _entity(collection, entity_id):
        if collection not in ("components", "apps"):
            return _error(404, "resource-not-found", f"no faults on {collection}", collection=collection)
        belongs = entity_filter(collection, entity_id)
        if belongs is None:
            return _error(404, "resource-not-found", f"no {collection[:-1]} '{entity_id}'", id=entity_id)
        return belongs

    return api, root
