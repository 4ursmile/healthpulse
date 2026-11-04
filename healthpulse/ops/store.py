"""Persistence for live response: incidents, keyed-in reports, zones, resources, allocation plans and the event feed.

Every write appends an `ops_event` row; the live stream (SSE) and other controllers' screens follow that feed, so two
people keying in at once see each other's entries within a second. Reports are never deleted, only voided with a
reason (the audit trail of what was known when matters in an outbreak review).
"""
from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel, Field, field_validator, model_validator

from ..core import db
from ..core.util import dumps, iso, loads, new_id, utcnow
from ..vietnam import sandbox as sb
from . import geo

REPORT_KINDS = ("case", "suspected", "death", "recovered", "vaccinated", "tested")
ZONE_KINDS = ("affected", "quarantine", "buffer", "safe", "vaccination")
RESOURCE_KINDS = ("team", "vaccine", "test_kit", "bed", "ambulance")
RESOURCE_UNITS = {"team": "teams", "vaccine": "doses", "test_kit": "kits", "bed": "beds", "ambulance": "vehicles"}
# default contact reduction a zone imposes on transmission from inside it (planner assumptions, editable per zone)
ZONE_DEFAULTS = {"affected": 0.35, "quarantine": 0.65, "buffer": 0.2, "safe": 0.0, "vaccination": 0.0}


class IncidentSettings(BaseModel):
    cluster_km: float = Field(2.0, gt=0.1, le=50, description="points closer than this join one cluster")
    report_delay_days: float = Field(2.0, ge=0, le=21)
    compliance: float = Field(0.75, ge=0, le=1, description="share of people who follow zone rules")
    free_bed_share: float = Field(0.15, ge=0, le=1, description="share of a province's beds available for this outbreak")
    r_override: float | None = Field(None, gt=0, le=30, description="use this R instead of the one estimated from reports")


class IncidentIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    pathogen: str = "covid"
    mode: str = Field("drill", pattern="^(live|drill)$")
    settings: IncidentSettings = IncidentSettings()

    @field_validator("pathogen")
    @classmethod
    def _p(cls, v):
        if v not in sb.PATHOGENS:
            raise ValueError(f"unknown pathogen {v}; one of {', '.join(sb.PATHOGENS)}")
        return v


class ReportIn(BaseModel):
    kind: str = Field("case", pattern="^(" + "|".join(REPORT_KINDS) + ")$")
    count: int = Field(1, ge=1, le=1_000_000)
    lat: float | None = Field(None, ge=5, le=26)
    lon: float | None = Field(None, ge=100, le=120)
    province_id: str | None = Field(None, pattern=r"^VN\d{2}$")
    event_date: date | None = None
    note: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _loc(self):
        if (self.lat is None) != (self.lon is None):
            raise ValueError("give both lat and lon, or neither")
        if self.lat is None and not self.province_id:
            raise ValueError("a report needs a location: click the map (lat/lon) or pick a province")
        if self.event_date and self.event_date > date.today() + timedelta(days=1):
            raise ValueError("event date is in the future")
        return self


class ZoneIn(BaseModel):
    kind: str = Field(..., pattern="^(" + "|".join(ZONE_KINDS) + ")$")
    name: str | None = Field(None, max_length=120)
    geometry: dict
    basis: str = Field("simulated", pattern="^(real|simulated)$")
    status: str = Field("active", pattern="^(proposed|active|lifted)$")
    contact_reduction: float | None = Field(None, ge=0, le=0.95)
    compliance: float | None = Field(None, ge=0, le=1)
    note: str | None = Field(None, max_length=500)


class ZonePatch(BaseModel):
    kind: str | None = Field(None, pattern="^(" + "|".join(ZONE_KINDS) + ")$")
    name: str | None = Field(None, max_length=120)
    geometry: dict | None = None
    basis: str | None = Field(None, pattern="^(real|simulated)$")
    status: str | None = Field(None, pattern="^(proposed|active|lifted)$")
    contact_reduction: float | None = Field(None, ge=0, le=0.95)
    compliance: float | None = Field(None, ge=0, le=1)


class ResourceIn(BaseModel):
    kind: str = Field(..., pattern="^(" + "|".join(RESOURCE_KINDS) + ")$")
    name: str | None = Field(None, max_length=120)
    qty: float = Field(..., gt=0, le=1e8)
    lat: float | None = Field(None, ge=5, le=26)
    lon: float | None = Field(None, ge=100, le=120)
    province_id: str | None = Field(None, pattern=r"^VN\d{2}$")

    @model_validator(mode="after")
    def _loc(self):
        if self.lat is None and not self.province_id:
            raise ValueError("a resource needs a depot location (map click or province)")
        return self


# ------------------------------------------------------------------------------------------------------------ events
def event(iid: str, kind: str, text: str, actor: str | None = None, data: dict | None = None) -> int:
    cur = db.conn().execute("INSERT INTO ops_event(incident_id, ts, actor, kind, text, data_json) VALUES(?,?,?,?,?,?)",
                            (iid, iso(utcnow()), actor, kind, text, dumps(data) if data else None))
    db.ex("UPDATE ops_incident SET updated_at=? WHERE incident_id=?", iso(utcnow()), iid)
    return cur.lastrowid


def events(iid: str, after: int = 0, limit: int = 200) -> list[dict]:
    rows = db.q("SELECT * FROM ops_event WHERE incident_id=? AND seq>? ORDER BY seq DESC LIMIT ?", iid, after, limit)
    for r in rows:
        raw = r.pop("data_json", None)
        r["data"] = loads(raw) if raw else None
    return rows[::-1]


def last_seq(iid: str) -> int:
    r = db.q1("SELECT MAX(seq) AS s FROM ops_event WHERE incident_id=?", iid)
    return int(r["s"] or 0)


# --------------------------------------------------------------------------------------------------------- incidents
def create_incident(body: IncidentIn, actor: str) -> dict:
    iid = new_id("inc")
    now = iso(utcnow())
    db.insert("ops_incident", {"incident_id": iid, "name": body.name, "pathogen": body.pathogen, "mode": body.mode,
                               "status": "open", "settings_json": body.settings.model_dump(), "created_by": actor,
                               "created_at": now, "updated_at": now})
    event(iid, "incident", f"{'Live incident' if body.mode == 'live' else 'Drill'} opened: {body.name} ({sb.PATHOGENS[body.pathogen]['name']})", actor)
    return get_incident(iid)


def get_incident(iid: str) -> dict | None:
    r = db.q1("SELECT * FROM ops_incident WHERE incident_id=?", iid)
    if not r:
        return None
    r["settings"] = IncidentSettings(**loads(r.pop("settings_json"))).model_dump()
    r["pathogen_spec"] = {k: sb.PATHOGENS[r["pathogen"]].get(k) for k in
                          ("name", "route", "r0", "latent", "infectious", "report", "p_hosp", "los", "cfr_hosp", "untreated", "vax_inf", "vax_sev", "immune")}
    r["semantics"] = "OBSERVED" if r["mode"] == "live" else "SYNTHETIC"
    return r


def list_incidents() -> list[dict]:
    rows = db.q("""SELECT i.incident_id, i.name, i.pathogen, i.mode, i.status, i.created_at, i.updated_at,
                   (SELECT COALESCE(SUM(count),0) FROM ops_report r WHERE r.incident_id=i.incident_id AND r.voided=0 AND r.kind='case') AS cases,
                   (SELECT COUNT(*) FROM ops_zone z WHERE z.incident_id=i.incident_id AND z.status='active') AS zones
                   FROM ops_incident i ORDER BY i.updated_at DESC""")
    return rows


def update_incident(iid: str, patch: dict, actor: str) -> dict:
    cur = get_incident(iid)
    vals = {}
    if "settings" in patch and patch["settings"] is not None:
        vals["settings_json"] = IncidentSettings(**{**cur["settings"], **patch["settings"]}).model_dump()
    for k in ("name", "status"):
        if patch.get(k):
            vals[k] = patch[k]
    if patch.get("pathogen"):
        if patch["pathogen"] not in sb.PATHOGENS:
            raise ValueError("unknown pathogen")
        vals["pathogen"] = patch["pathogen"]
    if vals:
        vals["updated_at"] = iso(utcnow())
        db.update("ops_incident", {"incident_id": iid}, vals)
        event(iid, "settings", "Incident settings changed: " + ", ".join(k.replace("_json", "") for k in vals if k != "updated_at"), actor)
    return get_incident(iid)


# ----------------------------------------------------------------------------------------------------------- reports
def add_reports(iid: str, items: list[ReportIn], actor: str, source: str = "manual", quiet: bool = False) -> list[dict]:
    now = iso(utcnow())
    out = []
    with db.tx() as c:
        for it in items:
            pid = it.province_id
            if it.lat is not None:
                pid = geo.province_at(it.lat, it.lon) or pid
            if not pid:
                raise ValueError(f"({it.lat}, {it.lon}) is outside every province")
            row = {"report_id": new_id("rep"), "incident_id": iid, "kind": it.kind, "count": it.count, "lat": it.lat, "lon": it.lon,
                   "province_id": pid, "event_date": (it.event_date or date.today()).isoformat(), "reported_at": now,
                   "reporter_id": actor, "source": source, "note": it.note}
            db.insert("ops_report", row, c)
            out.append(row)
    if not quiet:
        if len(out) == 1:
            r = out[0]
            name = geo.provinces()["attrs"].get(r["province_id"], {}).get("name", r["province_id"])
            event(iid, "report", f"{r['count']:,} {r['kind']}{'s' if r['count'] > 1 and r['kind'] in ('case', 'death') else ''} keyed in · {name}", actor,
                  {"report_id": r["report_id"], "kind": r["kind"], "count": r["count"], "lat": r["lat"], "lon": r["lon"], "province_id": r["province_id"]})
        elif out:
            tot = {}
            for r in out:
                tot[r["kind"]] = tot.get(r["kind"], 0) + r["count"]
            event(iid, "report", f"{len(out)} reports keyed in ({', '.join(f'{v:,} {k}' for k, v in tot.items())})", actor, {"n": len(out)})
    return out


def reports(iid: str, include_void: bool = False) -> list[dict]:
    return db.q(f"SELECT * FROM ops_report WHERE incident_id=? {'' if include_void else 'AND voided=0'} ORDER BY event_date, reported_at", iid)


def void_report(iid: str, rid: str, reason: str, actor: str) -> None:
    n = db.ex("UPDATE ops_report SET voided=1, void_reason=? WHERE incident_id=? AND report_id=? AND voided=0", reason, iid, rid)
    if not n:
        raise KeyError("report not found or already voided")
    event(iid, "void", f"Report voided: {reason}", actor, {"report_id": rid})


# -------------------------------------------------------------------------------------------------------------- zones
def _zone_row(r: dict) -> dict:
    r["geometry"] = loads(r.pop("geometry_json"))
    r["params"] = loads(r.pop("params_json"))
    return r


def zones(iid: str, include_lifted: bool = True) -> list[dict]:
    rows = db.q(f"SELECT * FROM ops_zone WHERE incident_id=? {'' if include_lifted else 'AND status!=?'} ORDER BY created_at",
                *((iid,) if include_lifted else (iid, "lifted")))
    return [_zone_row(r) for r in rows]


def get_zone(iid: str, zid: str) -> dict | None:
    r = db.q1("SELECT * FROM ops_zone WHERE incident_id=? AND zone_id=?", iid, zid)
    return _zone_row(r) if r else None


def add_zone(iid: str, z: ZoneIn, actor: str, origin: str = "manual", extra: dict | None = None) -> dict:
    g = geo.valid_polygon(z.geometry)
    pop = geo.zone_population(g)
    now = iso(utcnow())
    params = {"contact_reduction": ZONE_DEFAULTS[z.kind] if z.contact_reduction is None else z.contact_reduction,
              "compliance": z.compliance, "note": z.note, "area_km2": pop["area_km2"], "people_est": pop["people_est"],
              "provinces": [p["province_id"] for p in pop["provinces"]],
              "activated_at": now if z.status == "active" else None, **(extra or {})}
    zid = new_id("zone")
    name = z.name or f"{z.kind.title()} zone · {pop['provinces'][0]['name'] if pop['provinces'] else 'sea'}"
    db.insert("ops_zone", {"zone_id": zid, "incident_id": iid, "kind": z.kind, "name": name, "geometry_json": g, "basis": z.basis,
                           "status": z.status, "origin": origin, "params_json": params, "created_by": actor, "created_at": now, "updated_at": now})
    verb = {"proposed": "proposed", "active": "set", "lifted": "drawn (lifted)"}[z.status]
    event(iid, "zone", f"{'Simulated' if z.basis == 'simulated' else 'REAL'} {z.kind} zone {verb}: {name} · {pop['area_km2']:,.1f} km², ~{pop['people_est']:,} people",
          actor, {"zone_id": zid, "kind": z.kind, "basis": z.basis, "status": z.status, "origin": origin})
    return get_zone(iid, zid)


def patch_zone(iid: str, zid: str, p: ZonePatch, actor: str) -> dict:
    z = get_zone(iid, zid)
    if not z:
        raise KeyError("zone not found")
    vals, params = {}, dict(z["params"])
    for k in ("kind", "name", "basis", "status"):
        v = getattr(p, k)
        if v is not None and v != z[k]:
            vals[k] = v
    if p.geometry is not None:
        g = geo.valid_polygon(p.geometry)
        pop = geo.zone_population(g)
        vals["geometry_json"] = g
        params.update(area_km2=pop["area_km2"], people_est=pop["people_est"], provinces=[x["province_id"] for x in pop["provinces"]])
    for k in ("contact_reduction", "compliance"):
        v = getattr(p, k)
        if v is not None:
            params[k] = v
    if vals.get("status") == "active" or (vals.get("basis") and (vals.get("status") or z["status"]) == "active"):
        params["activated_at"] = iso(utcnow())
    if vals.get("status") == "lifted":
        params["lifted_at"] = iso(utcnow())
    vals["params_json"] = params
    vals["updated_at"] = iso(utcnow())
    db.update("ops_zone", {"zone_id": zid}, vals)
    ch = [f"{k} → {v}" for k, v in vals.items() if k in ("kind", "basis", "status", "name")]
    if p.contact_reduction is not None:
        ch.append(f"contact cut {p.contact_reduction:.0%}")
    if p.geometry is not None:
        ch.append("redrawn")
    event(iid, "zone", f"Zone {z['name']}: {', '.join(ch) or 'updated'}", actor, {"zone_id": zid, **{k: v for k, v in vals.items() if k in ("basis", "status", "kind")}})
    return get_zone(iid, zid)


def delete_zone(iid: str, zid: str, actor: str) -> None:
    z = get_zone(iid, zid)
    if not z:
        raise KeyError("zone not found")
    db.ex("DELETE FROM ops_zone WHERE zone_id=?", zid)
    event(iid, "zone", f"Zone removed: {z['name']}", actor, {"zone_id": zid})


# ---------------------------------------------------------------------------------------------------------- resources
def resources(iid: str) -> list[dict]:
    return db.q("SELECT * FROM ops_resource WHERE incident_id=? ORDER BY kind, created_at", iid)


def add_resource(iid: str, r: ResourceIn, actor: str, quiet: bool = False) -> dict:
    lat, lon, pid = r.lat, r.lon, r.province_id
    if lat is None:
        lat, lon = geo.centroid(pid)
    else:
        pid = geo.province_at(lat, lon) or pid
    rid = new_id("res")
    pname = geo.provinces()["attrs"].get(pid, {}).get("name", pid)
    name = r.name or f"{RESOURCE_UNITS[r.kind].title()} · {pname}"
    db.insert("ops_resource", {"resource_id": rid, "incident_id": iid, "kind": r.kind, "name": name, "qty": r.qty, "lat": lat, "lon": lon,
                               "province_id": pid, "created_by": actor, "created_at": iso(utcnow())})
    if not quiet:
        event(iid, "resource", f"Stock added: {r.qty:,.0f} {RESOURCE_UNITS[r.kind]} at {name}", actor, {"resource_id": rid})
    return db.q1("SELECT * FROM ops_resource WHERE resource_id=?", rid)


def set_resource_qty(iid: str, rid: str, qty: float, actor: str) -> dict:
    r = db.q1("SELECT * FROM ops_resource WHERE incident_id=? AND resource_id=?", iid, rid)
    if not r:
        raise KeyError("resource not found")
    if qty <= 0:
        db.ex("DELETE FROM ops_resource WHERE resource_id=?", rid)
        event(iid, "resource", f"Stock removed: {r['name']}", actor, {"resource_id": rid})
        return {}
    db.ex("UPDATE ops_resource SET qty=? WHERE resource_id=?", qty, rid)
    event(iid, "resource", f"Stock updated: {r['name']} {r['qty']:,.0f} → {qty:,.0f} {RESOURCE_UNITS[r['kind']]}", actor, {"resource_id": rid})
    return db.q1("SELECT * FROM ops_resource WHERE resource_id=?", rid)


# -------------------------------------------------------------------------------------------------------------- plans
def save_plan(iid: str, plan: dict, basis: str, actor: str, origin: str = "optimizer") -> dict:
    pid = new_id("plan")
    db.insert("ops_plan", {"plan_id": pid, "incident_id": iid, "basis": basis, "status": "proposed", "plan_json": plan,
                           "created_by": actor, "created_at": iso(utcnow())})
    s = plan.get("summary", {})
    event(iid, "plan", f"Allocation plan proposed ({origin}): {len(plan.get('allocations', []))} moves, "
                       f"~{s.get('cases_averted', 0):,.0f} cases averted over {s.get('horizon_days', 28)} days (scenario)", actor, {"plan_id": pid})
    return get_plan(iid, pid)


def get_plan(iid: str, pid: str) -> dict | None:
    r = db.q1("SELECT * FROM ops_plan WHERE incident_id=? AND plan_id=?", iid, pid)
    if r:
        r["plan"] = loads(r.pop("plan_json"))
    return r


def plans(iid: str) -> list[dict]:
    rows = db.q("SELECT * FROM ops_plan WHERE incident_id=? ORDER BY created_at DESC LIMIT 20", iid)
    for r in rows:
        r["plan"] = loads(r.pop("plan_json"))
    return rows


def approved_plan(iid: str) -> dict | None:
    r = db.q1("SELECT * FROM ops_plan WHERE incident_id=? AND status='approved' ORDER BY approved_at DESC LIMIT 1", iid)
    if r:
        r["plan"] = loads(r.pop("plan_json"))
    return r


def approve_plan(iid: str, pid: str, actor: str) -> dict:
    p = get_plan(iid, pid)
    if not p:
        raise KeyError("plan not found")
    now = iso(utcnow())
    with db.tx() as c:
        c.execute("UPDATE ops_plan SET status='superseded' WHERE incident_id=? AND status='approved'", (iid,))
        c.execute("UPDATE ops_plan SET status='approved', basis='real', approved_by=?, approved_at=? WHERE plan_id=?", (actor, now, pid))
    event(iid, "plan", f"Allocation plan APPROVED for dispatch ({len(p['plan'].get('allocations', []))} moves)", actor, {"plan_id": pid})
    return get_plan(iid, pid)
