"""Product-facing read APIs: overview, map, region detail, diseases, facilities, workforce, model metrics, alerts, jobs."""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import numpy as np
import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from ...config import get_settings
from ...core import db, jobs
from ...core.security import Actor, DEMO_USERS, demo_token
from ...core.util import dumps, iso, last_complete_week, parse_dt, utcnow
from ...features.compute import case_table
from ...ml import registry as mreg
from ...sources import catalog, silver
from ...sources.ingest import source_status
from ..deps import actor, do_audit, envelope, forecast_frame, latest_public_forecast, require

router = APIRouter()


def _disease(disease_id: str) -> dict:
    d = catalog.DISEASES.get(disease_id)
    if not d:
        raise HTTPException(404, f"unknown disease {disease_id}")
    return d


def _cases(disease_id: str) -> pl.DataFrame:
    t = case_table(disease_id)
    df = silver.latest(t)
    return df.filter(pl.col("disease_id") == disease_id) if df.height else df


def _status_for(df: pl.DataFrame) -> str:
    if not df.height:
        return "no_data"
    return "synthetic" if bool(df["_synthetic_snapshot"].any()) else "observed"


def _pop() -> pl.DataFrame:
    p = silver.latest("population_annual")
    if not p.height:
        return pl.DataFrame(schema={"municipality_id": pl.Utf8, "population": pl.Int64})
    return p.sort("reference_year").unique("municipality_id", keep="last").select("municipality_id", "population")


def _names() -> dict[str, dict]:
    dim = silver.latest("municipality_dim")
    return {r["municipality_id"]: r for r in dim.select("municipality_id", "name", "state_abbr", "centroid_lat", "centroid_lon",
                                                        "is_capital").iter_rows(named=True)}


def _in_state(state: str | None) -> list[str] | None:
    """Municipality ids of one UF (scope = Brazil / <state>), or None for the whole country."""
    if not state:
        return None
    dim = silver.latest("municipality_dim")
    return dim.filter(pl.col("state_abbr") == state.upper())["municipality_id"].to_list() if dim.height else []


def _freshness() -> list[dict]:
    out = []
    for s in db.q("SELECT * FROM source_registry ORDER BY source_group, source_id"):
        st = source_status(s)
        out.append({"source_id": s["source_id"], "display_name": s["display_name"], "group": s["source_group"],
                    "state": st["state"], "last_success_at": st["last_success_at"], "release_class": s["release_class"],
                    "coverage_end": st["coverage_end"]})
    return out


# ---------------------------------------------------------------- health
@router.get("/health")
def health():
    return {"status": "ok", "time": iso(utcnow())}


@router.get("/ready")
def ready(request: Request):
    checks = {}
    try:
        db.q1("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["database"] = f"error: {e}"
    hb = db.q1("SELECT MAX(heartbeat_at) t FROM jobs WHERE status='running'")
    checks["geometry"] = "ok" if (get_settings().gold_dir / "map" / "municipalities.geojson").exists() else "missing"
    checks["queue_depth"] = db.q1("SELECT COUNT(*) n FROM jobs WHERE status='queued'")["n"]
    checks["last_worker_heartbeat"] = hb["t"] if hb else None
    return envelope(checks, request)


# ---------------------------------------------------------------- session / demo accounts
@router.get("/auth/demo-accounts")
def demo_accounts(request: Request):
    if get_settings().env not in ("local", "demo", "test"):
        raise HTTPException(404)
    return envelope([{"user_id": u, "display_name": n, "role": r, "token": demo_token(u)} for u, n, r in DEMO_USERS], request,
                    meta={"note": "Seeded demo roles for a local pilot. Never enabled in production."})


@router.get("/me")
def me(request: Request, a: Actor = Depends(actor)):
    from ...core.security import PERMISSIONS
    return envelope({"user_id": a.user_id, "role": a.role, "display_name": a.display_name,
                     "permissions": sorted(k for k, v in PERMISSIONS.items() if a.role in v),
                     "workspace": get_settings().workspace, "environment": get_settings().env}, request)


# ---------------------------------------------------------------- metadata
@router.get("/metadata/sources")
def meta_sources(request: Request):
    return envelope(_freshness(), request)


@router.get("/metadata/regions")
def meta_regions(request: Request, q: str | None = None, state: str | None = None, scope: str = "pilot", limit: int = 50):
    dim = silver.latest("municipality_dim")
    if not dim.height:
        return envelope([], request, warnings=["IBGE municipality register not ingested yet"])
    pilot = set(get_settings().pilot)
    have = set(_cases("dengue")["municipality_id"].unique().to_list()) if _cases("dengue").height else set()
    if state:
        # inside one state the scope is "anything with a series", so SARI-only states still list their municipalities
        for k in catalog.DISEASES:
            if (c := _cases(k)).height:
                have |= set(c["municipality_id"].unique().to_list())
    df = dim
    if scope == "pilot":
        df = df.filter(pl.col("municipality_id").is_in(list(pilot | have)))
    if state:
        df = df.filter(pl.col("state_abbr") == state.upper())
    if q:
        df = df.filter(pl.col("name").str.to_lowercase().str.contains(q.lower(), literal=True) | pl.col("municipality_id").str.starts_with(q))
    pop = _pop()
    df = df.join(pop, on="municipality_id", how="left").sort(["is_capital", "population"], descending=True, nulls_last=True).head(limit)
    rows = [{**r, "has_disease_data": r["municipality_id"] in have, "in_pilot": r["municipality_id"] in pilot} for r in df.iter_rows(named=True)]
    return envelope(rows, request, meta={"geometry_version": "ibge_geometry latest", "total_register": dim.height})


@router.get("/diseases")
def diseases(request: Request):
    out = []
    for k, d in catalog.DISEASES.items():
        df = _cases(k)
        out.append({"disease_id": k, **d, "data_status": _status_for(df), "rows": df.height,
                    "latest_week": str(df["week_start"].max()) if df.height else None,
                    "public_forecast": (latest_public_forecast(k) or {}).get("forecast_id")})
    return envelope(out, request)


# ---------------------------------------------------------------- overview
@router.get("/overview")
def overview(request: Request, disease_id: str = "dengue", horizon: int = Query(2, ge=1, le=4), state: str | None = None):
    d = _disease(disease_id)
    df = _cases(disease_id)
    only = _in_state(state)
    if only is not None and df.height:
        df = df.filter(pl.col("municipality_id").is_in(only))
    names = _names()
    warnings = []
    if not df.height:
        return envelope({"disease": {"disease_id": disease_id, **d}, "kpis": None, "regions": [], "freshness": _freshness()}, request,
                        meta={"type": "OBSERVED", "data_status": "no_data"},
                        warnings=[f"No {d['label']} data has been ingested. Import or ingest a source in Admin → Data sources."])
    last = df["week_start"].max()
    prev = last - timedelta(weeks=1)
    pop = _pop()
    agg = df.filter(pl.col("week_start").is_in([last, prev])).group_by("week_start").agg(pl.col("observed_cases").sum())
    last_total = int(agg.filter(pl.col("week_start") == last)["observed_cases"].sum())
    prev_total = int(agg.filter(pl.col("week_start") == prev)["observed_cases"].sum())
    f = latest_public_forecast(disease_id)
    fc_rows, fc_total = [], None
    if f:
        F = forecast_frame(f["forecast_id"]).filter(pl.col("horizon") == horizon)
        if only is not None:
            F = F.filter(pl.col("municipality_id").is_in(only))
        ok = F.filter(pl.col("quality_status") == "ok")
        fc_total = {"p50": float(ok["p50"].sum()), "p10": float(ok["p10"].sum()), "p90": float(ok["p90"].sum()),
                    "target_week": str(F["target_week"].min()) if F.height else None, "regions_ok": ok.height,
                    "regions_insufficient": F.height - ok.height,
                    "note": "Sum of regional medians and of regional P10/P90; the band of a sum is narrower than this."}
        fc_rows = F.to_dicts()
    else:
        warnings.append(f"No published {d['label']} forecast. A reviewed model must be promoted to champion in Admin → Model Studio.")
    lastw = df.filter(pl.col("week_start") == last).select("municipality_id", "observed_cases")
    prevw = df.filter(pl.col("week_start") == prev).select("municipality_id", pl.col("observed_cases").alias("prev_cases"))
    reg = lastw.join(prevw, on="municipality_id", how="left").join(pop, on="municipality_id", how="left")
    if fc_rows:
        reg = reg.join(pl.DataFrame(fc_rows).select("municipality_id", "p10", "p50", "p90", "quality_status", "target_week"),
                       on="municipality_id", how="left")
    alerts = {r["municipality_id"]: r["n"] for r in db.q("SELECT municipality_id, COUNT(*) n FROM alert WHERE disease_id=? AND state IN ('draft','held') GROUP BY municipality_id", disease_id)
              if only is None or r["municipality_id"] in only}
    rows = []
    for r in reg.iter_rows(named=True):
        nm = names.get(r["municipality_id"], {})
        inc = (r["observed_cases"] / r["population"] * 1e5) if r.get("population") and r["observed_cases"] is not None else None
        finc = (r["p50"] / r["population"] * 1e5) if r.get("population") and r.get("p50") is not None else None
        rows.append({**r, "name": nm.get("name"), "state_abbr": nm.get("state_abbr"), "incidence_per_100k": inc,
                     "forecast_incidence_per_100k": finc, "change_vs_prev_week": (r["observed_cases"] - r["prev_cases"])
                     if r.get("prev_cases") is not None and r["observed_cases"] is not None else None,
                     "open_alerts": alerts.get(r["municipality_id"], 0)})
    rows.sort(key=lambda x: -(x.get("forecast_incidence_per_100k") or x.get("incidence_per_100k") or 0))
    kpis = {"latest_week": str(last), "latest_week_end": str(last + timedelta(days=6)),
            "reported_last_week": last_total, "reported_prev_week": prev_total, "change": last_total - prev_total,
            "forecast": fc_total, "horizon": horizon, "regions_with_data": reg.height,
            "open_alerts": sum(alerts.values()),
            "weeks_behind": (last_complete_week() - last).days // 7}
    if kpis["weeks_behind"] > 1:
        warnings.append(f"Latest released surveillance week is {kpis['weeks_behind']} weeks behind the last complete calendar week (reporting delay).")
    meta = {"type": "OBSERVED+FORECAST", "data_status": _status_for(df), "units": d["units"], "signal_type": d["signal_type"],
            "as_of": iso(utcnow()), "forecast_id": f["forecast_id"] if f else None,
            "model_version": (f or {}).get("provenance_json", {}).get("model_version") if f else None,
            "forecast_quality": f["quality_status"] if f else None,
            "source_vintage_ids": sorted(set(df["vintage_id"].unique().to_list()))}
    return envelope({"disease": {"disease_id": disease_id, **d}, "kpis": kpis, "regions": rows, "freshness": _freshness()}, request,
                    meta=meta, warnings=warnings)


# ---------------------------------------------------------------- map
LAYERS = {
    "observed_cases": {"label": "Reported cases", "semantics": "OBSERVED", "unit": "cases / week"},
    "incidence": {"label": "Reported cases per 100k", "semantics": "OBSERVED", "unit": "per 100k / week"},
    "forecast_p50": {"label": "Forecast median", "semantics": "FORECAST", "unit": "cases / week"},
    "forecast_incidence": {"label": "Forecast per 100k", "semantics": "FORECAST", "unit": "per 100k / week"},
    "facilities": {"label": "Registered CNES facilities", "semantics": "OBSERVED", "unit": "establishments"},
}


@router.get("/map/geometry")
def map_geometry(request: Request):
    p = get_settings().gold_dir / "map" / "municipalities.geojson"
    if not p.exists():
        raise HTTPException(404, "municipal geometry not ingested (Admin → Data sources → IBGE municipal boundaries)")
    gz = p.with_suffix(".geojson.gz")
    if not gz.exists() or gz.stat().st_mtime < p.stat().st_mtime:
        import gzip
        with gzip.open(gz.with_suffix(".tmp"), "wb", compresslevel=9) as f:
            f.write(p.read_bytes())
        gz.with_suffix(".tmp").replace(gz)
    etag = f'"{int(p.stat().st_mtime)}-{p.stat().st_size}"'
    if request.headers.get("if-none-match") == etag:
        from fastapi.responses import Response
        return Response(status_code=304, headers={"ETag": etag})
    headers = {"Cache-Control": "public, max-age=3600", "ETag": etag, "Vary": "Accept-Encoding"}
    if "gzip" in request.headers.get("accept-encoding", ""):
        return FileResponse(gz, media_type="application/geo+json", headers={**headers, "Content-Encoding": "gzip"})
    return FileResponse(p, media_type="application/geo+json", headers=headers)


@router.get("/map/regions")
def map_regions(request: Request, layer: str = "incidence", disease_id: str = "dengue", week: str | None = None,
                forecast_id: str | None = None, horizon: int = Query(1, ge=1, le=4), state: str | None = None):
    if layer not in LAYERS:
        raise HTTPException(400, f"layer must be one of {list(LAYERS)}")
    d = _disease(disease_id)
    meta = {**LAYERS[layer], "layer": layer, "disease_id": disease_id, "signal_type": d["signal_type"]}
    warnings = []
    pop = _pop()
    if layer in ("observed_cases", "incidence"):
        df = _cases(disease_id)
        if not df.height:
            return envelope({"values": {}, "weeks": []}, request, meta={**meta, "data_status": "no_data"})
        weeks = sorted(set(df["week_start"].to_list()))
        wk = date.fromisoformat(week) if week else weeks[-1]
        sel = df.filter(pl.col("week_start") == wk).select("municipality_id", "observed_cases")
        if layer == "incidence":
            sel = sel.join(pop, on="municipality_id", how="left").with_columns(
                (pl.col("observed_cases") / pl.col("population") * 1e5).alias("value"))
            if sel["population"].null_count():
                warnings.append("regions without a population denominator show counts as no-data on this layer")
        else:
            sel = sel.with_columns(pl.col("observed_cases").cast(pl.Float64).alias("value"))
        vals = {r["municipality_id"]: r["value"] for r in sel.iter_rows(named=True)}
        meta.update({"week": str(wk), "data_status": _status_for(df), "weeks_available": [str(w) for w in weeks[-156:]]})
    elif layer in ("forecast_p50", "forecast_incidence"):
        f = db.jcol(db.q1("SELECT * FROM forecast WHERE forecast_id=?", forecast_id), "provenance_json") if forecast_id else latest_public_forecast(disease_id)
        if not f:
            return envelope({"values": {}}, request, meta={**meta, "data_status": "no_forecast"}, warnings=["no published forecast"])
        F = forecast_frame(f["forecast_id"]).filter(pl.col("horizon") == horizon)
        if layer == "forecast_incidence":
            F = F.join(pop, on="municipality_id", how="left").with_columns((pl.col("p50") / pl.col("population") * 1e5).alias("value"),
                                                                           (pl.col("p10") / pl.col("population") * 1e5).alias("lo"),
                                                                           (pl.col("p90") / pl.col("population") * 1e5).alias("hi"))
        else:
            F = F.with_columns(pl.col("p50").alias("value"), pl.col("p10").alias("lo"), pl.col("p90").alias("hi"))
        vals = {r["municipality_id"]: r["value"] for r in F.iter_rows(named=True)}
        meta.update({"forecast_id": f["forecast_id"], "target_week": str(F["target_week"].min()) if F.height else None,
                     "horizon": horizon, "data_status": "forecast", "quality": f["quality_status"],
                     "intervals": {r["municipality_id"]: [r["lo"], r["hi"]] for r in F.iter_rows(named=True)}})
    else:
        fac = silver.latest("facility")
        if not fac.height:
            return envelope({"values": {}}, request, meta={**meta, "data_status": "no_data"},
                            warnings=["CNES facilities not ingested for this scope"])
        g = fac.group_by("municipality_id").agg(pl.len().alias("value"))
        vals = {r["municipality_id"]: float(r["value"]) for r in g.iter_rows(named=True)}
        meta.update({"data_status": "observed"})
    only = _in_state(state)
    if only is not None:
        keep = set(only)
        vals = {k: v for k, v in vals.items() if k in keep}
        if "intervals" in meta:
            meta["intervals"] = {k: v for k, v in meta["intervals"].items() if k in keep}
        meta["state"] = state.upper()
    finite = [v for v in vals.values() if v is not None and np.isfinite(v)]
    meta["breaks"] = [float(x) for x in np.quantile(finite, [0.2, 0.4, 0.6, 0.8])] if len(finite) >= 5 else (sorted(finite) if finite else [])
    meta["max"] = max(finite) if finite else None
    return envelope({"values": vals}, request, meta=meta, warnings=warnings)


# ---------------------------------------------------------------- region detail
@router.get("/regions/{municipality_id}")
def region(request: Request, municipality_id: str):
    names = _names()
    r = names.get(municipality_id)
    if not r:
        raise HTTPException(404, "unknown municipality")
    pop = _pop().filter(pl.col("municipality_id") == municipality_id)
    fac = silver.latest("facility")
    fac = fac.filter(pl.col("municipality_id") == municipality_id) if fac.height else fac
    diseases = {}
    for k in catalog.DISEASES:
        df = _cases(k)
        df = df.filter(pl.col("municipality_id") == municipality_id) if df.height else df
        diseases[k] = {"rows": df.height, "latest_week": str(df["week_start"].max()) if df.height else None}
    return envelope({**r, "population": int(pop["population"][0]) if pop.height else None, "facilities": fac.height,
                     "diseases": diseases}, request)


def _one(table: str, municipality_id: str, sort: str | None = None) -> dict | None:
    df = silver.latest(table)
    if not df.height:
        return None
    df = df.filter(pl.col("municipality_id") == municipality_id)
    if sort and df.height:
        df = df.sort(sort)
    return df.drop([c for c in ("vintage_id", "_synthetic_snapshot") if c in df.columns]).tail(1).to_dicts()[0] if df.height else None


@router.get("/regions/{municipality_id}/context")
def region_context(request: Request, municipality_id: str):
    """Place context from the slow-moving sources: official population, WorldPop grid, commuting arranjo, sanitation /
    land cover profile, latest remote-sensing week, registered beds. Every block names its source; absent = null."""
    if municipality_id not in _names():
        raise HTTPException(404, "unknown municipality")
    pop = silver.latest("population_annual")
    series = []
    if pop.height:
        series = (pop.filter(pl.col("municipality_id") == municipality_id).sort("reference_year")
                  .select("reference_year", "population", "source_id").to_dicts())
    grid = _one("population_grid_annual", municipality_id, "reference_year")
    arr = _one("commuting_arranjo", municipality_id)
    members, n_members = [], 0
    if arr:
        ca = silver.latest("commuting_arranjo")
        n_members = ca.filter(pl.col("arranjo_id") == arr["arranjo_id"]).height
        names = _names()
        members = [{**m, "name": names.get(m["municipality_id"], {}).get("name")} for m in
                   ca.filter((pl.col("arranjo_id") == arr["arranjo_id"]) & (pl.col("municipality_id") != municipality_id))
                   .sort("commuters_in_arranjo", descending=True, nulls_last=True)
                   .select("municipality_id", "is_core", "commuters_in_arranjo", "integration_index").head(12).to_dicts()]
    prof = _one("municipality_profile", municipality_id)
    env = _one("environment_weekly", municipality_id, "week_start")
    beds = silver.latest("facility_beds")
    bed_tot = None
    if beds.height:
        b = beds.filter(pl.col("municipality_id") == municipality_id)
        if b.height:
            bed_tot = {"hospitals": b.height, "competence": b["competence"].max(),
                       **{c: float(b[c].sum()) for c in ("registered_beds", "registered_beds_sus", "registered_icu_beds", "registered_icu_beds_sus")}}
    return envelope({"population_series": series, "worldpop": grid, "arranjo": arr, "arranjo_members": members, "arranjo_size": n_members,
                     "profile": prof, "environment_latest": env, "beds": bed_tot}, request,
                    meta={"semantics": "OBSERVED (registered / census / remote sensing); WorldPop is MODELLED",
                          "sources": {"population_series": "IBGE SIDRA 6579 (InfoDengue copy only for years IBGE lacks)",
                                      "worldpop": "WorldPop R2025A 1 km constrained, summed over the IBGE polygon",
                                      "arranjo": "IBGE Arranjos Populacionais 2nd ed., Census 2010 commuting",
                                      "profile": "Zenodo 22029053 (CC BY 4.0)", "environment_latest": "Zenodo 22029053 (MODIS, ENSO)",
                                      "beds": "OpenDataSUS Hospitais e Leitos, joined by CNES code"}},
                    warnings=[] if beds.height else ["Registered beds not ingested yet (Admin → Data sources → CNES)."])


@router.get("/regions/{municipality_id}/timeseries")
def region_timeseries(request: Request, municipality_id: str, disease_id: str = "dengue", weeks: int = Query(156, ge=8, le=1000)):
    d = _disease(disease_id)
    df = _cases(disease_id)
    df = df.filter(pl.col("municipality_id") == municipality_id).sort("week_start") if df.height else df
    if not df.height:
        return envelope({"observed": [], "forecast": [], "climate": [], "holidays": []}, request,
                        meta={"data_status": "no_data"}, warnings=[f"No {d['label']} series for this municipality"])
    df = df.tail(weeks)
    first = df["week_start"].min()
    # SARI (SIVEP) rows carry counts only; nowcast columns exist only for InfoDengue diseases -> typed nulls, never zeros
    want = ["week_start", "observed_cases", "estimated_cases", "estimated_min", "estimated_max", "vintage_id"]
    obs = df.select([pl.col(c) if c in df.columns else pl.lit(None, dtype=pl.Float64).alias(c) for c in want]).to_dicts()
    f = latest_public_forecast(disease_id)
    fc = []
    if f:
        F = forecast_frame(f["forecast_id"]).filter(pl.col("municipality_id") == municipality_id).sort("horizon")
        fc = F.select("target_week", "horizon", "p10", "p50", "p90", "quality_status").to_dicts()
    w = silver.latest("weather_weekly")
    clim = []
    if w.height:
        clim = (w.filter((pl.col("municipality_id") == municipality_id) & (pl.col("week_start") >= first)).sort("week_start")
                .select("week_start", "rain_mm_sum", "t2m_mean", "t2m_max", "rh_mean", "coverage").to_dicts())
    hol = silver.latest("holiday_daily")
    hols = []
    if hol.height:
        hols = (hol.filter(pl.col("is_national") & (pl.col("date") >= first)).sort("date").unique("date", keep="first")
                .select("date", "holiday_name").to_dicts())
    model = None
    if f:
        model = {"forecast_id": f["forecast_id"], "model_version": f["provenance_json"].get("model_version"),
                 "quality": f["quality_status"], "issue_at": f["issue_at"], "anchor_week": f["anchor_week"],
                 "nominal_coverage": f["provenance_json"].get("nominal_coverage")}
    return envelope({"observed": obs, "forecast": fc, "climate": clim, "holidays": hols, "model": model}, request,
                    meta={"type": "OBSERVED+FORECAST", "units": d["units"], "signal_type": d["signal_type"], "metric_label": d["metric_label"],
                          "data_status": _status_for(df), "source_vintage_ids": sorted(set(df["vintage_id"].to_list())),
                          "climate_source": "Open-Meteo ERA5 reanalysis (observed)", "as_of": iso(utcnow())})


@router.get("/regions/{municipality_id}/workforce")
def region_workforce(request: Request, municipality_id: str):
    wf = silver.latest("workforce_sector")
    src = catalog.get_source("ibge_cempre")
    if not wf.height:
        return envelope({"sectors": [], "source_state": src["status"]}, request, meta={"data_status": "no_data"},
                        warnings=["IBGE CEMPRE workforce data has not been ingested yet (Admin → Data sources → IBGE CEMPRE). "
                                  "No sector shares are estimated or substituted."])
    g = wf.filter(pl.col("municipality_id") == municipality_id)
    if not g.height:
        return envelope({"sectors": []}, request, meta={"data_status": "no_data"}, warnings=["no sector rows for this municipality"])
    y = g["year"].max()
    g = g.filter(pl.col("year") == y)
    tot = g["formal_workers"].sum()
    rows = [{**r, "share": (r["formal_workers"] / tot) if tot and r["formal_workers"] is not None else None}
            for r in g.drop("vintage_id", "_synthetic_snapshot", strict=False).sort("formal_workers", descending=True, nulls_last=True).iter_rows(named=True)]
    return envelope({"sectors": rows, "year": y, "total_formal_workers": tot}, request,
                    meta={"semantics": "OBSERVED (formal workforce)", "caveat": "Sector exposure is contextual; not a sector-specific infection rate."})


@router.get("/facilities")
def facilities(request: Request, region_id: str | None = None, facility_type: str | None = None, limit: int = Query(500, le=5000),
               state: str | None = None):
    fac = silver.latest("facility")
    beds_snap = db.q1("SELECT dq_status, dq_report_json, retrieved_at FROM source_snapshot WHERE silver_table='facility_beds' ORDER BY retrieved_at DESC LIMIT 1")
    warnings = ["Registered capacity is not operational availability; staffed beds and teams need confirmed planner inputs."]
    if beds_snap and beds_snap["dq_status"] == "quarantined":
        warnings.append("Registered-beds release is quarantined (see Admin → Data sources → CNES for the failed gate).")
    beds = silver.latest("facility_beds")
    if not fac.height:
        return envelope({"facilities": [], "by_type": [], "overrides": []}, request, meta={"data_status": "no_data"},
                        warnings=warnings + ["CNES facilities not ingested yet (Admin → Data sources → CNES)."])
    if region_id:
        fac = fac.filter(pl.col("municipality_id") == region_id)
    elif state:
        fac = fac.filter(pl.col("municipality_id").is_in(_in_state(state)))
    if facility_type:
        fac = fac.filter(pl.col("facility_type") == facility_type)
    if beds.height:
        fac = fac.join(beds.select("facility_id", "registered_beds", "registered_beds_sus", "registered_icu_beds"), on="facility_id", how="left")
    else:
        fac = fac.with_columns(*[pl.lit(None, dtype=pl.Float64).alias(c) for c in ("registered_beds", "registered_beds_sus", "registered_icu_beds")])
    by_type = fac.group_by("facility_type").agg(pl.len().alias("n"), pl.col("has_inpatient").sum().alias("inpatient"),
                                                pl.col("registered_beds").sum().alias("beds")).sort("n", descending=True)
    bed_totals = {"registered_beds": float(fac["registered_beds"].sum() or 0), "registered_beds_sus": float(fac["registered_beds_sus"].sum() or 0),
                  "registered_icu_beds": float(fac["registered_icu_beds"].sum() or 0),
                  "hospitals_with_beds": int(fac["registered_beds"].is_not_null().sum()),
                  "competence": beds["competence"].max() if beds.height else None} if beds.height else None
    ov = db.q("SELECT * FROM capacity_override WHERE (? IS NULL OR municipality_id=?) ORDER BY created_at DESC LIMIT 50", region_id, region_id)
    return envelope({"facilities": fac.sort("registered_beds", descending=True, nulls_last=True)
                     .select("facility_id", "municipality_id", "name", "facility_type", "lat", "lon", "has_inpatient", "sus_ambulatory",
                             "admin_sphere", "snapshot_month", "registered_beds", "registered_beds_sus", "registered_icu_beds").head(limit).to_dicts(),
                     "total": fac.height, "by_type": by_type.to_dicts(), "overrides": ov, "beds": bed_totals}, request,
                    meta={"semantics": "OBSERVED (registered)", "source": "CNES (dados abertos)", "data_status": "observed",
                          "as_of": fac["available_at"].max().isoformat() if fac.height else None}, warnings=warnings)


# ---------------------------------------------------------------- model metrics (public summary of the champion)
@router.get("/models/metrics")
def model_metrics(request: Request, disease_id: str = "dengue"):
    deps = db.q("SELECT * FROM model_deployment WHERE disease_id=? AND status='active' ORDER BY serving_alias", disease_id)
    out = []
    for dep in deps:
        m = mreg.get_model(dep["model_id"], dep["version"])
        rep = mreg.run_report(m["run_id"]) or {}
        out.append({"alias": dep["serving_alias"], "model": {k: m[k] for k in ("model_id", "version", "recipe_id", "target_signal",
                                                                                 "approval_state", "pit_status", "model_card_json", "created_at")},
                    "test": {rid: rr.get("test") for rid, rr in (rep.get("recipes") or {}).items()},
                    "folds": {rid: rr.get("folds") for rid, rr in (rep.get("recipes") or {}).items()},
                    "calibration": rep.get("calibration"), "comparison": rep.get("comparison"), "ablation": rep.get("ablation"),
                    "importance": rep.get("importance"), "split": rep.get("split"),
                    "leaderboard": rep.get("leaderboard")})
    return envelope(out, request, meta={"note": "Backtest metrics on held-out issue weeks; see PIT status for evidence level."})


# ---------------------------------------------------------------- alerts
@router.get("/alerts")
def alerts(request: Request, state: str | None = None, disease_id: str | None = None, uf: str | None = None):
    rows = db.q("SELECT * FROM alert WHERE (? IS NULL OR state=?) AND (? IS NULL OR disease_id=?) ORDER BY created_at DESC LIMIT 200",
                state, state, disease_id, disease_id)
    names = _names()
    if uf:
        rows = [r for r in rows if names.get(r["municipality_id"], {}).get("state_abbr") == uf.upper()]
    for r in rows:
        r["evidence"] = db.jcol({"e": r.pop("evidence_json")}, "e")["e"]
        r["name"] = names.get(r["municipality_id"], {}).get("name")
    return envelope(rows, request, meta={"semantics": "DRAFT — requires epidemiologist review before any communication"})


@router.post("/alerts/{alert_id}/review")
def review_alert(request: Request, alert_id: str, body: dict, a: Actor = Depends(require("alert.review"))):
    decision = body.get("decision")
    if decision not in ("approve", "hold", "reject"):
        raise HTTPException(400, "decision must be approve, hold or reject")
    if not (body.get("note") or "").strip():
        raise HTTPException(422, "a review note is required")
    row = db.q1("SELECT state FROM alert WHERE alert_id=?", alert_id)
    if not row:
        raise HTTPException(404)
    new = {"approve": "approved", "hold": "held", "reject": "rejected"}[decision]
    db.update("alert", {"alert_id": alert_id}, {"state": new, "reviewer_id": a.user_id, "review_note": body["note"], "reviewed_at": iso(utcnow())})
    do_audit(request, a, f"alert.{decision}", "alert", alert_id, old=row["state"], new=new, reason=body["note"])
    return envelope({"alert_id": alert_id, "state": new}, request,
                    warnings=["Approval records the review only; HealthPulse sends no external warning."])


# ---------------------------------------------------------------- jobs (shared by product and admin screens)
@router.get("/jobs/{job_id}")
def job(request: Request, job_id: str, a: Actor = Depends(actor)):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404)
    j.pop("payload", None)
    return envelope(j, request)


@router.get("/jobs/{job_id}/logs")
def job_logs(request: Request, job_id: str, after: int = 0):
    return envelope(jobs.logs(job_id, after), request)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(request: Request, job_id: str, a: Actor = Depends(actor)):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404)
    if a.user_id == "anonymous" or (j.get("created_by") != a.user_id and a.role != "platform_admin"):
        raise HTTPException(403, "only the job owner or a platform admin can cancel")
    out = jobs.request_cancel(job_id)
    do_audit(request, a, "job.cancel", "job", job_id)
    return envelope(out, request)


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str):
    """Server-sent events with real job state (polled from the queue table, never simulated)."""
    async def gen():
        last, after = None, 0
        for _ in range(3600):
            j = jobs.get(job_id)
            if not j:
                yield "event: error\ndata: {\"error\": \"unknown job\"}\n\n"
                return
            for l in jobs.logs(job_id, after, 200):
                after = l["seq"]
                yield f"event: log\ndata: {dumps(l)}\n\n"
            state = (j["status"], j["stage"], round(j["progress"] or 0, 3))
            if state != last:
                last = state
                j.pop("payload", None)
                yield f"event: job\ndata: {dumps(j)}\n\n"
            if j["status"] in jobs.TERMINAL:
                return
            await asyncio.sleep(0.5)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
