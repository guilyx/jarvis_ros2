# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The gateway's HTTP API (/api/v1), live stream, metrics and the dashboard bundle.

Reads come from the live snapshots, the gateway's recent events and the lake. Writes go to the
Jarvis component that owns the change (through the port), with the caller's name from their token:
every change is recorded on the event stream by the component that made it.
"""

import asyncio
import contextlib
import datetime
import fnmatch
import json
import pathlib
import subprocess
import tempfile
import time

import yaml
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jarvis_lake import model, storage

from jarvis_gateway import __version__, auth, bags, models, sovd, stream, vda5050
from jarvis_gateway.catalog import Catalog
from jarvis_gateway.history import History
from jarvis_gateway.metrics import Metrics
from jarvis_gateway.notifier import Notifier
from jarvis_gateway.state import LiveState

SEVERITIES = ("DEBUG", "INFO", "WARN", "ERROR", "CRITICAL")
COMMAND_TIMEOUT_S = 15.0


class Gateway:
    """Everything the routes share."""

    def __init__(self, settings, port, notifier=None):
        self.settings = settings
        self.port = port
        self.state = LiveState(settings.recent_capacity)
        self.history = History(settings.data_dir, self.state)
        self.catalog = Catalog(settings.catalog, settings.runbooks)
        self.metrics = Metrics()
        self.notifier = notifier or Notifier(settings.webhooks, on_delivery=self.metrics.on_delivery)
        self.clients = set()
        self.started_at = time.time()
        self.backfilled = False
        self._tasks = []
        self._bag_cache = {}

    async def start(self):
        self.state.listen(self.metrics.on_state)
        self.state.listen(lambda kind, payload: kind == "event" and self.notifier.on_event(payload))
        await self.port.start(self.state)
        self._tasks.append(asyncio.create_task(self._backfill()))
        self._tasks.append(asyncio.create_task(self._trim()))

    async def stop(self):
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.notifier.drain()
        await self.port.stop()

    async def _backfill(self):
        """Fetches what the event hub has that the lake does not (yet): the gap until now.

        Into history only: listeners (the notifier above all) see live events, so a restart does not
        send old notifications again."""
        while True:
            try:
                after = await asyncio.to_thread(self.history.lake_max_seq, 0.0)
                while True:
                    events, next_seq, _ = await self.port.query_events(after_seq=after, limit=1000)
                    for e in events:
                        self.state.recent.add(e)
                        self.state.robot = self.state.robot or (e.get("source") or {}).get("robot", "")
                    if not next_seq:
                        break
                    after = next_seq
                self.backfilled = True
                return
            except Exception:  # noqa: BLE001 - the hub is not up yet: try again
                await asyncio.sleep(2.0)

    async def _trim(self):
        """Forgets recent events once the lake has them (it converts a split every few minutes)."""
        while True:
            await asyncio.sleep(30.0)
            with contextlib.suppress(Exception):
                self.state.recent.trim_below(await asyncio.to_thread(self.history.lake_max_seq))

    def lake_status(self):
        return storage.read_json(self.history.lake.layout.status, None)

    async def command(self, name, coro):
        """Runs a command against Jarvis: 409 when it refuses, 503 when it is not reachable."""
        try:
            result = await asyncio.wait_for(coro, COMMAND_TIMEOUT_S)
        except (TimeoutError, ConnectionError) as e:
            self.metrics.requests.labels(name, "unavailable").inc()
            raise HTTPException(503, f"Jarvis did not answer: {e or 'timeout'}") from e
        self.metrics.requests.labels(name, "ok" if result.success else "refused").inc()
        if not result.success:
            raise HTTPException(409, result.message or "refused")
        return models.Outcome(success=True, message=result.message, data=result.data)


def create_app(settings, port, notifier=None):
    gw = Gateway(settings, port, notifier)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        await gw.start()
        try:
            yield
        finally:
            await gw.stop()

    app = FastAPI(
        title="Jarvis gateway",
        version=__version__,
        description="Events, faults, incidents, recovery and history of one robot (LAN only).",
        lifespan=lifespan,
    )
    app.state.gateway = gw
    if settings.cors_origins:  # browser clients served from elsewhere (a SOVD web UI)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

    # -- auth --------------------------------------------------------------------------------------

    def caller(role):
        def dep(request: Request, authorization: str | None = Header(None), token: str | None = Query(None)):
            client = request.client.host if request.client else None
            try:
                who = auth.identify(settings, auth.presented(token, authorization), request.headers, client)
            except auth.Refused as e:
                raise HTTPException(e.status, e.detail) from None
            if not who.can(role):
                raise HTTPException(403, f"needs the {role} role ({who.name} is {who.role})")
            return who

        return dep

    viewer, operator, admin = caller("viewer"), caller("operator"), caller("admin")
    api = APIRouter(prefix="/api/v1")

    # -- who and how -------------------------------------------------------------------------------

    @api.get("/health", response_model=models.Health, tags=["system"])
    def health():
        age = None
        if gw.state.last_event_monotonic is not None:
            age = round(time.monotonic() - gw.state.last_event_monotonic, 3)
        snapshots = gw.state.faults is not None and gw.state.executions is not None
        status = "ok" if gw.port.connected and snapshots else "starting"
        if time.time() - gw.started_at > 30 and status != "ok":
            status = "degraded"
        return models.Health(
            status=status,
            connected=gw.port.connected,
            faults_snapshot=gw.state.faults is not None,
            executions_snapshot=gw.state.executions is not None,
            last_event_age_s=age,
            robot=gw.state.robot,
            lake=gw.lake_status(),
        )

    @api.get("/me", response_model=models.Me, tags=["system"])
    def me(who=Depends(viewer)):
        return models.Me(name=who.name, role=who.role)

    @api.get("/system", tags=["system"])
    def system(who=Depends(viewer)):
        """Jarvis' own health: lifecycle states of the watched nodes, the lake, notifications."""
        states = gw.history.events(codes=["ros.lifecycle.*.state"], limit=500)
        components = {}
        for e in reversed(states):  # oldest first, so the newest wins
            attrs = e.get("attrs") or {}
            components[attrs.get("node") or e["code"]] = {
                "state": attrs.get("value", ""),
                "since": e["stamp"],
            }
        return {
            "version": __version__,
            "robot": gw.state.robot,
            "uptime_s": round(time.time() - gw.started_at, 1),
            "connected": gw.port.connected,
            "events_received": gw.state.events_received,
            "events_dropped": gw.port.dropped,
            "recent_events": len(gw.state.recent),
            "stream_clients": len(gw.clients),
            "components": components,
            "lake": gw.lake_status(),
            "notifications": list(gw.notifier.deliveries),
            "webhooks": [w.name for w in settings.webhooks],
        }

    # -- events ------------------------------------------------------------------------------------

    @api.get("/events", response_model=models.EventPage, tags=["events"])
    def events(
        who=Depends(viewer),
        after_seq: str | None = None,
        before_seq: str | None = None,
        since: str | None = Query(None, alias="from", description="ISO 8601"),
        until: str | None = Query(None, alias="to", description="ISO 8601"),
        kind: list[str] = Query(default=[]),
        code: list[str] = Query(default=[], description="globs, e.g. ros.*"),
        min_severity: str | None = None,
        component: str = "",
        correlation_id: str = "",
        q: str = "",
        limit: int = Query(200, ge=1, le=1000),
        order: str = Query("desc", pattern="^(asc|desc)$"),
    ):
        rows = gw.history.events(
            after_seq=int(after_seq) if after_seq else None,
            before_seq=int(before_seq) if before_seq else None,
            since=_datetime(since),
            until=_datetime(until),
            kinds=kind,
            codes=code,
            min_severity=min_severity,
            component=component,
            correlation_id=correlation_id,
            q=q,
            limit=limit,
            newest_first=order == "desc",
        )
        more = len(rows) == limit and order == "desc"
        return {"events": rows, "next_before_seq": rows[-1]["seq"] if more else None}

    @api.get("/events/{event_id}", response_model=models.EventDetail, tags=["events"])
    def event(event_id: str, who=Depends(viewer)):
        found = gw.history.event(event_id)
        if not found:
            raise HTTPException(404, f"no event '{event_id}'")
        return found

    @api.get("/audit", tags=["events"])
    def audit(who=Depends(viewer), limit: int = Query(200, ge=1, le=1000)):
        """What operators did: acks, clears, closes, mode and rule changes."""
        return {"events": gw.history.audit(limit)}

    # -- faults and incidents ----------------------------------------------------------------------

    @api.get("/faults", response_model=models.Faults, tags=["faults"])
    def faults(
        who=Depends(viewer),
        state: str = Query("active", pattern="^(active|all)$"),
        code: str = "",
        component: str = "",
    ):
        open_faults = [
            f
            for f in gw.state.faults or []
            if (not code or _glob(f["code"], code))
            and (not component or f["source"]["component"] == component)
        ]
        out = {"faults": open_faults}
        if state == "all":
            out["history"] = gw.history.fault_episodes(code=code, component=component)
        return out

    @api.get("/faults/{fault_id}", response_model=models.FaultDetail, tags=["faults"])
    def fault(fault_id: str, who=Depends(viewer)):
        live = gw.state.fault(fault_id)
        episodes = [e for e in gw.history.fault_episodes(limit=10000) if e["fault_id"] == fault_id]
        if not live and not episodes:
            raise HTTPException(404, f"no fault '{fault_id}'")
        incident = (live or {}).get("incident_id") or (episodes[0]["incident_id"] if episodes else "")
        return {
            "fault": live,
            "episode": episodes[0] if episodes else None,
            "transitions": gw.history.fault_transitions(fault_id),
            "executions": [
                x for x in gw.history.executions(limit=500) if incident and x["incident_id"] == incident
            ],
        }

    @api.get("/catalog", response_model=models.Catalog, tags=["faults"])
    def catalog(who=Depends(viewer)):
        """The fault catalog: what each code means, and which have a runbook."""
        return {"faults": gw.catalog.faults()}

    @api.get("/runbooks/{code}", response_model=models.Runbook, tags=["faults"])
    def runbook(code: str, who=Depends(viewer)):
        """What to check and do about a fault code (Markdown), from its catalog entry."""
        try:
            text = gw.catalog.runbook(code)
        except LookupError as e:
            raise HTTPException(500, str(e)) from None
        if text is None:
            raise HTTPException(404, f"no runbook for '{code}'")
        return {"code": code, "markdown": text}

    # VDA 5050 fields are strings: one without a value is left out, never null.
    @api.get(
        "/vda5050/errors",
        response_model=models.Vda5050Errors,
        response_model_exclude_none=True,
        tags=["faults"],
    )
    def vda5050_errors(who=Depends(viewer)):
        """Jarvis's open root-cause faults as VDA 5050 error objects, for the robot's VDA 5050 adapter
        to merge into the `errors` of its state (docs/guides/vda5050.md)."""
        settings = gw.settings.vda5050 or vda5050.Settings()
        return {
            "version": settings.version,
            "errors": vda5050.errors(gw.state.faults, settings, gw.catalog.runbook),
        }

    @api.post("/faults/{fault_id}:ack", response_model=models.Outcome, tags=["faults"])
    async def ack(fault_id: str, body: models.Note, who=Depends(operator)):
        return await gw.command("ack", gw.port.ack_fault(fault_id, who.name, body.note))

    @api.post("/faults/{fault_id}:clear", response_model=models.Outcome, tags=["faults"])
    async def clear(fault_id: str, body: models.Clear, who=Depends(operator)):
        return await gw.command(
            "clear", gw.port.clear_fault(fault_id, who.name, body.reason, body.skip_symptoms)
        )

    @api.get("/incidents", response_model=models.Incidents, tags=["incidents"])
    def incidents(who=Depends(viewer), state: str = "", limit: int = Query(100, ge=1, le=1000)):
        return {"incidents": gw.history.incidents(state, limit)}

    @api.get("/incidents/{incident_id}", response_model=models.IncidentDetail, tags=["incidents"])
    def incident(incident_id: str, who=Depends(viewer)):
        found = gw.history.incident(incident_id)
        if not found:
            raise HTTPException(404, f"no incident '{incident_id}'")
        return found

    @api.post("/incidents/{incident_id}:close", response_model=models.Outcome, tags=["incidents"])
    async def close(incident_id: str, body: models.Note, who=Depends(operator)):
        return await gw.command("close", gw.port.close_incident(incident_id, who.name, body.note))

    # -- recovery ----------------------------------------------------------------------------------

    @api.get("/recoveries", response_model=models.Recoveries, tags=["recovery"])
    def recoveries(who=Depends(viewer), limit: int = Query(100, ge=1, le=1000)):
        snap = gw.state.executions or {}
        return {
            "mode": snap.get("mode", "UNKNOWN"),
            "live": snap.get("executions") or [],
            "recent": list(reversed(snap.get("recent") or [])),
            "history": gw.history.executions(limit),
        }

    @api.get("/recoveries/{execution_id}", tags=["recovery"])
    def recovery(execution_id: str, who=Depends(viewer)):
        live = gw.state.execution(execution_id)
        past = gw.history.execution(execution_id)
        if not live and not past:
            raise HTTPException(404, f"no execution '{execution_id}'")
        return {"execution": live, "history": past}

    @api.post("/recoveries", response_model=models.Outcome, status_code=202, tags=["recovery"])
    async def recover(body: models.Recover, who=Depends(operator)):
        """Runs a plan as an operator, through the same gates as a rule."""
        return await gw.command(
            "recover",
            gw.port.recover(
                body.plan_id, body.args, who.name, body.rationale, body.dry_run, body.correlation_id
            ),
        )

    @api.post("/recoveries/{execution_id}:approve", response_model=models.Outcome, tags=["recovery"])
    async def approve(execution_id: str, body: models.Note, who=Depends(operator)):
        return await gw.command("approve", gw.port.decide(execution_id, True, who.name, body.note))

    @api.post("/recoveries/{execution_id}:reject", response_model=models.Outcome, tags=["recovery"])
    async def reject(execution_id: str, body: models.Note, who=Depends(operator)):
        return await gw.command("reject", gw.port.decide(execution_id, False, who.name, body.note))

    @api.post("/recoveries/{execution_id}:cancel", response_model=models.Outcome, tags=["recovery"])
    async def cancel(execution_id: str, body: models.Cancel, who=Depends(operator)):
        return await gw.command("cancel", gw.port.cancel(execution_id, who.name, body.reason))

    @api.put("/recovery/mode", response_model=models.Outcome, tags=["recovery"])
    async def mode(body: models.Mode, who=Depends(admin)):
        """The kill switch: ENABLED, DRY_RUN (decide everything, move nothing) or DISABLED."""
        return await gw.command(
            "mode", gw.port.set_mode(body.mode, who.name, body.reason, body.cancel_running)
        )

    @api.get("/plans", response_model=models.Plans, tags=["recovery"])
    async def plans(who=Depends(viewer), hours: float = 24.0):
        listed, current = await _ask(gw.port.list_plans())
        stats = {p["plan"]: p for p in gw.history.analytics("plans", hours)}
        return {"mode": current, "plans": [{**p, "stats": stats.get(p["id"])} for p in listed]}

    @api.get("/plans/{plan_id}", tags=["recovery"])
    async def plan(plan_id: str, who=Depends(viewer), hours: float = 24.0):
        """One plan, its effectiveness over the window and its latest executions."""
        listed, _ = await _ask(gw.port.list_plans())
        found = next((p for p in listed if p["id"] == plan_id), None)
        if not found:
            raise HTTPException(404, f"no plan '{plan_id}'")
        stats = next((p for p in gw.history.analytics("plans", hours) if p["plan"] == plan_id), None)
        return {**found, "stats": stats, "executions": gw.history.executions(50, plan=plan_id)}

    @api.post("/plans:reload", response_model=models.Outcome, tags=["recovery"])
    async def reload_plans(body: models.Reload, who=Depends(admin)):
        return await gw.command("reload_plans", gw.port.reload("plans", body.validate_only, who.name))

    # -- rules -------------------------------------------------------------------------------------

    @api.get("/rules", response_model=models.Rules, tags=["rules"])
    async def rules(who=Depends(viewer), hours: float = 24.0):
        listed = await _ask(gw.port.list_rules())
        recent = {r["rule"]: r for r in gw.history.analytics("rules", hours)}
        out = []
        for r in listed:
            window = recent.get(r["id"]) or {}
            out.append(
                {
                    **r,
                    "fires_window": window.get("fires", 0),
                    "dry_run_fires_window": window.get("dry_run_fires", 0),
                    "last_fire": window.get("last_fire"),
                }
            )
        return {"rules": out}

    @api.get("/rules/{rule_id}", tags=["rules"])
    async def rule(rule_id: str, who=Depends(viewer)):
        listed = await _ask(gw.port.list_rules())
        found = next((r for r in listed if r["id"] == rule_id), None)
        if not found:
            raise HTTPException(404, f"no rule '{rule_id}'")
        return {
            **found,
            "yaml": _rule_yaml(found.get("file"), rule_id),
            "matches": gw.history.rule_matches(rule_id),
        }

    @api.patch("/rules/{rule_id}", response_model=models.Outcome, tags=["rules"])
    async def set_rule(rule_id: str, body: models.RuleState, who=Depends(admin)):
        return await gw.command(
            "rule_state", gw.port.set_rule_state(rule_id, body.enabled, body.dry_run, who.name, body.reason)
        )

    @api.post("/rules:validate", response_model=models.Outcome, tags=["rules"])
    async def validate(body: models.Validate, who=Depends(admin)):
        """Checks rules YAML with `jarvis lint` against the loaded plans and catalog. Changes nothing."""
        return await asyncio.to_thread(_lint, settings, body.yaml)

    @api.post("/rules:reload", response_model=models.Outcome, tags=["rules"])
    async def reload_rules(body: models.Reload, who=Depends(admin)):
        return await gw.command("reload_rules", gw.port.reload("rules", body.validate_only, who.name))

    # -- history and analytics ---------------------------------------------------------------------

    @api.get("/stats", response_model=models.Stats, tags=["analytics"])
    def stats(who=Depends(viewer), hours: float = Query(24.0, gt=0)):
        counts = dict.fromkeys(SEVERITIES, 0)
        for f in gw.state.faults or []:
            if not f.get("muted_by"):
                counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        return {
            "hours": hours,
            "kpis": gw.history.analytics("kpis", hours),
            "faults_open": counts,
            "event_rate": gw.history.analytics("event_rate", hours, bucket_s=_bucket(hours)),
            "top_codes": gw.history.analytics("top_codes", hours),
            "plans": gw.history.analytics("plans", hours),
        }

    @api.get("/analytics/{name}", tags=["analytics"])
    def analytics_view(
        name: str,
        who=Depends(viewer),
        hours: float = Query(24.0, gt=0),
        bucket_s: int | None = None,
        min_cycles: int = 3,
    ):
        """components (health per component), top_codes (Pareto), heatmap, flapping, plans, rules."""
        extra = {}
        if name in ("heatmap", "event_rate"):
            extra["bucket_s"] = bucket_s or _bucket(hours)
        if name == "flapping":
            extra["min_cycles"] = min_cycles
        if name not in ("components", "top_codes", "heatmap", "flapping", "plans", "rules", "event_rate"):
            raise HTTPException(404, f"no analytics '{name}'")
        return {"name": name, "hours": hours, "rows": gw.history.analytics(name, hours, **extra)}

    @api.get("/insights", tags=["agent"])
    def insights(who=Depends(viewer), limit: int = Query(50, ge=1, le=500)):
        return {"insights": gw.history.insights(limit)}

    @api.get("/insights/{event_id}", tags=["agent"])
    def insight(event_id: str, who=Depends(viewer)):
        found = gw.history.insight(event_id)
        if not found:
            raise HTTPException(404, f"no insight '{event_id}'")
        return found

    @api.post(
        "/agent/ask",
        tags=["agent"],
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}, "description": "Server-sent events"}},
    )
    async def ask(body: models.Ask, who=Depends(operator)):
        """Asks the agent; the answer streams as server-sent events: `progress` ({stage, detail}) as
        it works, then `insight` (jarvis.insight/v1, also published as an INSIGHT event) or `error`
        ({message}). An operator's call: a model costs money."""
        if not body.incident_id and not body.question.strip():
            raise HTTPException(422, "ask about an incident, or ask a question")
        answers = gw.port.ask(body.incident_id, body.question.strip(), who.name)
        try:  # the first answer, here: a missing agent is a 503, not a stream with an error
            first = await anext(answers)
        except ConnectionError as e:
            gw.metrics.requests.labels("ask", "unavailable").inc()
            raise HTTPException(503, f"the agent did not answer: {e}") from e
        except StopAsyncIteration:
            first = None
        gw.metrics.requests.labels("ask", "ok").inc()

        async def stream():
            if first is not None:
                yield _sse(*first)
            try:
                async for kind, data in answers:
                    yield _sse(kind, data)
            except ConnectionError as e:
                yield _sse("error", {"message": str(e)})

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @api.get("/bags", tags=["recordings"])
    def bag_list(who=Depends(viewer)):
        incidents_seen = gw.history.incidents(limit=1000)
        ledger = storage.Ledger(gw.history.lake.layout.ledger)
        return {"bags": bags.listing(gw.history.lake.layout, ledger, incidents_seen, gw._bag_cache)}

    @api.get("/bags/{kind}/{bag}/{file}", tags=["recordings"])
    def bag_file(kind: str, bag: str, file: str, who=Depends(viewer)):
        path = bags.resolve(gw.history.lake.layout, kind, bag, file)
        if path is None:
            raise HTTPException(404, "no such recording")
        return FileResponse(path, media_type="application/octet-stream", filename=file)

    # -- live --------------------------------------------------------------------------------------

    @app.websocket("/api/v1/stream")
    async def live(ws: WebSocket, token: str | None = None):
        # A token in the Authorization header (programs), else ?token= (browsers cannot set headers).
        presented = auth.presented(token, ws.headers.get("authorization"))
        try:
            auth.identify(settings, presented, ws.headers, ws.client.host if ws.client else None)
        except auth.Refused as e:
            await ws.close(code=1008, reason=e.detail)
            return
        await ws.accept()
        await stream.serve(ws, gw.state, gw.history, gw.metrics.clients.set, gw.clients)

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return Response(gw.metrics.render(gw.lake_status()), media_type=gw.metrics.content_type)

    app.include_router(api)
    for routes in sovd.router(gw, viewer, operator):  # SOVD clients: /sovd/api/v1
        app.include_router(routes)

    @app.exception_handler(ValueError)
    async def bad_value(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    dashboard = pathlib.Path(settings.dashboard_dir) if settings.dashboard_dir else None
    if dashboard and (dashboard / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=dashboard / "assets", check_dir=False), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            target = (dashboard / path).resolve()
            if path and dashboard.resolve() in target.parents and target.is_file():
                return FileResponse(target)
            return FileResponse(dashboard / "index.html")  # the dashboard routes in the browser
    else:

        @app.get("/", include_in_schema=False)
        def home():
            return HTMLResponse(
                "<!doctype html><title>Jarvis gateway</title><h1>Jarvis gateway</h1>"
                "<p>The API is at <a href='/docs'>/docs</a>. The dashboard is not installed here.</p>"
            )

    return app


# -- helpers ---------------------------------------------------------------------------------------


async def _ask(coro):
    try:
        return await asyncio.wait_for(coro, COMMAND_TIMEOUT_S)
    except (TimeoutError, ConnectionError) as e:
        raise HTTPException(503, f"Jarvis did not answer: {e or 'timeout'}") from e


def _datetime(text):
    if not text:
        return None
    ns = model.parse_iso8601(text)
    return datetime.datetime.fromtimestamp(ns / 1e9, tz=datetime.UTC)


def _glob(text, pattern):
    return fnmatch.fnmatchcase(text, pattern)


def _bucket(hours):
    """A bucket size that gives about 60 points over the window."""
    return max(60, int(hours * 3600 / 60))


def _rule_yaml(file, rule_id):
    """The rule's definition as written in its file (YAML), or "" if the file is not readable."""
    if not file:
        return ""
    try:
        doc = yaml.safe_load(pathlib.Path(file).read_text()) or {}
    except (OSError, yaml.YAMLError):
        return ""
    rule = next((r for r in doc.get("rules") or [] if r.get("id") == rule_id), None)
    return yaml.safe_dump(rule, sort_keys=False) if rule else ""


def _lint(settings, text):
    with tempfile.TemporaryDirectory(prefix="jarvis_validate_") as tmp:
        rules = pathlib.Path(tmp) / "rules"
        rules.mkdir()
        (rules / "candidate.yaml").write_text(text)
        cmd = [settings.jarvis_bin, "lint", "--rules", str(rules)]
        if settings.lint_plans_dir:
            cmd += ["--plans", settings.lint_plans_dir]
        if settings.lint_catalog or settings.catalog:
            cmd += ["--catalog", settings.lint_catalog or settings.catalog]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except FileNotFoundError as e:
            raise HTTPException(503, f"`{settings.jarvis_bin}` is not installed here") from e
    findings = []
    for line in (out.stdout + out.stderr).splitlines():
        level, sep, text_ = line.partition(": ")
        if sep and level in ("error", "warning"):
            findings.append({"level": level, "message": text_.replace(str(rules) + "/", "")})
    summary = next((line for line in out.stdout.splitlines() if "error(s)" in line), "")
    return models.Outcome(
        success=out.returncode == 0,
        message=summary,
        data={"findings": findings},
    )


__all__ = ["create_app", "Gateway"]


def _sse(kind, data):
    """One server-sent event."""
    return f"event: {kind}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
