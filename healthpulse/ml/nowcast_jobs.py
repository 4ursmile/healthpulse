"""Nowcast job, immutable artefacts and the nowcast registry.

Release discipline (deliberately lighter than the forecasting registry, and why): a nowcast is a *correction of an observed
count for known right-truncation*, not a prognosis. It publishes no future value and drives no allocation by itself, so it does
not go through DRAFT -> REVIEWED -> APPROVED with two human sign-offs. It is held to three AUTOMATIC gates computed on a
hold-out half of the backtest (PIT audit passed, beats the raw count on WAPE, 80% interval coverage within [0.65, 0.92]).
A run that fails a gate is stored as `gate_failed` and is never served as "latest". Artefacts are immutable and hashed; a new
run never edits an old one. If the nowcast is later fed into forecasts or scenarios, it must move into the forecasting
registry with its dual sign-off.
"""
from __future__ import annotations

import os
import platform
import sys
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import polars as pl

from ..config import get_settings
from ..core import db, lake
from ..core.jobs import JobContext, handler
from ..core.util import dumps, epi_week_start, iso, loads, new_id, parse_dt, sha256_file, stable_hash, utcnow, write_json_atomic
from . import nowcast as nc

SOURCE_ID = "sivep_gripe"
METHOD_TEXT = ("Right-truncation nowcast: empirical onset-to-classified delay PMF from complete onset weeks (strict point in time), "
               "hierarchical Dirichlet shrinkage national -> state -> municipality, Negative-Binomial posterior for the cases still "
               "to arrive, mixture over Dirichlet draws of the PMF. See healthpulse/ml/nowcast.py.")
CONFIG_KEYS = set(nc.Config.__dataclass_fields__)


# ---------------------------------------------------------------- inputs
def latest_snapshot() -> dict | None:
    row = db.q1("SELECT * FROM source_snapshot WHERE source_id=? AND published=1 ORDER BY retrieved_at DESC LIMIT 1", SOURCE_ID)
    if not row or not row.get("manifest_uri"):
        return None
    man = loads((lake.data_path(row["manifest_uri"])).read_text())
    return {"snapshot_id": row["snapshot_id"], "manifest": man, "is_synthetic": bool(row["is_synthetic"]), "retrieved_at": row["retrieved_at"]}


def municipality_map(extra: list[str] | None = None) -> dict[str, str]:
    """6-digit residence code -> 7-digit municipality_id for the pilot (+ any requested municipalities)."""
    ids = list(get_settings().pilot or []) + list(extra or [])
    return {m[:6]: m for m in ids if m}


def live_week(last_available: date, asof: str | None) -> date:
    """Start of the as-of report week. With `asof` (any date) it is the week containing that date. By default it is the last
    report week that had fully ended by the latest date any record became available, so a half-finished week never counts."""
    if asof:
        return epi_week_start(date.fromisoformat(asof))
    wk = epi_week_start(last_available)
    return wk if last_available >= wk + timedelta(days=6) else wk - timedelta(days=7)


# ---------------------------------------------------------------- job
@handler("nowcast")
def nowcast_job(ctx: JobContext, p: dict) -> dict:
    cfg_in = {k: v for k, v in (p.get("config") or {}).items() if k in CONFIG_KEYS}
    cfg = nc.Config(**cfg_in)
    diseases = [d for d in (p.get("diseases") or list(nc.DISEASE_CLASS)) if d in nc.DISEASE_CLASS]
    if not diseases:
        raise ValueError("no nowcastable disease requested (covid19_sari, influenza_sari)")
    ctx.stage("inputs", 0.02)
    snap = None
    if p.get("paths"):                     # explicit files: used by tests and by `hp nowcast --file`
        paths = [Path(x) for x in p["paths"]]
        files = [{"name": x.name, "sha256": sha256_file(x)} for x in paths]
        snapshot_id, input_sha, synthetic = None, stable_hash(files), bool(p.get("synthetic"))
    else:
        snap = latest_snapshot()
        if not snap:
            raise RuntimeError("no SIVEP-Gripe snapshot ingested: run `hp ingest sivep_gripe`")
        man = snap["manifest"]
        paths = [lake.bronze_path(f["path"]) for f in man["files"]]
        files = [{"name": f["name"], "sha256": f["sha256"]} for f in man["files"]]
        snapshot_id, input_sha, synthetic = snap["snapshot_id"], man.get("sha256"), snap["is_synthetic"]
        if synthetic:
            raise RuntimeError("the ingested SIVEP-Gripe snapshot is SYNTHETIC and has no entry/closure dates; a nowcast needs the real OpenDataSUS files")
    munis = municipality_map(p.get("municipality_ids"))
    ctx.log(f"{len(paths)} file(s), {len(munis)} municipality series, diseases {diseases}, config {cfg.hash()}")
    ctx.check_cancel()

    ctx.stage("triangle", 0.10)
    long, tmeta = nc.build_triangle(paths, munis, p.get("delimiter", ";"))
    tri = nc.Triangle(long)
    ctx.log(f"triangle: {long.height:,} count cells, {tmeta['rows_used']:,} case rows reduced to counts; weeks {tmeta['first_onset_week']}..{tmeta['last_onset_week']}")
    last_av = date.fromisoformat(tmeta["last_availability_date"])
    asof_start = live_week(last_av, p.get("asof"))
    T_live = nc.week_index(asof_start, tri.origin)
    if T_live < cfg.max_delay + cfg.min_window:
        raise ValueError(f"as-of week {asof_start} leaves fewer than {cfg.min_window} complete onset weeks for the delay model")
    T_live = min(T_live, tri.n_weeks - 1)
    state_of = {m: nc.UF_ABBR[m[:2]] for m in munis.values()}

    ctx.stage("nowcast", 0.25, asof_week=str(tri.week(T_live)))
    live, bts = [], []
    for d in diseases:
        lay = nc.unit_layout(tri, d, state_of)
        live.append(nc.run_asof(tri, d, T_live, cfg, lay))
    live_df = pl.concat(live)

    ctx.stage("backtest", 0.45)
    for i, d in enumerate(diseases):
        ctx.check_cancel()
        lay = nc.unit_layout(tri, d, state_of)
        bts.append(nc.backtest(tri, d, cfg, lay, t_last=min(T_live, tri.n_weeks - 1 - cfg.max_delay)))
        ctx.progress(0.45 + 0.4 * (i + 1) / len(diseases), f"backtest {d} done")
    bt = nc.split_halves(pl.concat([b for b in bts if b.height])) if any(b.height for b in bts) else pl.DataFrame()

    ctx.stage("gates", 0.88)
    gate = nc.gates(bt) if bt.height else {"passed": False, "reason": "backtest produced no rows", "label": nc.LABEL, "pit_audit": True}
    metrics = _metrics(bt, gate)
    status = "published" if gate["passed"] else "gate_failed"
    ctx.log(f"gates {'passed' if gate['passed'] else 'FAILED'}: {gate}")

    ctx.stage("publish", 0.92)
    nid = new_id("nc")
    out = lake.artifact_dir("nowcast", nid)
    cutoff = tri.week(T_live) + timedelta(days=6)
    prov = {"nowcast_id": nid, "created_at": iso(utcnow()), "created_by": ctx.actor_id, "job_id": ctx.job_id, "label": nc.LABEL,
            "method": METHOD_TEXT, "config": cfg.__dict__ | {"hash": cfg.hash()}, "diseases": diseases,
            "asof_week": str(tri.week(T_live)), "asof_cutoff_date": str(cutoff), "snapshot_id": snapshot_id, "input_files": files,
            "input_sha256": input_sha, "is_synthetic": synthetic, "triangle": tmeta, "units": {"national": 1, "states": len(tri.units("state", diseases[0])),
                                                                                             "municipalities": sorted(munis.values())},
            "delay_definition": "weeks from onset week to the week the disease-specific record became available = max(DT_DIGITA, DT_ENCERRA)",
            "ignored": ["day-of-week entry effects", "reclassification after closure", "edits after first entry", f"delays beyond {cfg.max_delay} weeks",
                        "seasonal drift of the delay inside the window"],
            "viet_nam": "no nowcast: no line list with entry dates exists for Viet Nam",
            "gates": gate, "environment": {"python": sys.version.split()[0], "platform": platform.platform(), "polars": pl.__version__,
                                           "numpy": np.__version__, "code_version": "healthpulse 3.1.0"}}
    files_out = {}
    for name, df in (("nowcast.parquet", live_df), ("triangle.parquet", long)) + ((("backtest.parquet", bt),) if bt.height else ()):
        pth = out / name
        lake.write_parquet_atomic(df, pth)
        files_out[name] = sha256_file(pth)
    write_json_atomic(out / "metrics.json", metrics)
    files_out["metrics.json"] = sha256_file(out / "metrics.json")
    prov["outputs"] = files_out
    write_json_atomic(out / "provenance.json", prov)
    for f in out.iterdir():
        os.chmod(f, 0o444)
    db.insert("nowcast_run", {"nowcast_id": nid, "job_id": ctx.job_id, "created_by": ctx.actor_id, "created_at": prov["created_at"], "status": status,
                              "diseases_json": dumps(diseases), "asof_week": str(tri.week(T_live)), "asof_cutoff": str(cutoff),
                              "source_snapshot_id": snapshot_id, "input_sha256": input_sha, "config_json": dumps(cfg.__dict__), "config_hash": cfg.hash(),
                              "metrics_json": dumps(metrics["headline"]), "gates_json": dumps(gate), "artifact_uri": lake.rel_artifact(out),
                              "provenance_sha256": sha256_file(out / "provenance.json"), "is_synthetic": int(synthetic), "label": nc.LABEL})
    res = {"nowcast_id": nid, "status": status, "asof_week": str(tri.week(T_live)), "gates": gate, "headline": metrics["headline"]}
    if not gate["passed"]:
        from ..core.jobs import JobWarning
        raise JobWarning("succeeded_with_warnings", res)
    return res


def _metrics(bt: pl.DataFrame, gate: dict) -> dict:
    if not bt.height:
        return {"label": nc.LABEL, "gates": gate, "headline": {}, "by_level": [], "by_bucket": []}
    by_level = nc.summarise(bt, ["disease_id", "level", "period"])
    by_bucket = nc.summarise(bt.filter(pl.col("level").is_in(["national", "state"])), ["disease_id", "period", "bucket"])
    overall = nc.summarise(bt.filter(pl.col("level").is_in(["national", "state"]) & (pl.col("age_w") <= 8)), ["period"])
    head = {"holdout_wape": gate.get("holdout_wape"), "holdout_wape_naive": gate.get("holdout_wape_naive"), "holdout_cov80": gate.get("holdout_cov80"),
            "passed": gate.get("passed"), "label": nc.LABEL}
    rows = overall.filter(pl.col("period") == "holdout").to_dicts()
    if rows:
        head["holdout_skill_vs_naive"] = rows[0]["skill_vs_naive"]
        head["holdout_skill_vs_static_inflation"] = rows[0]["skill_vs_inflate"]
        head["holdout_cov95"] = rows[0]["cov95"]
    return {"label": nc.LABEL, "gates": gate, "headline": head, "overall": overall.to_dicts(), "by_level": by_level.to_dicts(),
            "by_bucket": by_bucket.to_dicts(), "as_of_weeks": {"first": str(bt["asof_week"].min()), "last": str(bt["asof_week"].max())},
            "note": "tuning = first half of as-of weeks (defaults were chosen there), holdout = second half; truth = counts in the snapshot "
                    f"for onset weeks at least {26} weeks before the end of the data; rows where the method declined (null) are excluded and reported as `withheld`."}


# ---------------------------------------------------------------- registry reads (artefacts are immutable, so caching is safe)
def _row(r: dict | None) -> dict | None:
    if not r:
        return None
    return db.jcol(r, "diseases_json", "config_json", "metrics_json", "gates_json")


def list_runs(disease_id: str | None = None, include_failed: bool = False) -> list[dict]:
    rows = db.q("SELECT * FROM nowcast_run " + ("" if include_failed else "WHERE status='published' ") + "ORDER BY created_at DESC LIMIT 100")
    out = [_row(r) for r in rows]
    return [r for r in out if not disease_id or disease_id in r["diseases_json"]]


def get_run(nid: str) -> dict | None:
    return _row(db.q1("SELECT * FROM nowcast_run WHERE nowcast_id=?", nid))


def latest_published(disease_id: str) -> dict | None:
    for r in list_runs(disease_id):
        return r
    return None


@lru_cache(maxsize=8)
def _live(nid: str) -> pl.DataFrame:
    r = db.q1("SELECT artifact_uri FROM nowcast_run WHERE nowcast_id=?", nid)
    return pl.read_parquet(lake.abs_artifact(r["artifact_uri"]) / "nowcast.parquet")


def series(nid: str, disease_id: str, state: str | None = None, municipality_id: str | None = None, weeks: int = 52) -> dict:
    """One unit's series. A municipality without its own series falls back to its state, and a state to the nation; `unit_level`
    says which was served so the UI never presents a coarser series as a finer one."""
    df = _live(nid).filter(pl.col("disease_id") == disease_id)
    want = []
    if municipality_id:
        want.append(("municipality", municipality_id))
    if state:
        want.append(("state", state.upper()))
    if municipality_id and not state:
        want.append(("state", nc.UF_ABBR.get(municipality_id[:2], "")))
    want.append(("national", "BR"))
    for level, unit in want:
        s = df.filter((pl.col("level") == level) & (pl.col("unit_id") == unit)).sort("week_start")
        if s.height:
            s = s.tail(weeks)
            return {"unit_level": level, "unit_id": unit, "requested": {"state": state, "municipality_id": municipality_id},
                    "fallback": (level, unit) != want[0], "rows": s.drop("level", "unit_id", "disease_id").to_dicts()}
    return {"unit_level": None, "unit_id": None, "rows": [], "fallback": False}


def backtest_metrics(nid: str) -> dict:
    r = db.q1("SELECT artifact_uri FROM nowcast_run WHERE nowcast_id=?", nid)
    return loads((lake.abs_artifact(r["artifact_uri"]) / "metrics.json").read_text())


def provenance(nid: str) -> dict:
    r = db.q1("SELECT artifact_uri FROM nowcast_run WHERE nowcast_id=?", nid)
    return loads((lake.abs_artifact(r["artifact_uri"]) / "provenance.json").read_text())


def enqueue(actor_id: str, diseases: list[str] | None = None, asof: str | None = None, config: dict | None = None, **extra) -> dict:
    from ..core import jobs
    cfg = nc.Config(**{k: v for k, v in (config or {}).items() if k in CONFIG_KEYS})
    snap = latest_snapshot()
    key = f"nowcast:{(snap or {}).get('manifest', {}).get('sha256')}:{asof}:{cfg.hash()}:{','.join(sorted(diseases or nc.DISEASE_CLASS))}"
    payload = {"diseases": diseases, "asof": asof, "config": config or {}, **extra}
    return jobs.enqueue("nowcast", payload, target_type="nowcast", target_id=None, actor_id=actor_id, timeout_s=3600,
                        idempotency_key=None if extra.get("paths") else key)
