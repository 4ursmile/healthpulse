"""Metadata registry on SQLite (WAL) with ordered, idempotent migrations.

SQLite is the single-node pilot store (the spec allows it for a controlled pilot). Every statement here is plain SQL
with no SQLite-only types in table definitions apart from JSON stored as TEXT, so a PostgreSQL port is a driver swap.
Immutable artefacts (Parquet, model files) live on disk; the database holds references and checksums only.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..config import get_settings
from .util import dumps, loads

MIGRATIONS: list[tuple[str, str]] = [
    ("0001_core", """
CREATE TABLE IF NOT EXISTS users(
  user_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, role TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS source_registry(
  source_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, owner_id TEXT, connector_type TEXT NOT NULL,
  source_group TEXT NOT NULL, status TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
  config_json TEXT NOT NULL, config_version INTEGER NOT NULL DEFAULT 1, auth_secret_ref TEXT,
  pii_class TEXT NOT NULL, license_ref TEXT, provenance_url TEXT, expected_grain TEXT, expected_cadence TEXT,
  release_class TEXT NOT NULL, approved_hostnames TEXT, freshness_days INTEGER, publication_lag_days INTEGER,
  schedule_cron TEXT, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS source_snapshot(
  snapshot_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES source_registry(source_id),
  job_id TEXT, bronze_uri TEXT, silver_uri TEXT, checksum TEXT, retrieved_at TEXT NOT NULL,
  data_available_at TEXT NOT NULL, coverage_start TEXT, coverage_end TEXT, schema_version TEXT,
  dq_status TEXT NOT NULL, dq_report_json TEXT, row_count INTEGER, manifest_uri TEXT, silver_table TEXT,
  is_synthetic INTEGER NOT NULL DEFAULT 0, published INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_snapshot_source_time ON source_snapshot(source_id, retrieved_at);
CREATE INDEX IF NOT EXISTS ix_snapshot_table ON source_snapshot(silver_table, published);

CREATE TABLE IF NOT EXISTS jobs(
  job_id TEXT PRIMARY KEY, job_type TEXT NOT NULL, target_type TEXT, target_id TEXT,
  status TEXT NOT NULL, stage TEXT, progress REAL NOT NULL DEFAULT 0, payload_json TEXT NOT NULL,
  result_json TEXT, error TEXT, idempotency_key TEXT UNIQUE, retry_of TEXT, attempt_no INTEGER NOT NULL DEFAULT 1,
  priority INTEGER NOT NULL DEFAULT 5, created_by TEXT, created_at TEXT NOT NULL, queued_at TEXT,
  started_at TEXT, finished_at TEXT, heartbeat_at TEXT, worker_id TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
  timeout_s INTEGER NOT NULL DEFAULT 1800, stages_json TEXT, logs_uri TEXT);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status, priority, created_at);
CREATE INDEX IF NOT EXISTS ix_jobs_target ON jobs(target_type, target_id);

CREATE TABLE IF NOT EXISTS job_log(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, ts TEXT NOT NULL, level TEXT NOT NULL,
  stage TEXT, message TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_job_log ON job_log(job_id, seq);

CREATE TABLE IF NOT EXISTS audit_event(
  event_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, actor_role TEXT, action TEXT NOT NULL,
  target_type TEXT, target_id TEXT, ts TEXT NOT NULL, request_id TEXT, reason TEXT,
  old_version TEXT, new_version TEXT, metadata_redacted TEXT);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_event(ts);
CREATE INDEX IF NOT EXISTS ix_audit_target ON audit_event(target_type, target_id);

CREATE TABLE IF NOT EXISTS feature_definition(
  feature_id TEXT NOT NULL, version INTEGER NOT NULL, owner_id TEXT, description TEXT,
  feature_group TEXT NOT NULL, template TEXT NOT NULL, params_json TEXT NOT NULL, entity_keys TEXT NOT NULL,
  grain TEXT NOT NULL, dtype TEXT NOT NULL, unit TEXT, source_refs TEXT NOT NULL, availability_json TEXT NOT NULL,
  null_policy TEXT NOT NULL, privacy_class TEXT NOT NULL, diseases TEXT NOT NULL, state TEXT NOT NULL,
  leakage_status TEXT NOT NULL DEFAULT 'unchecked', validation_json TEXT, hash TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(feature_id, version));

CREATE TABLE IF NOT EXISTS feature_set(
  feature_set_id TEXT NOT NULL, version INTEGER NOT NULL, description TEXT, disease_id TEXT NOT NULL,
  member_versions_json TEXT NOT NULL, ablation_groups_json TEXT, status TEXT NOT NULL, hash TEXT NOT NULL,
  created_by TEXT, created_at TEXT NOT NULL, PRIMARY KEY(feature_set_id, version));

CREATE TABLE IF NOT EXISTS feature_materialization(
  materialization_id TEXT PRIMARY KEY, feature_set_id TEXT NOT NULL, feature_set_version INTEGER NOT NULL,
  source_vintages_json TEXT, issue_range_json TEXT, parquet_uri TEXT, sha256 TEXT, row_count INTEGER,
  dq_status TEXT NOT NULL, pit_report_json TEXT, coverage_json TEXT, job_id TEXT, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS training_dataset(
  dataset_id TEXT PRIMARY KEY, name TEXT, disease_id TEXT NOT NULL, target_signal TEXT NOT NULL,
  feature_set_id TEXT NOT NULL, feature_set_version INTEGER NOT NULL, materialization_id TEXT,
  horizons_json TEXT NOT NULL, issue_range_json TEXT NOT NULL, label_cutoff TEXT NOT NULL,
  splits_json TEXT NOT NULL, label_vintages_json TEXT, pit_status TEXT NOT NULL, pit_report_json TEXT,
  parquet_uri TEXT, hash TEXT, row_count INTEGER, regions_json TEXT, status TEXT NOT NULL, job_id TEXT,
  created_by TEXT, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS experiment(
  experiment_id TEXT PRIMARY KEY, name TEXT NOT NULL, disease_id TEXT NOT NULL, target_signal TEXT NOT NULL,
  dataset_id TEXT NOT NULL, config_json TEXT NOT NULL, config_hash TEXT NOT NULL, status TEXT NOT NULL,
  validation_json TEXT, created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS experiment_run(
  run_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, dataset_id TEXT NOT NULL, job_id TEXT,
  status TEXT NOT NULL, config_hash TEXT NOT NULL, metrics_json TEXT, report_uri TEXT, artifacts_json TEXT,
  mlflow_run_id TEXT, env_json TEXT, created_by TEXT, created_at TEXT NOT NULL, finished_at TEXT);

CREATE TABLE IF NOT EXISTS model_registry(
  model_id TEXT NOT NULL, version INTEGER NOT NULL, model_family TEXT NOT NULL, recipe_id TEXT NOT NULL,
  disease_id TEXT NOT NULL, target_signal TEXT NOT NULL, horizons_json TEXT NOT NULL, run_id TEXT NOT NULL,
  dataset_id TEXT NOT NULL, feature_set_ref TEXT NOT NULL, artifact_uri TEXT NOT NULL, model_card_json TEXT,
  approval_state TEXT NOT NULL, reviewer_signoff_json TEXT, platform_signoff_json TEXT, pit_status TEXT,
  metrics_summary_json TEXT, hash TEXT NOT NULL, created_by TEXT, created_at TEXT NOT NULL,
  PRIMARY KEY(model_id, version));

CREATE TABLE IF NOT EXISTS model_review(
  review_id TEXT PRIMARY KEY, model_id TEXT NOT NULL, version INTEGER NOT NULL, reviewer_id TEXT NOT NULL,
  reviewer_role TEXT NOT NULL, kind TEXT NOT NULL, decision TEXT NOT NULL, notes TEXT, ts TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS model_deployment(
  deployment_id TEXT PRIMARY KEY, model_id TEXT NOT NULL, version INTEGER NOT NULL, disease_id TEXT NOT NULL,
  target_signal TEXT NOT NULL, geography_scope TEXT NOT NULL, serving_alias TEXT NOT NULL, status TEXT NOT NULL,
  approved_by TEXT, deployed_at TEXT, retired_at TEXT, previous_deployment_id TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_deploy_alias ON model_deployment(disease_id, target_signal, serving_alias, status);

CREATE TABLE IF NOT EXISTS forecast(
  forecast_id TEXT PRIMARY KEY, disease_id TEXT NOT NULL, target_signal TEXT NOT NULL, model_id TEXT NOT NULL,
  model_version INTEGER NOT NULL, deployment_id TEXT, serving_alias TEXT NOT NULL, issue_at TEXT NOT NULL,
  anchor_week TEXT NOT NULL, horizons_json TEXT NOT NULL, parquet_uri TEXT NOT NULL, sha256 TEXT NOT NULL,
  row_count INTEGER NOT NULL, provenance_json TEXT NOT NULL, quality_status TEXT NOT NULL,
  is_public INTEGER NOT NULL DEFAULT 0, is_synthetic INTEGER NOT NULL DEFAULT 0, job_id TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_forecast_pub ON forecast(disease_id, is_public, created_at);

CREATE TABLE IF NOT EXISTS scenario(
  scenario_id TEXT PRIMARY KEY, version INTEGER NOT NULL, name TEXT NOT NULL, mode TEXT NOT NULL,
  spec_json TEXT NOT NULL, spec_hash TEXT NOT NULL, status TEXT NOT NULL, validation_json TEXT,
  parent_scenario_id TEXT, created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  review_state TEXT NOT NULL DEFAULT 'draft');

CREATE TABLE IF NOT EXISTS simulation_run(
  run_id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL, scenario_version INTEGER NOT NULL, spec_hash TEXT NOT NULL,
  forecast_id TEXT, job_id TEXT, status TEXT NOT NULL, mode TEXT NOT NULL, summary_json TEXT, result_uri TEXT,
  result_sha256 TEXT, provenance_json TEXT, warnings_json TEXT, created_by TEXT, created_at TEXT NOT NULL,
  finished_at TEXT);

CREATE TABLE IF NOT EXISTS capacity_override(
  override_id TEXT PRIMARY KEY, facility_id TEXT, municipality_id TEXT NOT NULL, effective_date TEXT NOT NULL,
  verified_available_beds INTEGER, teams INTEGER, staff_hours REAL, visits_per_week REAL, planner_id TEXT NOT NULL,
  expires_at TEXT, note TEXT, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS alert(
  alert_id TEXT PRIMARY KEY, rule_id TEXT NOT NULL, disease_id TEXT NOT NULL, municipality_id TEXT NOT NULL,
  target_week TEXT NOT NULL, forecast_id TEXT NOT NULL, evidence_json TEXT NOT NULL, state TEXT NOT NULL,
  reviewer_id TEXT, review_note TEXT, created_at TEXT NOT NULL, reviewed_at TEXT);

CREATE TABLE IF NOT EXISTS settings_kv(key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS idempotency(
  key TEXT PRIMARY KEY, actor_id TEXT NOT NULL, route TEXT NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL);
"""),
    ("0010_nowcast", """
CREATE TABLE IF NOT EXISTS nowcast_run(
  nowcast_id TEXT PRIMARY KEY, job_id TEXT, created_by TEXT, created_at TEXT NOT NULL,
  status TEXT NOT NULL,            -- published | gate_failed (a failed run is stored, never served as latest)
  diseases_json TEXT NOT NULL, asof_week TEXT NOT NULL, asof_cutoff TEXT NOT NULL,
  source_snapshot_id TEXT, input_sha256 TEXT, config_json TEXT NOT NULL, config_hash TEXT NOT NULL,
  metrics_json TEXT, gates_json TEXT, artifact_uri TEXT NOT NULL, provenance_sha256 TEXT,
  is_synthetic INTEGER NOT NULL DEFAULT 0, label TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_nowcast_status_time ON nowcast_run(status, created_at);
"""),
    ("0020_ops", """
CREATE TABLE IF NOT EXISTS ops_incident(
  incident_id TEXT PRIMARY KEY, name TEXT NOT NULL, pathogen TEXT NOT NULL,
  mode TEXT NOT NULL,              -- live (real reports, OBSERVED) | drill (exercise, SYNTHETIC)
  status TEXT NOT NULL, settings_json TEXT NOT NULL, created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS ops_report(
  report_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES ops_incident(incident_id),
  kind TEXT NOT NULL,              -- case | suspected | death | recovered | vaccinated | tested
  count INTEGER NOT NULL, lat REAL, lon REAL, province_id TEXT, event_date TEXT NOT NULL, reported_at TEXT NOT NULL,
  reporter_id TEXT, source TEXT NOT NULL, note TEXT, voided INTEGER NOT NULL DEFAULT 0, void_reason TEXT);
CREATE INDEX IF NOT EXISTS ix_ops_report ON ops_report(incident_id, voided, event_date);

CREATE TABLE IF NOT EXISTS ops_zone(
  zone_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES ops_incident(incident_id),
  kind TEXT NOT NULL,              -- affected | quarantine | buffer | safe | vaccination
  name TEXT NOT NULL, geometry_json TEXT NOT NULL,
  basis TEXT NOT NULL,             -- real (enforced on the ground) | simulated (what-if only)
  status TEXT NOT NULL,            -- proposed | active | lifted
  origin TEXT NOT NULL,            -- manual | suggested | ai
  params_json TEXT NOT NULL, created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_ops_zone ON ops_zone(incident_id, status);

CREATE TABLE IF NOT EXISTS ops_resource(
  resource_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES ops_incident(incident_id),
  kind TEXT NOT NULL,              -- team | vaccine | test_kit | bed | ambulance
  name TEXT NOT NULL, qty REAL NOT NULL, lat REAL, lon REAL, province_id TEXT, created_by TEXT, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS ops_plan(
  plan_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES ops_incident(incident_id),
  basis TEXT NOT NULL, status TEXT NOT NULL, plan_json TEXT NOT NULL, created_by TEXT, created_at TEXT NOT NULL,
  approved_by TEXT, approved_at TEXT);

CREATE TABLE IF NOT EXISTS ops_event(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL, ts TEXT NOT NULL, actor TEXT,
  kind TEXT NOT NULL, text TEXT NOT NULL, data_json TEXT);
CREATE INDEX IF NOT EXISTS ix_ops_event ON ops_event(incident_id, seq);
"""),
]

_local = threading.local()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def conn() -> sqlite3.Connection:
    path = get_settings().db_path
    c = getattr(_local, "conn", None)
    if c is None or getattr(_local, "path", None) != path:
        c = _connect(path)
        _local.conn, _local.path = c, path
    return c


def close() -> None:
    c = getattr(_local, "conn", None)
    if c is not None:
        c.close()
        _local.conn = None


def migrate() -> list[str]:
    c = conn()
    c.execute("CREATE TABLE IF NOT EXISTS schema_migrations(id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    done = {r[0] for r in c.execute("SELECT id FROM schema_migrations")}
    applied = []
    for mid, sql in MIGRATIONS:
        if mid in done:
            continue
        c.executescript("BEGIN;" + sql + f"\nINSERT INTO schema_migrations VALUES('{mid}', datetime('now'));COMMIT;")
        applied.append(mid)
    return applied


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise


def q(sql: str, *args: Any) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, args)]


def q1(sql: str, *args: Any) -> dict | None:
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def ex(sql: str, *args: Any) -> int:
    cur = conn().execute(sql, args)
    return cur.rowcount


def insert(table: str, row: dict, c: sqlite3.Connection | None = None) -> None:
    cols = list(row)
    vals = [dumps(v) if isinstance(v, (dict, list)) else v for v in row.values()]
    (c or conn()).execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)


def update(table: str, where: dict, values: dict, c: sqlite3.Connection | None = None) -> int:
    sets = ",".join(f"{k}=?" for k in values)
    w = " AND ".join(f"{k}=?" for k in where)
    vals = [dumps(v) if isinstance(v, (dict, list)) else v for v in values.values()]
    return (c or conn()).execute(f"UPDATE {table} SET {sets} WHERE {w}", vals + list(where.values())).rowcount


def jcol(row: dict | None, *cols: str) -> dict | None:
    """Decode the JSON TEXT columns of a row in place."""
    if row is None:
        return None
    for col in cols:
        if col in row and isinstance(row[col], str):
            try:
                row[col] = loads(row[col])
            except Exception:
                pass
    return row
