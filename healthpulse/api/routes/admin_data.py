"""Admin: control center, data sources, ingestions, snapshots, catalog, quality, synthetic load fixtures."""
from __future__ import annotations

import shutil
from datetime import date, timedelta

import polars as pl
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from ...config import get_settings
from ...core import db, jobs
from ...core.security import Actor
from ...core.util import dumps, iso, loads, new_id, stable_hash, utcnow
from ...sources import catalog, silver
from ...sources.connectors import CONNECTORS
from ...sources.ingest import source_status
from ..deps import actor, do_audit, envelope, idem_key, page, require

router = APIRouter(prefix="/admin")

EDITABLE = {"enabled", "schedule_cron", "freshness_days", "publication_lag_days", "auth_secret_ref", "display_name", "owner_id"}
CONFIG_EDITABLE = {"start_year", "diseases", "rate_limit_s", "batch_size", "min_weekly_coverage", "start_date", "years_ahead",
                   "page_size", "max_pages_per_municipality", "delimiter", "simplify_tolerance_deg"}


def _src_public(s: dict) -> dict:
    s = dict(s)
    s["config"] = s.pop("config_json")
    s["auth_secret_ref"] = s.get("auth_secret_ref")  # a reference name only; values never leave the secrets store
    s["status_detail"] = source_status(s)
    s["connector_implemented"] = s["source_id"] in CONNECTORS and type(CONNECTORS[s["source_id"]]).__name__ != "Unavailable"
    return s


@router.get("/overview")
def control_center(request: Request, a: Actor = Depends(require("read"))):
    srcs = [_src_public(catalog.get_source(r["source_id"])) for r in db.q("SELECT source_id FROM source_registry ORDER BY source_group, source_id")]
    queued = db.q("SELECT job_id, job_type, target_id, status, stage, progress, created_at, created_by FROM jobs WHERE status IN ('queued','running') ORDER BY created_at DESC LIMIT 20")
    failed = db.q("SELECT job_id, job_type, target_id, status, error, finished_at FROM jobs WHERE status IN ('failed','timed_out','quarantined') ORDER BY finished_at DESC LIMIT 10")
    pending = db.q("SELECT model_id, version, recipe_id, disease_id, approval_state, created_at FROM model_registry WHERE approval_state IN ('CANDIDATE','REVIEWED') ORDER BY created_at DESC LIMIT 10")
    champions = []
    for d in db.q("SELECT * FROM model_deployment WHERE status='active' ORDER BY disease_id, serving_alias"):
        f = db.q1("SELECT forecast_id, created_at, anchor_week, quality_status FROM forecast WHERE deployment_id=? ORDER BY created_at DESC LIMIT 1", d["deployment_id"])
        champions.append({**d, "latest_forecast": f})
    mats = db.q("SELECT dataset_id, name, disease_id, target_signal, pit_status, row_count, created_at, status FROM training_dataset ORDER BY created_at DESC LIMIT 5")
    hb = db.q1("SELECT MAX(heartbeat_at) t FROM jobs")
    audits = db.q1("SELECT COUNT(*) n FROM audit_event WHERE ts >= ?", iso(utcnow() - timedelta(days=7)))["n"]
    stale_forecasts = [c for c in champions if c["latest_forecast"] and c["latest_forecast"]["created_at"] < iso(utcnow() - timedelta(days=8))]
    return envelope({"sources": srcs, "active_jobs": queued, "failed_jobs": failed, "pending_reviews": pending,
                     "deployments": champions, "stale_forecasts": stale_forecasts, "datasets": mats,
                     "worker": {"last_heartbeat": hb["t"] if hb else None,
                                "queue_depth": db.q1("SELECT COUNT(*) n FROM jobs WHERE status='queued'")["n"]},
                     "audit_events_7d": audits, "workspace": get_settings().workspace, "environment": get_settings().env}, request)


# ---------------------------------------------------------------- sources
@router.get("/sources")
def sources(request: Request, a: Actor = Depends(require("read"))):
    return envelope([_src_public(catalog.get_source(r["source_id"])) for r in db.q("SELECT source_id FROM source_registry ORDER BY source_group, source_id")], request)


@router.post("/sources", status_code=201)
def register_source(request: Request, body: dict, a: Actor = Depends(require("source.write"))):
    sid = (body.get("source_id") or "").strip()
    import re
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,48}", sid):
        raise HTTPException(400, "source_id must be a lowercase slug (immutable)")
    if catalog.get_source(sid):
        raise HTTPException(409, "source_id already registered")
    ctype = body.get("connector_type", "file_upload")
    if ctype not in ("http_json", "http_csv", "file_upload", "bulk_archive", "georaster"):
        raise HTTPException(400, "unsupported connector type")
    if any(k in (body.get("config") or {}) for k in ("password", "token", "api_key", "secret")):
        raise HTTPException(400, "plaintext credentials are refused; pass auth_secret_ref naming a stored secret")
    hosts = body.get("approved_hostnames") or []
    for h in hosts:
        if h not in get_settings().approved_hosts:
            raise HTTPException(400, f"host {h} is not on the platform allowlist (Settings → approved hosts)")
    now = iso(utcnow())
    db.insert("source_registry", {
        "source_id": sid, "display_name": body.get("display_name") or sid, "owner_id": a.user_id, "connector_type": ctype,
        "source_group": body.get("source_group", "other"), "status": "manual_import_required", "enabled": 1,
        "config_json": dumps(body.get("config") or {}), "config_version": 1, "auth_secret_ref": body.get("auth_secret_ref"),
        "pii_class": body.get("pii_class", "aggregated"), "license_ref": body.get("license_ref") or "verify-at-source",
        "provenance_url": body.get("provenance_url"), "expected_grain": body.get("expected_grain"),
        "expected_cadence": body.get("expected_cadence"), "release_class": body.get("release_class", "RELEASED_PERIODIC"),
        "approved_hostnames": dumps(hosts), "freshness_days": body.get("freshness_days"),
        "publication_lag_days": body.get("publication_lag_days"), "schedule_cron": None, "updated_at": now})
    do_audit(request, a, "source.register", "source", sid, new=1)
    return envelope(_src_public(catalog.get_source(sid)), request,
                    warnings=["Registered as manual_import_required: a reviewed connector module is needed before scheduled pulls."])


@router.get("/sources/{sid}")
def source_detail(request: Request, sid: str, a: Actor = Depends(require("read"))):
    s = catalog.get_source(sid)
    if not s:
        raise HTTPException(404)
    snaps = [db.jcol(r, "dq_report_json") for r in db.q("SELECT * FROM source_snapshot WHERE source_id=? ORDER BY retrieved_at DESC LIMIT 50", sid)]
    js, total = jobs.list_jobs(target_id=sid, limit=20)
    tables = sorted({x["silver_table"] for x in snaps})
    schema = {t: {k: str(v) for k, v in silver.SCHEMAS[t].items()} for t in tables if t in silver.SCHEMAS}
    return envelope({**_src_public(s), "snapshots": snaps, "jobs": js, "jobs_total": total, "schema": schema,
                     "keys": {t: silver.TABLES[t]["keys"] for t in tables if t in silver.TABLES}}, request)


@router.patch("/sources/{sid}")
def patch_source(request: Request, sid: str, body: dict, a: Actor = Depends(require("source.write"))):
    s = catalog.get_source(sid)
    if not s:
        raise HTTPException(404)
    if int(body.get("expected_version", -1)) != s["config_version"]:
        raise HTTPException(409, f"version conflict: current config_version is {s['config_version']}")
    vals = {k: v for k, v in body.items() if k in EDITABLE}
    cfg = dict(s["config_json"])
    for k, v in (body.get("config") or {}).items():
        if k not in CONFIG_EDITABLE:
            raise HTTPException(400, f"config key {k} is not editable from the UI")
        cfg[k] = v
    vals["config_json"] = dumps(cfg)
    vals["config_version"] = s["config_version"] + 1
    vals["updated_at"] = iso(utcnow())
    if "enabled" in vals:
        vals["enabled"] = 1 if vals["enabled"] else 0
    db.update("source_registry", {"source_id": sid}, vals)
    do_audit(request, a, "source.update", "source", sid, old=s["config_version"], new=vals["config_version"],
             reason=body.get("reason"), meta={"fields": sorted(set(vals) - {"config_json", "updated_at", "config_version"}) +
                                              [f"config.{k}" for k in (body.get("config") or {})]})
    return envelope(_src_public(catalog.get_source(sid)), request)


@router.post("/sources/{sid}/test", status_code=202)
def test_source(request: Request, sid: str, a: Actor = Depends(require("source.ingest"))):
    if not catalog.get_source(sid):
        raise HTTPException(404)
    j = jobs.enqueue("probe", {"source_id": sid}, target_type="source_probe", target_id=sid, actor_id=a.user_id, timeout_s=120, priority=1)
    do_audit(request, a, "source.test", "source", sid)
    return envelope({"job_id": j["job_id"], "status": j["status"]}, request)


@router.post("/sources/{sid}/preview")
def preview(request: Request, sid: str, body: dict | None = None, a: Actor = Depends(require("read"))):
    s = catalog.get_source(sid)
    if not s:
        raise HTTPException(404)
    snaps = db.q("SELECT DISTINCT silver_table FROM source_snapshot WHERE source_id=? AND published=1", sid)
    out = {}
    limit = min(int((body or {}).get("limit", 50)), 200)
    for t in [x["silver_table"] for x in snaps]:
        df = silver.latest(t)
        df = df.filter(pl.col("source_id") == sid) if "source_id" in df.columns else df
        cols = [c for c in df.columns if c not in ("_synthetic_snapshot",)]
        out[t] = {"rows": df.select(cols).head(limit).to_dicts(), "total": df.height,
                  "missingness": {c: round(df[c].null_count() / max(1, df.height), 4) for c in cols}}
    return envelope(out, request, meta={"note": "Normalized Silver columns only; raw restricted records are never previewed."})


@router.post("/sources/{sid}/ingest", status_code=202)
def ingest(request: Request, sid: str, body: dict | None = None, a: Actor = Depends(require("source.ingest")), key: str | None = Depends(idem_key)):
    s = catalog.get_source(sid)
    if not s:
        raise HTTPException(404)
    if not s["enabled"]:
        raise HTTPException(409, "source is disabled")
    if s["status"] in ("needs_authorization", "manual_import_required") and s["connector_type"] != "file_upload":
        raise HTTPException(409, f"source is {s['status']}: no data is fetched or substituted")
    if s["connector_type"] == "file_upload":
        raise HTTPException(409, "file-import source: use the upload action")
    params = (body or {}).get("params") or {}
    if params.get("municipalities") and len(params["municipalities"]) > 6000:
        raise HTTPException(400, "too many municipalities in one request")
    idem = key or f"ingest:{sid}:{s['config_version']}:{stable_hash(params)}:{date.today().isoformat()}"
    j = jobs.enqueue("ingest", {"source_id": sid, "params": params, "force": bool((body or {}).get("force"))}, target_type="source",
                     target_id=sid, actor_id=a.user_id, idempotency_key=idem, timeout_s=3600)
    do_audit(request, a, "source.ingest", "source", sid, meta={"params": params, "job_id": j["job_id"]})
    return envelope({"job_id": j["job_id"], "status": j["status"], "deduplicated": j["created_by"] != a.user_id or j["status"] != "queued"}, request)


@router.post("/sources/{sid}/upload", status_code=202)
async def upload(request: Request, sid: str, file: UploadFile = File(...), a: Actor = Depends(require("source.ingest"))):
    s = catalog.get_source(sid)
    if not s or s["connector_type"] != "file_upload":
        raise HTTPException(400, "this source does not accept uploads")
    name = (file.filename or "upload.csv").replace("/", "_").replace("\\", "_")
    if not name.lower().endswith((".csv", ".txt")):
        raise HTTPException(415, "only CSV uploads are accepted")
    dst = get_settings().uploads_dir / f"{new_id('up')}.csv"
    size, limit = 0, 4 * 1024 ** 3
    with open(dst, "wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                f.close()
                dst.unlink(missing_ok=True)
                raise HTTPException(413, "file exceeds 4 GB")
            if size == len(chunk) and chunk[:15].lower().startswith((b"<!doctype", b"<html")):
                f.close()
                dst.unlink(missing_ok=True)
                raise HTTPException(415, "HTML is not a CSV")
            f.write(chunk)
    j = jobs.enqueue("import_file", {"source_id": sid, "upload_path": str(dst), "filename": name}, target_type="source", target_id=sid,
                     actor_id=a.user_id, timeout_s=3600)
    do_audit(request, a, "source.upload", "source", sid, meta={"bytes": size, "job_id": j["job_id"]})
    return envelope({"job_id": j["job_id"], "bytes": size}, request)


@router.post("/synthetic/sivep", status_code=202)
def synth_sivep(request: Request, body: dict, a: Actor = Depends(require("source.ingest"))):
    rows = int(body.get("rows", 1_000_000))
    if not 1000 <= rows <= 50_000_000:
        raise HTTPException(400, "rows must be between 1,000 and 50,000,000")
    j = jobs.enqueue("synthetic_sivep", {"rows": rows, "weeks": int(body.get("weeks", 52)), "seed": int(body.get("seed", 7))},
                     target_type="source", target_id="sivep_gripe", actor_id=a.user_id, timeout_s=3600)
    do_audit(request, a, "fixture.sivep", "source", "sivep_gripe", meta={"rows": rows})
    return envelope({"job_id": j["job_id"]}, request, warnings=["SYNTHETIC fixture for load testing; flagged everywhere it appears."])


@router.get("/sources/{sid}/snapshots")
def snapshots(request: Request, sid: str, a: Actor = Depends(require("read"))):
    return envelope([db.jcol(r, "dq_report_json") for r in db.q("SELECT * FROM source_snapshot WHERE source_id=? ORDER BY retrieved_at DESC", sid)], request)


@router.post("/snapshots/{snap_id}/quarantine")
def quarantine(request: Request, snap_id: str, body: dict, a: Actor = Depends(require("source.write"))):
    s = db.q1("SELECT * FROM source_snapshot WHERE snapshot_id=?", snap_id)
    if not s:
        raise HTTPException(404)
    if not (body.get("reason") or "").strip():
        raise HTTPException(422, "a reason is required")
    db.update("source_snapshot", {"snapshot_id": snap_id}, {"published": 0, "dq_status": "quarantined"})
    silver.clear_cache()
    do_audit(request, a, "snapshot.quarantine", "snapshot", snap_id, reason=body["reason"])
    return envelope({"snapshot_id": snap_id, "published": False}, request,
                    warnings=["Readers now fall back to the previous published vintage; published forecasts are unchanged."])


@router.post("/snapshots/{snap_id}/release")
def release(request: Request, snap_id: str, body: dict, a: Actor = Depends(require("admin.settings"))):
    s = db.jcol(db.q1("SELECT * FROM source_snapshot WHERE snapshot_id=?", snap_id), "dq_report_json")
    if not s:
        raise HTTPException(404)
    if not s["silver_uri"]:
        raise HTTPException(409, "snapshot has no Silver data")
    if not (body.get("reason") or "").strip():
        raise HTTPException(422, "a reason is required to override a quarantine")
    hard = [g for g in (s["dq_report_json"] or {}).get("gates", []) if g["severity"] == "hard" and not g["passed"]]
    if any(g["gate"] in ("unique_natural_key", "ibge_code_present", "municipality_id_format") for g in hard):
        raise HTTPException(409, f"hard gate {hard[0]['gate']} cannot be overridden (it would duplicate or mis-join counts)")
    db.update("source_snapshot", {"snapshot_id": snap_id}, {"published": 1, "dq_status": "released_by_override"})
    silver.clear_cache()
    do_audit(request, a, "snapshot.release_override", "snapshot", snap_id, reason=body["reason"])
    return envelope({"snapshot_id": snap_id, "published": True}, request)


# ---------------------------------------------------------------- ingestions
@router.get("/ingestions")
def ingestions(request: Request, status: str | None = None, source_id: str | None = None, job_type: str = "ingest,import_file,probe,synthetic_sivep",
               limit: int = 50, offset: int = 0, a: Actor = Depends(require("read"))):
    limit, offset = page(limit, offset)
    rows, total = jobs.list_jobs(job_type=job_type, status=status, target_id=source_id, limit=limit, offset=offset)
    for r in rows:
        r.pop("payload", None)
    return envelope(rows, request, meta={"total": total, "limit": limit, "offset": offset})


@router.get("/ingestions/{job_id}")
def ingestion(request: Request, job_id: str, a: Actor = Depends(require("read"))):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404)
    j["payload"] = {k: v for k, v in (j.get("payload") or {}).items() if k != "upload_path"}
    snaps = db.q("SELECT snapshot_id, silver_table, dq_status, row_count, published FROM source_snapshot WHERE job_id=?", job_id)
    retries = db.q("SELECT job_id, status, attempt_no, created_at FROM jobs WHERE retry_of=? ORDER BY created_at", job_id)
    return envelope({**j, "logs": jobs.logs(job_id), "snapshots": snaps, "retries": retries}, request)


@router.post("/ingestions/{job_id}/retry", status_code=202)
def retry(request: Request, job_id: str, a: Actor = Depends(require("source.ingest"))):
    j = db.q1("SELECT * FROM jobs WHERE job_id=?", job_id)
    if not j:
        raise HTTPException(404)
    if j["status"] in ("queued", "running"):
        raise HTTPException(409, "job is still active")
    if j["job_type"] == "import_file":
        raise HTTPException(409, "upload jobs cannot be retried: the file was consumed; upload it again")
    nj = jobs.enqueue(j["job_type"], loads(j["payload_json"]), target_type=j["target_type"], target_id=j["target_id"],
                      actor_id=a.user_id, retry_of=job_id, timeout_s=j["timeout_s"])
    do_audit(request, a, "ingestion.retry", "job", nj["job_id"], meta={"retry_of": job_id})
    return envelope({"job_id": nj["job_id"], "attempt_no": nj["attempt_no"], "retry_of": job_id}, request)


# ---------------------------------------------------------------- catalog & quality
@router.get("/catalog")
def data_catalog(request: Request, a: Actor = Depends(require("read"))):
    tables = []
    for t, meta in silver.TABLES.items():
        snaps = silver.snapshots(t)
        latest = silver.latest(t) if snaps else None
        tables.append({"table": t, "layer": "silver", "grain": meta["grain"], "keys": meta["keys"], "mode": meta["mode"],
                       "schema": {k: str(v) for k, v in silver.SCHEMAS[t].items()}, "vintages": len(snaps),
                       "rows_current": latest.height if latest is not None else 0,
                       "sources": sorted({s["source_id"] for s in snaps}),
                       "privacy": "aggregated (no personal rows)" if t != "facility" else "public establishments",
                       "last_published": snaps[-1]["created_at"] if snaps else None})
    gold = {"feature_sets": db.q("SELECT feature_set_id, version, disease_id, status, created_at FROM feature_set ORDER BY created_at DESC"),
            "training_datasets": db.q("SELECT dataset_id, name, feature_set_id, feature_set_version, pit_status, row_count, created_at FROM training_dataset ORDER BY created_at DESC"),
            "forecasts": db.q("SELECT forecast_id, disease_id, model_id, model_version, is_public, quality_status, created_at FROM forecast ORDER BY created_at DESC LIMIT 20")}
    return envelope({"silver": tables, "gold": gold}, request)


@router.get("/lineage")
def lineage(request: Request, forecast_id: str | None = None, run_id: str | None = None, a: Actor = Depends(require("read"))):
    """Trace a simulation or forecast back through model, dataset, feature set and source vintages."""
    from ...simulation import jobs as sim
    nodes, edges = [], []

    def node(i, kind, label, **kw):
        if not any(n["id"] == i for n in nodes):
            nodes.append({"id": i, "kind": kind, "label": label, **kw})
    if run_id:
        r = sim.get_run(run_id)
        if not r:
            raise HTTPException(404)
        node(run_id, "simulation", r["scenario_id"], status=r["status"], result_sha256=r.get("result_sha256"))
        forecast_id = r["forecast_id"]
        if forecast_id:
            edges.append([forecast_id, run_id])
    if not forecast_id:
        raise HTTPException(400, "forecast_id or run_id required")
    f = db.jcol(db.q1("SELECT * FROM forecast WHERE forecast_id=?", forecast_id), "provenance_json")
    if not f:
        raise HTTPException(404)
    p = f["provenance_json"]
    node(forecast_id, "forecast", forecast_id, quality=f["quality_status"], public=bool(f["is_public"]), sha256=f["sha256"])
    mv = p["model_version"]
    node(mv, "model", mv, recipe=p.get("recipe"), sha256=p.get("artifact_sha256"))
    edges.append([mv, forecast_id])
    node(p["training_run_id"], "training_run", p["training_run_id"])
    edges.append([p["training_run_id"], mv])
    ds = db.q1("SELECT dataset_id, pit_status, hash, feature_set_id, feature_set_version, materialization_id FROM training_dataset WHERE dataset_id=?", p["training_dataset_id"])
    node(ds["dataset_id"], "training_dataset", ds["dataset_id"], pit_status=ds["pit_status"], sha256=ds["hash"])
    edges.append([ds["dataset_id"], p["training_run_id"]])
    fs = f"{ds['feature_set_id']}@{ds['feature_set_version']}"
    node(fs, "feature_set", fs)
    edges.append([fs, ds["dataset_id"]])
    for v in p.get("source_vintage_ids") or []:
        s = db.q1("SELECT source_id, silver_table, retrieved_at, checksum, dq_status FROM source_snapshot WHERE snapshot_id=?", v)
        node(v, "source_vintage", s["source_id"] if s else v, table=s and s["silver_table"], retrieved_at=s and s["retrieved_at"],
             checksum=s and s["checksum"], dq=s and s["dq_status"])
        edges.append([v, fs])
        edges.append([v, forecast_id])
    return envelope({"nodes": nodes, "edges": edges}, request)


@router.get("/quality")
def quality(request: Request, a: Actor = Depends(require("read"))):
    snaps = [db.jcol(r, "dq_report_json") for r in db.q("SELECT snapshot_id, source_id, silver_table, retrieved_at, dq_status, dq_report_json, row_count, published "
                                                         "FROM source_snapshot ORDER BY retrieved_at DESC LIMIT 200")]
    incidents = []
    for s in snaps:
        for g in (s["dq_report_json"] or {}).get("gates", []):
            if not g["passed"]:
                incidents.append({"snapshot_id": s["snapshot_id"], "source_id": s["source_id"], "table": s["silver_table"],
                                  "retrieved_at": s["retrieved_at"], "gate": g["gate"], "severity": g["severity"], "detail": g["detail"],
                                  "published": bool(s["published"])})
    stale = []
    for r in db.q("SELECT source_id FROM source_registry"):
        s = _src_public(catalog.get_source(r["source_id"]))
        if s["status_detail"]["state"] in ("stale", "quarantined"):
            stale.append({"source_id": s["source_id"], "state": s["status_detail"]["state"], "last_success_at": s["status_detail"]["last_success_at"]})
    return envelope({"incidents": incidents, "stale_sources": stale, "snapshots": snaps[:50]}, request)
