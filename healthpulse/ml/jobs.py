"""Training/backtest worker job and batch inference job."""
from __future__ import annotations

import os
import platform
import sys
from datetime import date, timedelta

import joblib
import numpy as np
import polars as pl

from ..core import db, lake
from ..core.jobs import JobContext, handler
from ..core.util import dumps, epi_week_code, epi_week_start, iso, loads, new_id, parse_dt, sha256_file, stable_hash, utcnow, write_json_atomic
from ..features import compute, registry as freg
from ..features.jobs import dataset_frame, feature_columns, get_dataset
from ..sources import catalog, silver
from . import evaluate as ev
from .recipes import BASELINES, RECIPES, fit

DT = pl.Datetime("us", "UTC")


def _env() -> dict:
    import scipy
    import sklearn
    return {"python": sys.version.split()[0], "platform": platform.platform(), "polars": pl.__version__,
            "numpy": np.__version__, "scikit_learn": sklearn.__version__, "scipy": scipy.__version__,
            "code_version": "healthpulse 3.1.0"}


def _cut(d: str) -> pl.Expr:
    return pl.lit(date.fromisoformat(d)).cast(DT) + pl.duration(days=1)


def _predict_block(fitted, frame: pl.DataFrame) -> np.ndarray:
    return fitted.predict(frame)


def leaderboard(recipes: dict, horizons: list[int]) -> list[dict]:
    """Rank recipes like a forecasting hub: mean relative WIS vs persistence over horizons (lower is better)."""
    pers = recipes.get("persistence", {}).get("test", {})
    rows = []
    for rid, rr in recipes.items():
        t = rr.get("test", {})
        rel = [t[str(h)]["wis"] / pers[str(h)]["wis"] for h in horizons
               if t.get(str(h), {}).get("wis") and pers.get(str(h), {}).get("wis")]
        mae = [t[str(h)]["mae"] for h in horizons if t.get(str(h), {}).get("mae") is not None]
        cv = [f["mae"] for f in rr.get("folds", []) if f.get("mae") is not None]
        cov = [t[str(h)]["coverage80"] for h in horizons if t.get(str(h), {}).get("coverage80") is not None]
        rows.append({"recipe": rid, "label": RECIPES[rid]["label"], "family": RECIPES[rid]["family"],
                     "relative_wis": float(np.exp(np.mean(np.log(rel)))) if rel else None,
                     "mae": float(np.mean(mae)) if mae else None, "cv_mae": float(np.mean(cv)) if cv else None,
                     "coverage80": float(np.mean(cov)) if cov else None})
    rows.sort(key=lambda r: (r["relative_wis"] is None, r["relative_wis"] if r["relative_wis"] is not None else r["mae"] or 1e18))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


@handler("train")
def train(ctx: JobContext, p: dict) -> dict:
    run = db.q1("SELECT * FROM experiment_run WHERE run_id=?", p["run_id"])
    exp = db.jcol(db.q1("SELECT * FROM experiment WHERE experiment_id=?", run["experiment_id"]), "config_json")
    cfg = exp["config_json"]
    db.update("experiment_run", {"run_id": run["run_id"]}, {"status": "preparing", "job_id": ctx.job_id})
    from threadpoolctl import threadpool_limits
    max_cpu = int((cfg.get("resources") or {}).get("max_cpu", 2))
    with threadpool_limits(limits=max_cpu):
        return _train(ctx, run, exp, cfg)


def _train(ctx: JobContext, run: dict, exp: dict, cfg: dict) -> dict:
    run_id = run["run_id"]
    ctx.stage("preparing", 0.02)
    ds = get_dataset(exp["dataset_id"])
    frame = dataset_frame(ds["dataset_id"])
    feats = [f for f in feature_columns(ds["dataset_id"]) if f not in (cfg.get("exclude_features") or [])]
    split = ds["splits_json"]
    horizons = [h for h in ds["horizons_json"] if h in (cfg.get("horizons") or ds["horizons_json"])]
    recipes = cfg.get("recipes") or [{"recipe": "persistence"}, {"recipe": "seasonal_naive"}, {"recipe": "ensemble"}]
    for r in recipes:
        if r["recipe"] not in RECIPES:
            raise ValueError(f"recipe {r['recipe']} is not allowlisted")
    seed = int(cfg.get("seed", 42))
    holdout = set(split.get("geography_holdout") or [])
    labeled = frame.filter(pl.col("label").is_not_null())
    ctx.log(f"dataset {ds['dataset_id']} ({labeled.height:,} labeled rows, {len(feats)} features, {ds['pit_status']})")
    db.update("experiment_run", {"run_id": run_id}, {"status": "training"})
    preds = []
    total = len(recipes) * len(horizons) * (len(split["folds"]) + 1)
    done = 0
    oof: dict[tuple[str, int], tuple[list, list]] = {}
    thr = ev.thresholds(labeled.filter(pl.col("target_week").cast(DT) < _cut(split["test"]["train_label_cutoff"])))
    ctx.stage("training", 0.05, recipes=[r["recipe"] for r in recipes], horizons=horizons, folds=len(split["folds"]))
    for r in recipes:
        rid = r["recipe"]
        for h in horizons:
            hframe = labeled.filter(pl.col("horizon") == h)
            ys, yh = [], []
            for fold in split["folds"] + [dict(split["test"], fold="test", validation_start=split["test"]["start"],
                                               validation_end=split["test"]["end"])]:
                ctx.check_cancel()
                tr = hframe.filter((pl.col("label_release_approx_at") <= _cut(fold["train_label_cutoff"]))
                                   & ~pl.col("municipality_id").is_in(list(holdout)))
                va = hframe.filter((pl.col("issue_week") >= date.fromisoformat(fold["validation_start"]))
                                   & (pl.col("issue_week") <= date.fromisoformat(fold["validation_end"])))
                if holdout and fold["fold"] == "test":
                    va = va.filter(pl.col("municipality_id").is_in(list(holdout))) if cfg.get("evaluate_holdout_only") else va
                done += 1
                if tr.height < 20 or va.height == 0:
                    ctx.log(f"{rid} h{h} fold {fold['fold']}: skipped ({tr.height} train / {va.height} eval rows)", level="warning")
                    continue
                m = fit(rid, tr, feats, h, r.get("params"), seed)
                yhat = m.predict(va)
                y = va["label"].cast(pl.Float64).to_numpy()
                if fold["fold"] != "test":
                    ys.append(y)
                    yh.append(yhat)
                preds.append(va.select("municipality_id", "issue_week", "target_week", "horizon", "label").with_columns(
                    pl.lit(rid).alias("recipe"), pl.lit(str(fold["fold"])).alias("fold"), pl.Series("pred", yhat)))
                ctx.progress(0.05 + 0.6 * done / total)
            oof[(rid, h)] = (np.concatenate(ys) if ys else np.array([]), np.concatenate(yh) if yh else np.array([]))
        ctx.log(f"{rid}: {len(split['folds'])} folds + test evaluated for horizons {horizons}")
    if not preds:
        raise ValueError("no fold produced predictions — dataset too small for the declared split")
    P = pl.concat(preds)
    ctx.stage("evaluating", 0.7)
    db.update("experiment_run", {"run_id": run_id}, {"status": "evaluating"})
    nominal = float((cfg.get("calibration") or {}).get("nominal_coverage", 0.8))
    intervals, calib = {}, {}
    los, his = [], []
    for (rid, h), (y, yh) in oof.items():
        qlo, qhi = ev.interval_factors(y, yh, nominal)
        intervals[f"{rid}|{h}"] = [qlo, qhi]
        cal = []
        for nom in (0.5, 0.8, 0.9):
            a, b = ev.interval_factors(y, yh, nom)
            sel = P.filter((pl.col("recipe") == rid) & (pl.col("horizon") == h) & (pl.col("fold") == "test"))
            lo, hi = ev.apply_interval(sel["pred"].to_numpy(), a, b)
            yy = sel["label"].cast(pl.Float64).to_numpy()
            ok = np.isfinite(lo)
            cal.append({"nominal": nom, "empirical": float(np.mean((yy[ok] >= lo[ok]) & (yy[ok] <= hi[ok]))) if ok.any() else None})
        calib[f"{rid}|{h}"] = cal
    P = P.with_columns(pl.struct("recipe", "horizon").map_elements(lambda s: intervals.get(f"{s['recipe']}|{s['horizon']}", [np.nan, np.nan])[0],
                                                                   return_dtype=pl.Float64).alias("_qlo"),
                       pl.struct("recipe", "horizon").map_elements(lambda s: intervals.get(f"{s['recipe']}|{s['horizon']}", [np.nan, np.nan])[1],
                                                                   return_dtype=pl.Float64).alias("_qhi"))
    P = P.with_columns(((pl.col("pred") + 1) * pl.col("_qlo") - 1).clip(0).alias("p10"),
                       ((pl.col("pred") + 1) * pl.col("_qhi") - 1).clip(0).alias("p90")).drop("_qlo", "_qhi")
    wisf = {f"{rid}|{h}": ev.wis_factors(y, yh) for (rid, h), (y, yh) in oof.items()}
    wcol = np.full(P.height, np.nan)
    for rid, h, idx in P.with_row_index("_i").group_by(["recipe", "horizon"]).agg(pl.col("_i")).iter_rows():
        ii = np.asarray(idx)
        wcol[ii] = ev.wis(P["label"].cast(pl.Float64).to_numpy()[ii], P["pred"].to_numpy()[ii], wisf.get(f"{rid}|{h}"))
    P = P.with_columns(pl.Series("wis", wcol))
    P = P.with_columns(pl.col("municipality_id").replace_strict(thr, default=None, return_dtype=pl.Float64).alias("threshold"))
    res: dict = {"recipes": {}, "by_region": {}, "wis_factors": wisf, "split": split, "nominal_coverage": nominal, "pit_status": ds["pit_status"],
                 "intervals": intervals, "calibration": calib, "thresholds": {"rule": "per-municipality 90th percentile of training labels (provisional)",
                                                                               "values": thr}}

    def M(df):
        return ev.metrics(df["label"].cast(pl.Float64).to_numpy(), df["pred"].to_numpy(), df["p10"].to_numpy(), df["p90"].to_numpy(),
                          df["threshold"].to_numpy(), df["wis"].to_numpy())
    test_full = P.filter(pl.col("fold") == "test").join(labeled.select(["municipality_id", "issue_week", "horizon"] +
                                                                       [c for c in ("holiday_in_target_week", "incidence_lag_1w") if c in labeled.columns]),
                                                        on=["municipality_id", "issue_week", "horizon"], how="left")
    for rid in [r["recipe"] for r in recipes]:
        rr = {"folds": [], "test": {}, "test_slices": {}}
        for (fold, h), g in P.filter(pl.col("recipe") == rid).group_by(["fold", "horizon"], maintain_order=True):
            if fold == "test":
                rr["test"][str(h)] = M(g)
            else:
                rr["folds"].append({"fold": fold, "horizon": h, **M(g)})
        for h in horizons:
            g = test_full.filter((pl.col("recipe") == rid) & (pl.col("horizon") == h))
            rr["test_slices"][str(h)] = {name: M(g.filter(expr)) for name, expr in ev.slices(g).items()}
            res["by_region"].setdefault(rid, {})[str(h)] = {m: M(gg) for (m,), gg in g.group_by(["municipality_id"])}
        res["recipes"][rid] = rr
    comparison = {}
    for h in horizons:
        bl = [(rid, rr["test"][str(h)]) for rid, rr in res["recipes"].items() if rid in BASELINES and rr["test"].get(str(h), {}).get("mae")]
        bname, base = min(bl, key=lambda x: x[1]["mae"]) if bl else (None, None)
        row = {}
        for rid, rr in res["recipes"].items():
            t = rr["test"].get(str(h))
            if t and base:
                row[rid] = {"mae": t["mae"], "skill_vs_baseline": 1 - t["mae"] / base["mae"], "baseline": bname}
                pers = res["recipes"].get("persistence", {}).get("test", {}).get(str(h), {})
                if t.get("wis") and pers.get("wis"):
                    row[rid].update(wis=t["wis"], relative_wis=t["wis"] / pers["wis"])
        comparison[str(h)] = row
    res["comparison"] = comparison
    res["leaderboard"] = leaderboard(res["recipes"], horizons)
    # ablation on the test window (gradient boosting), groups limited to features that exist in this dataset
    fs = freg.get_feature_set(ds["feature_set_id"], ds["feature_set_version"])
    if cfg.get("ablation", True) and any(r["recipe"] == "hgb_poisson" for r in recipes):
        ctx.stage("ablation", 0.78)
        abl = {}
        for gname, members in (fs.get("ablation_groups") or {}).items():
            cols = [c for c in members if c in feats]
            missing = [c for c in members if c not in feats]
            if gname == "+mobility" and missing:
                abl[gname] = {"status": "unavailable", "reason": f"blocked features: {missing}"}
                continue
            out = {}
            for h in horizons:
                ctx.check_cancel()
                hframe = labeled.filter(pl.col("horizon") == h)
                tr = hframe.filter(pl.col("label_release_approx_at") <= _cut(split["test"]["train_label_cutoff"]))
                te = hframe.filter(pl.col("issue_week") >= date.fromisoformat(split["test"]["start"]))
                m = fit("hgb_poisson", tr, cols, h, next((r.get("params") for r in recipes if r["recipe"] == "hgb_poisson"), None), seed)
                out[str(h)] = ev.metrics(te["label"].cast(pl.Float64).to_numpy(), m.predict(te))
            abl[gname] = {"status": "ok", "features": cols, "test": out}
        res["ablation"] = abl
    ctx.stage("packaging", 0.85)
    db.update("experiment_run", {"run_id": run_id}, {"status": "packaging"})
    adir = lake.artifact_dir("training", run_id)
    models = {}
    for r in recipes:
        rid = r["recipe"]
        mdir = lake.artifact_dir("models", rid, run_id)
        for h in horizons:
            ctx.check_cancel()
            hframe = labeled.filter(pl.col("horizon") == h)
            m = fit(rid, hframe, feats, h, r.get("params"), seed)
            joblib.dump(m, mdir / f"h{h}.joblib")
        if rid == "hgb_poisson":
            try:
                from sklearn.inspection import permutation_importance
                te = labeled.filter((pl.col("horizon") == horizons[0]) & (pl.col("issue_week") >= date.fromisoformat(split["test"]["start"])))
                tr = labeled.filter((pl.col("horizon") == horizons[0]) & (pl.col("label_release_approx_at") <= _cut(split["test"]["train_label_cutoff"])))
                mm = fit(rid, tr, feats, horizons[0], r.get("params"), seed)
                pi = permutation_importance(mm.model, te.select(feats).to_numpy().astype(float), te["label"].cast(pl.Float64).to_numpy(),
                                            n_repeats=3, random_state=seed, scoring="neg_mean_absolute_error")
                res["importance"] = {"horizon": horizons[0], "method": "permutation (test MAE)",
                                     "caveat": "Importance is predictive association, not causal attribution.",
                                     "features": sorted([{"feature": f, "importance": float(v)} for f, v in zip(feats, pi.importances_mean)],
                                                        key=lambda x: -x["importance"])}
            except Exception as e:  # noqa: BLE001
                ctx.log(f"importance skipped: {e}", level="warning")
        sig = {"recipe": rid, "features": feats, "horizons": horizons, "target": exp["target_signal"],
               "intervals": {str(h): intervals.get(f"{rid}|{h}") for h in horizons}, "nominal_coverage": nominal,
               "params": r.get("params") or {}, "inference_contract_version": "1"}
        write_json_atomic(mdir / "signature.json", sig)
        digest = stable_hash({f.name: sha256_file(f) for f in sorted(mdir.glob("*.joblib"))})
        (mdir / "sha256.txt").write_text(digest)
        models[rid] = {"artifact_uri": lake.rel_artifact(mdir), "sha256": digest}
    fp = adir / "fold_predictions.parquet"
    lake.write_parquet_atomic(P, fp)
    write_json_atomic(adir / "metrics.json", res)
    write_json_atomic(adir / "config.json", {"experiment": exp, "dataset": {k: ds[k] for k in ("dataset_id", "hash", "feature_set_id",
                                                                                                "feature_set_version", "pit_status")},
                                             "features": feats, "env": _env()})
    write_json_atomic(adir / "pit_audit.json", ds["pit_report_json"])
    summary = {h: {rid: (res["recipes"][rid]["test"].get(h) or {}) for rid in res["recipes"]} for h in map(str, horizons)}
    db.update("experiment_run", {"run_id": run_id}, {
        "status": "completed", "metrics_json": dumps({"test": summary, "comparison": comparison, "leaderboard": res["leaderboard"]}),
        "report_uri": lake.rel_artifact(adir), "artifacts_json": dumps({"models": models, "fold_predictions": lake.rel_artifact(fp),
                                                                        "fold_predictions_sha256": sha256_file(fp)}),
        "env_json": dumps(_env()), "finished_at": iso(utcnow())})
    db.update("experiment", {"experiment_id": exp["experiment_id"]}, {"status": "completed", "updated_at": iso(utcnow())})
    ctx.log(f"run {run_id} completed; artifacts in {lake.rel_artifact(adir)}")
    _mlflow_log(run_id, cfg, summary, adir)
    return {"run_id": run_id, "comparison": comparison, "models": models}


def _mlflow_log(run_id: str, cfg: dict, summary: dict, adir) -> None:
    uri = os.environ.get("HP_MLFLOW_URI")
    if not uri:
        return
    try:
        import mlflow
        mlflow.set_tracking_uri(uri)
        mlflow.set_experiment("healthpulse")
        with mlflow.start_run(run_name=run_id) as r:
            mlflow.log_params({"recipes": ",".join(x["recipe"] for x in cfg.get("recipes", [])), "seed": cfg.get("seed", 42)})
            for h, rr in summary.items():
                for rid, m in rr.items():
                    if m.get("mae") is not None:
                        mlflow.log_metric(f"{rid}_h{h}_mae", m["mae"])
            mlflow.log_artifacts(str(adir))
            db.update("experiment_run", {"run_id": run_id}, {"mlflow_run_id": r.info.run_id})
    except Exception:  # noqa: BLE001 — tracking is optional; the built-in registry is authoritative
        pass


# ---------------------------------------------------------------- batch inference
def current_issue_week(now=None) -> date:
    return epi_week_start((now or utcnow()).date())


@handler("inference")
def inference(ctx: JobContext, p: dict) -> dict:
    from . import registry as mreg
    dep = db.q1("SELECT * FROM model_deployment WHERE deployment_id=?", p["deployment_id"])
    model = mreg.get_model(dep["model_id"], dep["version"])
    run = db.jcol(db.q1("SELECT * FROM experiment_run WHERE run_id=?", model["run_id"]), "metrics_json")
    ds = get_dataset(model["dataset_id"])
    adir = lake.abs_artifact(model["artifact_uri"])
    sig = loads((adir / "signature.json").read_text())
    if stable_hash({f.name: sha256_file(f) for f in sorted(adir.glob("*.joblib"))}) != model["hash"]:
        raise RuntimeError("model artifact checksum mismatch — refusing to serve")
    ctx.stage("features", 0.1)
    munis = p.get("municipalities") or ds["regions_json"]
    fs = freg.get_feature_set(ds["feature_set_id"], ds["feature_set_version"])
    defs = [{"feature_id": d["feature_id"], "template": d["template"], "params": d["params"]}
            for d in (freg.get_def(m["feature_id"], m["version"]) for m in fs["members"]) if d["feature_id"] in sig["features"]]
    if p.get("issue_week"):  # hindcast at a past issue week, publication-lag replay (PIT_UNVERIFIED)
        issue_week = date.fromisoformat(p["issue_week"])
        hindcast, issue_at, policy = True, None, "publication_lag"
    else:  # live: issued now, anchored to the latest week actually released by now (release-accurate vintages)
        issue_at = utcnow()
        cases = silver.as_of(compute.case_table(model["disease_id"]), issue_at, "vintage")
        cases = cases.filter((pl.col("disease_id") == model["disease_id"]) & pl.col("municipality_id").is_in(munis))
        if not cases.height:
            raise ValueError("no released case data to anchor the forecast")
        issue_week = cases["week_start"].max() + timedelta(weeks=1)
        hindcast, policy = False, "vintage"
        ctx.log(f"anchored to the latest released week {cases['week_start'].max()} (issue instant {iso(issue_at)})")
    frame, rep = compute.evaluate(defs, model["disease_id"], munis, issue_week, issue_week, sig["horizons"], policy=policy,
                                  issue_at=issue_at)
    ctx.stage("predict", 0.5)
    out = []
    for h in sig["horizons"]:
        m = joblib.load(adir / f"h{h}.joblib")
        fr = frame.filter(pl.col("horizon") == h)
        ok = fr["cases_lag_1w"].is_not_null().to_numpy() if "cases_lag_1w" in fr.columns else np.ones(fr.height, bool)
        yhat = m.predict(fr) if fr.height else np.array([])
        q = sig["intervals"].get(str(h)) or [np.nan, np.nan]
        lo, hi = ev.apply_interval(yhat, q[0], q[1])
        out.append(fr.select("municipality_id", "issue_week", "target_week", "horizon", "max_input_available_at", "pit_approx").with_columns(
            pl.Series("p50", np.where(ok, yhat, np.nan)), pl.Series("p10", np.where(ok, lo, np.nan)), pl.Series("p90", np.where(ok, hi, np.nan)),
            pl.Series("quality_status", np.where(ok, "ok", "insufficient_data"))))
    F = pl.concat(out).with_columns(pl.col(c).fill_nan(None) for c in ("p50", "p10", "p90"))
    target = catalog.TARGETS[model["target_signal"]]
    fid = f"fcst_{epi_week_code(issue_week)}_{model['disease_id']}_{new_id('x')[-8:]}"
    F = F.with_columns(pl.lit(fid).alias("forecast_id"), pl.lit(model["disease_id"]).alias("disease_id"),
                       pl.lit(target["signal_type"]).alias("signal_type"), pl.lit(f"{model['model_id']}@{model['version']}").alias("model_version"))
    ctx.stage("publish", 0.85)
    fdir = lake.artifact_dir("forecasts", fid)
    digest = lake.write_parquet_atomic(F, fdir / "prediction.parquet")
    lake.write_parquet_atomic(frame, fdir / "features.parquet")  # serving features, for training/serving parity and drift checks
    synth = any("synthetic" in v for v in rep["vintages"])
    # live inputs are release-accurate; the model's backtest evidence keeps the dataset's PIT status
    pit_q = "PIT_UNVERIFIED" if (hindcast or ds["pit_status"] != "PIT_VERIFIED") else "PIT_VERIFIED"
    prov = {"forecast_id": fid, "model_version": f"{model['model_id']}@{model['version']}", "recipe": model["recipe_id"],
            "training_run_id": model["run_id"], "training_dataset_id": model["dataset_id"], "feature_set_ref": model["feature_set_ref"],
            "feature_materialization_id": ds["materialization_id"], "source_vintage_ids": rep["vintages"],
            "forecast_issue_at": iso(frame["issue_at"].max()) if frame.height else None, "label_signal": target["signal_type"],
            "target": model["target_signal"], "quality_status": pit_q, "method": "hindcast" if hindcast else "batch_forecast",
            "is_synthetic": synth, "serving_alias": dep["serving_alias"], "deployment_id": dep["deployment_id"],
            "artifact_sha256": model["hash"], "nominal_coverage": sig.get("nominal_coverage")}
    write_json_atomic(fdir / "provenance.json", prov)
    db.insert("forecast", {"forecast_id": fid, "disease_id": model["disease_id"], "target_signal": model["target_signal"],
                           "model_id": model["model_id"], "model_version": model["version"], "deployment_id": dep["deployment_id"],
                           "serving_alias": dep["serving_alias"], "issue_at": prov["forecast_issue_at"] or iso(utcnow()),
                           "anchor_week": str(issue_week - timedelta(weeks=1)), "horizons_json": dumps(sig["horizons"]),
                           "parquet_uri": lake.rel_artifact(fdir / "prediction.parquet"), "sha256": digest, "row_count": F.height,
                           "provenance_json": dumps(prov), "quality_status": pit_q,
                           "is_public": 1 if (dep["serving_alias"] == "champion" and dep["status"] == "active" and not hindcast) else 0,
                           "is_synthetic": 1 if synth else 0, "job_id": ctx.job_id, "created_at": iso(utcnow())})
    n_alerts = 0
    if dep["serving_alias"] == "champion" and not hindcast:
        n_alerts = _draft_alerts(fid, model, run, F)
    ctx.log(f"forecast {fid}: {F.height} rows, {int((F['quality_status'] == 'insufficient_data').sum())} insufficient-data rows, "
            f"{n_alerts} draft alerts")
    return {"forecast_id": fid, "rows": F.height, "is_public": dep["serving_alias"] == "champion", "alerts_drafted": n_alerts}


def _draft_alerts(fid: str, model: dict, run: dict, F: pl.DataFrame) -> int:
    metrics = loads((lake.abs_artifact(run["report_uri"]) / "metrics.json").read_text())
    thr = (metrics.get("thresholds") or {}).get("values") or {}
    n = 0
    for r in F.filter(pl.col("quality_status") == "ok").iter_rows(named=True):
        t = thr.get(r["municipality_id"])
        if t is None or r["p50"] is None or r["p50"] <= t:
            continue
        aid = new_id("alr")
        db.insert("alert", {"alert_id": aid, "rule_id": "p50_above_p90_training", "disease_id": model["disease_id"],
                            "municipality_id": r["municipality_id"], "target_week": str(r["target_week"]), "forecast_id": fid,
                            "evidence_json": dumps({"p50": r["p50"], "p10": r["p10"], "p90": r["p90"], "threshold": t, "horizon": r["horizon"],
                                                    "rule": "forecast median above the municipality's training 90th percentile (provisional)"}),
                            "state": "draft", "created_at": iso(utcnow())})
        n += 1
    return n
