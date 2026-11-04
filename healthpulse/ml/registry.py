"""Experiments, model registry, dual review, release gates, shadow/champion deployment and rollback.

Review state lives here (DRAFT -> CANDIDATE -> REVIEWED -> APPROVED -> DEPLOYED -> RETIRED | REJECTED); serving uses
aliases (`shadow`, `champion`) recorded as deployment rows, so every change is a new auditable record.
"""
from __future__ import annotations

from ..core import db, jobs, lake
from ..core.util import dumps, iso, loads, new_id, stable_hash, utcnow
from ..features.jobs import get_dataset
from ..sources import catalog
from .recipes import BASELINES, RECIPES


class GateError(ValueError):
    pass


def create_experiment(cfg: dict, actor: str) -> dict:
    ds = get_dataset(cfg["dataset_id"])
    if not ds or ds["status"] != "ready":
        raise ValueError("dataset not found or not ready")
    for r in cfg.get("recipes", []):
        if r["recipe"] not in RECIPES:
            raise ValueError(f"recipe {r['recipe']} is not allowlisted")
    cfg = {"seed": 42, "calibration": {"method": "out_of_fold_residual", "nominal_coverage": 0.8},
           "resources": {"max_cpu": 2, "max_memory_gb": 4, "timeout_minutes": 30}, "ablation": True, **cfg}
    cfg["resources"]["max_cpu"] = min(int(cfg["resources"].get("max_cpu", 2)), 8)
    cfg["resources"]["timeout_minutes"] = min(int(cfg["resources"].get("timeout_minutes", 30)), 120)
    eid = new_id("exp")
    now = iso(utcnow())
    db.insert("experiment", {"experiment_id": eid, "name": cfg.get("name") or f"{ds['disease_id']} {ds['target_signal']}",
                             "disease_id": ds["disease_id"], "target_signal": ds["target_signal"], "dataset_id": ds["dataset_id"],
                             "config_json": dumps(cfg), "config_hash": stable_hash(cfg), "status": "created", "created_by": actor,
                             "created_at": now, "updated_at": now})
    return get_experiment(eid)


def get_experiment(eid: str) -> dict | None:
    return db.jcol(db.q1("SELECT * FROM experiment WHERE experiment_id=?", eid), "config_json", "validation_json")


def validate_experiment(eid: str) -> dict:
    e = get_experiment(eid)
    ds = get_dataset(e["dataset_id"])
    checks = [
        {"check": "dataset_ready", "passed": ds["status"] == "ready", "detail": ds["dataset_id"]},
        {"check": "labeled_rows", "passed": (ds["pit_report_json"] or {}).get("labeled_rows", 0) > 100,
         "detail": f"{(ds['pit_report_json'] or {}).get('labeled_rows', 0):,} labeled rows"},
        {"check": "pit_audit", "passed": True, "detail": ds["pit_status"] + (" — limited evidence, prospective performance not claimed"
                                                                             if ds["pit_status"] != "PIT_VERIFIED" else "")},
        {"check": "baseline_included", "passed": any(r["recipe"] in BASELINES for r in e["config_json"].get("recipes", [])),
         "detail": "a transparent baseline runs on the same folds"},
        {"check": "target_signal", "passed": e["target_signal"] in catalog.TARGETS, "detail": catalog.TARGETS.get(e["target_signal"], {}).get("label")},
        {"check": "compute_bounds", "passed": True, "detail": f"{e['config_json']['resources']['max_cpu']} CPU, "
                                                              f"{e['config_json']['resources']['timeout_minutes']} min timeout"},
        {"check": "embargo_covers_horizon", "passed": ds["splits_json"]["embargo_weeks"] > max(ds["horizons_json"]),
         "detail": f"embargo {ds['splits_json']['embargo_weeks']} weeks vs max horizon {max(ds['horizons_json'])}"},
    ]
    ok = all(c["passed"] for c in checks)
    res = {"valid": ok, "checks": checks, "estimate": {"fits": len(e["config_json"].get("recipes", [])) * len(ds["horizons_json"]) *
                                                       (len(ds["splits_json"]["folds"]) + 2), "note": "non-binding estimate"}}
    db.update("experiment", {"experiment_id": eid}, {"validation_json": dumps(res), "status": "validated" if ok else "invalid",
                                                     "updated_at": iso(utcnow())})
    return res


def start_run(eid: str, actor: str, idem: str | None = None) -> dict:
    e = get_experiment(eid)
    if e["status"] not in ("validated", "completed", "failed"):
        v = validate_experiment(eid)
        if not v["valid"]:
            raise GateError("experiment failed validation")
    rid = new_id("trn")
    db.insert("experiment_run", {"run_id": rid, "experiment_id": eid, "dataset_id": e["dataset_id"], "status": "queued",
                                 "config_hash": e["config_hash"], "created_by": actor, "created_at": iso(utcnow())})
    j = jobs.enqueue("train", {"run_id": rid}, target_type="training_run", target_id=rid, actor_id=actor, idempotency_key=idem,
                     timeout_s=int(e["config_json"]["resources"]["timeout_minutes"]) * 60)
    db.update("experiment_run", {"run_id": rid}, {"job_id": j["job_id"]})
    db.update("experiment", {"experiment_id": eid}, {"status": "running", "updated_at": iso(utcnow())})
    return {"run_id": rid, "job_id": j["job_id"]}


def get_run(rid: str) -> dict | None:
    r = db.jcol(db.q1("SELECT * FROM experiment_run WHERE run_id=?", rid), "metrics_json", "artifacts_json", "env_json")
    if r and r.get("job_id"):
        j = jobs.get(r["job_id"])
        if j:
            r["job"] = {k: j[k] for k in ("status", "stage", "progress", "stages", "error", "elapsed_s", "started_at", "finished_at")}
            if j["status"] in ("failed", "canceled", "timed_out") and r["status"] not in ("completed",):
                r["status"] = j["status"]
    return r


def run_report(rid: str) -> dict | None:
    r = get_run(rid)
    if not r or not r.get("report_uri"):
        return None
    d = lake.abs_artifact(r["report_uri"])
    return loads((d / "metrics.json").read_text())


# ---------------------------------------------------------------- registry
def model_card(run: dict, recipe: str, ds: dict, metrics: dict) -> dict:
    t = catalog.TARGETS[ds["target_signal"]]
    test = metrics["recipes"][recipe]["test"]
    return {
        "model": RECIPES[recipe]["label"], "recipe": recipe, "target_definition": t["label"], "signal_type": t["signal_type"],
        "disease": catalog.DISEASES[t["disease_id"]]["definition"], "geography": ds["regions_json"],
        "training_window": ds["issue_range_json"], "label_cutoff": ds["label_cutoff"], "horizons": ds["horizons_json"],
        "data_sources": (ds["pit_report_json"] or {}).get("sources"), "feature_set": f"{ds['feature_set_id']}@{ds['feature_set_version']}",
        "pit_status": ds["pit_status"], "validation": {"method": "rolling-origin by issue date", "split": ds["splits_json"],
                                                        "test_metrics": test},
        "uncertainty": f"{int(metrics['nominal_coverage'] * 100)}% interval from out-of-fold multiplicative residuals; empirical test coverage "
                       + ", ".join(f"h{h}: {v.get('coverage80', float('nan')):.0%}" for h, v in test.items() if v.get("coverage80") is not None),
        "usage_restrictions": ["Decision support for trained public-health staff; not a diagnosis or an automatic alert.",
                               "Forecasts reported notifications, which are revised after publication.",
                               "Not validated outside the listed municipalities (out-of-distribution warning)."],
        "limitations": [(ds["pit_report_json"] or {}).get("note")] if (ds["pit_report_json"] or {}).get("note") else [],
        "populations_represented": f"{len(ds['regions_json'])} municipalities", "responsible_contact": "HealthPulse model owner (u_scientist)",
        "validation_date": iso(utcnow()),
    }


def register(run_id: str, recipe: str, actor: str) -> dict:
    run = get_run(run_id)
    if not run or run["status"] != "completed":
        raise GateError("only completed training runs can be registered")
    art = (run["artifacts_json"] or {}).get("models", {}).get(recipe)
    if not art:
        raise GateError(f"run has no artifact for recipe {recipe}")
    ds = get_dataset(run["dataset_id"])
    metrics = run_report(run_id)
    model_id = f"{ds['target_signal']}__{recipe}"
    v = (db.q1("SELECT MAX(version) v FROM model_registry WHERE model_id=?", model_id)["v"] or 0) + 1
    card = model_card(run, recipe, ds, metrics)
    db.insert("model_registry", {
        "model_id": model_id, "version": v, "model_family": RECIPES[recipe]["family"], "recipe_id": recipe,
        "disease_id": ds["disease_id"], "target_signal": ds["target_signal"], "horizons_json": dumps(ds["horizons_json"]),
        "run_id": run_id, "dataset_id": ds["dataset_id"], "feature_set_ref": f"{ds['feature_set_id']}@{ds['feature_set_version']}",
        "artifact_uri": art["artifact_uri"], "model_card_json": dumps(card), "approval_state": "CANDIDATE", "pit_status": ds["pit_status"],
        "metrics_summary_json": dumps(metrics["recipes"][recipe]["test"]), "hash": art["sha256"], "created_by": actor,
        "created_at": iso(utcnow())})
    return get_model(model_id, v)


def get_model(model_id: str, version: int) -> dict | None:
    r = db.q1("SELECT * FROM model_registry WHERE model_id=? AND version=?", model_id, int(version))
    r = db.jcol(r, "horizons_json", "model_card_json", "reviewer_signoff_json", "platform_signoff_json", "metrics_summary_json")
    if r:
        r["reviews"] = db.q("SELECT * FROM model_review WHERE model_id=? AND version=? ORDER BY ts", model_id, int(version))
        r["deployments"] = db.q("SELECT * FROM model_deployment WHERE model_id=? AND version=? ORDER BY created_at", model_id, int(version))
        r["gates"] = release_gates(r)
    return r


def list_models() -> list[dict]:
    rows = db.q("SELECT model_id, version FROM model_registry ORDER BY created_at DESC")
    return [get_model(r["model_id"], r["version"]) for r in rows]


def release_gates(m: dict) -> list[dict]:
    test = m.get("metrics_summary_json") or {}
    run = db.jcol(db.q1("SELECT metrics_json FROM experiment_run WHERE run_id=?", m["run_id"]), "metrics_json") or {}
    comp = (run.get("metrics_json") or {}).get("comparison") or {}
    cov = [v.get("coverage80") for v in test.values() if v.get("coverage80") is not None]
    clinical = any(r["kind"] == "clinical" and r["decision"] == "approve" for r in m.get("reviews", []))
    platform = any(r["kind"] == "platform" and r["decision"] == "approve" for r in m.get("reviews", []))
    return [
        {"gate": "label_definition", "passed": m["target_signal"] in catalog.TARGETS, "hard": True, "detail": m["target_signal"]},
        {"gate": "pit_audit", "passed": True, "hard": False,
         "detail": m["pit_status"] + ("" if m["pit_status"] == "PIT_VERIFIED" else " — labelled limited-evidence; prospective claims excluded")},
        {"gate": "temporal_holdout", "passed": bool(test), "hard": True, "detail": f"{len(test)} horizons evaluated on held-out issue weeks"},
        {"gate": "baseline_comparison", "passed": bool(comp) and all(v for v in comp.values()),
         "hard": True, "detail": "best transparent baseline on matched folds: " + ", ".join(
             f"h{h} {v.get(m['recipe_id'], {}).get('skill_vs_baseline', 0):+.0%} vs {next(iter(v.values()), {}).get('baseline')}" for h, v in comp.items())},
        {"gate": "uncertainty_evaluated", "passed": bool(cov), "hard": True,
         "detail": "empirical 80% coverage " + ", ".join(f"{c:.0%}" for c in cov) if cov else "no interval coverage"},
        {"gate": "coverage_within_tolerance", "passed": bool(cov) and min(cov) >= 0.6, "hard": False,
         "detail": "every horizon ≥ 60% at nominal 80% (soft)"},
        {"gate": "clinical_signoff", "passed": clinical, "hard": True, "detail": "epidemiology reviewer"},
        {"gate": "platform_signoff", "passed": platform, "hard": True, "detail": "platform admin"},
    ]


def review(model_id: str, version: int, kind: str, decision: str, notes: str, actor_id: str, actor_role: str) -> dict:
    m = get_model(model_id, version)
    if not m:
        raise KeyError(model_id)
    if m["approval_state"] in ("REJECTED", "RETIRED"):
        raise GateError(f"model is {m['approval_state']}")
    if decision not in ("approve", "reject", "request_changes"):
        raise ValueError("decision must be approve, reject or request_changes")
    if not notes or len(notes.strip()) < 5:
        raise ValueError("a review needs a written reason")
    db.insert("model_review", {"review_id": new_id("rev"), "model_id": model_id, "version": int(version), "reviewer_id": actor_id,
                               "reviewer_role": actor_role, "kind": kind, "decision": decision, "notes": notes, "ts": iso(utcnow())})
    m = get_model(model_id, version)
    if decision == "reject":
        state = "REJECTED"
    else:
        kinds = {r["kind"] for r in m["reviews"] if r["decision"] == "approve"}
        state = "APPROVED" if {"clinical", "platform"} <= kinds else ("REVIEWED" if kinds else "CANDIDATE")
        if m["approval_state"] == "DEPLOYED":
            state = "DEPLOYED"
    sign = {"by": actor_id, "decision": decision, "at": iso(utcnow()), "notes": notes}
    col = "reviewer_signoff_json" if kind == "clinical" else "platform_signoff_json"
    db.update("model_registry", {"model_id": model_id, "version": int(version)}, {"approval_state": state, col: dumps(sign)})
    return get_model(model_id, version)


def active(disease_id: str, target: str, alias: str) -> dict | None:
    return db.q1("SELECT * FROM model_deployment WHERE disease_id=? AND target_signal=? AND serving_alias=? AND status='active' "
                 "ORDER BY created_at DESC LIMIT 1", disease_id, target, alias)


def deploy(model_id: str, version: int, alias: str, actor: str, scope: str = "pilot") -> dict:
    m = get_model(model_id, version)
    if not m:
        raise KeyError(model_id)
    if alias not in ("shadow", "champion"):
        raise ValueError("alias must be shadow or champion")
    if m["approval_state"] in ("REJECTED", "RETIRED", "DRAFT"):
        raise GateError(f"a {m['approval_state']} model cannot be deployed")
    if alias == "champion":
        failed = [g["gate"] for g in m["gates"] if g["hard"] and not g["passed"]]
        if failed:
            raise GateError(f"release gates not met: {failed}")
    now = iso(utcnow())
    prev = active(m["disease_id"], m["target_signal"], alias)
    with db.tx() as c:
        if prev:
            c.execute("UPDATE model_deployment SET status='superseded', retired_at=? WHERE deployment_id=?", (now, prev["deployment_id"]))
        did = new_id("dep")
        db.insert("model_deployment", {"deployment_id": did, "model_id": model_id, "version": int(version), "disease_id": m["disease_id"],
                                       "target_signal": m["target_signal"], "geography_scope": scope, "serving_alias": alias,
                                       "status": "active", "approved_by": actor, "deployed_at": now,
                                       "previous_deployment_id": prev["deployment_id"] if prev else None, "created_at": now}, c)
        if alias == "champion":
            c.execute("UPDATE model_registry SET approval_state='DEPLOYED' WHERE model_id=? AND version=?", (model_id, int(version)))
    j = jobs.enqueue("inference", {"deployment_id": did}, target_type="deployment", target_id=did, actor_id=actor)
    return {"deployment": db.q1("SELECT * FROM model_deployment WHERE deployment_id=?", did), "inference_job_id": j["job_id"],
            "previous_deployment_id": prev["deployment_id"] if prev else None}


def rollback(disease_id: str, target: str, actor: str) -> dict:
    cur = active(disease_id, target, "champion")
    if not cur or not cur["previous_deployment_id"]:
        raise GateError("no previous champion to restore")
    prev = db.q1("SELECT * FROM model_deployment WHERE deployment_id=?", cur["previous_deployment_id"])
    now = iso(utcnow())
    with db.tx() as c:
        c.execute("UPDATE model_deployment SET status='rolled_back', retired_at=? WHERE deployment_id=?", (now, cur["deployment_id"]))
        did = new_id("dep")
        db.insert("model_deployment", {"deployment_id": did, "model_id": prev["model_id"], "version": prev["version"],
                                       "disease_id": disease_id, "target_signal": target, "geography_scope": prev["geography_scope"],
                                       "serving_alias": "champion", "status": "active", "approved_by": actor, "deployed_at": now,
                                       "previous_deployment_id": prev["previous_deployment_id"], "created_at": now}, c)
        c.execute("UPDATE model_registry SET approval_state='APPROVED' WHERE model_id=? AND version=?", (cur["model_id"], cur["version"]))
    j = jobs.enqueue("inference", {"deployment_id": did}, target_type="deployment", target_id=did, actor_id=actor)
    return {"restored": {"model_id": prev["model_id"], "version": prev["version"]}, "deployment_id": did,
            "rolled_back": cur["deployment_id"], "inference_job_id": j["job_id"]}


def deployments(disease_id: str | None = None) -> list[dict]:
    if disease_id:
        return db.q("SELECT * FROM model_deployment WHERE disease_id=? ORDER BY created_at DESC", disease_id)
    return db.q("SELECT * FROM model_deployment ORDER BY created_at DESC")
