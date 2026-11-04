"""Scenario Studio: versioned drafts, validation, async runs, results, comparison, export, review, analyst."""
from __future__ import annotations

import io

import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import ValidationError

from ...assistant import analyst
from ...core import db
from ...core.security import Actor
from ...core.util import dumps, iso, utcnow
from ...simulation import engine
from ...simulation import jobs as sim
from ...simulation.schemas import ScenarioSpec
from ...sources import silver
from ..deps import actor, do_audit, envelope, idem_key, remember, replay, require

router = APIRouter()


def _spec(body: dict) -> ScenarioSpec:
    try:
        return ScenarioSpec.model_validate(body)
    except ValidationError as e:
        raise HTTPException(422, detail=[{"loc": ".".join(map(str, x["loc"])), "msg": x["msg"]} for x in e.errors()])


@router.get("/scenarios/defaults")
def defaults(request: Request, disease_id: str = "dengue"):
    from ..deps import latest_public_forecast
    f = latest_public_forecast(disease_id)
    spec = ScenarioSpec(name="Preparedness scenario", forecast_id=f["forecast_id"] if f else None, disease_id=disease_id)
    return envelope(spec.model_dump(), request, meta={"schema": ScenarioSpec.model_json_schema(),
                                                      "note": "Defaults are labelled planner assumptions, not data."})


@router.get("/scenarios")
def list_scenarios(request: Request, limit: int = 50):
    rows = db.q("SELECT s.* FROM scenario s JOIN (SELECT scenario_id, MAX(version) v FROM scenario GROUP BY scenario_id) m "
                "ON s.scenario_id=m.scenario_id AND s.version=m.v ORDER BY s.updated_at DESC LIMIT ?", limit)
    out = []
    for r in rows:
        r = db.jcol(r, "spec_json", "validation_json")
        last = db.q1("SELECT run_id, status, summary_json FROM simulation_run WHERE scenario_id=? ORDER BY created_at DESC LIMIT 1", r["scenario_id"])
        r["last_run"] = db.jcol(last, "summary_json")
        out.append(r)
    return envelope(out, request)


@router.post("/scenarios", status_code=201)
def create(request: Request, body: dict, a: Actor = Depends(require("scenario.write"))):
    spec = _spec(body)
    sc = sim.save_scenario(spec, a.user_id, parent=body.get("parent_scenario_id") if isinstance(body, dict) else None)
    do_audit(request, a, "scenario.create", "scenario", sc["scenario_id"], new=sc["version"])
    return envelope(sc, request)


@router.get("/scenarios/{sid}")
def get(request: Request, sid: str, version: int | None = None):
    sc = sim.get_scenario(sid, version)
    if not sc:
        raise HTTPException(404)
    return envelope(sc, request)


@router.put("/scenarios/{sid}")
def update(request: Request, sid: str, body: dict, a: Actor = Depends(require("scenario.write"))):
    expected = body.pop("expected_version", None)
    cur = sim.get_scenario(sid)
    if not cur:
        raise HTTPException(404)
    if expected is not None and int(expected) != cur["version"]:
        raise HTTPException(409, f"version conflict: current is {cur['version']}")
    spec = _spec(body)
    try:
        sc = sim.save_scenario(spec, a.user_id, scenario_id=sid)
    except PermissionError as e:
        raise HTTPException(409, str(e))
    do_audit(request, a, "scenario.update", "scenario", sid, old=cur["version"], new=sc["version"])
    return envelope(sc, request)


@router.post("/scenarios/{sid}/clone", status_code=201)
def clone(request: Request, sid: str, a: Actor = Depends(require("scenario.write"))):
    cur = sim.get_scenario(sid)
    if not cur:
        raise HTTPException(404)
    body = dict(cur["spec_json"])
    body["name"] = (body["name"] + " (copy)")[:160]
    sc = sim.save_scenario(ScenarioSpec.model_validate(body), a.user_id, parent=sid)
    do_audit(request, a, "scenario.clone", "scenario", sc["scenario_id"], meta={"from": sid})
    return envelope(sc, request)


@router.post("/scenarios/{sid}/validate")
def validate(request: Request, sid: str, a: Actor = Depends(actor)):
    sc = sim.get_scenario(sid)
    if not sc:
        raise HTTPException(404)
    v = sim.validate_spec(ScenarioSpec.model_validate(sc["spec_json"]))
    if sc["status"] in ("draft", "invalid", "validated"):
        db.update("scenario", {"scenario_id": sid, "version": sc["version"]}, {"validation_json": dumps(v), "status": "validated" if v["valid"] else "invalid"})
    return envelope(v, request)


@router.post("/scenarios/validate")
def validate_body(request: Request, body: dict):
    return envelope(sim.validate_spec(_spec(body)), request)


@router.post("/scenarios/{sid}/run", status_code=202)
def run(request: Request, sid: str, a: Actor = Depends(require("scenario.run")), key: str | None = Depends(idem_key)):
    prev = replay(key, a, f"run:{sid}")
    if prev:
        return envelope(prev, request)
    if not sim.get_scenario(sid):
        raise HTTPException(404)
    try:
        out = sim.start_run(sid, a.user_id, idem=f"sim:{sid}:{key}" if key else None)
    except ValueError as e:
        raise HTTPException(422, str(e))
    remember(key, a, f"run:{sid}", out)
    do_audit(request, a, "scenario.run", "simulation_run", out["run_id"], meta={"scenario_id": sid})
    return envelope(out, request)


@router.post("/scenarios/{sid}/review")
def review(request: Request, sid: str, body: dict, a: Actor = Depends(require("scenario.review"))):
    sc = sim.get_scenario(sid)
    if not sc:
        raise HTTPException(404)
    if not (body.get("note") or "").strip():
        raise HTTPException(422, "a review note is required")
    state = {"approve": "reviewed", "reject": "rejected"}.get(body.get("decision"))
    if not state:
        raise HTTPException(400, "decision must be approve or reject")
    db.update("scenario", {"scenario_id": sid, "version": sc["version"]}, {"review_state": state})
    do_audit(request, a, f"scenario.review.{state}", "scenario", sid, reason=body["note"])
    return envelope({"scenario_id": sid, "review_state": state}, request,
                    warnings=["Review is recorded for audit; no resource is dispatched from HealthPulse."])


@router.get("/simulations")
def list_runs(request: Request, limit: int = 50):
    rows = db.q("SELECT r.*, s.name FROM simulation_run r LEFT JOIN scenario s ON s.scenario_id=r.scenario_id AND s.version=r.scenario_version "
                "ORDER BY r.created_at DESC LIMIT ?", limit)
    return envelope([db.jcol(r, "summary_json", "provenance_json", "warnings_json") for r in rows], request)


@router.get("/simulations/{rid}")
def get_run(request: Request, rid: str):
    r = sim.get_run(rid)
    if not r:
        raise HTTPException(404)
    meta = {"type": "SCENARIO", "interpretation_status": "scenario_estimate_not_observed_impact",
            "synthetic": bool((r.get("provenance_json") or {}).get("synthetic"))}
    if r["mode"] == "transmission_research":
        meta["type"] = "HYPOTHETICAL"
    return envelope(r, request, meta=meta, warnings=r.get("warnings_json") or [])


@router.get("/simulations/{rid}/results")
def results(request: Request, rid: str, municipality_id: str | None = None):
    r = sim.get_run(rid)
    if not r or r["status"] != "completed":
        raise HTTPException(404, "run not completed")
    df = sim.run_results(rid)
    if municipality_id:
        df = df.filter(pl.col("municipality_id") == municipality_id)
    names = {x["municipality_id"]: x["name"] for x in silver.latest("municipality_dim").select("municipality_id", "name").iter_rows(named=True)}
    rows = df.to_dicts()
    for x in rows:
        x["name"] = names.get(x["municipality_id"])
    return envelope(rows, request, meta={"type": "SCENARIO", "rows": len(rows)})


@router.get("/simulations/{rid}/compare")
def compare(request: Request, rid: str, baseline_id: str):
    a, b = sim.get_run(baseline_id), sim.get_run(rid)
    if not a or not b:
        raise HTTPException(404)
    if a["status"] != "completed" or b["status"] != "completed":
        raise HTTPException(409, "both runs must be completed")
    out = engine.compare_runs(a, b)
    return envelope(out, request, warnings=[] if out["comparable"] else ["Runs are not comparable: " + ", ".join(out["issues"])])


@router.get("/simulations/{rid}/export")
def export(rid: str, fmt: str = "csv"):
    r = sim.get_run(rid)
    if not r or r["status"] != "completed":
        raise HTTPException(404)
    df = sim.run_results(rid)
    header = (f"# HealthPulse scenario export — run {rid} — SCENARIO ESTIMATE under listed assumptions, not observed impact\n"
              f"# forecast {r['forecast_id']} · result sha256 {r['result_sha256']}\n")
    if fmt == "json":
        return Response(dumps({"run": r, "results": df.to_dicts()}), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{rid}.json"'})
    buf = io.StringIO()
    df.write_csv(buf)
    return Response(header + buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{rid}.csv"'})


@router.post("/assistant/explain")
def explain(request: Request, body: dict, a: Actor = Depends(actor)):
    q = (body.get("question") or "").strip()
    if not q or len(q) > 2000:
        raise HTTPException(400, "question must be 1–2000 characters")
    out = analyst.explain(q, disease_id=body.get("disease_id", "dengue"), forecast_id=body.get("forecast_id"),
                          municipality_id=body.get("municipality_id"), run_id=body.get("run_id"))
    return envelope(out, request, meta={"engine": "grounded rule-based analyst (tool-gated)", "requires_review": True})
