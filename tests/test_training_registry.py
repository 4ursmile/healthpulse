"""Training, evaluation, registry gates, deployment and rollback (acceptance §24.3 scenarios 5–8, 12)."""
from datetime import date

import polars as pl
import pytest

from healthpulse.core import db, lake
from healthpulse.features.jobs import get_dataset
from healthpulse.ml import registry as mreg

from .conftest import run


def test_rolling_origin_respects_embargo(trained, dataset):
    rep = mreg.run_report(trained)
    split = get_dataset(dataset)["splits_json"]
    assert split["embargo_weeks"] > 4
    for f in split["folds"]:
        assert date.fromisoformat(f["train_label_cutoff"]) < date.fromisoformat(f["validation_start"])
    for rid in ("persistence", "seasonal_naive", "hgb_poisson"):
        assert set(rep["recipes"][rid]["test"]) == {"1", "2", "3", "4"}
        for m in rep["recipes"][rid]["test"].values():
            assert m["n"] > 0 and m["mae"] >= 0
    assert all("coverage80" in m for m in rep["recipes"]["hgb_poisson"]["test"].values())
    assert rep["comparison"]["1"]["hgb_poisson"]["baseline"] in ("persistence", "seasonal_naive")


def test_artifacts_are_hashed_and_reproducible(trained):
    r = mreg.get_run(trained)
    art = r["artifacts_json"]["models"]["hgb_poisson"]
    d = lake.abs_artifact(art["artifact_uri"])
    assert (d / "sha256.txt").read_text() == art["sha256"]
    assert (d / "signature.json").exists() and len(list(d.glob("h*.joblib"))) == 4


def test_candidate_cannot_be_champion_without_dual_approval(trained):
    m = mreg.register(trained, "seasonal_naive", "u_scientist")
    with pytest.raises(mreg.GateError):
        mreg.deploy(m["model_id"], m["version"], "champion", "u_admin")
    mreg.review(m["model_id"], m["version"], "clinical", "approve", "looks appropriate", "u_reviewer", "epi_reviewer")
    with pytest.raises(mreg.GateError):
        mreg.deploy(m["model_id"], m["version"], "champion", "u_admin")
    rej = mreg.register(trained, "persistence", "u_scientist")
    out = mreg.review(rej["model_id"], rej["version"], "clinical", "reject", "naive model not suitable", "u_reviewer", "epi_reviewer")
    assert out["approval_state"] == "REJECTED" and len(out["reviews"]) == 1  # rejected stays auditable
    with pytest.raises(mreg.GateError):
        mreg.deploy(rej["model_id"], rej["version"], "shadow", "u_admin")


def test_shadow_does_not_publish(trained, champion):
    m = mreg.register(trained, "hgb_poisson", "u_scientist")
    d = mreg.deploy(m["model_id"], m["version"], "shadow", "u_admin")
    from healthpulse.core import jobs
    j = jobs.run_job_inline(d["inference_job_id"])
    assert j["status"] == "succeeded"
    f = db.q1("SELECT is_public FROM forecast WHERE forecast_id=?", j["result"]["forecast_id"])
    assert f["is_public"] == 0


def test_rollback_restores_previous_and_keeps_old_forecasts(trained, champion):
    from healthpulse.core import jobs
    old = db.q1("SELECT sha256, parquet_uri FROM forecast WHERE forecast_id=?", champion["forecast_id"])
    before = mreg.active("dengue", "dengue_reported_cases", "champion")
    m2 = mreg.register(trained, "hgb_poisson", "u_scientist")
    mreg.review(m2["model_id"], m2["version"], "clinical", "approve", "second version ok", "u_reviewer", "epi_reviewer")
    mreg.review(m2["model_id"], m2["version"], "platform", "approve", "second version ok", "u_admin", "platform_admin")
    d2 = mreg.deploy(m2["model_id"], m2["version"], "champion", "u_admin")
    jobs.run_job_inline(d2["inference_job_id"])
    rb = mreg.rollback("dengue", "dengue_reported_cases", "u_admin")
    assert rb["restored"]["version"] == before["version"]
    cur = mreg.active("dengue", "dengue_reported_cases", "champion")
    assert (cur["model_id"], cur["version"]) == (before["model_id"], before["version"])
    rolled = db.q1("SELECT status FROM model_deployment WHERE deployment_id=?", d2["deployment"]["deployment_id"])
    assert rolled["status"] == "rolled_back"
    jobs.run_job_inline(rb["inference_job_id"])
    from healthpulse.core.util import sha256_file
    assert sha256_file(lake.abs_artifact(old["parquet_uri"])) == old["sha256"]  # immutable


def test_forecast_never_silently_zero(champion):
    from healthpulse.simulation.jobs import forecast_frame
    F = forecast_frame(champion["forecast_id"])
    bad = F.filter(pl.col("quality_status") == "insufficient_data")
    assert bad.filter(pl.col("p50").is_not_null()).height == 0
    ok = F.filter(pl.col("quality_status") == "ok")
    assert ok.height > 0 and (ok["p10"] <= ok["p50"]).all() and (ok["p50"] <= ok["p90"]).all()
