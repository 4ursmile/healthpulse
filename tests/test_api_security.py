"""API contracts, RBAC, secrets, SSRF, idempotency and privacy (acceptance §24.3 scenarios 9–11)."""
import pytest

from healthpulse.core import db, jobs, lake
from healthpulse.core.security import redact


def test_envelope_and_semantics(client, champion):
    r = client.get("/api/v1/overview?disease_id=dengue&horizon=2")
    assert r.status_code == 200
    j = r.json()
    assert set(j) >= {"data", "meta", "warnings", "request_id"}
    assert j["meta"]["data_status"] == "synthetic"  # fixture data is never presented as observed
    assert j["meta"]["forecast_id"]
    assert r.headers["X-Request-ID"]


def test_clinical_terminology(client, fixture_data):
    d = {x["disease_id"]: x for x in client.get("/api/v1/diseases").json()["data"]}
    assert "SARI" in d["covid19_sari"]["metric_label"] or "severe respiratory" in d["covid19_sari"]["metric_label"].lower()
    assert "all covid" not in d["covid19_sari"]["metric_label"].lower()


def test_viewer_and_anonymous_cannot_mutate(client, tokens, fixture_data):
    assert client.post("/api/v1/admin/sources/infodengue/ingest").status_code == 401
    assert client.post("/api/v1/admin/sources/infodengue/ingest", headers=tokens["viewer"]).status_code == 403
    assert client.post("/api/v1/admin/deployments", json={"model_id": "x", "version": 1}, headers=tokens["data_scientist"]).status_code == 403
    assert client.get("/api/v1/admin/audit", headers=tokens["viewer"]).status_code == 403
    assert client.get("/api/v1/admin/security", headers=tokens["epi_reviewer"]).status_code == 403


def test_author_cannot_review_own_model(client, tokens, trained):
    r = client.post("/api/v1/admin/registry/models", json={"run_id": trained, "recipe": "hgb_poisson"}, headers=tokens["data_scientist"])
    assert r.status_code == 201
    m = r.json()["data"]
    # a data scientist has no clinical permission; the reviewer can, the admin cannot give a clinical sign-off
    assert client.post(f"/api/v1/admin/registry/{m['model_id']}/{m['version']}/review", json={"kind": "clinical", "decision": "approve", "notes": "ok ok"},
                       headers=tokens["data_scientist"]).status_code == 403
    assert client.post(f"/api/v1/admin/registry/{m['model_id']}/{m['version']}/review", json={"kind": "clinical", "decision": "approve", "notes": "ok ok"},
                       headers=tokens["platform_admin"]).status_code == 403
    assert client.post(f"/api/v1/admin/registry/{m['model_id']}/{m['version']}/review", json={"kind": "clinical", "decision": "approve", "notes": "reviewed"},
                       headers=tokens["epi_reviewer"]).status_code == 200


def test_plaintext_secrets_refused_and_redacted(client, tokens, fixture_data):
    r = client.post("/api/v1/admin/sources", json={"source_id": "my_feed", "config": {"api_key": "abc123"}}, headers=tokens["data_operator"])
    assert r.status_code == 400
    assert redact({"token": "x", "nested": {"password": "y"}, "url": "https://u:p@h/x?token=zz"}) == \
        {"token": "***", "nested": {"password": "***"}, "url": "https://***@h/x?token=***"}
    r = client.patch("/api/v1/admin/sources/infodengue", json={"expected_version": 1, "auth_secret_ref": "vault://infodengue", "reason": "ref"},
                     headers=tokens["data_operator"])
    assert r.status_code == 200
    assert "vault://infodengue" == r.json()["data"]["auth_secret_ref"]
    r2 = client.patch("/api/v1/admin/sources/infodengue", json={"expected_version": 1, "enabled": True}, headers=tokens["data_operator"])
    assert r2.status_code == 409  # optimistic locking


def test_ssrf_controls():
    for bad in ("http://info.dengue.mat.br/api", "https://169.254.169.254/latest", "https://evil.example.com/x", "https://u:p@date.nager.at/"):
        with pytest.raises(lake.UnsafeURL):
            lake.check_url(bad)
    lake.check_url("https://date.nager.at/api/v3/PublicHolidays/2025/BR")


def test_offline_fetch_fails_loudly_and_source_keeps_last_good(fixture_data):
    """Scenario 11: a failed download leaves the last good snapshot intact and records a failed attempt (no silent success)."""
    before = db.q1("SELECT COUNT(*) n FROM source_snapshot WHERE published=1")["n"]
    j = jobs.enqueue("ingest", {"source_id": "nager_holidays", "params": {}}, target_type="source", target_id="nager_holidays")
    r = jobs.run_job_inline(j["job_id"])
    assert r["status"] == "failed" and "offline" in r["error"]
    assert db.q1("SELECT COUNT(*) n FROM source_snapshot WHERE published=1")["n"] == before


def test_unavailable_connector_never_substitutes(client, tokens, fixture_data):
    # every catalogued source now has a real connector, so put one into the gated state to test the guard itself
    prev = db.q1("SELECT status FROM source_registry WHERE source_id='nager_holidays'")["status"]
    with db.tx() as c:
        c.execute("UPDATE source_registry SET status='needs_authorization' WHERE source_id='nager_holidays'")
    try:
        r = client.post("/api/v1/admin/sources/nager_holidays/ingest", headers=tokens["data_operator"])
        assert r.status_code == 409 and "no data is fetched or substituted" in r.json()["error"]["detail"]
    finally:
        with db.tx() as c:
            c.execute("UPDATE source_registry SET status=? WHERE source_id='nager_holidays'", (prev,))


def test_idempotent_ingest_enqueue(fixture_data):
    a = jobs.enqueue("probe", {"source_id": "infodengue"}, idempotency_key="k-1")
    b = jobs.enqueue("probe", {"source_id": "infodengue"}, idempotency_key="k-1")
    assert a["job_id"] == b["job_id"]


def test_sivep_rows_aggregated_without_personal_columns(tmp_path, fixture_data):
    from datetime import date

    from healthpulse.sources import silver
    from healthpulse.sources.connectors import write_synthetic_sivep
    p = tmp_path / "srag.csv"
    n = write_synthetic_sivep(p, 50_000, ["3304557", "3550308"], date(2025, 1, 5), 20)
    j = jobs.enqueue("import_file", {"source_id": "sivep_gripe", "upload_path": str(p), "filename": "srag.csv", "synthetic": True})
    r = jobs.run_job_inline(j["job_id"])
    assert r["status"].startswith("succeeded"), r["error"]
    s = silver.latest("sari_weekly", include_synthetic=True)
    assert int(s["observed_cases"].sum()) == n
    assert not {"CS_SEXO", "NU_IDADE_N", "DT_SIN_PRI"} & set(s.columns)
    snap = db.q1("SELECT bronze_uri FROM source_snapshot WHERE silver_table='sari_weekly'")
    files = list(lake.data_path(snap["bronze_uri"]).glob("*.csv"))
    assert files and not (files[0].stat().st_mode & 0o222)  # bronze is read-only


def test_simulation_run_api_flow(client, tokens, champion):
    body = {"name": "API flow scenario", "forecast_id": champion["forecast_id"], "resources": {"mobile_teams_available": 2, "max_travel_minutes": 800},
            "simulation": {"draws": 100}}
    r = client.post("/api/v1/scenarios", json=body, headers=tokens["data_scientist"])
    assert r.status_code == 201
    sid = r.json()["data"]["scenario_id"]
    assert client.post(f"/api/v1/scenarios/{sid}/run", headers=tokens["viewer"]).status_code == 403
    r = client.post(f"/api/v1/scenarios/{sid}/run", headers={**tokens["data_scientist"], "Idempotency-Key": "abc"})
    assert r.status_code == 202
    again = client.post(f"/api/v1/scenarios/{sid}/run", headers={**tokens["data_scientist"], "Idempotency-Key": "abc"})
    assert again.json()["data"]["run_id"] == r.json()["data"]["run_id"]
    jobs.run_job_inline(r.json()["data"]["job_id"])
    run = client.get(f"/api/v1/simulations/{r.json()['data']['run_id']}").json()
    assert run["meta"]["type"] == "SCENARIO" and run["data"]["status"] == "completed"
    exp = client.get(f"/api/v1/simulations/{r.json()['data']['run_id']}/export?fmt=csv")
    assert exp.status_code == 200 and "SCENARIO ESTIMATE" in exp.text
    a = client.post("/api/v1/assistant/explain", json={"question": "How many lives will this save?"}).json()["data"]
    assert a["intent"] == "refused_outcome_claim"
    a = client.post("/api/v1/assistant/explain", json={"question": "Explain the tradeoff", "run_id": r.json()["data"]["run_id"]}).json()["data"]
    assert a["intent"] == "tradeoff" and a["citations"]
