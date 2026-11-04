"""Live response API (/ops): incidents, live key-in, zones (real or simulated), resources, projection, allocation
optimizer, live event stream (SSE) and the tool-using AI analyst (SSE).

RBAC: reading is open to every role; keying in reports, stock and simulated zones needs `ops.report`; enforcing a
REAL zone, approving an allocation plan or opening a LIVE incident needs `ops.command`. The AI can only propose.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import time
from collections import OrderedDict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from ...core.security import Actor
from ...ops import agent, analysis, drill, geo, optimize, project, store
from ...vietnam import sandbox as sb
from ..deps import actor, do_audit, envelope, require
from .vietnam import _need

router = APIRouter(prefix="/ops", tags=["live response"])
_cache: OrderedDict = OrderedDict()


def _inc(iid: str) -> dict:
    _need()
    inc = store.get_incident(iid)
    if not inc:
        raise HTTPException(404, "incident not found")
    return inc


def _analysis(iid: str) -> dict:
    key = (iid, store.last_seq(iid), date.today().isoformat())
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    an = analysis.analyze(iid)
    _cache[key] = an
    while len(_cache) > 16:
        _cache.popitem(last=False)
    return an


def _sem(inc: dict) -> dict:
    return {"semantics": inc["semantics"], "mode": inc["mode"]}


# ---------------------------------------------------------------------------------------------------------- incidents
@router.get("/meta")
def meta(request: Request):
    return envelope({"report_kinds": store.REPORT_KINDS, "zone_kinds": store.ZONE_KINDS, "zone_defaults": store.ZONE_DEFAULTS,
                     "resource_kinds": store.RESOURCE_KINDS, "resource_units": store.RESOURCE_UNITS,
                     "pathogens": {k: v["name"] for k, v in sb.PATHOGENS.items()}, "assumptions": project.ASSUMPTIONS,
                     "ai": {"tools": [t[0] for t in agent.TOOLS], "available": agent.available()[0]}}, request)


@router.get("/incidents")
def incidents(request: Request):
    _need()
    return envelope(store.list_incidents(), request)


@router.post("/incidents")
def create_incident(body: store.IncidentIn, request: Request, a: Actor = Depends(require("ops.report"))):
    _need()
    if body.mode == "live" and not a.can("ops.command"):
        raise HTTPException(403, "opening a LIVE incident needs the ops.command permission (epidemiology reviewer or admin); open a drill instead")
    inc = store.create_incident(body, a.user_id)
    do_audit(request, a, "ops.incident.create", "ops_incident", inc["incident_id"], meta={"mode": body.mode, "pathogen": body.pathogen})
    return envelope(inc, request)


class IncidentPatch(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    status: str | None = Field(None, pattern="^(open|closed)$")
    pathogen: str | None = None
    settings: dict | None = None


@router.patch("/incidents/{iid}")
def patch_incident(iid: str, body: IncidentPatch, request: Request, a: Actor = Depends(require("ops.report"))):
    inc = _inc(iid)
    if inc["mode"] == "live" and not a.can("ops.command"):
        raise HTTPException(403, "changing a LIVE incident needs ops.command")
    try:
        out = store.update_incident(iid, body.model_dump(exclude_none=True), a.user_id)
    except (ValueError, ValidationError) as e:
        raise HTTPException(422, str(e))
    do_audit(request, a, "ops.incident.update", "ops_incident", iid, meta=body.model_dump(exclude_none=True))
    return envelope(out, request)


@router.get("/incidents/{iid}")
def get_incident(iid: str, request: Request):
    inc = _inc(iid)
    t = time.perf_counter()
    an = _analysis(iid)
    return envelope(an, request, meta={**_sem(inc), "projection": "SCENARIO", "ms": round((time.perf_counter() - t) * 1000), "seq": store.last_seq(iid)},
                    warnings=an["warnings"])


# ------------------------------------------------------------------------------------------------------------ reports
class ReportsIn(BaseModel):
    reports: list[store.ReportIn] = Field(..., min_length=1, max_length=5000)


@router.get("/incidents/{iid}/reports")
def list_reports(iid: str, request: Request, include_void: bool = False):
    inc = _inc(iid)
    return envelope(store.reports(iid, include_void), request, meta=_sem(inc))


@router.post("/incidents/{iid}/reports")
def add_reports(iid: str, body: ReportsIn, request: Request, a: Actor = Depends(require("ops.report"))):
    inc = _inc(iid)
    if inc["status"] != "open":
        raise HTTPException(409, "incident is closed")
    try:
        out = store.add_reports(iid, body.reports, a.user_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return envelope(out, request, meta=_sem(inc))


class BulkIn(BaseModel):
    csv: str = Field(..., min_length=3, max_length=2_000_000)


@router.post("/incidents/{iid}/reports/bulk")
def bulk(iid: str, body: BulkIn, request: Request, a: Actor = Depends(require("ops.report"))):
    """CSV with a header: kind,count,lat,lon,province_id,event_date,note (any subset; kind defaults to case, count to 1)."""
    inc = _inc(iid)
    rows = list(csv.DictReader(io.StringIO(body.csv.strip())))
    if not rows:
        raise HTTPException(422, "no rows (the first line must be a header such as kind,count,lat,lon,event_date)")
    items, errors = [], []
    for i, r in enumerate(rows, start=2):
        r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
        try:
            items.append(store.ReportIn(kind=r.get("kind") or "case", count=int(float(r.get("count") or 1)),
                                        lat=float(r["lat"]) if r.get("lat") else None, lon=float(r["lon"]) if r.get("lon") else None,
                                        province_id=r.get("province_id") or None, event_date=r.get("event_date") or None, note=r.get("note") or None))
        except ValidationError as e:
            errors.append(f"line {i}: " + "; ".join(f"{'.'.join(map(str, x['loc'])) or 'row'}: {x['msg']}" for x in e.errors())[:200])
        except ValueError as e:
            errors.append(f"line {i}: {e}"[:200])
    if errors and not items:
        raise HTTPException(422, errors[:20])
    try:
        out = store.add_reports(iid, items, a.user_id, source="bulk")
    except ValueError as e:
        raise HTTPException(422, str(e))
    return envelope({"added": len(out), "errors": errors[:50]}, request, meta=_sem(inc), warnings=errors[:5])


class VoidIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=300)


@router.post("/incidents/{iid}/reports/{rid}/void")
def void(iid: str, rid: str, body: VoidIn, request: Request, a: Actor = Depends(require("ops.report"))):
    _inc(iid)
    try:
        store.void_report(iid, rid, body.reason, a.user_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    do_audit(request, a, "ops.report.void", "ops_report", rid, reason=body.reason)
    return envelope({"voided": rid}, request)


# -------------------------------------------------------------------------------------------------------------- zones
def _zone_perm(a: Actor, basis: str, status: str):
    if basis == "real" and status == "active" and not a.can("ops.command"):
        raise HTTPException(403, "enforcing a REAL zone needs ops.command (epidemiology reviewer or admin); save it as simulated or proposed")


@router.post("/incidents/{iid}/zones")
def add_zone(iid: str, body: store.ZoneIn, request: Request, a: Actor = Depends(require("ops.report"))):
    _inc(iid)
    _zone_perm(a, body.basis, body.status)
    try:
        z = store.add_zone(iid, body, a.user_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if body.basis == "real":
        do_audit(request, a, "ops.zone.real", "ops_zone", z["zone_id"], meta={"kind": body.kind, "status": body.status})
    return envelope(z, request)


@router.patch("/incidents/{iid}/zones/{zid}")
def patch_zone(iid: str, zid: str, body: store.ZonePatch, request: Request, a: Actor = Depends(require("ops.report"))):
    _inc(iid)
    cur = store.get_zone(iid, zid)
    if not cur:
        raise HTTPException(404, "zone not found")
    basis, status = body.basis or cur["basis"], body.status or cur["status"]
    _zone_perm(a, basis, status)
    if cur["basis"] == "real" and cur["status"] == "active" and not a.can("ops.command"):
        raise HTTPException(403, "changing an enforced REAL zone needs ops.command")
    try:
        z = store.patch_zone(iid, zid, body, a.user_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if basis == "real":
        do_audit(request, a, "ops.zone.update", "ops_zone", zid, meta=body.model_dump(exclude_none=True, exclude={"geometry"}))
    return envelope(z, request)


@router.delete("/incidents/{iid}/zones/{zid}")
def delete_zone(iid: str, zid: str, request: Request, a: Actor = Depends(require("ops.report"))):
    _inc(iid)
    cur = store.get_zone(iid, zid)
    if not cur:
        raise HTTPException(404, "zone not found")
    if cur["basis"] == "real" and cur["status"] != "proposed":
        raise HTTPException(409, "a real zone is kept for the record: lift it instead of deleting it")
    store.delete_zone(iid, zid, a.user_id)
    return envelope({"deleted": zid}, request)


class SuggestIn(BaseModel):
    index: int = Field(..., ge=0)
    basis: str = Field("simulated", pattern="^(real|simulated)$")
    status: str = Field("active", pattern="^(proposed|active)$")


@router.post("/incidents/{iid}/suggestions/accept")
def accept_suggestion(iid: str, body: SuggestIn, request: Request, a: Actor = Depends(require("ops.report"))):
    _inc(iid)
    an = _analysis(iid)
    if body.index >= len(an["suggestions"]):
        raise HTTPException(409, "the suggestions changed — refresh and try again")
    _zone_perm(a, body.basis, body.status)
    s = an["suggestions"][body.index]
    z = store.add_zone(iid, store.ZoneIn(kind=s["kind"], geometry=s["geometry"], basis=body.basis, status=body.status, note=s["why"]),
                       a.user_id, origin="suggested", extra={"cluster": s["cluster"]})
    return envelope(z, request)


# ---------------------------------------------------------------------------------------------------------- resources
@router.post("/incidents/{iid}/resources")
def add_resource(iid: str, body: store.ResourceIn, request: Request, a: Actor = Depends(require("ops.report"))):
    _inc(iid)
    return envelope(store.add_resource(iid, body, a.user_id), request)


class QtyIn(BaseModel):
    qty: float = Field(..., ge=0, le=1e8)


@router.patch("/incidents/{iid}/resources/{rid}")
def set_qty(iid: str, rid: str, body: QtyIn, request: Request, a: Actor = Depends(require("ops.report"))):
    _inc(iid)
    try:
        return envelope(store.set_resource_qty(iid, rid, body.qty, a.user_id), request)
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.get("/incidents/{iid}/resources")
def list_resources(iid: str, request: Request):
    _inc(iid)
    return envelope({"stock": store.resources(iid), "plans": store.plans(iid)}, request)


# ------------------------------------------------------------------------------------------------- optimise & project
class OptIn(BaseModel):
    basis: str = Field("simulated", pattern="^(real|simulated)$")
    save: bool = True


@router.post("/incidents/{iid}/optimize")
def run_optimize(iid: str, body: OptIn, request: Request, a: Actor = Depends(require("ops.report"))):
    inc = _inc(iid)
    t = time.perf_counter()
    o = optimize.optimize(_analysis(iid), body.basis)
    ms = round((time.perf_counter() - t) * 1000)
    if body.save and o["allocations"]:
        p = store.save_plan(iid, o, "simulated", a.user_id)
        o["plan_id"] = p["plan_id"]
    return envelope(o, request, meta={"semantics": "SCENARIO", "mode": inc["mode"], "ms": ms})


@router.post("/incidents/{iid}/plans/{pid}/approve")
def approve(iid: str, pid: str, request: Request, a: Actor = Depends(require("ops.command"))):
    _inc(iid)
    try:
        p = store.approve_plan(iid, pid, a.user_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    do_audit(request, a, "ops.plan.approve", "ops_plan", pid)
    return envelope(p, request)


class ProjectIn(BaseModel):
    plan_id: str | None = None
    zones: list[dict] = Field(default_factory=list, max_length=20)


@router.post("/incidents/{iid}/project")
def run_project(iid: str, body: ProjectIn, request: Request):
    """What-if projection with extra hypothetical zones ({kind, geometry, contact_reduction}); nothing is saved."""
    _inc(iid)
    an = _analysis(iid)
    extra = []
    for z in body.zones:
        try:
            extra.append({"zone_id": "whatif", "kind": z.get("kind", "quarantine"), "basis": "simulated", "status": "active",
                          "geometry": geo.valid_polygon(z.get("geometry")), "params": {"contact_reduction": float(z.get("contact_reduction", 0.6))}})
        except (ValueError, TypeError) as e:
            raise HTTPException(422, str(e))
    plan = store.get_plan(iid, body.plan_id) if body.plan_id else None
    return envelope(project.scenarios(an, plan, extra), request, meta={"semantics": "SCENARIO"})


# -------------------------------------------------------------------------------------------------------------- drill
class DrillIn(BaseModel):
    action: str = Field("seed", pattern="^(seed|advance)$")
    days: int = Field(1, ge=1, le=7)


@router.post("/incidents/{iid}/drill")
def run_drill(iid: str, body: DrillIn, request: Request, a: Actor = Depends(require("ops.report"))):
    _inc(iid)
    try:
        out = drill.seed(iid, a.user_id) if body.action == "seed" else drill.advance(iid, a.user_id, body.days)
    except PermissionError as e:
        raise HTTPException(409, str(e))
    return envelope(out, request, meta={"semantics": "SYNTHETIC"})


# ------------------------------------------------------------------------------------------------------- live streams
def _sse(ev: str, data) -> str:
    return f"event: {ev}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.get("/incidents/{iid}/events")
def list_events(iid: str, request: Request, after: int = 0, limit: int = 100):
    _inc(iid)
    return envelope(store.events(iid, after, min(limit, 500)), request)


@router.get("/incidents/{iid}/stream")
async def stream(iid: str, request: Request, after: int = 0):
    """Server-sent events: every new feed entry, as it is written (≈1 s latency). Other controllers' key-ins show up here."""
    _inc(iid)

    async def gen():
        last, idle = after, 0
        yield _sse("hello", {"seq": store.last_seq(iid)})
        while True:
            if await request.is_disconnected():
                break
            evs = store.events(iid, last, 200)
            if evs:
                last = evs[-1]["seq"]
                idle = 0
                yield _sse("events", evs)
            else:
                idle += 1
                if idle % 15 == 0:
                    yield ": ping\n\n"
            await asyncio.sleep(1.0)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class AskIn(BaseModel):
    question: str = Field("", max_length=2000)
    lang: str = Field("en", pattern="^(en|vi)$")
    history: list[dict] = Field(default_factory=list, max_length=12)


@router.post("/incidents/{iid}/ask")
def ask(iid: str, body: AskIn, request: Request, a: Actor = Depends(actor)):
    _inc(iid)
    who = a.user_id if a.user_id != "anonymous" else "viewer"
    # anonymous viewers can read the brief, but only signed-in roles that may key in can let the AI store proposals
    can_propose = a.can("ops.report") and a.user_id != "anonymous"

    def gen():
        for ev, data in agent.ask(iid, who, body.question, body.lang, body.history, can_propose=can_propose):
            yield _sse(ev, data)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
