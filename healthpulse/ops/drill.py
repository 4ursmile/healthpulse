"""Drill (exercise) data: seed a SYNTHETIC outbreak and advance it one day at a time.

Only allowed on `mode = drill` incidents. `advance()` draws the next day's reports from the incident's own *current*
projection (real zones + approved plan), so the zones and dispatches a controller sets change what arrives next —
that is the point of a drill. Reports carry `source = 'drill'` and the incident is labelled SYNTHETIC everywhere.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from . import analysis, geo, project, store
from .store import ReportIn, ResourceIn

SEEDS = [  # (lat, lon, label) — dense urban districts, so clusters fall where people live
    (10.7769, 106.7009, "HCMC District 1"),
    (10.8231, 106.6297, "HCMC Tân Bình"),
    (21.0285, 105.8542, "Hà Nội Hoàn Kiếm"),
    (16.0544, 108.2022, "Đà Nẵng"),
]
DEPOTS = [  # (kind, qty, lat, lon, name)
    ("team", 6, 21.0245, 105.8412, "NIHE rapid-response teams (Hà Nội)"),
    ("team", 8, 10.7863, 106.6886, "Pasteur HCMC rapid-response teams"),
    ("team", 3, 12.2470, 109.1960, "Pasteur Nha Trang teams"),
    ("vaccine", 60000, 21.0245, 105.8412, "National EPI cold store (Hà Nội)"),
    ("vaccine", 25000, 10.7863, 106.6886, "Southern EPI cold store (HCMC)"),
    ("test_kit", 40000, 10.7863, 106.6886, "Pasteur HCMC test kits"),
    ("test_kit", 15000, 16.0544, 108.2022, "Đà Nẵng CDC test kits"),
    ("bed", 300, 10.95, 106.82, "Field hospital beds (Đồng Nai depot)"),
    ("ambulance", 12, 10.80, 106.66, "HCMC 115 ambulances"),
]


def _check(iid: str) -> dict:
    inc = store.get_incident(iid)
    if not inc:
        raise KeyError("incident not found")
    if inc["mode"] != "drill":
        raise PermissionError("synthetic data can only be added to a DRILL incident, never to a live one")
    return inc


def _points(rng, lat, lon, radius_km, k):
    ang = rng.uniform(0, 2 * np.pi, k)
    rad = np.abs(rng.normal(0, radius_km, k))
    return lat + rad * np.sin(ang) / geo.KM_LAT, lon + rad * np.cos(ang) / geo.km_per_deg_lon(lat)


def seed(iid: str, actor: str, days: int = 18, seeds: int = 3, rng_seed: int = 7, with_resources: bool = True) -> dict:
    inc = _check(iid)
    rng = np.random.default_rng(rng_seed)
    today = date.today()
    items: list[ReportIn] = []
    growth = [0.16, 0.11, 0.07, 0.05][:seeds]
    for (lat, lon, _), g, start in zip(SEEDS[:seeds], growth, (0, 4, 8, 10)):
        for d in range(start, days):
            lam = 1.5 * np.exp(g * (d - start))
            k = int(rng.poisson(lam))
            if not k:
                continue
            radius = 0.6 + 0.12 * (d - start)
            la, lo = _points(rng, lat, lon, radius, k)
            dt = today - timedelta(days=days - 1 - d)
            for a, b in zip(la, lo):
                items.append(ReportIn(kind="case" if rng.random() > 0.25 else "suspected", count=1, lat=float(a), lon=float(b), event_date=dt))
            if rng.random() < 0.08 * k:
                items.append(ReportIn(kind="death", count=1, lat=float(la[0]), lon=float(lo[0]), event_date=dt))
    # sporadic travel-linked cases
    for _ in range(6):
        pid = rng.choice(["VN48", "VN31", "VN92", "VN56", "VN38"])
        items.append(ReportIn(kind="case", count=1, province_id=str(pid), event_date=today - timedelta(days=int(rng.integers(0, 10)))))
    # early vaccination and testing near the first seed
    items.append(ReportIn(kind="vaccinated", count=2500, lat=SEEDS[0][0] + 0.01, lon=SEEDS[0][1], event_date=today - timedelta(days=3)))
    items.append(ReportIn(kind="tested", count=1800, lat=SEEDS[0][0], lon=SEEDS[0][1] + 0.01, event_date=today - timedelta(days=1)))
    store.add_reports(iid, items, actor, source="drill", quiet=True)
    n_res = 0
    if with_resources and not store.resources(iid):
        for kind, qty, lat, lon, name in DEPOTS:
            store.add_resource(iid, ResourceIn(kind=kind, qty=qty, lat=lat, lon=lon, name=name), actor, quiet=True)
            n_res += 1
    store.event(iid, "drill", f"Drill scenario seeded: {len(items)} synthetic reports over {days} days around {seeds} cities"
                              + (f", {n_res} depots stocked" if n_res else ""), actor)
    return {"reports": len(items), "resources": n_res}


def _shift_back(iid: str) -> None:
    """The drill clock keeps 'today' = the real date: moving it forward one day shifts every report, zone activation
    and plan approval one day into the past, so 'a zone active for 3 days' stays true in drill time."""
    from datetime import datetime

    from ..core import db
    from ..core.util import dumps, iso, loads
    db.ex("UPDATE ops_report SET event_date=date(event_date, '-1 day') WHERE incident_id=?", iid)
    for z in db.q("SELECT zone_id, params_json FROM ops_zone WHERE incident_id=?", iid):
        p = loads(z["params_json"])
        for k in ("activated_at", "lifted_at"):
            if p.get(k):
                p[k] = iso(datetime.fromisoformat(p[k].replace("Z", "+00:00")) - timedelta(days=1))
        db.ex("UPDATE ops_zone SET params_json=? WHERE zone_id=?", dumps(p), z["zone_id"])
    for r in db.q("SELECT plan_id, approved_at FROM ops_plan WHERE incident_id=? AND approved_at IS NOT NULL", iid):
        db.ex("UPDATE ops_plan SET approved_at=? WHERE plan_id=?",
              iso(datetime.fromisoformat(r["approved_at"].replace("Z", "+00:00")) - timedelta(days=1)), r["plan_id"])


def advance(iid: str, actor: str, days: int = 1) -> dict:
    """Generate the next `days` of drill reports from the current projection (so controllers' choices matter)."""
    _check(iid)
    rng = np.random.default_rng()
    total = 0
    for _ in range(max(1, min(days, 7))):
        _shift_back(iid)
        nxt = date.today()
        an = analysis.analyze(iid, with_projection=False)
        us = project.units(an)
        if not us:
            break
        cur = project.run(an, "current", (store.approved_plan(iid) or {}).get("plan", {}).get("allocations", []), None, us)
        items = []
        for u, row in zip(us, cur["units"]):
            lam = max(0.0, row["next14"] / 14) if row["next14"] else u["I0"]
            k = int(rng.poisson(min(lam, 5000)))
            if not k:
                continue
            la, lo = _points(rng, u["lat"], u["lon"], max(0.4, u["radius_km"] * 0.35), min(k, 400))
            per = max(1, k // len(la))
            for a, b in zip(la, lo):
                items.append(ReportIn(kind="case" if rng.random() > 0.2 else "suspected", count=per, lat=float(a), lon=float(b), event_date=nxt))
        if items:
            store.add_reports(iid, items, actor, source="drill", quiet=True)
        n = sum(i.count for i in items)
        total += n
        store.event(iid, "drill", f"Drill clock → {nxt.isoformat()}: {n:,} new synthetic reports", actor, {"date": nxt.isoformat(), "n": n})
    return {"new_cases": total}
