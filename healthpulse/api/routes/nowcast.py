"""Nowcast API: right-truncation-corrected SARI counts for Brazil (published runs only; Viet Nam has no nowcast)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ...core.security import Actor
from ...ml import nowcast as nc
from ...ml import nowcast_jobs as nj
from ..deps import do_audit, envelope, require

router = APIRouter()

CAVEAT = ("Nowcast of the count each recent onset week will eventually have once all records are entered and classified. "
          "Delay reconstructed from first-entry and closure dates in one snapshot (PIT_RECONSTRUCTED): later edits and "
          "reclassifications are invisible. It is a correction of an observed count, not a forecast.")


def _resolve(nid: str, disease_id: str | None) -> dict:
    if nid == "latest":
        if disease_id not in nc.DISEASE_CLASS:
            raise HTTPException(422, f"disease_id must be one of {sorted(nc.DISEASE_CLASS)} (SIVEP-Gripe SARI diseases)")
        r = nj.latest_published(disease_id)
        if not r:
            raise HTTPException(404, "no published nowcast for this disease")
        return r
    r = nj.get_run(nid)
    if not r:
        raise HTTPException(404, "unknown nowcast")
    return r


@router.get("/nowcasts")
def list_nowcasts(request: Request, disease_id: str | None = None, include_failed: bool = False, a: Actor = Depends(require("read"))):
    return envelope(nj.list_runs(disease_id, include_failed), request, meta={"label": nc.LABEL, "diseases": sorted(nc.DISEASE_CLASS)},
                    warnings=[CAVEAT])


@router.get("/nowcasts/{nid}")
def get_nowcast(request: Request, nid: str, disease_id: str | None = None, a: Actor = Depends(require("read"))):
    r = _resolve(nid, disease_id)
    return envelope({**r, "provenance": nj.provenance(r["nowcast_id"])}, request, meta={"label": nc.LABEL}, warnings=[CAVEAT])


@router.get("/nowcasts/{nid}/series")
def nowcast_series(request: Request, nid: str, disease_id: str, state: str | None = None, municipality_id: str | None = None,
                   weeks: int = 52, a: Actor = Depends(require("read"))):
    if disease_id not in nc.DISEASE_CLASS:
        raise HTTPException(422, f"disease_id must be one of {sorted(nc.DISEASE_CLASS)}")
    r = _resolve(nid, disease_id)
    if disease_id not in r["diseases_json"]:
        raise HTTPException(404, "this nowcast does not cover that disease")
    s = nj.series(r["nowcast_id"], disease_id, state, municipality_id, max(4, min(weeks, 156)))
    warnings = [CAVEAT]
    if s["fallback"]:
        warnings.append(f"No separate series for the requested place: showing the {s['unit_level']} series ({s['unit_id']}).")
    bt = (r.get("metrics_json") or {})
    return envelope(s, request, meta={"type": "NOWCAST", "label": nc.LABEL, "nowcast_id": r["nowcast_id"], "asof_week": r["asof_week"],
                                       "asof_cutoff": r["asof_cutoff"], "disease_id": disease_id, "is_synthetic": bool(r["is_synthetic"]),
                                       "max_delay_weeks": r["config_json"]["max_delay"], "backtest": {k: bt.get(k) for k in
                                       ("holdout_wape", "holdout_wape_naive", "holdout_cov80", "holdout_cov95")}, "status": r["status"],
                                       "units": "SARI cases per onset week"}, warnings=warnings)


@router.get("/nowcasts/{nid}/backtest")
def nowcast_backtest(request: Request, nid: str, disease_id: str | None = None, a: Actor = Depends(require("read"))):
    r = _resolve(nid, disease_id)
    m = nj.backtest_metrics(r["nowcast_id"])
    if disease_id:
        for k in ("by_level", "by_bucket"):
            m[k] = [x for x in m.get(k, []) if x.get("disease_id") == disease_id]
    return envelope(m, request, meta={"label": nc.LABEL, "nowcast_id": r["nowcast_id"]},
                    warnings=["Backtest truth = the count in the same snapshot for onset weeks at least 26 weeks old; reconstructed from entry/closure dates."])


@router.post("/nowcasts", status_code=202)
def run_nowcast(request: Request, body: dict | None = None, a: Actor = Depends(require("nowcast.run"))):
    body = body or {}
    ds = body.get("diseases")
    if ds is not None and (not isinstance(ds, list) or any(d not in nc.DISEASE_CLASS for d in ds)):
        raise HTTPException(422, f"diseases must be a list drawn from {sorted(nc.DISEASE_CLASS)}")
    if body.get("asof"):
        try:
            from datetime import date
            date.fromisoformat(body["asof"])
        except ValueError:
            raise HTTPException(422, "asof must be an ISO date (YYYY-MM-DD)")
    j = nj.enqueue(a.user_id, ds, body.get("asof"), body.get("config"))
    do_audit(request, a, "nowcast.run", "nowcast", j["job_id"], meta={"diseases": ds, "asof": body.get("asof")})
    return envelope({"job_id": j["job_id"]}, request)
