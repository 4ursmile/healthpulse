"""Scenario service (versioned drafts, validation, runs) and the simulation worker job."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl

from ..core import db, jobs, lake
from ..core.jobs import JobContext, handler
from ..core.util import dumps, iso, loads, new_id, sha256_file, stable_hash, utcnow, write_json_atomic
from ..sources import catalog, silver
from . import engine, transmission
from .schemas import ScenarioSpec


def get_forecast(fid: str) -> dict | None:
    return db.jcol(db.q1("SELECT * FROM forecast WHERE forecast_id=?", fid), "horizons_json", "provenance_json")


def forecast_frame(fid: str) -> pl.DataFrame:
    f = get_forecast(fid)
    return pl.read_parquet(lake.abs_artifact(f["parquet_uri"]))


def validate_spec(spec: ScenarioSpec) -> dict:
    checks, warnings = [], []

    def c(name, ok, detail, hard=True):
        checks.append({"check": name, "passed": bool(ok), "hard": hard, "detail": detail})
    if spec.mode == "operational":
        f = get_forecast(spec.forecast_id) if spec.forecast_id else None
        c("forecast_pinned", f is not None, spec.forecast_id or "no forecast_id")
        if f:
            c("disease_matches_forecast", f["disease_id"] == spec.disease_id, f"forecast {f['disease_id']} vs scenario {spec.disease_id}")
            F = forecast_frame(spec.forecast_id)
            regs = set(F["municipality_id"].to_list())
            missing = [r for r in spec.region_ids if r not in regs]
            c("regions_in_forecast", not missing, f"{len(missing)} region(s) not in the forecast" if missing else f"{len(spec.region_ids) or len(regs)} regions")
            weeks = sorted(set(map(str, F["target_week"].to_list())))
            start = spec.window.start_week or weeks[0]
            win = [str(date.fromisoformat(start) + timedelta(weeks=i)) for i in range(spec.window.weeks)]
            c("window_within_forecast", set(win) <= set(weeks), f"target weeks {win[0]}…{win[-1]} vs forecast {weeks[0]}…{weeks[-1]}")
            ins = F.filter((pl.col("quality_status") != "ok") & pl.col("target_week").cast(pl.Utf8).is_in(win))
            if ins.height:
                warnings.append(f"{ins.height} region-weeks have insufficient data and are excluded (never treated as zero)")
            sig = catalog.DISEASES[f["disease_id"]]["signal_type"]
            if sig == "sari_admission" and spec.assumptions.visit_rate not in (1.0,):
                warnings.append("SARI signals are already severe cases; the visit rate is not applied a second time")
            c("weather_use", spec.stressors.weather_use in ("none", "scenario_override"), "future observed weather is not accepted")
            if f["is_synthetic"]:
                warnings.append("forecast was produced from SYNTHETIC fixture data")
            if f["quality_status"] != "PIT_VERIFIED":
                warnings.append(f"forecast quality {f['quality_status']}: backtest evidence is limited")
        bases = spec.resources.team_bases
        if bases and f:
            regs_in = set(spec.region_ids) or set(forecast_frame(spec.forecast_id)["municipality_id"].to_list())
            c("team_bases_in_scope", set(bases) <= regs_in, f"bases {bases}")
        if spec.resources.mobile_teams_available and spec.resources.total_budget_brl is not None:
            min_cost = spec.resources.cost_per_team_week_brl * spec.window.weeks
            c("budget_covers_one_team", spec.resources.total_budget_brl >= min_cost or spec.resources.total_budget_brl == 0,
              f"one team costs ≥ BRL {min_cost:,.0f} for the window", hard=False)
        if spec.resources.effective_visit_capacity_source == "planner_input" or spec.capacity.basis == "per_10k_residents":
            warnings.append("Visit rate and operational capacity are planner assumptions, not verified availability")
    else:
        c("transmission_parameters", spec.transmission is not None, "research-mode parameters supplied")
        if spec.transmission:
            c("r0_range", spec.transmission.r0_low <= spec.transmission.r0_high, f"{spec.transmission.r0_low}–{spec.transmission.r0_high}")
        c("region_limit", len(spec.region_ids) <= 1000, "research mode is limited to 1,000 regions")
        warnings.append("HYPOTHETICAL research mode: uncalibrated transmission assumptions; not a programme-effect estimate")
    c("draw_budget", spec.simulation.draws * max(1, len(spec.region_ids) or 30) * spec.window.weeks <= 400_000_000,
      f"{spec.simulation.draws:,} trials")
    ok = all(x["passed"] for x in checks if x["hard"])
    return {"valid": ok, "checks": checks, "warnings": warnings}


def save_scenario(spec: ScenarioSpec, actor: str, scenario_id: str | None = None, parent: str | None = None) -> dict:
    now = iso(utcnow())
    body = spec.model_dump()
    h = stable_hash(body)
    if scenario_id:
        cur = db.q1("SELECT * FROM scenario WHERE scenario_id=? ORDER BY version DESC LIMIT 1", scenario_id)
        if cur is None:
            raise KeyError(scenario_id)
        if cur["status"] in ("completed", "published"):
            raise PermissionError("completed scenarios are immutable; clone to edit")
        v = cur["version"] + 1
        db.ex("UPDATE scenario SET status='superseded' WHERE scenario_id=? AND version=?", scenario_id, cur["version"])
    else:
        scenario_id, v = new_id("scn"), 1
    db.insert("scenario", {"scenario_id": scenario_id, "version": v, "name": spec.name, "mode": spec.mode, "spec_json": dumps(body),
                           "spec_hash": h, "status": "draft", "parent_scenario_id": parent, "created_by": actor, "created_at": now,
                           "updated_at": now})
    return get_scenario(scenario_id)


def get_scenario(sid: str, version: int | None = None) -> dict | None:
    if version:
        r = db.q1("SELECT * FROM scenario WHERE scenario_id=? AND version=?", sid, version)
    else:
        r = db.q1("SELECT * FROM scenario WHERE scenario_id=? ORDER BY version DESC LIMIT 1", sid)
    r = db.jcol(r, "spec_json", "validation_json")
    if r:
        r["runs"] = db.q("SELECT run_id, status, created_at, finished_at, spec_hash FROM simulation_run WHERE scenario_id=? ORDER BY created_at DESC", sid)
    return r


def start_run(sid: str, actor: str, idem: str | None = None) -> dict:
    sc = get_scenario(sid)
    spec = ScenarioSpec.model_validate(sc["spec_json"])
    v = validate_spec(spec)
    db.update("scenario", {"scenario_id": sid, "version": sc["version"]}, {"validation_json": dumps(v),
                                                                             "status": "validated" if v["valid"] else "invalid"})
    if not v["valid"]:
        raise ValueError("scenario failed validation: " + "; ".join(c["detail"] for c in v["checks"] if not c["passed"] and c["hard"]))
    rid = new_id("sim")
    db.insert("simulation_run", {"run_id": rid, "scenario_id": sid, "scenario_version": sc["version"], "spec_hash": sc["spec_hash"],
                                 "forecast_id": spec.forecast_id, "status": "queued", "mode": spec.mode, "warnings_json": dumps(v["warnings"]),
                                 "created_by": actor, "created_at": iso(utcnow())})
    j = jobs.enqueue("simulate", {"run_id": rid}, target_type="simulation_run", target_id=rid, actor_id=actor, idempotency_key=idem,
                     timeout_s=1800)
    db.update("simulation_run", {"run_id": rid}, {"job_id": j["job_id"]})
    return {"run_id": rid, "job_id": j["job_id"], "status": "queued", "warnings": v["warnings"]}


def get_run(rid: str) -> dict | None:
    r = db.jcol(db.q1("SELECT * FROM simulation_run WHERE run_id=?", rid), "summary_json", "provenance_json", "warnings_json")
    if r and r.get("job_id"):
        j = jobs.get(r["job_id"])
        if j:
            r["job"] = {k: j.get(k) for k in ("status", "stage", "progress", "error", "elapsed_s")}
            if j["status"] in ("failed", "canceled", "timed_out"):
                r["status"] = j["status"]
    return r


def region_inputs(regions: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[str]]:
    dim = silver.latest("municipality_dim")
    pop = silver.latest("population_annual")
    fac = silver.latest("facility")
    warn = []
    d = pl.DataFrame({"municipality_id": regions}).join(dim.select("municipality_id", "centroid_lat", "centroid_lon"), on="municipality_id", how="left")
    if pop.height:
        latest_pop = pop.sort("reference_year").unique("municipality_id", keep="last").select("municipality_id", "population")
        d = d.join(latest_pop, on="municipality_id", how="left")
    else:
        d = d.with_columns(pl.lit(None, dtype=pl.Int64).alias("population"))
    if d["population"].null_count():
        warn.append(f"{d['population'].null_count()} region(s) without a population estimate; capacity defaults to 0 there")
    if fac.height:
        fc = fac.group_by("municipality_id").agg(pl.len().alias("facilities"))
        d = d.join(fc, on="municipality_id", how="left")
    else:
        d = d.with_columns(pl.lit(None, dtype=pl.UInt32).alias("facilities"))
        warn.append("no CNES facilities ingested: access-gap equity weights are uniform")
    return (d["population"].fill_null(0).to_numpy().astype(np.float32), d["centroid_lat"].to_numpy(), d["centroid_lon"].to_numpy(),
            d["facilities"].fill_null(0).to_numpy().astype(np.float32), warn)


@handler("simulate")
def simulate(ctx: JobContext, p: dict) -> dict:
    run = get_run(p["run_id"])
    sc = get_scenario(run["scenario_id"], run["scenario_version"])
    spec = ScenarioSpec.model_validate(sc["spec_json"])
    if stable_hash(spec.model_dump()) != run["spec_hash"]:
        raise RuntimeError("scenario changed after the run was queued")
    db.update("simulation_run", {"run_id": run["run_id"]}, {"status": "running"})
    ctx.stage("inputs", 0.02)
    warnings = list(run["warnings_json"] or [])
    out_dir = lake.artifact_dir("simulation", run["run_id"])
    prov: dict = {"scenario_id": sc["scenario_id"], "scenario_version": sc["version"], "spec_hash": sc["spec_hash"],
                  "random_seed": spec.simulation.random_seed, "draws": spec.simulation.draws, "mode": spec.mode,
                  "disease_id": spec.disease_id}
    if spec.mode == "operational":
        f = get_forecast(spec.forecast_id)
        F = forecast_frame(spec.forecast_id)
        weeks_all = sorted(set(map(str, F["target_week"].to_list())))
        start = spec.window.start_week or weeks_all[0]
        weeks = [str(date.fromisoformat(start) + timedelta(weeks=i)) for i in range(spec.window.weeks)]
        F = F.with_columns(pl.col("target_week").cast(pl.Utf8))
        if spec.region_ids:
            F = F.filter(pl.col("municipality_id").is_in(spec.region_ids))
        bad = F.filter(pl.col("target_week").is_in(weeks) & (pl.col("quality_status") != "ok"))["municipality_id"].unique().to_list()
        F = F.filter(~pl.col("municipality_id").is_in(bad) & pl.col("target_week").is_in(weeks))
        regions = sorted(F["municipality_id"].unique().to_list())
        if not regions:
            raise ValueError("no region has usable forecast values in the window")
        piv = {k: F.pivot(on="target_week", index="municipality_id", values=k).sort("municipality_id") for k in ("p10", "p50", "p90")}
        arr = {k: piv[k].select(weeks).to_numpy().astype(np.float32) for k in piv}
        pop, lat, lon, facs, w2 = region_inputs(regions)
        warnings += w2
        sig = catalog.DISEASES[f["disease_id"]]["signal_type"]
        inp = engine.Inputs(regions, weeks, arr["p10"], arr["p50"], arr["p90"], pop, lat, lon, facs, sig)
        ctx.stage("simulate", 0.08, regions=len(regions), weeks=len(weeks), draws=spec.simulation.draws)

        def prog(x, msg):
            ctx.check_cancel()
            ctx.progress(x, msg)
        res = engine.run_operational(spec, inp, prog)
        warnings += res["warnings"]
        fp = f["provenance_json"]
        prov.update({"forecast_id": f["forecast_id"], "model_version": fp.get("model_version"), "training_run_id": fp.get("training_run_id"),
                     "source_vintage_ids": fp.get("source_vintage_ids"), "forecast_quality": f["quality_status"],
                     "signal_type": sig, "regions": regions, "weeks": weeks, "optimizer_version": engine.OPTIMIZER_VERSION,
                     "engine_version": engine.ENGINE_VERSION, "excluded_regions_insufficient_data": bad,
                     "assumptions": spec.assumptions.model_dump(), "capacity_basis": spec.capacity.basis,
                     "resources_snapshot": spec.resources.model_dump(), "synthetic": bool(f["is_synthetic"])})
    else:
        regions = spec.region_ids or silver.latest("municipality_dim").filter(pl.col("is_capital"))["municipality_id"].to_list()
        pop, lat, lon, facs, w2 = region_inputs(regions)
        cases = silver.latest("disease_weekly").filter((pl.col("disease_id") == spec.disease_id) & pl.col("municipality_id").is_in(regions))
        last = cases.sort("week_start").group_by("municipality_id").agg(pl.col("observed_cases").last())
        rc = pl.DataFrame({"municipality_id": regions}).join(last, on="municipality_id", how="left")["observed_cases"].fill_null(0).to_numpy()
        ctx.stage("simulate", 0.08, regions=len(regions), individuals=int(pop.sum()))

        def prog(x, msg):
            ctx.check_cancel()
            ctx.progress(x, msg)
        res = transmission.run(spec, regions, pop, lat, lon, rc.astype(float), prog)
        res["assignments"] = []
        prov.update({"regions": regions, "engine_version": transmission.VERSION, "hypothetical": True})
    ctx.stage("persist", 0.96)
    rdf = pl.DataFrame(res["regions"])
    digest = lake.write_parquet_atomic(rdf, out_dir / "result.parquet")
    write_json_atomic(out_dir / "scenario.json", sc["spec_json"])
    summary = {**res["summary"], "assignments": res["assignments"], "warnings": warnings,
               "interpretation_status": res["summary"].get("interpretation_status", "scenario_estimate_not_observed_impact"),
               "assumptions_verified": spec.resources.effective_visit_capacity_source == "verified_operational"}
    write_json_atomic(out_dir / "summary.json", summary)
    prov["result_sha256"] = digest
    db.update("simulation_run", {"run_id": run["run_id"]}, {
        "status": "completed", "summary_json": dumps(summary), "result_uri": lake.rel_artifact(out_dir / "result.parquet"),
        "result_sha256": digest, "provenance_json": dumps(prov), "warnings_json": dumps(warnings), "finished_at": iso(utcnow())})
    db.update("scenario", {"scenario_id": sc["scenario_id"], "version": sc["version"]}, {"status": "completed", "updated_at": iso(utcnow())})
    return {"run_id": run["run_id"], "result_sha256": digest, "regions": len(regions)}


def run_results(rid: str) -> pl.DataFrame:
    r = get_run(rid)
    return pl.read_parquet(lake.abs_artifact(r["result_uri"]))
