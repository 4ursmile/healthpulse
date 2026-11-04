"""Ingestion job: discover -> download -> checksum -> parse -> schema-map -> validate -> silver -> publish -> refresh."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import polars as pl

from ..core import db, lake
from ..core.jobs import JobContext, JobWarning, handler
from ..core.util import dumps, iso, new_id, parse_dt, sha256_bytes, sha256_file, stamp, utcnow, write_json_atomic
from . import catalog, quality, silver
from .connectors import CONNECTORS

STAGES = ["discover", "download", "checksum", "parse", "schema-map", "validate", "silver", "publish", "refresh-dependent-features"]


def _latest_checksum(source_id: str) -> str | None:
    r = db.q1("SELECT checksum FROM source_snapshot WHERE source_id=? AND published=1 ORDER BY retrieved_at DESC LIMIT 1", source_id)
    return r["checksum"] if r else None


def _known_munis() -> set[str] | None:
    dim = silver.latest("municipality_dim")
    return set(dim["municipality_id"].to_list()) if dim.height else None


def _process(ctx: JobContext, src: dict, man: dict, *, synthetic: bool = False) -> dict:
    conn = CONNECTORS[src["source_id"]]
    ctx.stage("parse", 0.6)
    frames = conn.parse(ctx, src, man)
    geojson = frames.pop("_geojson", None)  # type: ignore[arg-type]
    # an optional table with nothing new (e.g. InfoDengue population once IBGE estimates exist) is skipped, not quarantined
    frames = {t: df for t, df in frames.items() if df.height or t not in getattr(conn, "optional_tables", ())}
    ctx.stage("schema-map", 0.7, tables=list(frames))
    ctx.stage("validate", 0.75)
    known = _known_munis()
    reports = {}
    for t, df in frames.items():
        prev = silver.latest(t) if silver.TABLES[t]["mode"] == "vintaged" else None
        rep = quality.check(t, df, known_munis=known, prev=prev)
        reports[t] = rep
        ctx.log(f"{t}: {rep['status']} ({rep['rows']:,} rows, {rep['hard_failures']} hard / {rep['warnings']} soft)",
                level="warning" if rep["status"] != "passed" else "info")
    ctx.stage("silver", 0.85)
    snaps = []
    for t, df in frames.items():
        rep = reports[t]
        sid = f"{src['source_id']}__{t}__{stamp()}_{new_id('v')[-6:]}"
        uri, digest = lake.silver_publish(t, src["source_id"], sid, df) if df.height else (None, None)
        cov_s = cov_e = None
        for c in ("week_start", "date"):
            if c in df.columns and df.height:
                cov_s, cov_e = str(df[c].min()), str(df[c].max())
        snaps.append({
            "snapshot_id": sid, "source_id": src["source_id"], "job_id": ctx.job_id, "bronze_uri": man["bronze_dir"],
            "silver_uri": uri, "checksum": man["sha256"], "retrieved_at": man["retrieved_at"],
            "data_available_at": man["available_at"], "coverage_start": cov_s, "coverage_end": cov_e,
            "schema_version": man.get("schema_version", "1.0"), "dq_status": rep["status"], "dq_report_json": dumps(rep),
            "row_count": df.height, "manifest_uri": f"{man['bronze_dir']}/manifest.json", "silver_table": t,
            "is_synthetic": 1 if synthetic else 0, "published": 0, "created_at": iso(utcnow()),
        })
    if geojson is not None:
        out = lake.get_settings().gold_dir / "map" / "municipalities.geojson"
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_text(dumps(geojson))
        tmp.replace(out)
        import gzip
        gz = out.with_suffix(".geojson.gz")
        with gzip.open(gz.with_suffix(".tmp"), "wb", compresslevel=9) as f:
            f.write(out.read_bytes())
        gz.with_suffix(".tmp").replace(gz)
        ctx.log(f"map geometry published ({out.stat().st_size / 1e6:.1f} MB)")
    ctx.stage("publish", 0.92)
    published, quarantined = [], []
    with db.tx() as c:
        for s in snaps:
            ok = s["dq_status"] != "quarantined" and s["silver_uri"]
            s["published"] = 1 if ok else 0
            db.insert("source_snapshot", s, c)
            (published if ok else quarantined).append(s["snapshot_id"])
        new_status = "quarantined" if quarantined and not published else "healthy"
        c.execute("UPDATE source_registry SET status=?, updated_at=? WHERE source_id=?", (new_status, iso(utcnow()), src["source_id"]))
    silver.clear_cache()
    ctx.stage("refresh-dependent-features", 0.97)
    deps = db.q("SELECT DISTINCT feature_set_id, version FROM feature_set WHERE status='published'")
    if deps:
        ctx.log(f"{len(deps)} feature set(s) can be re-materialized against the new vintage; no automatic retraining")
    result = {"source_id": src["source_id"], "snapshots_published": published, "snapshots_quarantined": quarantined,
              "dq": {t: {"status": r["status"], "rows": r["rows"]} for t, r in reports.items()},
              "bronze_dir": man["bronze_dir"], "checksum": man["sha256"], "dependent_feature_sets": len(deps)}
    if quarantined:
        raise JobWarning("quarantined" if not published else "succeeded_with_warnings", result)
    if any(r["status"] == "passed_with_warnings" for r in reports.values()):
        raise JobWarning("succeeded_with_warnings", result)
    return result


@handler("ingest")
def ingest(ctx: JobContext, payload: dict) -> dict:
    sid = payload["source_id"]
    src = catalog.get_source(sid)
    if not src or not src["enabled"]:
        raise RuntimeError(f"source {sid} is not registered or disabled")
    conn = CONNECTORS.get(sid)
    if conn is None:
        raise RuntimeError(f"no connector for {sid}")
    ctx.stage("discover", 0.02, connector=src["connector_type"])
    ctx.log(f"connector {sid} ({src['connector_type']}), params {payload.get('params') or {}}")
    ctx.stage("download", 0.05)
    payloads, extra = conn.collect(ctx, src, payload.get("params") or {})
    total = sum(b.stat().st_size if isinstance(b, Path) else len(b) for _, b in payloads)
    ctx.counts(files=len(payloads), bytes=total)
    ctx.stage("checksum", 0.55)
    combined = sha256_bytes("".join(sha256_file(b) if isinstance(b, Path) else sha256_bytes(b) for _, b in payloads).encode())
    if combined == _latest_checksum(sid) and not payload.get("force"):
        ctx.log("upstream bytes unchanged since the last published snapshot — no new vintage created")
        for _, b in payloads:
            if isinstance(b, Path):
                b.unlink(missing_ok=True)
        return {"source_id": sid, "unchanged": True, "checksum": combined}
    man = lake.bronze_write(sid, payloads, lake.manifest_base(sid, src.get("provenance_url"), license=src["license_ref"],
                                                               **extra))
    ctx.log(f"bronze snapshot {man['bronze_dir']} ({len(payloads)} files, {total / 1e6:.2f} MB)")
    return _process(ctx, src, man)


@handler("import_file")
def import_file(ctx: JobContext, payload: dict) -> dict:
    sid = payload["source_id"]
    src = catalog.get_source(sid)
    up = Path(payload["upload_path"])
    ctx.stage("discover", 0.02, file=up.name)
    ctx.stage("download", 0.05, bytes=up.stat().st_size)
    ctx.stage("checksum", 0.1)
    man = lake.bronze_write_file(sid, up, payload.get("filename", up.name),
                                 lake.manifest_base(sid, src.get("provenance_url"), license=src["license_ref"],
                                                    is_synthetic=bool(payload.get("synthetic"))))
    try:
        up.unlink()
    except OSError:
        pass
    ctx.log(f"file moved to bronze {man['bronze_dir']} (sha256 {man['sha256'][:12]}…)")
    return _process(ctx, src, man, synthetic=bool(payload.get("synthetic")))


@handler("probe")
def probe(ctx: JobContext, payload: dict) -> dict:
    sid = payload["source_id"]
    src = catalog.get_source(sid)
    ctx.stage("probe", 0.1)
    res = CONNECTORS[sid].probe(src)
    ctx.log(f"probe result: {'ok' if res.get('ok') else 'not ok'}")
    return {"source_id": sid, **res}


# ---------------------------------------------------------------- status read model
def source_status(src: dict) -> dict:
    snaps = db.q("SELECT snapshot_id, silver_table, retrieved_at, dq_status, row_count, published, coverage_start, coverage_end, "
                 "is_synthetic FROM source_snapshot WHERE source_id=? ORDER BY retrieved_at DESC", src["source_id"])
    good = [s for s in snaps if s["published"]]
    last_job = db.q1("SELECT job_id, status, finished_at, created_at, error FROM jobs WHERE target_id=? AND job_type IN ('ingest','import_file') "
                     "ORDER BY created_at DESC LIMIT 1", src["source_id"])
    jobs30 = db.q("SELECT status FROM jobs WHERE target_id=? AND job_type IN ('ingest','import_file') AND created_at >= ?",
                  src["source_id"], iso(utcnow() - timedelta(days=30)))
    done30 = [j for j in jobs30 if j["status"] not in ("queued", "running")]
    ok30 = [j for j in done30 if j["status"].startswith("succeeded")]
    state = src["status"]
    if not src["enabled"]:
        state = "disabled"
    elif state in ("healthy", "available") and good:
        last = parse_dt(good[0]["retrieved_at"])
        fd = src.get("freshness_days")
        state = "stale" if fd and (utcnow() - last).days > fd else "healthy"
        if last_job and last_job["status"] == "failed" and (parse_dt(last_job["created_at"]) or last) > last:
            state = "stale"
    return {
        "state": state, "last_success_at": good[0]["retrieved_at"] if good else None,
        "last_attempt": last_job, "snapshots": len(snaps), "published_snapshots": len(good),
        "coverage_start": min((s["coverage_start"] for s in good if s["coverage_start"]), default=None),
        "coverage_end": max((s["coverage_end"] for s in good if s["coverage_end"]), default=None),
        "rows_latest": sum(s["row_count"] or 0 for s in good if s["retrieved_at"] == good[0]["retrieved_at"]) if good else 0,
        "success_rate_30d": (len(ok30) / len(done30)) if done30 else None, "attempts_30d": len(done30),
        "tables": sorted({s["silver_table"] for s in good}),
        "is_synthetic_only": bool(good) and all(s["is_synthetic"] for s in good),
    }


@handler("synthetic_sivep")
def synthetic_sivep(ctx: JobContext, payload: dict) -> dict:
    """SYNTHETIC case-level SRAG file (load test of the million-row import path), then the normal import pipeline."""
    from datetime import date as _date
    from ..config import get_settings
    from .connectors import write_synthetic_sivep
    s = get_settings()
    munis = s.pilot
    start = _date.fromisoformat(payload.get("start", f"{utcnow().year - 1}-01-04"))
    ctx.stage("generate", 0.02, rows=payload["rows"])
    path = s.uploads_dir / f"{new_id('syn')}.csv"
    n = write_synthetic_sivep(path, int(payload["rows"]), munis, start, int(payload.get("weeks", 52)), int(payload.get("seed", 7)))
    ctx.log(f"generated {n:,} SYNTHETIC case rows ({path.stat().st_size / 1e6:.0f} MB) for {len(munis)} municipalities")
    return import_file(ctx, {"source_id": "sivep_gripe", "upload_path": str(path), "filename": "synthetic_srag.csv", "synthetic": True})
