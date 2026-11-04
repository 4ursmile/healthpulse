"""Admin: feature store, datasets, experiments, training runs, registry, deployments, monitoring, audit, settings."""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Request

from ...config import get_settings
from ...core import db, jobs, lake
from ...core.security import DEMO_USERS, PERMISSIONS, ROLES, Actor
from ...core.util import dumps, iso, loads, utcnow
from ...features import compute
from ...features import registry as freg
from ...features.jobs import get_dataset
from ...ml import registry as mreg
from ...ml.recipes import RECIPES
from ...simulation import jobs as sim
from ...sources import catalog, silver
from ..deps import do_audit, envelope, idem_key, page, remember, replay, require

router = APIRouter(prefix="/admin")


# ---------------------------------------------------------------- features
@router.get("/feature-templates")
def templates(request: Request, a: Actor = Depends(require("read"))):
    return envelope({k: {"level": v["level"], "params": v["params"]} for k, v in compute.TEMPLATES.items()}, request,
                    meta={"tables": list(silver.TABLES), "publication_lag_days": compute.TABLE_LAG_DAYS, "groups": freg.GROUPS})


@router.get("/features")
def features(request: Request, q: str | None = None, group: str | None = None, state: str | None = None, a: Actor = Depends(require("read"))):
    return envelope(freg.list_defs(q, group, state), request)


@router.post("/features", status_code=201)
def create_feature(request: Request, body: dict, a: Actor = Depends(require("feature.write"))):
    import re
    fid = (body.get("feature_id") or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,60}", fid):
        raise HTTPException(400, "feature_id must be a lowercase slug")
    if body.get("template") not in compute.TEMPLATES:
        raise HTTPException(400, f"template must be one of {list(compute.TEMPLATES)}")
    allowed = set(compute.TEMPLATES[body["template"]]["params"])
    params = body.get("params") or {}
    if set(params) - allowed:
        raise HTTPException(400, f"unknown parameters {sorted(set(params) - allowed)}")
    if params.get("table") and params["table"] not in ("cases", *silver.TABLES):
        raise HTTPException(400, "unknown table")
    if params.get("column") and params.get("table") not in (None, "cases") and params["column"] not in silver.SCHEMAS.get(params["table"], {}):
        raise HTTPException(400, "unknown column for the table")
    if body.get("group") not in freg.GROUPS:
        raise HTTPException(400, f"group must be one of {freg.GROUPS}")
    cur = freg.get_def(fid)
    v = (cur["version"] + 1) if cur else 1
    if cur and cur["state"] == "draft":
        raise HTTPException(409, "a draft version exists; edit it instead")
    row = freg.definition_row({"feature_id": fid, "group": body["group"], "template": body["template"], "params": params,
                               "unit": body.get("unit"), "description": body.get("description") or fid}, v, "draft", a.user_id)
    db.insert("feature_definition", row)
    do_audit(request, a, "feature.create", "feature", fid, new=v)
    return envelope(freg.get_def(fid, v), request)


@router.get("/features/{fid}/versions/{v}")
def feature_version(request: Request, fid: str, v: int, a: Actor = Depends(require("read"))):
    d = freg.get_def(fid, v)
    if not d:
        raise HTTPException(404)
    d["referenced_by"] = freg.referenced_by(fid)
    return envelope(d, request)


@router.patch("/features/{fid}/versions/{v}")
def patch_feature(request: Request, fid: str, v: int, body: dict, a: Actor = Depends(require("feature.write"))):
    d = freg.get_def(fid, v)
    if not d:
        raise HTTPException(404)
    if d["state"] != "draft":
        raise HTTPException(409, "approved versions are immutable; create a new version")
    params = body.get("params", d["params"])
    row = freg.definition_row({"feature_id": fid, "group": body.get("group", d["feature_group"]), "template": d["template"],
                               "params": params, "unit": body.get("unit", d["unit"]), "description": body.get("description", d["description"])},
                              v, "draft", d["owner_id"])
    row.pop("created_at")
    db.update("feature_definition", {"feature_id": fid, "version": v}, {k: row[k] for k in ("params_json", "description", "unit", "feature_group",
                                                                                           "hash", "availability_json", "source_refs", "updated_at")} |
              {"leakage_status": "unchecked", "validation_json": None})
    do_audit(request, a, "feature.update", "feature", fid, new=v)
    return envelope(freg.get_def(fid, v), request)


@router.post("/features/{fid}/validate")
def validate_feature(request: Request, fid: str, body: dict | None = None, a: Actor = Depends(require("read"))):
    d = freg.get_def(fid, (body or {}).get("version"))
    if not d:
        raise HTTPException(404)
    res = freg.validate_def(d, (body or {}).get("disease_id", "dengue"))
    if a.can("feature.write"):
        freg.record_validation(fid, d["version"], res)
    return envelope(res, request)


@router.post("/features/{fid}/approve")
def approve_feature(request: Request, fid: str, body: dict, a: Actor = Depends(require("feature.write"))):
    d = freg.get_def(fid, body.get("version"))
    if not d:
        raise HTTPException(404)
    if d["leakage_status"] != "pass":
        raise HTTPException(409, f"validation status is {d['leakage_status']}; only PIT-validated definitions can be approved")
    db.update("feature_definition", {"feature_id": fid, "version": d["version"]}, {"state": "approved", "updated_at": iso(utcnow())})
    do_audit(request, a, "feature.approve", "feature", fid, new=d["version"], reason=body.get("reason"))
    return envelope(freg.get_def(fid, d["version"]), request)


@router.post("/features/{fid}/preview")
def preview_feature(request: Request, fid: str, body: dict | None = None, a: Actor = Depends(require("read"))):
    d = freg.get_def(fid, (body or {}).get("version"))
    if not d:
        raise HTTPException(404)
    disease = (body or {}).get("disease_id", "dengue")
    cases = silver.latest(compute.case_table(disease))
    if not cases.height:
        raise HTTPException(409, "no case data")
    last = cases["week_start"].max()
    try:
        frame, rep = compute.evaluate([{"feature_id": fid, "template": d["template"], "params": d["params"]}], disease,
                                      get_settings().pilot, last - timedelta(weeks=52), last, [1])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, str(e))
    if rep["features"][fid]["status"] != "ok":
        return envelope({"rows": [], "report": rep["features"][fid]}, request)
    vals = frame[fid].drop_nulls().to_numpy()
    hist = np.histogram(vals, bins=20) if len(vals) else ([], [])
    return envelope({"rows": frame.select("municipality_id", "issue_week", "issue_at", fid, "max_input_available_at", "pit_approx")
                     .sort("issue_week", descending=True).head(200).to_dicts(), "report": rep["features"][fid],
                     "histogram": {"counts": [int(x) for x in hist[0]], "edges": [float(x) for x in hist[1]]},
                     "coverage": frame.group_by("municipality_id").agg(pl.col(fid).is_not_null().mean().alias("non_null")).to_dicts()}, request,
                    meta={"rows_cap": 200, "privacy": "aggregated municipality-week values only"})


# ---------------------------------------------------------------- feature sets
@router.get("/feature-sets")
def feature_sets(request: Request, a: Actor = Depends(require("read"))):
    rows = db.q("SELECT feature_set_id, version FROM feature_set ORDER BY feature_set_id, version DESC")
    out = []
    for r in rows:
        fs = freg.get_feature_set(r["feature_set_id"], r["version"])
        fs["readiness"] = freg.readiness(fs)
        out.append(fs)
    return envelope(out, request)


@router.post("/feature-sets", status_code=201)
def create_feature_set(request: Request, body: dict, a: Actor = Depends(require("feature.write"))):
    if body.get("disease_id") not in catalog.DISEASES:
        raise HTTPException(400, "unknown disease")
    try:
        fs = freg.create_feature_set(body["feature_set_id"], body["disease_id"], body.get("members") or [], body.get("description", ""), a.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    do_audit(request, a, "feature_set.create", "feature_set", fs["feature_set_id"], new=fs["version"])
    fs["readiness"] = freg.readiness(fs)
    return envelope(fs, request)


@router.get("/feature-sets/{fsid}")
def feature_set(request: Request, fsid: str, version: int | None = None, a: Actor = Depends(require("read"))):
    fs = freg.get_feature_set(fsid, version)
    if not fs:
        raise HTTPException(404)
    fs["readiness"] = freg.readiness(fs)
    fs["datasets"] = db.q("SELECT dataset_id, name, target_signal, pit_status, row_count, created_at FROM training_dataset WHERE feature_set_id=? AND feature_set_version=?",
                          fsid, fs["version"])
    return envelope(fs, request)


@router.get("/feature-sets/{fsid}/lineage")
def feature_set_lineage(request: Request, fsid: str, version: int | None = None, a: Actor = Depends(require("read"))):
    fs = freg.get_feature_set(fsid, version)
    if not fs:
        raise HTTPException(404)
    nodes, edges = [{"id": f"{fsid}@{fs['version']}", "kind": "feature_set"}], []
    for m in fs["members"]:
        d = freg.get_def(m["feature_id"], m["version"])
        nid = f"{m['feature_id']}@{m['version']}"
        nodes.append({"id": nid, "kind": "feature", "template": d["template"], "status": d["leakage_status"]})
        edges.append([nid, f"{fsid}@{fs['version']}"])
        for t in d["source_refs"]:
            t2 = compute.case_table(fs["disease_id"]) if t == "cases" else t
            if not any(n["id"] == t2 for n in nodes):
                srcs = sorted({s["source_id"] for s in silver.snapshots(t2)}) if t2 in silver.TABLES else []
                nodes.append({"id": t2, "kind": "silver_table", "sources": srcs})
                for s in srcs:
                    if not any(n["id"] == s for n in nodes):
                        nodes.append({"id": s, "kind": "source"})
                    edges.append([s, t2])
            edges.append([t2, nid])
    return envelope({"nodes": nodes, "edges": edges}, request)


# ---------------------------------------------------------------- training datasets
@router.get("/training-datasets")
def datasets(request: Request, a: Actor = Depends(require("read"))):
    rows = db.q("SELECT dataset_id FROM training_dataset ORDER BY created_at DESC")
    return envelope([get_dataset(r["dataset_id"]) for r in rows], request)


@router.post("/training-datasets", status_code=202)
def build_dataset(request: Request, body: dict, a: Actor = Depends(require("dataset.build")), key: str | None = Depends(idem_key)):
    if body.get("target") not in catalog.TARGETS:
        raise HTTPException(400, "unknown target")
    if not freg.get_feature_set(body.get("feature_set_id", ""), body.get("feature_set_version")):
        raise HTTPException(404, "unknown feature set")
    j = jobs.enqueue("dataset_build", body, target_type="training_dataset", target_id=body.get("feature_set_id"), actor_id=a.user_id,
                     idempotency_key=f"ds:{a.user_id}:{key}" if key else None, timeout_s=1800)
    do_audit(request, a, "dataset.build", "feature_set", body["feature_set_id"], meta={"job_id": j["job_id"], "target": body["target"]})
    return envelope({"job_id": j["job_id"]}, request)


@router.get("/training-datasets/{dsid}")
def dataset(request: Request, dsid: str, a: Actor = Depends(require("read"))):
    d = get_dataset(dsid)
    if not d:
        raise HTTPException(404)
    df = pl.read_parquet(lake.data_path(d["parquet_uri"]))
    d["label_summary"] = (df.group_by("horizon").agg(pl.col("label").is_not_null().sum().alias("labeled"), pl.len().alias("rows"),
                                                     pl.col("label").mean().alias("mean"), pl.col("label").max().alias("max"))
                          .sort("horizon").to_dicts())
    d["issue_week_counts"] = (df.group_by(pl.col("issue_week").dt.year().alias("year")).agg(pl.len().alias("rows")).sort("year").to_dicts())
    return envelope(d, request)


# ---------------------------------------------------------------- experiments & runs
@router.get("/model-recipes")
def recipes(request: Request, a: Actor = Depends(require("read"))):
    return envelope(RECIPES, request, meta={"bounds": {"max_cpu": 8, "timeout_minutes": 120}})


@router.get("/experiments")
def experiments(request: Request, a: Actor = Depends(require("read"))):
    rows = db.q("SELECT * FROM experiment ORDER BY created_at DESC LIMIT 200")
    out = []
    for r in rows:
        r = db.jcol(r, "config_json", "validation_json")
        r["runs"] = db.q("SELECT run_id, status, created_at, finished_at FROM experiment_run WHERE experiment_id=? ORDER BY created_at DESC", r["experiment_id"])
        for x in r["runs"]:
            rr = mreg.get_run(x["run_id"])
            x["status"] = rr["status"]
            x["progress"] = (rr.get("job") or {}).get("progress")
        out.append(r)
    return envelope(out, request)


@router.post("/experiments", status_code=201)
def create_experiment(request: Request, body: dict, a: Actor = Depends(require("experiment.run"))):
    try:
        e = mreg.create_experiment(body, a.user_id)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    do_audit(request, a, "experiment.create", "experiment", e["experiment_id"])
    return envelope(e, request)


@router.get("/experiments/compare")
def compare(request: Request, ids: str, a: Actor = Depends(require("read"))):
    runs = [mreg.get_run(i) for i in ids.split(",") if i]
    if any(r is None for r in runs):
        raise HTTPException(404)
    reps = {r["run_id"]: mreg.run_report(r["run_id"]) for r in runs}
    ds = {r["run_id"]: get_dataset(r["dataset_id"]) for r in runs}
    warnings = []
    splits = {stable(d["splits_json"]) for d in ds.values()}
    if len(splits) > 1:
        warnings.append("Runs use different splits/folds: superiority cannot be claimed from these numbers.")
    if len({d["target_signal"] for d in ds.values()}) > 1:
        warnings.append("Runs predict different target signals.")
    return envelope({"runs": [{"run_id": rid, "dataset_id": ds[rid]["dataset_id"], "pit_status": ds[rid]["pit_status"],
                               "test": {k: v["test"] for k, v in (rep or {}).get("recipes", {}).items()}} for rid, rep in reps.items()],
                     "matched_folds": len(splits) == 1}, request, warnings=warnings)


def stable(x) -> str:
    return dumps(x)


@router.get("/experiments/{eid}")
def experiment(request: Request, eid: str, a: Actor = Depends(require("read"))):
    e = mreg.get_experiment(eid)
    if not e:
        raise HTTPException(404)
    e["runs"] = [mreg.get_run(r["run_id"]) for r in db.q("SELECT run_id FROM experiment_run WHERE experiment_id=? ORDER BY created_at DESC", eid)]
    e["dataset"] = get_dataset(e["dataset_id"])
    return envelope(e, request)


@router.post("/experiments/{eid}/validate")
def validate_experiment(request: Request, eid: str, a: Actor = Depends(require("experiment.run"))):
    if not mreg.get_experiment(eid):
        raise HTTPException(404)
    return envelope(mreg.validate_experiment(eid), request)


@router.post("/experiments/{eid}/run", status_code=202)
def run_experiment(request: Request, eid: str, a: Actor = Depends(require("experiment.run")), key: str | None = Depends(idem_key)):
    prev = replay(key, a, f"exp:{eid}")
    if prev:
        return envelope(prev, request)
    if not mreg.get_experiment(eid):
        raise HTTPException(404)
    active = db.q1("SELECT COUNT(*) n FROM experiment_run r JOIN jobs j ON j.job_id=r.job_id WHERE r.created_by=? AND j.status IN ('queued','running')", a.user_id)["n"]
    if active >= 2:
        raise HTTPException(429, "per-user concurrency limit: 2 active training runs")
    try:
        out = mreg.start_run(eid, a.user_id)
    except mreg.GateError as e:
        raise HTTPException(422, str(e))
    remember(key, a, f"exp:{eid}", out)
    do_audit(request, a, "experiment.run", "training_run", out["run_id"], meta={"experiment_id": eid})
    return envelope(out, request)


@router.get("/training-runs/{rid}")
def training_run(request: Request, rid: str, a: Actor = Depends(require("read"))):
    r = mreg.get_run(rid)
    if not r:
        raise HTTPException(404)
    r["logs"] = jobs.logs(r["job_id"]) if r.get("job_id") else []
    return envelope(r, request)


@router.get("/training-runs/{rid}/report")
def training_report(request: Request, rid: str, a: Actor = Depends(require("read"))):
    rep = mreg.run_report(rid)
    if not rep:
        raise HTTPException(404, "report not available (run not completed)")
    rep.pop("thresholds", None)
    return envelope(rep, request)


@router.get("/training-runs/{rid}/predictions")
def training_predictions(request: Request, rid: str, recipe: str | None = None, horizon: int | None = None,
                         municipality_id: str | None = None, fold: str | None = None, a: Actor = Depends(require("read"))):
    r = mreg.get_run(rid)
    if not r or not r.get("artifacts_json"):
        raise HTTPException(404)
    P = pl.read_parquet(lake.abs_artifact(r["artifacts_json"]["fold_predictions"]))
    for col, v in (("recipe", recipe), ("horizon", horizon), ("municipality_id", municipality_id), ("fold", fold)):
        if v is not None:
            P = P.filter(pl.col(col) == v)
    return envelope(P.drop("threshold").sort("target_week").head(5000).to_dicts(), request)


@router.post("/training-runs/{rid}/cancel")
def cancel_run(request: Request, rid: str, a: Actor = Depends(require("experiment.run"))):
    r = mreg.get_run(rid)
    if not r:
        raise HTTPException(404)
    jobs.request_cancel(r["job_id"])
    do_audit(request, a, "training.cancel", "training_run", rid)
    return envelope(mreg.get_run(rid), request)


# ---------------------------------------------------------------- registry & deployments
@router.get("/registry/models")
def models(request: Request, a: Actor = Depends(require("read"))):
    return envelope(mreg.list_models(), request)


@router.post("/registry/models", status_code=201)
def register(request: Request, body: dict, a: Actor = Depends(require("model.propose"))):
    try:
        m = mreg.register(body["run_id"], body["recipe"], a.user_id)
    except (mreg.GateError, KeyError) as e:
        raise HTTPException(422, str(e))
    do_audit(request, a, "model.register", "model", m["model_id"], new=m["version"])
    return envelope(m, request)


@router.get("/registry/{model_id}/{version}")
def model(request: Request, model_id: str, version: int, a: Actor = Depends(require("read"))):
    m = mreg.get_model(model_id, version)
    if not m:
        raise HTTPException(404)
    return envelope(m, request)


@router.post("/registry/{model_id}/{version}/review")
def review(request: Request, model_id: str, version: int, body: dict, a: Actor = Depends(require("read"))):
    kind = body.get("kind")
    perm = {"clinical": "model.review.clinical", "platform": "model.review.platform"}.get(kind)
    if not perm:
        raise HTTPException(400, "kind must be clinical or platform")
    if not a.can(perm):
        raise HTTPException(403, f"role {a.role} cannot give a {kind} sign-off")
    m = mreg.get_model(model_id, version)
    if m and m["created_by"] == a.user_id:
        raise HTTPException(403, "the model's author cannot review it")
    try:
        m = mreg.review(model_id, version, kind, body.get("decision"), body.get("notes", ""), a.user_id, a.role)
    except (mreg.GateError, ValueError) as e:
        raise HTTPException(422, str(e))
    except KeyError:
        raise HTTPException(404)
    do_audit(request, a, f"model.review.{kind}.{body.get('decision')}", "model", model_id, new=version, reason=body.get("notes"))
    return envelope(m, request)


@router.get("/deployments")
def deployments(request: Request, a: Actor = Depends(require("read"))):
    rows = mreg.deployments()
    for r in rows:
        r["forecasts"] = db.q("SELECT forecast_id, created_at, anchor_week, is_public, quality_status FROM forecast WHERE deployment_id=? ORDER BY created_at DESC LIMIT 5",
                              r["deployment_id"])
    return envelope(rows, request)


@router.post("/deployments", status_code=201)
def deploy(request: Request, body: dict, a: Actor = Depends(require("model.deploy"))):
    try:
        out = mreg.deploy(body["model_id"], int(body["version"]), body.get("alias", "shadow"), a.user_id)
    except mreg.GateError as e:
        raise HTTPException(422, str(e))
    except KeyError:
        raise HTTPException(404)
    do_audit(request, a, f"model.deploy.{body.get('alias', 'shadow')}", "model", body["model_id"], new=body["version"],
             old=out["previous_deployment_id"], reason=body.get("reason"))
    return envelope(out, request)


@router.post("/deployments/{did}/promote")
def promote(request: Request, did: str, body: dict | None = None, a: Actor = Depends(require("model.deploy"))):
    d = db.q1("SELECT * FROM model_deployment WHERE deployment_id=?", did)
    if not d or d["serving_alias"] != "shadow":
        raise HTTPException(400, "only an active shadow deployment can be promoted")
    try:
        out = mreg.deploy(d["model_id"], d["version"], "champion", a.user_id)
    except mreg.GateError as e:
        raise HTTPException(422, str(e))
    do_audit(request, a, "model.promote", "deployment", did, new=out["deployment"]["deployment_id"], reason=(body or {}).get("reason"))
    return envelope(out, request)


@router.post("/deployments/rollback")
def rollback(request: Request, body: dict, a: Actor = Depends(require("model.deploy"))):
    if not (body.get("reason") or "").strip():
        raise HTTPException(422, "a rollback reason is required")
    try:
        out = mreg.rollback(body["disease_id"], body["target"], a.user_id)
    except mreg.GateError as e:
        raise HTTPException(409, str(e))
    do_audit(request, a, "model.rollback", "deployment", out["rolled_back"], new=out["deployment_id"], reason=body["reason"])
    return envelope(out, request, warnings=["Earlier published forecasts are immutable and unchanged."])


@router.post("/deployments/{did}/infer", status_code=202)
def infer(request: Request, did: str, body: dict | None = None, a: Actor = Depends(require("model.infer"))):
    d = db.q1("SELECT * FROM model_deployment WHERE deployment_id=? AND status='active'", did)
    if not d:
        raise HTTPException(404, "no active deployment")
    j = jobs.enqueue("inference", {"deployment_id": did, **({"issue_week": body["issue_week"]} if (body or {}).get("issue_week") else {})},
                     target_type="deployment", target_id=did, actor_id=a.user_id)
    do_audit(request, a, "model.infer", "deployment", did, meta={"job_id": j["job_id"]})
    return envelope({"job_id": j["job_id"]}, request)


@router.get("/forecasts")
def forecasts(request: Request, disease_id: str | None = None, a: Actor = Depends(require("read"))):
    rows = db.q("SELECT * FROM forecast WHERE (? IS NULL OR disease_id=?) ORDER BY created_at DESC LIMIT 100", disease_id, disease_id)
    return envelope([db.jcol(r, "provenance_json", "horizons_json") for r in rows], request)


# ---------------------------------------------------------------- monitoring
def _psi(a: np.ndarray, b: np.ndarray, bins: int = 10) -> float | None:
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    if len(a) < 20 or len(b) < 3:
        return None
    edges = np.unique(np.quantile(a, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return None
    pa = np.histogram(a, edges)[0] / len(a) + 1e-4
    pb = np.histogram(np.clip(b, edges[0], edges[-1]), edges)[0] / len(b) + 1e-4
    return float(np.sum((pb - pa) * np.log(pb / pa)))


@router.get("/model-monitoring")
def monitoring(request: Request, disease_id: str = "dengue", a: Actor = Depends(require("read"))):
    out = []
    for dep in db.q("SELECT * FROM model_deployment WHERE disease_id=? AND status='active'", disease_id):
        m = mreg.get_model(dep["model_id"], dep["version"])
        fcs = db.q("SELECT forecast_id, parquet_uri, created_at FROM forecast WHERE model_id=? AND model_version=? ORDER BY created_at",
                   dep["model_id"], dep["version"])
        obs = silver.latest(compute.case_table(disease_id)).filter(pl.col("disease_id") == disease_id).select(
            "municipality_id", pl.col("week_start").alias("target_week"), pl.col("observed_cases").alias("observed"))
        realized = []
        frames = []
        for f in fcs:
            F = pl.read_parquet(lake.abs_artifact(f["parquet_uri"]))
            frames.append(F)
            j = F.join(obs, on=["municipality_id", "target_week"], how="inner").filter(pl.col("p50").is_not_null())
            realized.append(j)
        R = pl.concat(realized) if realized else pl.DataFrame()
        by_h = []
        if R.height:
            for (h,), g in R.group_by(["horizon"], maintain_order=True):
                y, p = g["observed"].cast(pl.Float64).to_numpy(), g["p50"].to_numpy()
                lo, hi = g["p10"].to_numpy(), g["p90"].to_numpy()
                by_h.append({"horizon": h, "n": g.height, "mae": float(np.mean(np.abs(p - y))),
                             "coverage80": float(np.mean((y >= lo) & (y <= hi)))})
        ds = get_dataset(m["dataset_id"])
        train = pl.read_parquet(lake.data_path(ds["parquet_uri"]))
        drift = []
        feat_files = [lake.abs_artifact(f["parquet_uri"]).parent / "features.parquet" for f in fcs]
        feat_files = [p for p in feat_files if p.exists()]
        if feat_files:
            recent = pl.concat([pl.read_parquet(p) for p in feat_files], how="diagonal_relaxed")
            for col in [c for c in ("cases_lag_1w", "rain_sum_4w", "temp_mean_4w", "rh_mean_4w", "incidence_lag_1w") if c in recent.columns and c in train.columns]:
                drift.append({"feature": col, "psi": _psi(train[col].cast(pl.Float64).to_numpy(), recent[col].cast(pl.Float64).to_numpy())})
        latest_obs = obs["target_week"].max() if obs.height else None
        out.append({"deployment": dep, "model": {k: m[k] for k in ("model_id", "version", "recipe_id", "approval_state", "pit_status")},
                    "forecasts": len(fcs), "realized_rows": R.height, "realized_by_horizon": by_h,
                    "backtest_test": m["metrics_summary_json"], "drift": drift,
                    "label_delay_weeks": ((utcnow().date() - latest_obs).days // 7) if latest_obs else None,
                    "notes": ["PSI is a diagnostic, not proof of drift.", "Alerts open review tickets; no automatic promotion."]})
    return envelope(out, request)


# ---------------------------------------------------------------- simulation audit
@router.get("/simulation-audit")
def simulation_audit(request: Request, a: Actor = Depends(require("read"))):
    rows = db.q("SELECT r.run_id, r.scenario_id, r.scenario_version, r.spec_hash, r.forecast_id, r.status, r.mode, r.result_sha256, "
                "r.created_by, r.created_at, r.finished_at, r.provenance_json, s.name FROM simulation_run r LEFT JOIN scenario s "
                "ON s.scenario_id=r.scenario_id AND s.version=r.scenario_version ORDER BY r.created_at DESC LIMIT 200")
    return envelope([db.jcol(r, "provenance_json") for r in rows], request)


@router.post("/simulation-audit/{rid}/reproduce", status_code=202)
def reproduce(request: Request, rid: str, a: Actor = Depends(require("scenario.run"))):
    r = sim.get_run(rid)
    if not r:
        raise HTTPException(404)
    from ...core.util import new_id
    nid = new_id("sim")
    db.insert("simulation_run", {"run_id": nid, "scenario_id": r["scenario_id"], "scenario_version": r["scenario_version"],
                                 "spec_hash": r["spec_hash"], "forecast_id": r["forecast_id"], "status": "queued", "mode": r["mode"],
                                 "warnings_json": dumps(r.get("warnings_json") or []), "created_by": a.user_id, "created_at": iso(utcnow())})
    j = jobs.enqueue("simulate", {"run_id": nid}, target_type="simulation_run", target_id=nid, actor_id=a.user_id)
    db.update("simulation_run", {"run_id": nid}, {"job_id": j["job_id"]})
    do_audit(request, a, "simulation.reproduce", "simulation_run", nid, meta={"of": rid})
    return envelope({"run_id": nid, "job_id": j["job_id"], "reproduces": rid, "expected_sha256": r["result_sha256"]}, request)


# ---------------------------------------------------------------- security, audit, settings
@router.get("/security")
def security(request: Request, a: Actor = Depends(require("admin.security"))):
    users = db.q("SELECT user_id, display_name, role, created_at, disabled FROM users ORDER BY user_id")
    matrix = {perm: sorted(roles) for perm, roles in PERMISSIONS.items()}
    licenses = db.q("SELECT source_id, display_name, license_ref, pii_class, provenance_url FROM source_registry ORDER BY source_id")
    secrets = sorted({r["auth_secret_ref"] for r in db.q("SELECT auth_secret_ref FROM source_registry WHERE auth_secret_ref IS NOT NULL")})
    return envelope({"users": users, "roles": list(ROLES), "permissions": matrix, "licenses": licenses,
                     "secret_refs": secrets, "secret_store": "environment-backed references; values are never returned"}, request)


@router.patch("/security/users/{uid}")
def set_role(request: Request, uid: str, body: dict, a: Actor = Depends(require("admin.security"))):
    if body.get("role") not in ROLES:
        raise HTTPException(400, "unknown role")
    if uid == a.user_id:
        raise HTTPException(409, "admins cannot change their own role")
    old = db.q1("SELECT role FROM users WHERE user_id=?", uid)
    if not old:
        raise HTTPException(404)
    db.update("users", {"user_id": uid}, {"role": body["role"]})
    do_audit(request, a, "user.role", "user", uid, old=old["role"], new=body["role"], reason=body.get("reason"))
    return envelope({"user_id": uid, "role": body["role"]}, request)


@router.get("/audit")
def audit_log(request: Request, action: str | None = None, actor_id: str | None = None, target_id: str | None = None,
              limit: int = 100, offset: int = 0, a: Actor = Depends(require("audit.read"))):
    limit, offset = page(limit, offset)
    w, args = [], []
    for col, v in (("action", action), ("actor_id", actor_id), ("target_id", target_id)):
        if v:
            w.append(f"{col} LIKE ?" if col == "action" else f"{col}=?")
            args.append(f"{v}%" if col == "action" else v)
    where = ("WHERE " + " AND ".join(w)) if w else ""
    total = db.q1(f"SELECT COUNT(*) n FROM audit_event {where}", *args)["n"]
    rows = db.q(f"SELECT * FROM audit_event {where} ORDER BY ts DESC LIMIT ? OFFSET ?", *args, limit, offset)
    for r in rows:
        r["metadata"] = loads(r.pop("metadata_redacted"))
    return envelope(rows, request, meta={"total": total, "limit": limit, "offset": offset})


SETTINGS_DEFAULTS = {"worker_concurrency": 2, "training_max_cpu": 4, "training_timeout_minutes": 30, "retention_days_bronze": 3650,
                     "max_simulation_draws": 100000, "preview_row_cap": 200}


@router.get("/settings")
def get_settings_(request: Request, a: Actor = Depends(require("read"))):
    vals = dict(SETTINGS_DEFAULTS)
    for r in db.q("SELECT key, value_json FROM settings_kv"):
        vals[r["key"]] = loads(r["value_json"])
    s = get_settings()
    return envelope({"values": vals, "environment": s.env, "workspace": s.workspace, "pilot": s.pilot,
                     "approved_hosts": list(s.approved_hosts), "offline": s.offline}, request)


@router.put("/settings")
def put_settings(request: Request, body: dict, a: Actor = Depends(require("admin.settings"))):
    bad = set(body) - set(SETTINGS_DEFAULTS)
    if bad:
        raise HTTPException(400, f"unknown settings {sorted(bad)}")
    for k, v in body.items():
        if not isinstance(v, (int, float)) or v < 0:
            raise HTTPException(400, f"{k} must be a non-negative number")
        db.ex("INSERT INTO settings_kv(key, value_json, updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
              k, dumps(v), iso(utcnow()))
    do_audit(request, a, "settings.update", "settings", "global", meta=body)
    return get_settings_(request, a)
