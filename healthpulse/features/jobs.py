"""Materialization and training-dataset jobs (immutable outputs, PIT audited)."""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from ..config import get_settings
from ..core import db, lake
from ..core.jobs import JobContext, JobWarning, handler
from ..core.util import dumps, iso, new_id, parse_dt, sha256_file, stable_hash, utcnow, write_json_atomic
from ..sources import catalog, silver
from . import compute, pit, registry

LABEL_LAG_DAYS = {"disease_weekly": 3, "sari_weekly": 14}


def default_split(issue_weeks: list[date], horizons: list[int], folds: int = 4, validation_weeks: int = 26,
                  test_weeks: int = 26, embargo_weeks: int | None = None) -> dict:
    emb = embargo_weeks if embargo_weeks is not None else max(horizons) + 1
    ws = sorted(issue_weeks)
    test_start = ws[-test_weeks] if len(ws) > test_weeks else ws[len(ws) // 2]
    fold_list = []
    end = test_start
    for k in range(folds):
        v_start = end - timedelta(weeks=validation_weeks)
        fold_list.append({"fold": folds - k, "validation_start": str(v_start), "validation_end": str(end - timedelta(days=1)),
                          "train_label_cutoff": str(v_start - timedelta(weeks=emb))})
        end = v_start
    fold_list.reverse()
    return {"kind": "rolling_origin", "embargo_weeks": emb, "validation_weeks": validation_weeks, "folds": fold_list,
            "test": {"start": str(test_start), "end": str(ws[-1]), "train_label_cutoff": str(test_start - timedelta(weeks=emb))},
            "rule": "train rows: target_week + label publication lag <= split cutoff; evaluation rows: issue_week inside window"}


@handler("dataset_build")
def build_dataset(ctx: JobContext, p: dict) -> dict:
    s = get_settings()
    fs = registry.get_feature_set(p["feature_set_id"], p.get("feature_set_version"))
    if not fs:
        raise ValueError("unknown feature set")
    target = catalog.TARGETS[p["target"]]
    disease = target["disease_id"]
    if fs["disease_id"] != disease:
        raise ValueError(f"feature set is for {fs['disease_id']}, target is {disease}: mixed signals are refused")
    ctx.stage("readiness", 0.02)
    rd = registry.readiness(fs)
    excluded = list(p.get("exclude_features") or [])
    if rd["unsafe"]:
        unsafe = [f for f in rd["unsafe"] if f not in excluded]
        if unsafe:
            raise pit.LeakageError(f"feature set contains features that are not PIT-validated or approved: {unsafe}")
    defs = []
    for m in fs["members"]:
        if m["feature_id"] in excluded or m["feature_id"] in rd["blocked"]:
            continue
        d = registry.get_def(m["feature_id"], m["version"])
        defs.append({"feature_id": d["feature_id"], "template": d["template"], "params": d["params"], "group": d["feature_group"]})
    warnings = []
    if rd["blocked"]:
        warnings.append(f"features blocked by missing sources and excluded (reduced model): {rd['blocked']}")
        ctx.log(warnings[-1], level="warning")
    munis = [str(m).zfill(7) for m in (p.get("municipalities") or s.pilot)]
    horizons = [int(h) for h in p.get("horizons", [1, 2, 3, 4])]
    cases = silver.latest(compute.case_table(disease)).filter(pl.col("disease_id") == disease)
    if not cases.height:
        raise ValueError(f"no published {disease} data")
    first, last = cases["week_start"].min(), cases["week_start"].max()
    issue_start = date.fromisoformat(p["issue_start"]) if p.get("issue_start") else first + timedelta(weeks=60)
    issue_end = date.fromisoformat(p["issue_end"]) if p.get("issue_end") else last + timedelta(weeks=1)
    issue_start = issue_start - timedelta(days=(issue_start.weekday() + 1) % 7)
    label_cutoff = parse_dt(p.get("label_cutoff")) or utcnow()
    policy = p.get("pit_policy", "publication_lag")
    ctx.stage("materialize", 0.1, features=len(defs), municipalities=len(munis))
    done = {"n": 0}

    def tick(name):
        done["n"] += 1
        ctx.check_cancel()
        ctx.progress(0.1 + 0.6 * done["n"] / max(1, len(defs)), f"feature {name}")
    frame, rep = compute.evaluate(defs, disease, munis, issue_start, issue_end, horizons, policy=policy, on_feature=tick)
    ctx.stage("labels", 0.75)
    table = compute.case_table(disease)
    tcol = target["column"]
    lab = silver.all_vintages(table).filter((pl.col("disease_id") == disease) & pl.col("municipality_id").is_in(munis))
    cands = pit.candidates(lab, ["municipality_id"], "week_start", [tcol], policy)
    sp = frame.select("municipality_id", "issue_week", "issue_at", "horizon", "target_week").with_columns(
        pl.lit(label_cutoff).cast(pit.DT).alias("_cut"))
    lj = pit.lookup(sp.rename({"issue_at": "_ia"}).rename({"_cut": "issue_at"}), cands, keys=["municipality_id"],
                    spine_event="target_week", event_col="week_start", value_cols=[tcol])
    lj = lj.rename({"issue_at": "label_cutoff", "_ia": "issue_at", tcol: "label", "known_at": "label_known_at", "approx": "label_approx"})
    frame = pit.safe_join(frame, lj.select("municipality_id", "issue_at", "horizon", "label", "label_known_at", "label_approx"),
                          on=["municipality_id", "issue_at", "horizon"], what="labels")
    assert frame.filter(pl.col("target_week") <= pl.col("anchor_week")).height == 0, "label week must follow the anchor week"
    pit.assert_unique(frame, ["municipality_id", "issue_week", "horizon"], "training rows")
    labeled = frame.filter(pl.col("label").is_not_null())
    if labeled.height == 0:
        raise ValueError("zero labeled rows — training is blocked")
    lag = LABEL_LAG_DAYS.get(table, 7)
    frame = frame.with_columns((pl.col("target_week").cast(pit.DT) + pl.duration(days=7 + lag)).alias("label_release_approx_at"))
    ctx.stage("splits", 0.85)
    weeks = sorted(set(labeled["issue_week"].to_list()))
    sp_cfg = p.get("split") or {}
    split = default_split(weeks, horizons, folds=int(sp_cfg.get("folds", 4)), validation_weeks=int(sp_cfg.get("validation_weeks", 26)),
                          test_weeks=int(sp_cfg.get("test_weeks", 26)), embargo_weeks=sp_cfg.get("embargo_weeks"))
    split["geography_holdout"] = p.get("geography_holdout") or []
    approx_rows = int(frame["pit_approx"].sum())
    pit_status = "PIT_VERIFIED" if approx_rows == 0 and policy == "vintage" else "PIT_UNVERIFIED"
    ctx.stage("write", 0.9)
    dsid = p.get("dataset_id") or new_id("ds")
    out = s.gold_dir / "training_sets" / dsid / "train.parquet"
    digest = lake.write_parquet_atomic(frame, out)
    feat_cols = [d["feature_id"] for d in defs if rep["features"].get(d["feature_id"], {}).get("status") == "ok"]
    pit_report = {
        "policy": policy, "pit_status": pit_status, "rows": frame.height, "labeled_rows": labeled.height,
        "approx_feature_rows": approx_rows, "max_input_available_at_le_issue_at": True,
        "label_after_anchor": True, "unique_keys": ["municipality_id", "issue_week", "horizon"],
        "features": rep["features"], "source_vintages": rep["vintages"], "sources": rep["sources"], "warnings": warnings,
        "note": ("Historical source vintages were not archived before this pilot; first-release times are approximated by "
                 "documented publication lags. Prospective (release-accurate) performance is not claimed.") if pit_status != "PIT_VERIFIED" else None,
    }
    mat_id = new_id("mat")
    db.insert("feature_materialization", {
        "materialization_id": mat_id, "feature_set_id": fs["feature_set_id"], "feature_set_version": fs["version"],
        "source_vintages_json": dumps(rep["vintages"]), "issue_range_json": dumps({"start": str(issue_start), "end": str(issue_end)}),
        "parquet_uri": str(out.relative_to(s.data_dir)), "sha256": digest, "row_count": frame.height, "dq_status": "ready",
        "pit_report_json": dumps(pit_report), "coverage_json": dumps({"municipalities": munis, "horizons": horizons}),
        "job_id": ctx.job_id, "created_at": iso(utcnow())})
    db.insert("training_dataset", {
        "dataset_id": dsid, "name": p.get("name") or f"{disease} {p['target']} {issue_start}–{issue_end}", "disease_id": disease,
        "target_signal": p["target"], "feature_set_id": fs["feature_set_id"], "feature_set_version": fs["version"],
        "materialization_id": mat_id, "horizons_json": dumps(horizons),
        "issue_range_json": dumps({"start": str(issue_start), "end": str(issue_end)}), "label_cutoff": iso(label_cutoff),
        "splits_json": dumps(split), "label_vintages_json": dumps(sorted(set(lab["vintage_id"].to_list()))), "pit_status": pit_status,
        "pit_report_json": dumps(pit_report), "parquet_uri": str(out.relative_to(s.data_dir)), "hash": digest,
        "row_count": frame.height, "regions_json": dumps(munis), "status": "ready", "job_id": ctx.job_id,
        "created_by": ctx.actor_id, "created_at": iso(utcnow())})
    if fs["status"] == "draft":
        db.update("feature_set", {"feature_set_id": fs["feature_set_id"], "version": fs["version"]}, {"status": "published"})
    write_json_atomic(out.parent / "manifest.json", {"dataset_id": dsid, "feature_set": f"{fs['feature_set_id']}@{fs['version']}",
                                                     "feature_columns": feat_cols, "split": split, "pit": pit_report, "sha256": digest})
    ctx.log(f"dataset {dsid}: {frame.height:,} rows ({labeled.height:,} labeled), {len(feat_cols)} features, {pit_status}")
    result = {"dataset_id": dsid, "rows": frame.height, "labeled_rows": labeled.height, "pit_status": pit_status,
              "feature_columns": feat_cols, "materialization_id": mat_id, "warnings": warnings}
    if warnings:
        raise JobWarning("succeeded_with_warnings", result)
    return result


def get_dataset(dsid: str) -> dict | None:
    r = db.q1("SELECT * FROM training_dataset WHERE dataset_id=?", dsid)
    return db.jcol(r, "horizons_json", "issue_range_json", "splits_json", "label_vintages_json", "pit_report_json", "regions_json")


def dataset_frame(dsid: str) -> pl.DataFrame:
    d = get_dataset(dsid)
    return pl.read_parquet(lake.data_path(d["parquet_uri"]))


def feature_columns(dsid: str) -> list[str]:
    d = get_dataset(dsid)
    return [k for k, v in d["pit_report_json"]["features"].items() if v.get("status") == "ok"]
