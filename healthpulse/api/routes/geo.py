"""Geography API: polygon-crosswalk quality reports and the Meta mobility-range profile (distance from home, not flows)."""
from __future__ import annotations

import numpy as np
import polars as pl
from fastapi import APIRouter, HTTPException, Query, Request

from ...geo import pipeline as gp
from ..deps import envelope

router = APIRouter(prefix="/geo", tags=["geo"])
BANDS = gp.BANDS
BAND_LABELS = {"share_0km": "at the home location (0 km)", "share_0_10km": "0-10 km from home", "share_10_100km": "10-100 km from home",
               "share_100plus_km": "100+ km from home"}
CAVEATS = [
    "Distance-from-home indicator: the share of device pings by distance from the device's home location. It has no destination, "
    "so it is not an origin-destination flow and cannot be used as one.",
    "Recent profile only: the source keeps ~90 days, so the series starts at our first ingest. It is not a point-in-time training "
    "feature and is not used by any forecast model.",
    "Mean of daily shares over the days held; Meta devices are a non-random sample of people.",
    "Units come from a polygon crosswalk (GADM 4.1 -> IBGE / OCHA), area- or population-weighted; units the crosswalk does not "
    "cover well are null, never zero.",
]


@router.get("/crosswalks")
def crosswalks(request: Request):
    return envelope(gp.list_crosswalks(), request, meta={"method": "polygon intersection on an equal-area plane; names are never used"})


@router.get("/crosswalks/{pair}")
def crosswalk_detail(request: Request, pair: str, status: str | None = Query(None, pattern="^(ACCEPTED|AMBIGUOUS|REFUSED)$"),
                     limit: int = Query(0, ge=0, le=500), offset: int = Query(0, ge=0)):
    """Quality report (method, thresholds, input hashes, coverage, audit) and, with limit>0, candidate rows (optionally by status)."""
    if pair not in gp.PAIRS:
        raise HTTPException(404, f"unknown crosswalk pair {pair}")
    try:
        df, rep = gp.load_crosswalk(pair)
    except gp.GeoError as e:
        raise HTTPException(409, str(e))
    rows = None
    if limit:
        d = df.filter(pl.col("status") == status) if status else df
        total = d.height
        rows = d.sort("source_id", "target_id").slice(offset, limit).to_dicts()
        return envelope({"report": rep, "rows": rows}, request, meta={"total": total, "limit": limit, "offset": offset})
    return envelope({"report": rep, "rows": rows}, request)


def _meta_or_409():
    try:
        return gp.mobility_meta()
    except gp.GeoError as e:
        raise HTTPException(409, str(e))


@router.get("/mobility")
def mobility_overview(request: Request):
    m = _meta_or_409()
    return envelope(m, request, meta={"bands": BAND_LABELS, "semantics": "OBSERVED (Meta devices, recent window)"}, warnings=CAVEATS[:2])


def _table(scope: str) -> pl.DataFrame:
    if scope not in ("br", "vn"):
        raise HTTPException(404, f"unknown scope {scope}; use br or vn")
    try:
        return gp.mobility_table(scope)
    except gp.GeoError as e:
        raise HTTPException(409, str(e))


@router.get("/mobility/{scope}")
def mobility_list(request: Request, scope: str, prefix: str | None = Query(None, max_length=7, pattern="^[0-9A-Za-z]*$"),
                  limit: int = Query(500, ge=1, le=6000), offset: int = Query(0, ge=0)):
    """All units of a scope (VN: the 34 provinces; BR: IBGE municipalities, optionally by code prefix, e.g. 33 for Rio de Janeiro state)."""
    t = _table(scope)
    if prefix:
        t = t.filter(pl.col("unit_id").str.starts_with(prefix))
    total = t.height
    rows = t.sort("unit_id").slice(offset, limit).to_dicts()
    return envelope(rows, request, meta={"total": total, "limit": limit, "offset": offset, "bands": BAND_LABELS}, warnings=CAVEATS[:2])


@router.get("/mobility/{scope}/{unit_id}")
def mobility_unit(request: Request, scope: str, unit_id: str):
    """One unit's profile with its rank among all units that have values (percentile of the 100+ km share)."""
    t = _table(scope)
    r = t.filter(pl.col("unit_id") == unit_id)
    if not r.height:
        raise HTTPException(404, f"{unit_id} is not a {scope.upper()} unit in the mobility profile")
    row = r.to_dicts()[0]
    have = t.filter(pl.col("share_100plus_km").is_not_null())
    ctx = None
    if row["share_100plus_km"] is not None and have.height > 1:
        v = have["share_100plus_km"].to_numpy()
        ctx = {"n_units": have.height, "percentile_100plus": float((v < row["share_100plus_km"]).mean()),
               "median": {b: float(have[b].median()) for b in BANDS}}
    meta = _meta_or_409()
    u = meta["units"].get(scope, {})
    warn = list(CAVEATS)
    if row["share_100plus_km"] is None:
        warn.insert(0, f"No profile for {unit_id}: the polygon crosswalk covers too little of it or Meta holds too few days. Shown as missing, not zero.")
    return envelope({"unit": row, "context": ctx, "window": {"from": u.get("ds_min"), "to": u.get("ds_max"), "days": u.get("days")}},
                    request, meta={"bands": BAND_LABELS, "semantics": "OBSERVED (Meta devices, recent window)",
                                   "weight_basis": row.get("weight_basis"), "crosswalk_version": row.get("crosswalk_version")}, warnings=warn)
