# Architecture

```
public APIs ──► Bronze (data/raw/{source}/ingested_at=TS/{sha}.{ext}, manifest.json, read-only)
                  │  checksum · sniff · schema map · DQ gates (hard→quarantine, soft→warn)
                  ▼
              Silver (data/silver/{table}/source_id=/vintage_id=/part-0.parquet, every release kept)
                  │  pit.candidates / pit.lookup: value used only if known_at ≤ issue_at
                  ▼
              Gold: feature materialization + training dataset (data/gold/training_sets/{id}/train.parquet, hash, PIT audit)
                  │  worker: rolling-origin backtest, intervals, ablation → artifacts/training/{run}/, artifacts/models/{recipe}/{run}/
                  ▼
              Registry (dual review, gates) → deployment alias (shadow | champion) → inference job
                  │  artifacts/forecasts/{forecast_id}/{prediction,features}.parquet + provenance.json (immutable)
                  ▼
              Scenario (pins forecast_id + assumptions + seed) → simulate job → artifacts/simulation/{run}/result.parquet (sha256)
```

## Processes

| Process | Command | Role |
|---|---|---|
| API | `uvicorn healthpulse.api.main:app` | read models, validation, enqueue (202 + job_id), SSE job events, static web app |
| Worker supervisor | `python -m healthpulse.worker` | claims jobs from the DB queue, runs each in a child process with a hard timeout, cooperative then forced cancel, heartbeats, orphan reaping |

Job types: `probe`, `ingest`, `import_file`, `synthetic_sivep`, `dataset_build`, `train`, `inference`, `simulate`, `nowcast`.

## Metadata (SQLite WAL; plain SQL, portable to PostgreSQL)

`source_registry`, `source_snapshot`, `jobs`, `job_log`, `audit_event`, `feature_definition`, `feature_set`,
`feature_materialization`, `training_dataset`, `experiment`, `experiment_run`, `model_registry`, `model_review`,
`model_deployment`, `forecast`, `scenario`, `simulation_run`, `capacity_override`, `alert`, `settings_kv`, `idempotency`.

## Time semantics

- Epidemiological weeks start on Sunday. Week 1 is the first week with ≥4 days in the year (`core/util.py`).
- Live forecasts are issued at the real instant. The anchor week is the latest case week actually released by then, and
  horizon *h* targets anchor + *h* weeks.
- Backtests use a nominal Wednesday 12:00 UTC issue in each issue week. Inputs are restricted to releases known by
  then.
- Without archived historical vintages, a backtest replays the documented publication lag, and the dataset is flagged
  `PIT_UNVERIFIED`.
- Climate features use lags ≥ 2 weeks because ERA5 reanalysis is released ~6 days after the week ends. The validator
  blocks shorter lags (see the shipped `rain_sum_1w_lag1` draft).

## Simulation maths

- **Demand draws.** For region *r* and week *t*, cases are drawn as
  C = exp(μ + σ(√ρ·Z_trial + √(1−ρ)·Z_rt)) − 1, with μ = log(1+p50) and σ = (log(1+p90) − log(1+p10)) / 2.563.
  ρ is the share of uncertainty common to all regions in a trial.
- **Visits.** Visits = C × visit_rate. SARI signals are already severe cases, so they are not multiplied again.
- **Unmet demand.** Unmet = max(V − capacity − n·v, 0).
- **Allocation (MILP).** Integer teams x_{b,r} from base *b* to region *r*. The objective is
  Σ w_r·u_r + λ·Σ cost·x, subject to:
  - u_r ≥ the piecewise-linear expected shortfall curve;
  - team availability at each base;
  - the budget;
  - the travel radius;
  - per-region caps;
  - equity floors.
- **Allocation (greedy).** Marginal-gain greedy at national scale; exact for equal costs.
- **Evaluation.** Baseline and proposal are evaluated on the same draws (common random numbers).
