# HealthPulse 3.1

Climate-informed disease forecasting, operational preparedness simulation and an auditable Admin & AI Development
Studio for a Brazil municipal pilot. Specification: [`docs/spec/`](docs/spec/).

Everything on screen is backed by an API call against loaded records. Each number says whether it is **observed**,
**forecast**, **scenario** or **synthetic**, with its period, unit and source.

## Quick start

```bash
python server.py        # that's it
```

This one command works from a fresh checkout with any Python 3.11 or newer:

1. It creates `.venv` and installs dependencies, using `uv` if present and `venv` + pip otherwise.
2. It loads `.env`.
3. On the first run it downloads the real public data and runs the full pipeline (about 5 minutes).
4. It starts the API and the worker, then opens <http://127.0.0.1:8080>.

`python -m healthpulse` does the same.

| Flag | Effect |
|---|---|
| `--port 9000` | serve on another port (or set `$PORT`) |
| `--no-data` | start with empty states and skip the first-run download |
| `--fetch-missing` | also ingest every source that has never been ingested (SRAG, WorldPop, Zenodo …) |
| `--full` | the first run also downloads the 565 MB Zenodo multimodal dataset |
| `--no-browser` | don't open a browser tab |

Manual equivalents:

```bash
make install            # Python 3.12 venv with uv
.venv/bin/hp demo       # pulls real data, then runs the full pipeline (below); --full adds Zenodo
.venv/bin/hp fetch      # ingest every available source that is still missing
.venv/bin/hp serve      # API + worker on http://127.0.0.1:8080
make test               # 38 offline tests, synthetic fixtures, no network
```

`hp demo` runs the full vertical slice on **real public data**:

1. **Ingest.** Real data:
   - IBGE municipality register (5,571 codes), IBGE-coded municipal polygons, official IBGE population (SIDRA 6579),
     IBGE Census 2010 commuting arranjos and CEMPRE workforce (SIDRA 6450).
   - InfoDengue (dengue and chikungunya, 2018 to now, the pilot plus its commuting neighbours) and OpenDataSUS SRAG
     (COVID-19 and influenza SARI).
   - Open-Meteo ERA5 weather and Nager.Date holidays.
   - CNES facilities with registered beds.
   - The WorldPop 1 km grid, and with `--full` the Zenodo multimodal dataset (NDVI, LST, ENSO, sanitation).
2. **Validate features.** Every feature definition gets a point-in-time (PIT) and cardinality check.
3. **Build the dataset.** An immutable training dataset is frozen per issue week, with a PIT audit.
4. **Train.** Nine recipes are backtested with rolling-origin splits, an embargo, 80% intervals calibrated on
   out-of-fold residuals and an ablation: four naive references (persistence, seasonal naive, historical median, damped
   trend), two count models (Poisson GLM, Poisson boosting), two growth models that predict log growth vs the last
   observed week (ridge, absolute-error boosting) and the recommended **ensemble** (0.7 growth boosting + 0.3 growth
   ridge, geometric). Runs are ranked on a leaderboard by relative WIS vs persistence, the forecasting-hub convention.
   On the dengue pilot the ensemble scores 0.76 relative WIS (persistence 1.00, Poisson boosting 0.96).
   `python scripts/bench_recipes.py` replays a dataset's folds in ~10 s for quick recipe experiments.
5. **Release.** The ensemble is registered and gets two sign-offs, clinical and platform. It is promoted to champion and a
   versioned forecast is published.
6. **Simulate.** An operational scenario is pinned to that `forecast_id`.

Docker: `cp .env.example .env && docker compose up --build`, then `docker compose run --rm worker hp demo`. Docker was
not available on the build machine, so the compose file has not been run here.

Sign in at `/login` with one of the five seeded roles (local/demo only): viewer, data operator, data scientist,
epidemiology reviewer, platform admin. Every permission is enforced by the API (`healthpulse/core/security.py`).

## What is in the box

| Area | Where | Notes |
|---|---|---|
| Source registry, connectors, Bronze → Silver | `healthpulse/sources/` | append-only Bronze with SHA-256 manifests, read-only files; allowlisted HTTPS (SSRF control), size limits, HTML sniffing, backoff; declarative schema mapping; DQ gates (hard → quarantine, soft → warning); atomic Silver publication; every release kept as a vintage |
| Point-in-time feature store | `healthpulse/features/` | allowlisted templates (lag, rolling, growth, incidence, static as-of, seasonal lag, holiday window, calendar, neighbour pressure); `pit.lookup` only reads releases with `known_at ≤ issue_at`; many-to-one joins or `CardinalityError`; validator blocks leaky lags; immutable versions and feature sets |
| Training lab | `healthpulse/ml/` | worker-only training (never inside a request); rolling-origin folds with label-release embargo; MAE/WAPE/bias/Poisson deviance/coverage/width/outbreak precision-recall; slices, per-region, ablation, permutation importance (non-causal caveat); hashed artefacts; optional MLflow mirror (`HP_MLFLOW_URI`) |
| Nowcast (Brazil SARI) | `healthpulse/ml/nowcast.py`, `nowcast_jobs.py`, `hp nowcast`, `/api/v1/nowcasts` | Corrects the incomplete recent weeks of COVID-19 and influenza SARI for records not yet entered and classified. A reporting triangle is rebuilt from the row-level SIVEP-Gripe files (five columns, reduced to counts at once) with delay = onset week → `max(DT_DIGITA, DT_ENCERRA)`; the delay PMF uses only onset weeks old enough to be complete at the as-of week (strict point in time), Dirichlet shrinkage national → state → municipality, Negative-Binomial posterior with the delay uncertainty mixed in; weeks the data cannot speak about are null. Pseudo-prospective backtest over every as-of week, hold-out half, against the raw count and a static-inflation baseline; three automatic release gates, immutable hashed artefacts under `artifacts/nowcast/`. Labelled `PIT_RECONSTRUCTED`. Not available for Viet Nam (no line list). Not a forecast |
| Registry & deployment | `healthpulse/ml/registry.py` | DRAFT → CANDIDATE → REVIEWED → APPROVED → DEPLOYED; dual sign-off (epidemiology + platform), author cannot review; release gates; shadow runs never publish; champion/previous aliases; rollback is a new record; forecasts immutable |
| Simulation | `healthpulse/simulation/` | lognormal demand draws from the forecast interval, shared shock per trial, common random numbers baseline vs proposal; MILP (SciPy/HiGHS) with budget, team availability, travel radius, per-region caps, equity weights and floors, or a marginal-gain greedy at national scale; binding-constraint explanations; transmission what-if (metapopulation SEIR) in a separate, **hypothetical** research mode |
| Grounded analyst | `healthpulse/assistant/analyst.py` | tool-gated, deterministic; cites forecast/run/vintage ids; refuses outcome claims ("lives saved"); drafts scenarios but never runs them |
| Polygon crosswalk | `healthpulse/geo/` | exact polygon intersection on an equal-area plane; mutual-best 1:1, split and merge weights that sum to 1; AMBIGUOUS / REFUSED instead of guessing; version = hash of method, thresholds and input layers; code-based audit that never changes a weight; `hp crosswalk`, `/api/v1/geo/crosswalks` |
| Durable jobs | `healthpulse/core/jobs.py` | DB-backed queue, separate worker supervisor, a child process per job (hard timeout and cancel), heartbeats and orphan reaping, idempotency keys, retries linked by `retry_of`, stages and logs streamed over SSE |
| API | `healthpulse/api/` | `/api/v1`, envelope `{data, meta, warnings, request_id}`, typed errors (400/401/403/404/409/422/429), audit log with redaction, optimistic locking, OpenAPI at `/docs` |
| Web | `web/` | no build step: Preact + htm modules, MapLibre GL (WebGL, feature-state choropleth over the IBGE polygons, no tile server), SVG charts, URL-carried filters, light/dark, 390 / 768 / 1440 px layouts; all vendored for offline use |

## Viet Nam module

### Live response (`/vn/ops`)

Incident command for a real outbreak or a drill (`healthpulse/ops/`, API `/api/v1/ops`). Open an incident (**LIVE**:
reports are OBSERVED; **DRILL**: SYNTHETIC, can be seeded and advanced day by day from its own projection), then:

- **Key in** cases, suspected cases, deaths, recoveries, vaccine doses and tests by clicking the map (or a province), or
  paste CSV. Reports are never deleted, only voided with a reason. Every write goes to a feed that all controllers receive
  over a live stream (SSE, ~1 s).
- **See the situation**: DBSCAN clusters (`cluster_km`), each with a quasi-Poisson growth fit over the last 14 complete
  days (the reporting-delay days are left out), R = (1+rL)(1+rD) with its 95% interval (pathogen prior when data are thin),
  doubling time, spread speed, affected area (buffered hull) and people inside (province density × area, ESTIMATE).
  Safe provinces (no reports, far from every cluster) are shaded green.
- **Zones**: affected / quarantine / buffer / safe / vaccination ring, suggested from the clusters or drawn as a circle or
  polygon. Each is **real** (enforced; needs `ops.command`) or **simulated** (what-if), or **proposed** (waiting for a decision).
- **Projection** (SCENARIO, 28 days, band = growth-rate interval): baseline (nothing new) vs current (real zones + approved
  plan) vs plan (current + simulated zones + latest proposed plan), with hospital beds and deaths by province. Real zones
  active ≥ 7 days are already in the observed growth and are not counted again.
- **Resource optimiser**: greedy marginal allocation of teams, vaccine, test kits (cases averted) then beds and ambulances
  (deaths averted), from the nearest depot with stock, delayed by travel. Plans are proposed; approval needs `ops.command`.
- **AI analyst**: Claude runs a tool loop over the live incident (situation, clusters, reports, zones, stock, what-if
  projection, optimiser) and may *propose* zones or plans; it can never enforce one. OpenAI-compatible providers get one
  grounded call; no key → rule-based brief.

Roles: everyone can follow; `ops.report` (operator, scientist, reviewer, admin) keys in, adds stock and simulated zones;
`ops.command` (reviewer, admin) opens LIVE incidents, enforces real zones and approves plans (all audited).

### Outbreak sandbox (`/vn/sandbox`)

A live, interactive SCENARIO game over the 34 provinces (`healthpulse/vietnam/sandbox.py`, API `/api/v1/vn/sandbox`).
Pick a pathogen (dengue, seasonal / pandemic influenza, COVID-19, measles, HFMD, cholera, custom), set weather
(normals, latest observed anomalies, or a custom shift), mobility (gravity model + corridors you draw), workforce
(current estimate, equal per capita, or custom per province), vaccine coverage and efficacy, seed it on the map, then
play day by day and intervene: distancing, border closure, vaccination campaigns, rapid-response teams that travel
from the four MoH institutes, nurse redeployment, staff surge, vector control, weather events. Stochastic S-E-I-R-V
+ hospital ensemble (P10–P90 bands); deterministic replay powers *Rewind here*. Dengue uses the fitted seasonal curve
and GLM climate response; other pathogens use labelled assumptions. The **AI advisor** proposes validated actions
you apply with one click (rule-based when the AI is off/unavailable); **Autopilot** lets the rules act weekly.
Admins choose the AI provider/model/effort, or switch it off, in **Admin → Settings → AI analyst**.

Screens: **Viet Nam → Situation map** (`/vn`), **Response simulator** (`/vn/simulate`) and **Outbreak sandbox** (`/vn/sandbox`). Data: `.venv/bin/hp vn-fetch`
(`python server.py` runs it on first start; `--build-only` rebuilds and refits without downloading).

| Source (real, public) | Used for |
|---|---|
| OCHA COD-AB v02 — 34 provinces valid from 1 July 2025 (Resolution 202/2025/QH15) | map, province points, distances |
| HeiGIT / HDX ADM1 inputs (WorldPop) | population, under-5 / 65+, rural share, OSM hospital and primary-care counts, ≤1 h hospital access |
| OpenDengue V1.3 | province-month dengue 1994–2010 (Project Tycho); national weekly/monthly 2011–2025 (WHO WPRO) |
| WHO GHO via HDX | doctors, nurses, hospital beds per 10,000 (national) |
| NASA POWER | monthly temperature, rain, humidity per province 1994–last year; daily for the current year |

- **Polygon crosswalk and Meta mobility.** `healthpulse/geo` joins GADM 4.1 polygons to IBGE municipalities and to the 34
  P-codes by exact polygon intersection on an equal-area plane (`hp crosswalk`; GADM and Meta are ingested by
  `hp ingest gadm_boundaries meta_mobility`). Each version stores its thresholds, input hashes and a quality report
  (`/api/v1/geo/crosswalks`), and is audited against an independent code reference without that reference ever changing a
  weight. Meta Movement Distribution is a *distance-from-home* indicator (share of pings at 0, <10, 10-100, 100+ km), not
  origin-destination flows; only ~90 days exist, so it is context on screen and a reference for the sandbox's
  "Contacts outside home province" slider (never applied silently), not a model feature.
- **Joins are by code.** Old provinces map to the 34 new ones through FAO GAUL codes (`healthpulse/vietnam/crosswalk.py`).
  OpenDengue reuses ISO code VN-30 for both Gia Lai and the former Hà Tây, so ISO codes are not used as keys.
- **Model.** A Poisson GLM is fitted on 2004–2010: province + region×month + lagged climate anomalies. 2010 is held
  out: MAE 201 against 206 for the seasonal-only model, with 0.44 of deviance explained. The level is calibrated to
  the latest 24 months of WHO national reports. Against WHO national totals for 2011–2025, the national seasonal shape
  correlates at r = 0.91. Provincial values after 2010 are **model estimates**, not reports.
- **Calibration** (`hp vn-calibrate`, `/api/v1/vn/calibration`, artefact `data/gold/vn/calibration.json`). Dengue: the climate
  GLM explains only 14% of the year-to-year amplitude of national totals (reported 29k to 368k, model about 130k to 150k), so
  2022 (367,729 reported; the OpenDengue V1.3 extract has the 2022 annual total only) is under-estimated 2.4-fold and the
  year amplitude is a **scenario** (quiet x0.28, typical x0.66, 2022-like x3.46), not a forecast. The ensemble spread is set
  from Viet Nam's own year-to-year variability. The WHO level basis (latest 24 months) was kept: no alternative was better
  in prospective scoring. COVID-19 (WHO weekly cases and deaths): R(t), early growth, deaths per reported case
  (Delta 1.35%, Omicron 0.03%) and the immune share the Omicron wave size allows are fitted and pinned; reporting fraction,
  immunity split, intrinsic Delta R0 and the whole hospital layer are **not identifiable** from national weekly series and stay
  labelled assumptions. The sandbox shows both calibrated and literature values.
- **Alerts.** Current alerts compare the estimate with the seasonal norm and with incidence
  (alert ≥1.5× and ≥10/100k; warning ≥1.25× and ≥5/100k; watch ≥1.1× or ≥25/100k). Historical frames from 1997 to 2010
  use a leave-one-year-out endemic channel (mean + 2 SD).
- **Simulator.** A stochastic SEIR model runs over the 34 provinces with gravity mobility, driven by the fitted seasonal
  R(t). Beds and nurse staffing set capacity; they are estimated from the WHO national densities. Rapid-response teams
  are based at NIHE, Pasteur Nha Trang, the Tây Nguyên Institute and Pasteur HCMC. Teams travel by road and add capacity
  and vector control on site. Dispatch is auto, manual or none. Each scenario runs with and without teams on the same
  seed. Results are SCENARIO output; every rate is an editable assumption (the sandbox's calibrated values are listed above;
  this Response simulator still uses planner assumptions).
- **LLM analyst.** Set `ANTHROPIC_API_KEY` (default model `claude-opus-5-5`), or `OPENAI_API_KEY` + `HP_LLM_MODEL`, in
  `.env` and restart. For a custom endpoint set `ANTHROPIC_BASE_URL` (Claude gateway/proxy, with `ANTHROPIC_AUTH_TOKEN` if
  it wants a Bearer token) or `OPENAI_BASE_URL` (vLLM, Ollama, Azure…), or set it in Admin → Settings → AI analyst. The analyst streams a brief grounded on the screen's JSON, which can be inspected under
  "context sent". Without a key it gives a rule-based brief.

## Demo path: the Story screen

The app opens on **Story** (`/story`), which walks one flow end to end on the official IMDC data:

1. **Would it have warned us?** The 2023-24 replay headline and the IMDC benchmark, with the honest reading.
2. **Replay 2023-24.** One state's weekly curve with the HealthPulse alert, the epidemic-level crossing and the week threshold
   monitoring would have seen it.
3. **Next season.** The 2026-27 forecast (data to EW25 2026) and the largest expected seasons.
4. **Brief & decide.** A grounded brief (Claude when configured, rule-based otherwise; the exact CONTEXT JSON it sees is shown)
   and a human **Approve / Reject** that only an epidemiology reviewer or admin can make. It is written to the audit log.

Prepare it once with `hp sprint && hp retro` (about 30 s). To have Claude write the brief, set `ANTHROPIC_API_KEY` in `.env`
(the default model is `claude-opus-5-5`; Admin → Settings → AI analyst can switch it). Without a key everything still works with
the rule-based brief.

## IMDC 2026 benchmark (Infodengue-Mosqlimate Dengue Challenge)

`hp sprint` (about 15 s) replays the sprint's four validation tests on the **official sprint files**
(`ftp://info.dengue.mat.br/data_imdc_2026`, downloaded to `data/sprint/raw/`). Each test trains on data up to EW25 and forecasts
weekly probable dengue cases for **every state except ES** from EW41 to EW40 of the next year, which is 16 to 68 weeks ahead.
Scoring is the sprint's weekly WIS on the median and the 50/80/90/95% intervals. Code: `healthpulse/sprint/`,
`scripts/bench_sprint.py`, API `/api/v1/sprint/report` and `/api/v1/sprint/forecast/{UF}`.

| Model | rel WIS (each state equal) | mean WIS (cases) | 90% coverage | season-total error (median abs log ratio) |
|---|---|---|---|---|
| Climatology (reference) | 1.00 | 1,361 | 0.75 | 0.78 |
| **HealthPulse shifted climatology** | 1.09 | 1,102 | 0.83 | 0.60 |
| Scenario + climatology blend | 1.07 | 1,152 | 0.90 | 0.66 |
| Season total x past shapes | 1.35 | 1,050 | 0.95 | 0.59 |

The shifted model takes each state's own climatological distribution for each season week and moves it by the anomaly
predicted from what is known at EW25: the running season's size and tail, recent regime, and ENSO. It cuts case-weighted
WIS by 19% and the season-total error by a quarter. It is **not** better than climatology when every state counts equally,
because it loses in small states and in 2025-26. Model choices were made on 2015-2021 only (rel WIS 0.92 there), and the tests
were scored once afterwards. Test 4 is scored through EW25 2026. This is not an official ranking: other teams' forecasts need a
Mosqlimate API key. Submission CSVs in the template format, including a 2026-27 forecast made after the deadline, are written to
`data/sprint/submission/<model>/<season>/<UF>.csv`.

### 2023-24 retrospective (`hp retro`)

In the record 2023-24 season, **all 19 states that passed the MoH epidemic level (300/100k) were warned before threshold
monitoring would have seen it, a median of 6 weeks earlier**, with one false alarm (PE, at 284/100k). About 4 of those weeks
come from projecting forward at all, and the growth model adds about 2. Full write-up, comparator and caveats:
[`docs/CASE_STUDY_2023_24.md`](docs/CASE_STUDY_2023_24.md).

## Measured performance (this machine, 16 cores)

| What | Result |
|---|---|
| Simulation, 5,570 municipalities × 1,000 trials × 4 weeks (22 M draws) + allocation | 7.1 s (`hp bench`) |
| Simulation, 5,570 × 5,000 trials (111 M draws) | 21.3 s |
| Pilot scenario, 3 regions × 2,000 trials, MILP | 0.09 s |
| Training: 9 recipes × 4 horizons × (4 folds + test + final), ablation, importance | 65 s |
| Dataset build (18 PIT features, 4,812 rows) | 1.7 s |
| Full `hp demo` from an empty directory (network bound) | 5.5 min |
| Map geometry (5,571 polygons) | 2.1 MB gzip, cached by ETag |
| API p95, warm: overview / map layer / region series / facilities / model metrics | 35 / 8 / 25 / 39 / 16 ms (target was < 500 ms) |

Scale paths built in:

- **Case-level imports.** SIVEP-Gripe files are streamed through Polars, so multi-million-row files aggregate without
  being loaded into memory.
- **Load testing.** `hp synth-sivep --rows 5000000` generates a SYNTHETIC case-level file and runs it through the same
  import path.
- **Wider coverage.** `HP_PILOT=capitals` widens the pilot to the 27 state capitals. National map geometry is already
  loaded.

## Honesty constraints (enforced in code)

- **No substitute data.** Every gap listed in the first release is now filled from a real public download (see
  [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md)). Meta mobility is loaded through a polygon crosswalk for Viet Nam, as a
  *distance-from-home* profile (not flows) over the ~90 days HDX keeps, and is never a training feature. Brazil's profile is refused until
  the GADM version Meta's ids belong to (3.6) is ingested. Nothing is substituted.
- **Code joins only.** Beds are joined to municipalities through the CNES establishment code. Names are never used as
  join keys. Where no shared code exists (GADM polygons vs IBGE / OCHA), the join is made from geometry alone, and
  ambiguous or poorly covered polygons are refused rather than guessed.
- **Registered capacity is not availability.** Operational capacity in scenarios is a labelled planner input.
- **PIT_UNVERIFIED backtests.** Historical source vintages were not archived before this pilot. Backtests replay
  first-release times from documented publication lags, so they are marked `PIT_UNVERIFIED` and no prospective claim
  is made. Live inference uses real ingestion vintages.
- **Nowcasts are reconstructed, not archived (`PIT_RECONSTRUCTED`).** Delays come from first-entry and closure dates in one
  snapshot, so later edits and reclassifications are invisible and the bands are optimistic at the margin. The newest
  report week is withheld (null) rather than extrapolated from a handful of cases.
- **Missing is null, not zero.** Insufficient-data forecasts are null, not zero. Missing weather weeks are null, not
  zero.

See [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md), [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
[`docs/DATA_GOVERNANCE.md`](docs/DATA_GOVERNANCE.md).
