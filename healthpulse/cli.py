"""`hp` command line: init, serve, worker, ingest, demo (the full vertical slice), bench, synth-sivep."""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time

from .config import get_settings


def _init():
    from .bootstrap import init
    init()


def _run(job_type: str, payload: dict, **kw) -> dict:
    from .core import jobs
    j = jobs.enqueue(job_type, payload, **kw)
    r = jobs.run_job_inline(j["job_id"])
    print(f"  {job_type:14s} {r['status']:24s} {r.get('elapsed_s', 0):6.1f}s  {r.get('error') or ''}")
    if r["status"] in ("failed", "timed_out", "canceled"):
        for line in jobs.logs(j["job_id"])[-3:]:
            print("    ", line["message"][-400:])
    return r


def cmd_serve(a):
    """API (uvicorn) and the worker supervisor as two processes; Ctrl-C stops both."""
    _init()
    env = os.environ.copy()
    procs = [subprocess.Popen([sys.executable, "-m", "healthpulse.worker", "--concurrency", str(a.workers)], env=env),
             subprocess.Popen([sys.executable, "-m", "uvicorn", "healthpulse.api.main:app", "--host", a.host, "--port", str(a.port),
                               "--workers", str(a.api_workers), "--log-level", "warning"], env=env)]
    print(f"HealthPulse on http://{a.host}:{a.port}  (worker concurrency {a.workers})")

    def stop(*_):
        for p in procs:
            p.send_signal(signal.SIGTERM)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while all(p.poll() is None for p in procs):
        time.sleep(0.5)
    stop()
    for p in procs:
        p.wait(10)


def cmd_worker(a):
    from .worker import main
    main(["--concurrency", str(a.concurrency)])


def cmd_ingest(a):
    _init()
    for sid in a.sources:
        _run("ingest", {"source_id": sid, "params": {}, "force": a.force}, target_type="source", target_id=sid, actor_id="u_operator")


def cmd_demo(a):
    """Bronze -> Silver -> PIT features -> dataset -> train/backtest -> dual review -> champion -> forecast -> scenario."""
    _init()
    from .core import db
    from .features import registry as freg
    from .ml import registry as mreg
    from .simulation import jobs as sim
    from .simulation.schemas import ScenarioSpec
    print("1. ingest real sources")
    core = ["ibge_municipalities", "ibge_population", "ibge_commuting", "nager_holidays", "infodengue", "open_meteo"]
    extra = [] if a.fast else ["ibge_geometry", "cnes_facilities_beds", "ibge_cempre", "sivep_gripe", "worldpop"]
    big = ["brazil_dengue_multimodal"] if getattr(a, "full", False) else []
    for sid in ([] if getattr(a, "skip_ingest", False) else core + extra + big):
        _run("ingest", {"source_id": sid, "params": {}}, target_type="source", target_id=sid, actor_id="u_operator")
    print("2. validate feature definitions (PIT, cardinality)")
    for d in freg.list_defs():
        freg.record_validation(d["feature_id"], d["version"], freg.validate_def(d))
    freg.seed_feature_sets()
    print("3. training dataset (PIT audited)")
    r = _run("dataset_build", {"feature_set_id": "br_dengue", "target": "dengue_reported_cases"}, actor_id="u_scientist")
    ds = r["result"]["dataset_id"]
    print("4. experiment: baselines + GLM + gradient boosting, rolling-origin backtest")
    e = mreg.create_experiment({"dataset_id": ds, "name": "Dengue pilot backtest", "recipes": [
        {"recipe": "persistence"}, {"recipe": "seasonal_naive"}, {"recipe": "historical_median"}, {"recipe": "glm_poisson"},
        {"recipe": "hgb_poisson"}, {"recipe": "damped_trend"}, {"recipe": "ridge_growth"}, {"recipe": "hgb_growth"},
        {"recipe": "ensemble"}]}, "u_scientist")
    run = mreg.start_run(e["experiment_id"], "u_scientist")
    from .core import jobs
    jr = jobs.run_job_inline(run["job_id"])
    print(f"  train          {jr['status']:24s} {jr.get('elapsed_s', 0):6.1f}s")
    print("5. register, dual review, deploy champion, publish forecast")
    m = mreg.register(run["run_id"], "ensemble", "u_scientist")
    mreg.review(m["model_id"], m["version"], "clinical", "approve", "Target definition, caveats and coverage reviewed", "u_reviewer", "epi_reviewer")
    mreg.review(m["model_id"], m["version"], "platform", "approve", "Artifact checksum, runtime and access verified", "u_admin", "platform_admin")
    d = mreg.deploy(m["model_id"], m["version"], "champion", "u_admin")
    fr = jobs.run_job_inline(d["inference_job_id"])
    fid = fr["result"]["forecast_id"]
    print(f"  forecast       {fid}")
    print("6. operational scenario pinned to the forecast")
    spec = ScenarioSpec(name="Pilot preparedness: 6 mobile teams", forecast_id=fid, stressors={"holiday_surge_pct": 25},
                        resources={"mobile_teams_available": 6, "total_budget_brl": 90000, "max_travel_minutes": 600},
                        capacity={"visits_per_10k_residents_per_week": 0.08})
    sc = sim.save_scenario(spec, "u_scientist")
    sr = sim.start_run(sc["scenario_id"], "u_scientist")
    jobs.run_job_inline(sr["job_id"])
    s = sim.get_run(sr["run_id"])["summary_json"]
    print(f"  simulation     unmet visits p50 {s['unmet_visits_baseline']['p50']:,.0f} -> {s['unmet_visits_proposed']['p50']:,.0f}, "
          f"cost BRL {s['cost_brl']:,.0f}, method {s['optimizer']['method']}")
    print("done. start the app with: hp serve")


def cmd_fetch(a):
    _init()
    from .core import db
    from .sources.connectors import CONNECTORS
    order = ["ibge_municipalities", "ibge_geometry", "ibge_population", "ibge_commuting", "nager_holidays", "infodengue",
             "open_meteo", "cnes_facilities_beds", "ibge_cempre", "sivep_gripe", "worldpop", "brazil_dengue_multimodal"]
    have = {r["source_id"] for r in db.q("SELECT DISTINCT source_id FROM source_snapshot WHERE published=1")}
    avail = {r["source_id"] for r in db.q("SELECT source_id FROM source_registry WHERE enabled=1 AND status IN ('available','healthy','stale')")}
    todo = [s for s in order if s in avail and s in CONNECTORS and (a.all or s not in have)]
    print(f"fetching: {', '.join(todo) or 'nothing missing'}")
    for sid in todo:
        _run("ingest", {"source_id": sid, "params": {}}, target_type="source", target_id=sid, actor_id="u_operator")


def cmd_bench(a):
    import numpy as np
    from .simulation import engine
    from .simulation.schemas import ScenarioSpec
    rng = np.random.default_rng(1)
    R, K = a.regions, a.draws
    p50 = rng.gamma(2, 60, (R, 4)).astype(np.float32)
    inp = engine.Inputs([f"{i:07d}" for i in range(R)], ["w1", "w2", "w3", "w4"], p50 * 0.6, p50, p50 * 1.6,
                        rng.lognormal(10, 1.2, R).astype(np.float32), rng.uniform(-30, -3, R), rng.uniform(-60, -35, R),
                        rng.integers(0, 50, R).astype(np.float32), "notification")
    spec = ScenarioSpec(name="benchmark (SYNTHETIC)", forecast_id="synthetic", resources={
        "mobile_teams_available": a.teams, "max_travel_minutes": 600, "team_bases": [f"{i:07d}" for i in range(min(R, 8))]},
        capacity={"visits_per_10k_residents_per_week": 1.0}, simulation={"draws": K})
    t = time.perf_counter()
    res = engine.run_operational(spec, inp)
    dt = time.perf_counter() - t
    s = res["summary"]
    print(f"SYNTHETIC benchmark: {R:,} regions × {K:,} trials × 4 weeks = {R * K * 4 / 1e6:,.0f}M demand draws in {dt:.2f}s "
          f"({R * K * 4 / dt / 1e6:,.1f}M/s); optimizer {s['optimizer']['method']}, {s['teams_allocated']} teams")


def cmd_vn_fetch(a):
    """Viet Nam: download the public sources into Bronze, build Gold tables, fit the climate model."""
    _init()
    from .vietnam import data
    t = time.perf_counter()
    if not a.build_only:
        print("Viet Nam: fetch sources")
        data.fetch_all(force=a.force)
    print("Viet Nam: build")
    data.build()
    from .vietnam import model
    i = model.model_info()
    h = i["holdout_2010"]
    print(f"  model: holdout 2010 MAE {h['mae']:.1f} (seasonal-only {h['mae_seasonal_only']:.1f}), deviance explained "
          f"{h['deviance_explained']:.2f}; national shape r={i['national_shape_corr_2011_on'] or float('nan'):.2f}; kappa {i['kappa']:.2f}")
    print(f"done in {time.perf_counter() - t:.0f}s")


def cmd_vn_calibrate(a):
    """Viet Nam: calibrate the dengue model / sandbox on OpenDengue annual + weekly totals and the COVID-19 waves on WHO weekly data."""
    _init()
    from .vietnam import calibration
    art = calibration.run(quick=a.quick, fetch=not a.no_fetch)
    for k, v in art["status"].items():
        print(f"  {k}: {v}")


def cmd_crosswalk(a):
    """Polygon crosswalks (GADM -> IBGE / OCHA P-codes) and, when Meta mobility has been ingested, the mobility-range profile."""
    _init()
    from .geo import pipeline as gp
    pairs = a.pair or gp.buildable_pairs()
    t = time.perf_counter()
    for sid in ("gadm_boundaries",):
        from .sources import silver
        if not silver.latest("gadm_unit").height:
            print(f"  {sid} not ingested yet, ingesting")
            _run("ingest", {"source_id": sid, "params": {}, "force": False}, target_type="source", target_id=sid, actor_id="u_operator")
    print("Polygon crosswalks")
    for pair in pairs:
        gp.build_crosswalk(pair)
    if not a.no_profile:
        from .sources import silver
        if silver.latest("mobility_polygon").height:
            print("Mobility-range profile")
            gp.build_mobility_profile(min_days=a.min_days)
        else:
            print("  (no Meta mobility rows yet: hp ingest meta_mobility, then hp crosswalk again)")
    print(f"done in {time.perf_counter() - t:.0f}s")


def cmd_nowcast(a):
    """Nowcast COVID-19 / influenza SARI for Brazil from the ingested SIVEP-Gripe files (point-in-time backtest + release gates)."""
    _init()
    from .ml import nowcast_jobs as nj
    j = nj.enqueue("u_scientist", [a.disease] if a.disease else None, a.asof, None)
    from .core import jobs
    out = jobs.run_job_inline(j["job_id"])
    print(f"  nowcast        {out['status']:24s} {out.get('elapsed_s', 0):6.1f}s  {out.get('error') or ''}")
    res = out.get("result") or {}
    if res:
        h = res.get("headline", {})
        print(f"  {res['nowcast_id']}  as-of week {res['asof_week']}  [{res['status']}]  label {h.get('label')}")
        print(f"  hold-out WAPE {h.get('holdout_wape'):.3f} (raw count {h.get('holdout_wape_naive'):.3f}), 80% coverage {h.get('holdout_cov80'):.2f}")


def cmd_synth(a):
    _init()
    _run("synthetic_sivep", {"rows": a.rows, "weeks": a.weeks}, target_type="source", target_id="sivep_gripe", actor_id="u_operator")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="hp", description="HealthPulse 3.1")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=lambda a: (_init(), print(f"initialised {get_settings().db_path}")))
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--workers", type=int, default=2)
    s.add_argument("--api-workers", type=int, default=2)
    s.set_defaults(fn=cmd_serve)
    w = sub.add_parser("worker")
    w.add_argument("--concurrency", type=int, default=2)
    w.set_defaults(fn=cmd_worker)
    i = sub.add_parser("ingest")
    i.add_argument("sources", nargs="+")
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_ingest)
    d = sub.add_parser("demo")
    d.add_argument("--fast", action="store_true", help="skip national geometry, CNES, CEMPRE, SRAG and WorldPop downloads")
    d.add_argument("--full", action="store_true", help="also download the Zenodo multimodal dataset (~565 MB)")
    d.add_argument("--skip-ingest", action="store_true", help="reuse the data already ingested; rerun features → forecast → scenario")
    d.set_defaults(fn=cmd_demo)
    f = sub.add_parser("fetch", help="ingest every available source that has never been ingested")
    f.add_argument("--all", action="store_true", help="re-ingest every available source, not only missing ones")
    f.set_defaults(fn=cmd_fetch)
    b = sub.add_parser("bench")
    b.add_argument("--regions", type=int, default=5570)
    b.add_argument("--draws", type=int, default=1000)
    b.add_argument("--teams", type=int, default=300)
    b.set_defaults(fn=cmd_bench)
    v = sub.add_parser("vn-fetch", help="Viet Nam: download sources, build tables, fit the climate model")
    v.add_argument("--force", action="store_true", help="re-download static sources too (weather is always refreshed)")
    v.add_argument("--build-only", action="store_true", help="rebuild from the latest Bronze snapshots without downloading")
    v.set_defaults(fn=cmd_vn_fetch)
    vc = sub.add_parser("vn-calibrate", help="Viet Nam: calibrate the dengue model and the sandbox (dengue 2022, COVID-19 Delta / Omicron)")
    vc.add_argument("--quick", action="store_true", help="fewer trials / bootstrap draws (about 1 minute instead of several)")
    vc.add_argument("--no-fetch", action="store_true", help="do not download the WHO COVID-19 file when it is missing")
    vc.set_defaults(fn=cmd_vn_calibrate)
    c = sub.add_parser("crosswalk", help="build the GADM -> IBGE / OCHA polygon crosswalks and the Meta mobility-range profile")
    c.add_argument("--pair", action="append", choices=["gadm_br_ibge", "gadm36_br_ibge", "gadm_vn2_cod", "gadm_vn1_cod"], help="default: every pair whose GADM layer is ingested")
    c.add_argument("--no-profile", action="store_true", help="skip the mobility-range profile")
    c.add_argument("--min-days", type=int, default=7, help="minimum days of Meta data per polygon")
    c.set_defaults(fn=cmd_crosswalk)
    n = sub.add_parser("nowcast", help="Brazil: nowcast the incomplete recent weeks of COVID-19 / influenza SARI (needs the SIVEP-Gripe files)")
    n.add_argument("--disease", choices=["covid19_sari", "influenza_sari"], default=None, help="default: both")
    n.add_argument("--asof", default=None, help="ISO date; the report week containing it (default: last fully ended week of data)")
    n.set_defaults(fn=cmd_nowcast)
    y = sub.add_parser("synth-sivep")
    y.add_argument("--rows", type=int, default=1_000_000)
    y.add_argument("--weeks", type=int, default=52)
    y.set_defaults(fn=cmd_synth)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
