"""Offline test harness: an isolated data/artifact/db directory per session, SYNTHETIC fixture data, no network."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="hp_test_"))
os.environ.update({"HP_DATA_DIR": str(_TMP / "data"), "HP_ARTIFACTS_DIR": str(_TMP / "artifacts"), "HP_DB": str(_TMP / "hp.db"),
                   "HP_OFFLINE": "1", "HP_ENV": "test", "HP_SECRET_KEY": "test-secret", "HP_PILOT": "3304557,3550308,3106200"})

from healthpulse.config import reset_settings_cache  # noqa: E402

reset_settings_cache()

from healthpulse.bootstrap import init  # noqa: E402
from healthpulse.core import db, jobs  # noqa: E402


@pytest.fixture(scope="session")
def fixture_data():
    init()
    from healthpulse.sources import fixtures
    info = fixtures.install()
    from healthpulse.features import registry as freg
    for d in freg.list_defs():
        freg.record_validation(d["feature_id"], d["version"], freg.validate_def(d))
    freg.seed_feature_sets()
    return info


def run(job_type: str, payload: dict, **kw) -> dict:
    j = jobs.enqueue(job_type, payload, **kw)
    return jobs.run_job_inline(j["job_id"])


@pytest.fixture(scope="session")
def dataset(fixture_data):
    r = run("dataset_build", {"feature_set_id": "br_dengue", "target": "dengue_reported_cases",
                              "split": {"folds": 3, "validation_weeks": 20, "test_weeks": 20}}, actor_id="u_scientist")
    assert r["status"].startswith("succeeded"), r
    return r["result"]["dataset_id"]


@pytest.fixture(scope="session")
def trained(dataset):
    from healthpulse.ml import registry as mreg
    e = mreg.create_experiment({"dataset_id": dataset, "recipes": [{"recipe": "persistence"}, {"recipe": "seasonal_naive"},
                                                                    {"recipe": "hgb_poisson", "params": {"max_iter": 60}}],
                                "ablation": False}, "u_scientist")
    r = mreg.start_run(e["experiment_id"], "u_scientist")
    j = jobs.run_job_inline(r["job_id"])
    assert j["status"] == "succeeded", (j["error"], jobs.logs(r["job_id"])[-3:])
    return r["run_id"]


@pytest.fixture(scope="session")
def champion(trained):
    from healthpulse.ml import registry as mreg
    m = mreg.register(trained, "hgb_poisson", "u_scientist")
    mreg.review(m["model_id"], m["version"], "clinical", "approve", "reviewed target and caveats", "u_reviewer", "epi_reviewer")
    mreg.review(m["model_id"], m["version"], "platform", "approve", "runtime verified", "u_admin", "platform_admin")
    d = mreg.deploy(m["model_id"], m["version"], "champion", "u_admin")
    j = jobs.run_job_inline(d["inference_job_id"])
    assert j["status"] == "succeeded", (j["error"], jobs.logs(d["inference_job_id"])[-3:])
    return {"model": m, "deployment_id": d["deployment"]["deployment_id"], "forecast_id": j["result"]["forecast_id"]}


@pytest.fixture(scope="session")
def client(fixture_data):
    from fastapi.testclient import TestClient
    from healthpulse.api.main import create_app
    return TestClient(create_app())


@pytest.fixture(scope="session")
def tokens(fixture_data):
    from healthpulse.core.security import DEMO_USERS, demo_token
    return {role: {"Authorization": f"Bearer {demo_token(uid)}"} for uid, _, role in DEMO_USERS}
