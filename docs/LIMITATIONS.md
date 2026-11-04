# Limitations (visible in the product as warnings)

## Closed in this release (data now downloaded automatically)

| Was | Now | Source |
|---|---|---|
| Respiratory SARI needed a manual SIVEP-Gripe file | Downloaded from OpenDataSUS. The yearly parquet exports are aggregated to municipality-week when imported. Personal columns are never read, and Silver holds counts only. | `sivep_gripe`: dadosabertos.saude.gov.br `srag-2019-a-2026` |
| No registered beds (the API feed has no IBGE code) | Beds come from the OpenDataSUS *Hospitais e Leitos* CSV, joined on the **CNES establishment code** to the CNES facility register, which carries the IBGE code. Names are still never used. | `cnes_facilities_beds` |
| Population came from the copy InfoDengue republishes | Official IBGE estimates for every municipality (2015 to now), from SIDRA table 6579. InfoDengue's copy only fills years IBGE has not published (2022 census year, 2023). | `ibge_population` |
| CEMPRE workforce needed a manual import | Salaried workers by CNAE 2.0 section, from SIDRA table 6450. Cells IBGE suppresses (`X`) stay null, never zero. | `ibge_cempre` |
| Commuting/OD blocked, so the mobility feature was blocked | IBGE *Arranjos Populacionais* (Census 2010 commuting): arranjo membership and an integration index per municipality. `commuter_pressure_lag_1w` is now computed, and InfoDengue also fetches the pilot's arranjo neighbours. | `ibge_commuting` |
| WorldPop needed a manual import | WorldPop R2025A 1 km constrained raster, summed over the IBGE polygons with an exact cell-centre scanline fill. | `worldpop` |
| Zenodo multimodal needed a manual import | Zenodo 22029053 (CC BY 4.0, md5 verified): weekly NDVI/EVI/LST/ENSO and a per-municipality sanitation and land-cover profile. | `brazil_dengue_multimodal` |
| Meta mobility was blocked: HDX keys it by GADM ids and name-based crosswalks are refused | GADM polygons are joined to IBGE municipalities and to the 34 OCHA P-codes by exact polygon intersection on an equal-area plane (`healthpulse/geo`); thresholds, input hashes and a quality report are stored with every version. Viet Nam is live: 709 of 710 GADM districts accepted, all 34 provinces have a mobility-range profile. Brazil's crosswalk is built (GADM 4.1: 5,253 one-to-one pairs, median IoU 0.97, 99.9% agreement with GADM's own CC_2 code) but Brazil's mobility profile is **refused**, see item 3. | `gadm_boundaries`, `meta_mobility` |

Fetch any of these with `hp ingest <source_id>`, `hp fetch` (everything still missing), or `python server.py --fetch-missing`.

## Still open

1. **Backtests are PIT_UNVERIFIED.** No historical InfoDengue or Open-Meteo vintages existed before this pilot, so
   first-release times are approximated by documented publication lags. Release-accurate evidence accumulates as the
   scheduler stores new vintages.
2. **Forecasts are of reported notifications.** InfoDengue counts are revised, and they do not equal infections.
   SRAG counts are severe hospitalised cases by week of symptom onset, not community incidence. The most recent
   weeks are also right-truncated (records not yet entered or classified); see the nowcast entry below (item 11).
3. **Meta mobility is a recent distance-from-home profile, not flows.** Movement Distribution gives, per GADM polygon and
   day, the share of device pings 0 km, <10 km, 10-100 km and 100+ km from the device's home location. It has no destination,
   so it cannot be an origin-destination matrix and is not used as one. HDX keeps only ~90 days, so our history starts at our
   first ingest (each ingest adds days; nothing is back-filled). A feature that does not exist before that date would be null
   for every training week, so the profile is **not** a point-in-time model feature; it is shown on the Brazil region page and
   the Viet Nam province page and gives the sandbox a data-grounded reference for "contacts outside the home province".
   Meta adds privacy noise (negative fractions, bands that do not sum to 1, ~10% of Brazilian and ~0.5% of Vietnamese polygon-days,
   mostly small polygons); those polygon-days are set to null, not clipped. Meta devices are a non-random sample of people.
   **Brazil is not loaded yet.** Meta's Brazilian ids (`BRA.16.70_1`) belong to an older GADM version than 4.1 and GADM renumbers
   between versions: only 1,675 of 5,496 labels agree if the ids are matched anyway (Meta's `BRA.16.70_1` is Carlópolis, GADM 4.1's
   `BRA.16.70` is Cascavel), so the version check refuses them. The fix is the GADM 3.6 polygons (`hp ingest gadm_boundaries` then
   `hp crosswalk`, configured but not yet downloaded: geodata.ucdavis.edu was unreachable from the build machine at the time).
   Viet Nam's ids match GADM 4.1 exactly (710/710 ids, 710/710 labels). For the Brazil pair that is built, 75 GADM 4.1 polygons
   (45 in Bahia) overlap several IBGE municipalities without a clean split and 74 IBGE municipalities are reached by no accepted polygon,
   so they would have no profile (null). GADM's licence allows free academic and non-commercial use only; it is a join key here, never drawn.
4. **Commuting is from 2010.** The arranjo integration index comes from the 2010 census sample, and IBGE has not yet
   published a 2022 origin-destination table. The commuter-pressure feature is a connectivity proxy, not imported
   infections. Its weights are treated as known from the 2nd-edition publication (2016).
5. **Registered capacity is not availability.** Beds are *registered* CNES capacity for the latest competence month.
   Staffed or operational capacity in scenarios is still a labelled planner input.
6. **IBGE register and geometry fallbacks.** The IBGE servicodados API is still unreachable from the build machine.
   The register comes from the IBGE-coded `kelvins/municipios-brasileiros` CSV and polygons from `tbrugz/geodata-br`
   (IBGE malha). Population now comes from IBGE itself, through SIDRA.
7. **The Zenodo dataset is frozen and ends in 2025.** Its remote-sensing columns use a conservative 21-day release lag
   for PIT replay and are marked PIT_UNVERIFIED. WorldPop is a *modelled* grid and is labelled as a proxy, not a
   count.
8. **Scenario outputs are estimates, not observed impact.** Visit rates, capacities, costs and surge percentages are
   planner assumptions. Transmission mode is hypothetical and uncalibrated.
9. **Thresholds are provisional.** Outbreak alert thresholds (training 90th percentile) must be co-defined with
   epidemiologists.
10. **The analyst is rule-based.** It is deterministic, and an LLM can be placed behind the same tool gate.
11. **Nowcasts (SARI) are reconstructed and short-history.** The delay triangle is rebuilt from first-entry
    (`DT_DIGITA`) and closure (`DT_ENCERRA`) dates in one OpenDataSUS snapshot, labelled `PIT_RECONSTRUCTED`: it cannot see
    edits, reclassifications or deletions made later, and a true vintage archive only accumulates from now. Only three yearly
    files (2024 to now) are ingested, so the backtest spans 86 as-of weeks (2024-09 to 2026-04), COVID-19 volumes in 2025 to 2026 are low,
    and municipality results rest on three pilot cities. The method is not materially better than a static national
    inflation factor at national level (hold-out WAPE 3.8% vs 4.1% COVID-19, 3.7% vs 3.8% influenza); its gain is at state
    and municipality level, in calibrated intervals, and in declining to estimate. The report week itself is never nowcast
    (only about 4 to 5% of the eventual records are classified within it, about 27 to 29% after one week). Delays beyond 26 weeks (about 2%) are not modelled and add a small
    negative bias. State delay uses the residence state, not the notifying unit. Interval widths came from a tuned
    effective-sample-size cap and may drift as reporting practice changes. **No nowcast exists for Viet Nam:** there is no
    line list with entry dates, so none is simulated or borrowed from Brazil.

12. **The IMDC benchmark is self-scored.** `hp sprint` reproduces the sprint protocol and metric but compares only with our
    own climatology reference; other teams' submissions need a Mosqlimate API key. The champion beats climatology on
    case-weighted WIS (-19%) but not on the state-equal score (1.09). Season-ahead dengue size is largely unpredictable at
    EW25 (the national year effect swings about +-1 log unit).

## Viet Nam module

- Province-level dengue reports end in 2010; later provincial numbers are climate-model estimates calibrated to WHO national totals. National WHO reports in OpenDengue end in Dec 2024 (weekly) / Mar 2025 (monthly).
- Before 2004 some pre-2025 provinces reported under parent units (e.g. Lai Châu incl. Điện Biên, Sông Bé), so the model is fitted on 2004–2010 only.
- Doctors, nurses and beds per province are national WHO densities × population (ESTIMATE), not a facility census; OSM facility counts are shown separately.
- The current-month estimate uses climate up to the previous month (1-month lead). NASA POWER daily data lags ~4 days.
- Response simulator rates (reporting 15%, hospitalisation 30%, LOS 4 d, immunity 65%, team capacity/effect) are planner assumptions. `sim.py` now uses the same discrete-time transmission rate as the sandbox, R*(1-exp(-gamma)) (fixed 2026-11-04; it was R*gamma, about 10% too high).
- **Calibration (outbreak sandbox) — what it is and is not.**
  - Dengue 2022 is calibrated on ONE annual total (367,729); OpenDengue V1.3 has no monthly or weekly 2022. The climate model explains 14% of year-to-year amplitude; a scale fitted on 2022 over-predicts other years 1.1 to 10-fold, so year amplitude is a scenario. Weekly season timing is about 13 days early on average (range -41 to +16); no R multiplier was consistent across years, so none is applied.
  - COVID-19 uses WHO national weekly cases and deaths only. Not identifiable: reporting fraction, immune share (apart from the bound report x (1 - immune)), hospitalisation vs fatality split, length of stay, beds, no-bed death multiplier (no hospital data). The intrinsic Delta R0 is not identified (early growth was under containment).
  - Deaths: a constant fatality does not reproduce the Aug-Sep 2021 peak (about 2.5 times higher than fitted; consistent with overload or batch reporting, six weeks are excluded). For Omicron the infection-to-death delay is not identified (deaths fall while cases still rise) and the fatality is an upper bound (Jan-Feb infections are mixed Delta/Omicron).
  - Back-tests are in-sample replays of the fitted R(t) in the real engine (ensemble shock 0.02); engine WAPE: Delta cases 10%, deaths 41%; Omicron cases 7%, deaths 27%. Cumulative cases reproduced: 90% and 101%.
  - A calibration is flagged `stale` when the engine source changes: re-run `hp vn-calibrate`.
- Mobility range per province comes from Meta device pings by distance from home (GADM districts -> 34 provinces, population-weighted by WorldPop). It brackets, but is not, the share of contacts made outside the home province: pings >= 100 km away are a lower bound, pings >= 10 km away an upper bound.
