# HEALTHPULSE 3.1 — ADMIN, FEATURE STORE & MODEL DEVELOPMENT STUDIO

**Status:** Detailed implementation specification; none of the admin/ML Studio services described here are claimed to be implemented.  
**Extends:** `docs/IMPLEMENTATION_PLAN_AI_SIMULATION_WEB.md` (HealthPulse 3.0).  
**Product scope:** Brazilian municipality-level dengue and severe respiratory signals (COVID-19 SARI / influenza SARI); separate occupational vulnerability, hospital demand and simulation layers.  
**Design principle:** Data -> quality-controlled vintage -> point-in-time feature set -> reproducible training -> reviewed model -> published forecast -> version-pinned simulation.

## 17. Product outcome and administrator journeys

HealthPulse Admin is not a decorative settings page and not a notebook embedded in an iframe. It is a first-class, role-gated operational environment in the same React application, with an API-driven workflow for all state-changing actions.

An operator should be able to (1) register an authorized data source; (2) verify/download a preview; (3) create an ingestion schedule; (4) inspect raw/normalized schema, quality and last successful snapshot; (5) define features and create an immutable training dataset with a specified forecast issue date; (6) configure a reproducible training/backtest run; (7) compare experiments and evaluate risk; (8) propose and approve a model release; (9) monitor the model and roll back; (10) trace any published prediction and simulation to the source releases that generated it.

### 17.1 Admin information architecture

```text
Admin
├─ Control Center       /admin                       source health, queue, stale versions, approvals
├─ Data Sources         /admin/sources                registry, connector settings, frequency, credentials refs
│  ├─ Source Detail     /admin/sources/:sourceId      versions, schema map, test connection, manifests
│  └─ Ingestion Runs    /admin/ingestions             schedule, retry, logs, quarantine, backfills
├─ Data Catalog         /admin/catalog                Bronze/Silver/Gold schemas, geography, labels, lineage
│  └─ Quality Monitor   /admin/quality                validation gates, warnings, freshness, incident history
├─ Feature Store        /admin/features               entities, feature registry, feature sets, versions
│  ├─ Feature Builder   /admin/features/new           expression editor, preview, PIT validator
│  ├─ Feature Sets      /admin/feature-sets            immutable snapshots, offline availability
│  └─ Dataset Builder   /admin/datasets/new           labels, splits, leakage audits, materialize
├─ Model Studio         /admin/models                 model catalog + disease / target coverage
│  ├─ Experiments      /admin/experiments             wizard, queued runs, logs, scores, ablations
│  ├─ Compare           /admin/experiments/compare    forecast baselines, per-fold / horizon metrics
│  ├─ Registry         /admin/registry                model versions, cards, approvals, aliases
│  ├─ Deployments      /admin/deployments             staging/champion, rollout, rollback, jobs
│  └─ Monitoring       /admin/model-monitoring        delayed labels, drift, calibration, freshness
├─ Simulation Audit    /admin/simulation-audit        pinned forecast/model/assumptions/results
├─ Access & Audit      /admin/security                roles, approvals, source licensing, audit log
└─ Settings            /admin/settings                compute, worker limits, secrets refs, retention
```

Use persistent left navigation and a command/status bar. All pages display active workspace (`pilot_br`), environment (`local`, `staging`, `production`), and data freshness. Every interactive button must call an API (or be explicitly disabled with a reason); no fake successful training progress. All tables support search, filters and pagination. Important warnings appear inline with the item they affect.

### 17.2 Role matrix

| Permission | Viewer | Data Operator | Data Scientist | Epidemiology Reviewer | Platform Admin |
|---|---|---|---|---|---|
| Inspect aggregated sources / feature metadata / model metrics | Yes | Yes | Yes | Yes | Yes |
| Edit connector config, schedule ingestion, map schema | No | Yes | No | No | Yes |
| Register feature definition and build datasets | No | No | Yes | No | Yes |
| Launch training/tuning/backtesting | No | No | Yes | No | Yes |
| Propose model release / review model card | No | No | Yes | Yes (review) | Yes |
| Approve model to live forecast service | No | No | No | Yes (clinical signoff) | Yes (platform signoff) |
| Manage service credentials / users / retention | No | No | No | No | Yes |
| View raw sensitive SIVEP records | No | Restricted only | No by default | No | Not automatically |

Use dual approval for production model change where feasible: an epidemiological reviewer assesses appropriateness; a platform admin confirms technical readiness. Roles are enforced server-side, not only by hidden buttons. For a demo, use authenticated accounts or seeded explicit demo roles; do not show a public production admin page.

## 18. Data Source Manager — real ingestion operations

### 18.1 Connectors in MVP

| Connector ID | Ingestion mode | Natural grain | MVP behavior | Special guardrails |
|---|---|---|---|---|
| `brazil_dengue_multimodal` | Download a pinned Zenodo file / checksum | municipality-week | pinned historic training snapshot | audit retrospective columns and license |
| `infodengue` | HTTP API by IBGE code and year | municipality-week plus release vintage | schedule incremental pulls + keep revisions | don't overwrite earlier releases |
| `sivep_gripe` | Official bulk export, safe local import | patient rows -> municipality-week | restricted transform then aggregated Silver | segregate raw personal data; label SARI |
| `meta_mobility` | Authorized bulk import / permitted API where available | source geo-day | import with licensed source and crosswalk | not a measured origin-destination matrix |
| `ibge_commuting` | Official census normalized file import | origin/destination census period | static OD structural proxy | baseline, not live trips |
| `nager_holidays` | HTTP year / country request | date-country | year refresh / precompute future holidays | local holidays need separate source |
| `open_meteo` | Historical + forecast APIs | coordinate/time, issue_at | hourly/weather release load and snapshot | do not confuse reanalysis vs issued forecast |
| `worldpop` | Versioned raster import/approved download | raster/year | aggregate with authoritative geography | processing can be expensive; area-weighted QA |
| `geoboundaries` | Download release/polygon | administrative polygons/version | validate against official IBGE codes | geometry crosswalk not name-based |
| `ibge_cempre` | Census/statistics file import | municipality/year/sector | periodic workforce update | no sector-specific illness counts |
| `cnes_facilities_beds` | Government export / scheduled import | establishment/month/bed type | snapshot and dedup | registered != staffed/available beds |

For missing/unlicensed sources, the connector status is `needs_authorization` or `manual_import_required`, not `connected`. All imports must show whether data is observed, model-estimated, manually entered, or synthetic. Never silently substitute simulated input for absent source data.

### 18.2 Source registration form

Fields: `source_id` (immutable slug), display name, owner, provenance URL, connector type (`http_json|http_csv|file_upload|bulk_archive|georaster`), upstream cadence, default time zone, region/grain, contract version, data sensitivity classification, license/attribution, expected schema, ID mapping, field units, retry/rate limit, timeout, expected publication lag, freshness threshold, credential reference, historical retention, and QA policy.

HTTP connector properties are allowlisted: server-side URL scheme `https`, approved hostnames and redirects, bounded response size, no user-submitted arbitrary local/metadata URLs (SSRF control). Uploads require extensions/content sniffing, archive traversal and decompression limits, virus scan where deployed, checksums and quarantine. Secret values must never appear in URL logs, admin payloads or Git.

The **source detail view** displays: connector state, last attempt / last successful snapshot, oldest/newest covered period, last `available_at`, file bytes/row count, 7/30-day success rate, schema drift, missingness, geo coverage and three actions: `Test`, `Preview sample`, `Run ingest`. All actions return a real job status. Preview only approved aggregated or nonsensitive columns; no raw SIVEP patient previews in general Admin UI.

### 18.3 Ingestion job finite state machine

`draft -> queued -> running -> (succeeded | succeeded_with_warnings | quarantined | failed | canceled)`; retry is a new attempt linked to the original job, never a reset of the audit record. Jobs have idempotency keys derived from source/partition/connector config + payload checksum, bounded retries with exponential backoff and a dead-letter / manual-review state.

Stages are `discover -> download -> checksum -> parse -> schema-map -> validate -> silver -> publish -> refresh-dependent-features`. Each stage records timestamps, progress, input/output row counts, warnings, logs and artifact URI. Silver publication is atomic; an unsuccessful refresh must leave the last known-good version intact and marked stale. Backfill jobs allow a requested period and show estimated impact on dependent feature sets and models; no automatic retraining or model deployment without policy.

### 18.4 Schema mapping & quality

Schema mapping is a versioned per-source contract, not free-form arbitrary user Python. Example `raw.IBGE7 -> municipality_id`, `raw.data -> notification_date`, `raw.casoshospitalizados -> sari_admissions`; conversions specify parse format, unit, null policy and whether a field contains PII. Municipality ID always preserved as zero-padded string; no implicit conversions to integer.

Gates: unique natural keys, numeric ranges, expected unit constraints, disease definition validity, geography crosswalk, date/epi-week consistency, missingness by field and partition, report lag, anomaly warnings (large count jumps are reviewable, not automatically invalid), licensed usage and anti-leakage metadata. A failing hard gate quarantines the release; soft gates produce warnings. Store a DQ summary separately for every snapshot.

### 18.5 Data Source API contracts

| Method | Route (admin auth) | Behavior |
|---|---|---|
| GET / POST | `/api/v1/admin/sources` | list / register connector metadata |
| GET / PATCH | `/api/v1/admin/sources/{id}` | details / safe configuration revision with optimistic locking |
| POST | `/api/v1/admin/sources/{id}/test` | enqueue bounded connection/schema probe, returns `202 job_id` |
| POST | `/api/v1/admin/sources/{id}/preview` | sample normalized safe columns, capped rows |
| POST | `/api/v1/admin/sources/{id}/ingest` | request bounded ingestion period; `202 job_id` |
| GET | `/api/v1/admin/ingestions` | paginated ingest attempts, health and filtering |
| GET | `/api/v1/admin/ingestions/{job_id}` | job progress/stages/artifacts/warnings |
| POST | `/api/v1/admin/ingestions/{job_id}/retry` | requeue with new attempt ID; idempotent |
| GET | `/api/v1/admin/sources/{id}/snapshots` | immutable Bronze/Silver versions and DQ reports |
| GET | `/api/v1/admin/catalog` | dataset schemas, lineage, ownership |
| GET | `/api/v1/admin/quality` | quality incidents, stale sources, quarantines |

Request body must never include plaintext password/token; use a secret reference owned by a protected secrets subsystem. Return only safe metadata for sensitive sources. Version conflict returns HTTP 409; missing permission returns 403; quota exceeded returns 429.

## 19. Feature Store Studio — definitions, PIT correctness and lineage

### 19.1 Architecture: MVP versus scale-out

**MVP:** Offline Gold store = partitioned Parquet, Polars LazyFrames and optional DuckDB for preview; metadata catalog = PostgreSQL or SQLite; immutable feature set manifests and content-addressed artifacts. This meets weekly batch forecasts without operational Redis or an online feature database. Later, optionally add Feast with an offline/online serving adapter if serving requirements actually justify it. Feast can coordinate historical/online features but does not replace custom surveillance release-vintage semantics; test both event-time and `available_at` constraints before migration.

**Feature layers:**

```text
Bronze source snapshots (versioned hash + ingestion time)
  -> Silver canonical typed tables (source release version preserved)
     -> candidate feature definitions (approved, owner, logic + tests)
        -> Gold offline feature views (municipality / week / disease)
           -> dataset snapshot for each forecast issue / training fold
              -> model training + batch inference
                 -> optionally materialize latest permissible features to online store (P2)
```

Feature store is **not** a single huge joined table of every source. Maintain distinct view grains and join on explicit entity + time contracts. `facility-month` and `municipality-year-sector` do not join directly to municipality-week disease without aggregation / as-of logic. Define a feature's observation timestamp, source-publication timestamp and upstream release vintage separately.

### 19.2 Core concepts and schema

| Entity / table | Primary identity | Mandatory metadata |
|---|---|---|
| `feature_entity` | `entity_id` | name, key dtype, geography scope; e.g. `municipality_id:Utf8[7]` |
| `feature_definition` | `feature_id, version` | owner, description, sources, expression hash, unit, dtype, lag, null policy, availability contract, leakage status |
| `feature_view` | `view_id, version` | entities, grain, inputs, windows, TTL/freshness, schedule, transformation code SHA |
| `feature_set` | `feature_set_id, version` | pinned list of feature IDs/versions, target compatibility, hash, approval |
| `feature_materialization` | `materialization_id` | feature_set ID, data snapshot IDs, geography/week range, storage URI, checksum, DQ status, created_at |
| `training_dataset` | `dataset_id` | target, disease, horizons, issue dates, label availability, split definitions, materialization ID, rows/checksum |
| `feature_lineage` | `(upstream_ref, downstream_ref)` | deterministic artifact DAG edges and process IDs |

**Feature contract fields:** `feature_name`, `description`, `entities`, `grain`, `dtype`, `unit`, `feature_group`, `source_refs`, `expression`, `valid_time_column`, `available_at_column`, `lookback_window`, `min_history`, `null_policy`, `release_delay`, `quality_rules`, `privacy_class`, `stability`, `owner`, `version`. Store expressions in checked-in code and declarative definitions; UI is a controlled editor, not arbitrary code execution.

Example feature groups:
- Epidemiology: dengue `cases_lag_1w/2w/4w`, reporting-adjusted recent counts (if released), 4-week growth; disease/signal-specific.
- Climate: rain sum last 2–4 fully observed weeks, humidity, heat anomaly, forecast temperature/rain for the horizon from a **previously issued forecast**.
- Mobility: recent distance-band movement, mobility deviation from baseline, commuter-weighted neighboring lagged incidence **proxy**.
- Holiday: known calendar holidays within horizon, days to/from holiday, potential school breaks only with verified source.
- Workforce: annual sector share, commuters per working-age population, outdoor-work exposure proxy with documented construction.
- Health system: CNES registered capacity, verified/planner-entered available capacity **only** for simulations; do not use post-outcome capacity as a predictor of past incidence.

### 19.3 Point-in-time join algorithm (must be a tested library function)

For each training/scoring row `(municipality_id, disease_id, forecast_issue_at, horizon)`:

1. Build an **issue-time entity spine** and choose the label definition (`observed_notifications`, `estimated_notifications`, or `sari_admissions`) independently. `target_week` starts after issue week; training labels must have a recorded label release time and be available by experiment cutoff.
2. Restrict source snapshots to `snapshot.available_at <= forecast_issue_at`; if a corrected historical release arrived later, it **must not** appear in the earlier simulation/backtest row.
3. For each feature, select the most recent eligible `event_time <= feature_observation_cutoff` and `published_at <= forecast_issue_at`, within lag/freshness limits. Join keys must be unique at the chosen grain; assertions reject fan-out.
4. Future **public holidays** are permitted as known-ahead features. Future climate must come from an issued forecast available at issue time; future observed rain and future observed mobility are forbidden in historical backtests.
5. Compute lag/rolling aggregates strictly before target start, respecting source publication lag. Record `max_input_available_at`, source vintage IDs, feature expression hash and coverage statistics for every row/set.
6. If historical release vintages do not exist, mark retrospective data features `PIT_UNVERIFIED` and exclude any claim of release-accurate prospective performance. Provide a conservative published-data-lag approximation with its limitations; never fabricate `available_at` timestamps.

**Example query-time invariants:** `feature.available_at <= issue_at`, `label.week_start > issue_week_start`, `feature_set approved`, `(municipality_id, week_start, disease_id, feature_set_version)` unique. Separate transformations for cross-region commuter edges with normalized IBGE origin/destination.

### 19.4 Feature Builder UI

Tabs: `Catalog`, `New Feature`, `Feature Sets`, `Materializations`, `Lineage`, `Data Preview`.

Builder workflow: choose source tables -> select entity/grain -> choose safe transform template (`lag`, `rolling_mean`, `rolling_sum`, `holiday_window`, `static_asof`, `neighbor_pressure`, `ratio`) -> set lookback and availability rules -> preview first N **deidentified** rows -> run PIT and cardinality tests -> save draft -> approve definition -> materialize asynchronously. Preview shows distribution, missingness, region/date coverage and sample lineage. Every change creates a version; currently used definitions are immutable and cannot be deleted while referenced.

Feature Set screen shows readiness matrix: source data missing / stale / PIT safe / blocked / published for each disease × horizon. Dataset Builder lets data scientist choose model target and feature set, issue dates, train/validation/test spans, geography groups, optional feature ablation and label cutoff; result is a pinned immutable dataset manifest. Zero rows, missing target labels and leakage warnings prevent training.

### 19.5 Feature Store API

| Method | Endpoint | Action |
|---|---|---|
| GET / POST | `/api/v1/admin/features` | list / create validated feature definition draft |
| GET / PATCH | `/api/v1/admin/features/{id}/versions/{v}` | inspect / update only mutable draft |
| POST | `/api/v1/admin/features/{id}/validate` | PIT, dtype, cardinality, null and privacy checks |
| POST | `/api/v1/admin/features/{id}/preview` | capped safe dataset preview, no executable arbitrary SQL |
| POST | `/api/v1/admin/feature-sets` | assemble and version feature sets |
| POST | `/api/v1/admin/feature-sets/{id}/materialize` | async job, pinned source snapshot refs |
| GET | `/api/v1/admin/feature-sets/{id}/lineage` | DAG for user inspection |
| POST | `/api/v1/admin/training-datasets` | materialize dataset with issue-time + target release constraints |
| GET | `/api/v1/admin/training-datasets/{id}` | manifest, splits, feature report, PIT audit |

Feature updates never silently alter existing model versions. Materialization becomes `ready` only after all hard DQ gates and checksum checks pass.

## 20. Built-in AI Model Development & Training Lab

### 20.1 Scope and responsibilities

Admin Model Studio provides **experiment management, structured training configuration, reproducible execution, evaluation, registry workflow, and inference deployment**. It is not a remote arbitrary-code execution service. User-selectable model recipes are allowlisted and executed in isolated bounded workers, not in the API server process. Advanced users can develop recipes in checked-in Python packages and register reviewed recipe IDs; UI users cannot paste unrestricted shell commands.

**Target selection:** `dengue_reported_cases`, `dengue_estimated_cases`, `covid19_sari_admissions`, `influenza_sari_admissions` as separate series with distinct labels and metrics. Forecast horizons 1/2/3/4 weeks; do not conflate SARI admissions with community incidence. Train on data available under the dataset's declared cutoff; eval on later held-out periods. Disease simulation components are consumers of published forecasts; they are not train targets by default.

### 20.2 Web experiment wizard (six steps)

1. **Problem:** choose disease, target signal, geography scope, issue-time cadence, 1–4 week horizon; select count/rate and population denominator.
2. **Data:** choose an immutable approved `training_dataset` version; inspect target completeness, time range, available label releases and missingness.
3. **Features:** select curated feature set; display missingness, PIT status and planned ablation groups (cases-only, + climate, + holiday, + mobility, + commuting graph). No target leakage features.
4. **Algorithm:** select `seasonal_naive`, `HistGradientBoostingRegressor(poisson)`, optional `LightGBM` Poisson, optional Negative Binomial model; choose bounded hyperparameter search (Optuna optional), random seed and CPU/memory limits.
5. **Validation:** rolling-origin folds with gap/report-lag embargo, test window, geography holdout, prediction interval method, disease-specific metrics and quality thresholds.
6. **Review & Start:** estimate run cost/time as nonbinding estimate, show exact manifest hashes, owner and permissions; `POST train` yields job ID and live stage logs. Show cancel/stop actions, no claims of completion until worker reports success.

### 20.3 Reproducible training run manifest

```yaml
experiment:
  experiment_id: dengue_br_h1_feature_ablation
  target: dengue_reported_cases
  target_signal: notification
  geography: municipality
  horizons_weeks: [1, 2, 3, 4]
  dataset_id: ds_20261102_br_dengue_v1
  feature_set: {id: br_dengue, version: 2}
  train_cutoff_as_of: '2026-10-25T00:00:00Z'
  data_vintage_policy: actual_vintages_required_for_prospective_claim
  split:
    kind: rolling_origin
    folds: 4
    validation_weeks: 8
    embargo_weeks: 2
    geography_holdout: []
  algorithms:
    - recipe: seasonal_naive
    - recipe: hgb_poisson
      search: {enabled: false}
    - recipe: lightgbm_poisson
      search: {enabled: true, n_trials: 12, timeout_seconds: 900}
  calibration: {method: out_of_fold_residual, nominal_coverage: 0.8}
  resources: {max_cpu: 4, max_memory_gb: 6, timeout_minutes: 35}
  seed: 42
  required_reports: [temporal_leakage, performance, calibration, ablation, model_card]
```

The training worker persists: code commit/hash, Python/package lockfile, dataset and feature-set content hashes, upstream source releases, split boundaries, fitted preprocessing, algorithm params, seed, per-fold predictions and labels, metrics by disease/region/horizon, logs, generated model files and immutable evaluation reports.

### 20.4 Training lifecycle, workers and safety

Training states: `created -> validated -> queued -> preparing -> training -> evaluating -> packaging -> completed` or `failed/canceled/timed_out`. Worker stack: `api` (FastAPI), `queue` (Redis + RQ/Arq/Celery) or a durable DB queue for one-node pilot, `worker-training`, `worker-ingestion`, `mlflow` (tracking + registry), `postgres` (metadata), optional `minio` (artifacts). FastAPI `BackgroundTasks` is **not** used for long CPU-bound training or large imports. Enforce per-user run concurrency, dataset row/size caps, memory and execution timeouts, cancellation, file sandboxing and safe artifact paths.

MLflow stores experiments, runs, metrics, artifacts and model versions. For registry UI/API, configure a database-backed tracking backend and durable artifacts. Avoid relying on deprecated stage semantics; maintain review state in HealthPulse (`DRAFT -> CANDIDATE -> REVIEWED -> APPROVED -> DEPLOYED -> RETIRED`) and use model registry aliases such as `champion` and `challenger` for serving assignment. MLflow alone does not provide HealthPulse clinical review or row-level health-data access policies.

### 20.5 Evaluation dashboard

Tabs: `Overview`, `Temporal folds`, `Geographic transfer`, `Error analysis`, `Intervals`, `Ablation`, `Lineage`, `Model card`.

Required results: MAE, WAPE (defined with denominator safeguards), bias, count deviance or Poisson deviance as appropriate, epidemic-warning precision/recall with reviewed threshold, empirical 80% interval coverage and interval width; line plots forecast vs observed by origin and horizon; failure slices for zero/low counts, low-population areas, holiday periods and missing mobility. Always display model versus persistence/seasonal naive with matched folds. Performance metrics may be missing, but should never be fabricated or shown from test-set leakage.

**Release gates (configurable and disease-reviewed):** data/label definition complete, PIT audit PASS or explicitly labeled limited-evidence nonproduction, temporal holdout completed, no critical DQ failures, baseline comparison provided, uncertainty evaluated, subgroup performance and robustness reported, model card accepted, reviewer and platform approvals. An impressive aggregate MAE alone cannot bypass missing releases or calibration failures. CI can block operational deployment while still allowing demo staging of an explicitly labeled experimental model.

### 20.6 Model Registry, deployment and inference

Model registry table: `model_family`, `disease_id`, `target_signal`, `horizon`, `version`, `artifact_uri`, `training_run_id`, `feature_set_version`, `model_card_uri`, `approval_status`, `approved_by`, `created_at`, `serving_alias`, `inference_contract_version`.

Deployment workflow:
1. Data scientist registers candidate (immutable artifact) and proposes it for review.
2. Reviewer verifies label, target, sources, uncertainty and domain constraints; admin checks runtime and access. Both approvals recorded.
3. Deploy to **shadow** first: generates forecasts without replacing public outputs; compare against incumbent and audit latency/freshness.
4. Promote model alias to `champion` for chosen disease/horizon/regions only after review. Keep `previous_champion` and rollback path.
5. Batch inference reads a pinned ready feature set, checks availability + schema, produces versioned forecast file and publishes metadata atomically. Simulation service consumes a specific `forecast_id`, not whatever champion changes to later.
6. On blocked or stale features, return `insufficient_data` or route to approved baseline; no silent zeros or last-week-value mislabeled as model prediction.

Monitoring: weekly data freshness and schema change, label delay, feature distribution shift (PSI or comparable indicators used as diagnostics not definitive drift proof), prediction output distributions, model-vs-baseline error once outcomes become available, interval coverage, missingness, training/serving feature parity, and region-level fairness. Alerts produce review tickets rather than autonomous model promotion.

### 20.7 Model Development APIs

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/v1/admin/model-recipes` | approved algorithms, params and resource bounds |
| POST | `/api/v1/admin/experiments` | create experiment draft with immutable config revision |
| POST | `/api/v1/admin/experiments/{id}/validate` | dataset/feature/target/PIT/compute checks |
| POST | `/api/v1/admin/experiments/{id}/run` | queue training, return `202 + job_id` |
| GET | `/api/v1/admin/training-runs/{job_id}` | stage, logs, resource use, report refs |
| POST | `/api/v1/admin/training-runs/{job_id}/cancel` | authorized cancellation |
| GET | `/api/v1/admin/experiments/compare?ids=...` | comparable metrics + split compatibility warnings |
| GET / POST | `/api/v1/admin/registry/models` | list / register validated model artifact |
| POST | `/api/v1/admin/registry/{model_id}/review` | reviewer decision, notes and reason |
| POST | `/api/v1/admin/deployments/{id}/promote` | gated shadow/champion promotion |
| POST | `/api/v1/admin/deployments/{id}/rollback` | restore pinned prior model alias |
| GET | `/api/v1/admin/model-monitoring` | drift, calibration, inference health |

All mutations take `Idempotency-Key`, `actor`, `reason` where appropriate and `expected_version` for conflict detection. Audit event includes old/new refs without exposing secrets or sensitive patient records.

## 21. Data models and persistence layout

### 21.1 PostgreSQL/SQLite metadata registry (suggested)

```text
source_registry(source_id PK, connector_type, owner_id, status, config_json, config_version,
                auth_secret_ref, pii_class, license_ref, expected_grain, expected_cadence,
                approved_hostnames, updated_at)
source_snapshot(snapshot_id PK, source_id FK, bronze_uri, silver_uri, checksum, retrieved_at,
                data_available_at, coverage_start, coverage_end, schema_version, dq_status,
                row_count, manifest_uri)
ingestion_job(job_id PK, source_id FK, snapshot_id nullable, job_type, status, stage,
              requested_range, retry_of, attempt_no, started_at, finished_at, logs_uri)
feature_definition(feature_id, version PK, owner_id, entity_keys, grain, type, unit, expression_ref,
                   source_refs, availability_contract, quality_policy, state, hash)
feature_set(feature_set_id, version PK, member_versions_json, status, hash, created_at)
feature_materialization(materialization_id PK, feature_set_ref, source_vintages_json,
                        issue_time_range, parquet_uri, sha256, dq_status)
training_dataset(dataset_id PK, feature_materialization_id, target_signal, issue_range,
                 splits_json, label_vintages_json, pit_report_uri, parquet_uri, hash)
experiment_run(run_id PK, experiment_id, dataset_id, recipe_id, mlflow_run_id,
               status, config_hash, metrics_uri, report_uri, created_by)
model_registry(model_id, version PK, model_family, target_signal, horizons, run_id,
               artifact_uri, model_card_uri, approval_state, reviewer_signoff, hash)
model_deployment(deployment_id PK, model_id, version, disease_id, horizon,
                 geography_scope, serving_alias, status, approved_by, deployed_at)
audit_event(event_id PK, actor_id, action, target_type, target_id, timestamp,
            request_id, old_version, new_version, metadata_redacted)
```

Use normalized SQL migrations and indexes on `source_id + retrieved_at`, `dataset_id`, `model_id+version`, `status+created_at`, and geographic/temporal Parquet partition keys. Immutable snapshots and model artifacts live on filesystem/object store; metadata DB stores only references and checksums. PostgreSQL is recommended for multiuser environments; SQLite is suitable only for controlled single-process pilot. Use application-side UUID/ULID IDs or server-generated IDs, never user-controlled filesystem paths.

### 21.2 Artifact and partition paths

```text
data/raw/{source_id}/ingested_at={UTC_TIMESTAMP}/{sha256}.{ext}
data/silver/{table}/source_id={source_id}/vintage_id={snapshot_id}/part-*.parquet
data/gold/feature_views/{view_id}/version={view_version}/week={YYYY-WW}/part-*.parquet
data/gold/training_sets/{dataset_id}/train.parquet
artifacts/training/{run_id}/{config.json,metrics.json,fold_predictions.parquet,pit_audit.json}
artifacts/models/{family}/{version}/{model,signature.json,model_card.md,sha256.txt}
artifacts/forecasts/{forecast_id}/prediction.parquet
artifacts/simulation/{run_id}/{scenario.json,result.parquet,summary.json}
```

Never overwrite a published feature set, model or forecast in-place. Snapshot directories are immutable; aliases/metadata entries point to new versions. Retention policies avoid deleting artifacts still referenced by published forecasts or saved scenarios.

### 21.3 Prediction / simulation provenance contract

```json
{
  "forecast_id": "fcst_...",
  "model_version": "dengue_h1/4",
  "training_run_id": "trn_...",
  "training_dataset_id": "ds_...",
  "feature_set_ref": "br_dengue@2",
  "feature_materialization_id": "mat_...",
  "source_vintage_ids": ["infodengue_...", "openmeteo_..."],
  "forecast_issue_at": "2026-11-02T00:00:00Z",
  "label_signal": "notification",
  "quality_status": "PIT_VERIFIED_OR_EXPLICITLY_UNVERIFIED",
  "method": "batch_forecast",
  "is_synthetic": false
}
```

The example above is a **schema illustration**, not a real forecast; IDs are placeholders. Each simulation additionally pins `forecast_id`, service-use assumption set, solver version, random seed, available-resource snapshot, region scope and optimizer output hash.

## 22. Unified web UX and design specifications

### 22.1 Navigation boundary

Product-facing pages (`Overview`, `Explore`, `Forecast`, `Workforce`, `Facilities`, `Simulation`) are primarily read-only with safe scenario management. Admin pages (`Data Sources`, `Feature Store`, `Model Studio`, `Deployments`, `Monitoring`, `System`) require appropriate authorization. Both use the same design tokens and global region/disease/time state, but admin actions must not unintentionally change the published public forecast.

### 22.2 Screen compositions

**Control Center**: source health matrix; queued/failed jobs; stale forecasts; unapproved model candidates; latest feature materializations; training worker status; audit issues. Click item navigates to exact detail and filtered logs.

**Data Source Manager**: left source explorer and status badges (`healthy`, `stale`, `needs_auth`, `quarantined`, `disabled`); center source configuration form and coverage timeline; right schema mapping, freshness chart, provenance and preview. Include actions for connection test, create schedule, manual ingest, retry, quarantine/review, backfill and deprecate.

**Feature Store**: searchable feature dictionary with type, unit, disease compatibility, sources, availability lag, data quality and version. Drill-down shows transformation DAG, preview, distribution and leakage test. Materialization view shows geography/date coverage and latest readiness; Dataset Builder has stepwise validation and immutable outputs.

**AI Model Studio**: top tabs `Experiments`, `Run Compare`, `Registry`, `Deployments`, `Monitoring`; new experiment wizard; run view with real progress/log/metrics; compare side-by-side baselines on same folds and horizons; release approval drawer with model card and signoffs. Charts show observed vs prediction bands, error by region and horizon, calibration, feature importance **with caveat** that importance is not causal attribution.

**Simulation Studio integration**: selecting a scenario includes `forecast_id` and model card link; users can fork scenario based on older model version and compare to current model forecasts only when geographic/time/label compatibility is satisfied. Display two independent comparisons: `different model forecasts` vs `different resource allocation scenarios`; never imply model change itself prevented infections.

### 22.3 UX/state requirements

- Full working responsive layout (1440px desktop and 390px mobile), keyboard support, labeled form fields, WCAG AA contrast target, no color-only warnings, sensible reduced motion.
- State machine visible per operation: `idle -> validating -> queued -> running -> success/partial/fail`; progress comes from backend job state via SSE or polling, not simulated counters. Long jobs can be canceled.
- Every derived number shows `observed / estimated / forecast / scenario / synthetic`, period, unit, geography, current source and last update. Backtest and deployment tabs distinguish `PIT_VERIFIED` and `PIT_UNVERIFIED`.
- Provide optimistic editing with 409 conflict handling; no data loss on failed save. Destructive actions require confirmation and enforce reference checks.
- Use clear empty states: no mobility access, no SIVEP recent data, missing verified operational beds, no trained model; offer import/configure action rather than invented statistics.
- Model cards include usage restrictions, populations represented, target definition, data history, validation date, OOD warnings, uncertainty and responsible contact.
- Never expose raw worker movements, row-level patient attributes or secrets in map/detail/chart API responses.

## 23. Monorepo extension and backend service boundaries

Add modules while preserving existing CLI and Polars transformations:

```text
healthpulse_pipeline/
  healthpulse/
    admin/
      source_registry.py        # connector metadata, credential references, revisions
      ingestion_jobs.py         # enqueue/progress/retry/snapshot issuance
      schema_mapping.py         # controlled normalization mapping and DQ gates
      catalog.py                # dataset/source/column lineage and privacy classifications
      feature_registry.py       # definition validation, immutable versions, lineage
      feature_materialize.py    # Polars lazy views, issue-time snapshots
      pit_builder.py            # strict event_time and available_at joins
      dataset_builder.py        # labels, issue-time spine, freeze splits
      model_recipes.py          # allowlisted prediction algorithms
      training_runner.py        # separate worker tasks, limits, cancellation
      evaluation.py             # temporal folds, baselines, intervals, ablation
      model_registry.py         # MLflow adapter, approval, promotion, rollback
      monitoring.py             # data freshness, label delays, drift and calibration
      permissions.py            # role/action enforcement
      audit.py                  # immutable actor/action references
    api/routes/admin/
      sources.py ingestions.py catalog.py quality.py
      features.py feature_sets.py datasets.py
      experiments.py training_runs.py registry.py deployments.py
      monitoring.py audit.py
  web/src/pages/admin/
    AdminOverview.tsx Sources.tsx SourceDetail.tsx IngestionRuns.tsx
    Catalog.tsx Quality.tsx FeatureStore.tsx FeatureBuilder.tsx
    FeatureSets.tsx DatasetBuilder.tsx ExperimentWizard.tsx
    TrainingRun.tsx ExperimentCompare.tsx Registry.tsx
    Deployments.tsx ModelMonitoring.tsx AccessAudit.tsx
  config/admin/
    sources.example.yaml feature_definitions.example.yaml experiment.example.yaml
  docs/
    ADMIN_ML_STUDIO_IMPLEMENTATION.md
    MODEL_CARD_TEMPLATE.md
  tests/admin/
    test_source_contracts.py test_ingestion_idempotence.py
    test_feature_pit.py test_feature_immutable_versions.py
    test_training_dataset_splits.py test_training_jobs.py
    test_registry_release_gate.py test_deployment_rollback.py
    test_admin_rbac.py test_simulation_model_pinning.py
  web/tests/admin-e2e.spec.ts
```

No new admin Python implementation is assumed to exist just because files are listed here. Implement sequentially, with integration tests before moving to the next workstream.

## 24. Execution roadmap, scope and acceptance tests

### 24.1 Prioritized tickets

| Ticket | Priority | Owner skill | Definition of done |
|---|---|---|---|
| ADM-01 Source registry + DQ | P0 | Backend/Data | CRUD safe config, one real connector probe, manifest + quarantine + last-good snapshots |
| ADM-02 Ingestion operations UI | P0 | Frontend/API | operators can inspect, test, run and retry ingestion with real status/logs |
| FS-01 Feature catalog + availability contract | P0 | ML/Data | feature versions, units, owners, lineage; no invalid grain joins |
| FS-02 PIT-safe dataset builder | P0 | ML/Data | release-accurate replay where vintages exist; test catches future leaks |
| ML-01 Built-in experiment runner | P0 | ML/Backend | wizard queues bounded training; actual metrics and artifacts stored |
| ML-02 Evaluation + baseline comparison | P0 | ML/Frontend | fold charts, interval diagnostics, split parity, missing-data warnings |
| ML-03 Registry + version-pinned forecast | P0 | Backend/ML | register, reviewer approval, publish shadow, visible model ID, rollback |
| SIM-01 Scenario provenance integration | P0 | Simulation | saved scenario pins `forecast_id`, source and assumptions, reproducible result |
| ADM-03 Admin RBAC + audit | P0 | Backend/Security | API permissions, immutable audit, secret masking and sensitive data tests |
| ML-04 Monitoring + refresh policies | P1 | ML/Data | drift/coverage/freshness, retrain proposal, no autonomous promotion |
| FS-03 Optional low-latency online store | P2 | Platform | only if latency/QPS benchmarks justify it |
| ML-05 Optional advanced search / neural models | P2 | ML | only after strong baseline and external validity established |

### 24.2 Adjusted sprint plan through 20 Nov 2026

- **2–5 Nov — P0 foundations:** metadata tables, source registry, source snapshots, freeze schema contracts, real dengue + weather pipeline and source-status web view; keep existing read/map plan.
- **6–10 Nov — feature pipeline and minimal web studio:** PIT test dataset, feature catalog + dataset builder, runnable experiment with baseline and HGB Poisson, real training statuses and MLflow logging. Preserve public map and forecasting pages.
- **11–15 Nov — model registry + operational simulation:** publish one reviewed dengue model, issue versioned forecasts, scenario demand/optimizer consumes pinned forecasts; create compare and audit views.
- **16–19 Nov — integration, quality, demo:** source manager UI, leakage & privacy tests, simulated capacity transparency, model card and E2E demo. Shadow/rollback basic UI if time permits.
- **20 Nov — freeze & submit:** deterministic build, seed fixtures, visible limitations, short evidence-backed walkthrough.

**MVP scope cut:** limit training UI to existing vetted recipes, one disease as primary demo, three pilot municipalities, one feature set, manual source import for unavailable datasets, and simple reviewer signoff. Defer Feast, live employee telemetry, AutoML over arbitrary code, distributed GPU workers, nationwide real-time OD, and automatic public health actions.

### 24.3 Cross-module acceptance scenarios

1. **Source unavailable:** disable Meta connector; the UI flags missing mobility coverage and training dataset builder blocks mobility-inclusive feature sets or offers a labeled reduced model; no fabricated movement data.
2. **Revised disease vintage:** ingest a later InfoDengue revision and rerun *an older issue date*; the PIT training builder still selects the historically available version (or marks replay unverified if missing).
3. **Cardinality blow-up:** join facility-month to disease-week without aggregation; hard fail prevents multiplied case counts.
4. **PIT leak test:** inject feature with `available_at` one week after issue date; dataset builder rejects it.
5. **End-to-end train:** choose dengue, approved feature set, 1–4 week horizons; launch experiment; logs, run ID, per-fold metrics and artifact hashes are visible and consistent.
6. **Model approval:** candidate cannot promote without reviewer/platform approval; rejected model remains auditable. Shadow runs don't modify public forecasts.
7. **Pinned scenario:** after champion alias changes, rerunning an old scenario returns identical output for same input artifacts/solver/seed, within declared tolerance.
8. **Rollback:** restore prior champion; new inference uses prior approved version; earlier immutable forecasts remain unchanged.
9. **Clinical terminology:** switching to COVID-19 shows `COVID-19 SARI admissions`, not `all COVID cases`, throughout UI and export.
10. **Security:** viewer cannot mutate admin endpoints; source secrets never appear in response/log; patient rows never enter public API or generic admin preview.
11. **Resilience:** source download fails; dashboard retains last-known-good snapshot and visibly warns stale data; there is a retry, no silent success state.
12. **Model evaluation:** rolling-origin splits and geography holdouts report exact issue dates, fold metrics, and feature availability; comparisons on mismatched folds show a warning and cannot claim superiority.

### 24.4 Proposed engineering SLOs (not achieved metrics)

- P0 source-status list p95 under 500 ms from metadata database in three-municipality pilot.
- Feature list/search p95 under 700 ms; preview capped at 1,000 safe aggregated rows and under 5s for local pilot after warm cache.
- Training and ingestion requests return 202 with stable job ID in under 1s; heavy work is in bounded worker.
- A reproducible one-model demo can be built from pinned snapshots with one documented command for ingestion, dataset materialization, experiment, approval and forecast generation.
- Public-facing forecasts remain served from the last successfully approved release if an ingestion or training run fails, with freshness warning.

### 24.5 Engineering references to verify during implementation

- MLflow tracking / registry / aliases: https://mlflow.org/docs/latest/ml/model-registry/ and https://mlflow.org/docs/latest/tracking/
- Feast overview and historical retrieval: https://docs.feast.dev/ and https://docs.feast.dev/reference/offline-stores/overview
- FastAPI heavy tasks caveat: https://fastapi.tiangolo.com/tutorial/background-tasks/
- Polars joins: https://docs.pola.rs/api/python/stable/reference/dataframe/api/polars.DataFrame.join_asof.html

## 25. Explicit instructions for coding agents

Treat this document and the 3.0 implementation plan as a single specification. **Do not implement static fake training charts or pretend an unsupported connector is live.** Preserve `healthpulse.cli` behavior and existing source adapters. Implement a vertical slice first: real source ingest -> Bronze/Silver snapshot -> feature set -> frozen dengue training dataset -> real experiment metrics -> reviewed model -> published forecast -> simulation consumes forecast ID -> Admin UI shows lineage.

For each PR include: schema migration, typed Pydantic API models, real service implementation, unit/integration tests with historical-vintage fixture, frontend screen and Playwright test, observability, admin role tests, and a verified run command. Document anything missing or unverified. The application must remain truthful when real mobility, operational hospital capacity or clinical effectiveness data are absent.
