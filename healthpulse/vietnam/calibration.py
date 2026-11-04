"""Calibration of the Viet Nam model and outbreak sandbox against real national outbreak data.

What is calibrated, with what, and what the data CANNOT identify (the artefact `data/gold/vn/calibration.json` says the
same, so the dashboard and the sandbox show it):

DENGUE (OpenDengue national reports: yearly 2011-2022, monthly 2011-2015 and 2024, weekly 2016-2019 and 2023-2024)
  * Year totals. The climate GLM x kappa gives an almost flat annual level (~120-150k cases); reported totals swing between
    29k and 368k (2022). The model therefore explains little of the epidemic-YEAR amplitude and 2022 is under-estimated by
    a large factor. This is reported, not hidden.
  * Level (kappa). Candidate bases are scored prospectively (predict year y from earlier years only); the basis is changed
    in model.py only when the validation supports it.
  * Amplitude. `epidemic_scale` per year = reported / engine total at the fitted level; 2022 is the calibration target
    year. The scale is a SCENARIO amplitude, not a predictable quantity: leave-one-year-out error is reported.
  * Dispersion. The sandbox's per-trial transmission shock (`shock_sd`) is set so the spread of annual totals across trials
    equals the spread of Viet Nam's year-to-year amplitudes.
  * Shape. Weekly 2016-2019 and 2023-2024 are replayed with that year's observed weather and compared (after matching
    the total) with the seasonal-only engine and a flat line. An R multiplier is fitted per year; it is applied only
    if its range excludes 1.
  * NOT identifiable from national reported cases: reporting fraction, share immune (they cancel out of reported counts).
COVID-19 (WHO weekly cases and deaths, Viet Nam; Delta 2021 and Omicron 2022)
  * Identified: R(t) path (deterministic reduction of the engine, then replayed in the real engine), early growth rate and the
    reproduction number it implies, the reported case-fatality (deaths per reported case) and the mean infection-to-death delay.
  * Pinned combinations (the other factor stays a labelled assumption): p_hosp x cfr_hosp / report, and R0 x (1 - immune).
  * NOT identifiable: report vs hospitalisation vs immunity separately, the hospital layer (length of stay, beds,
    untreated-death multiplier: no hospital data), and the intrinsic Delta R0 (the early growth was under containment).
Every number carries its data window; backtests are in-sample unless marked HOLDOUT.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import polars as pl
from scipy.optimize import least_squares

from ..core.util import write_json_atomic
from . import data, model
from . import sandbox as sb
from .crosswalk import MERGER

VERSION = "1.0"
PIDS = list(MERGER)
FILE = "calibration.json"
N_POP = None


def _path() -> Path:
    return data.gold_dir() / FILE


def _sha_file(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


# ================================================================================================ load / overlay / backtest
@lru_cache(maxsize=1)
def _load_cached(mtime: float) -> dict | None:
    p = _path()
    return json.loads(p.read_text()) if p.exists() else None


def load() -> dict | None:
    p = _path()
    return _load_cached(p.stat().st_mtime) if p.exists() else None


def overlay() -> dict:
    """{pathogen_key: {"params": {name: {value, interval, kind, applied, source, window, depends_on, note}}, "note": str, "status": str, "stale": bool}}
    for the sandbox. `stale` = the engine source changed since the calibration was produced (re-run `hp vn-calibrate`)."""
    return sb.read_overlay()


def backtest(name: str | None = None) -> dict | None:
    c = load()
    if not c:
        return None
    b = c.get("backtests", {})
    return b if name is None else b.get(name)


# ================================================================================================ dengue: data
def dengue_annual() -> list[dict]:
    """One reference total per year: the yearly report if present, else 12 monthly reports, else >= 52 weekly reports."""
    n = model.national()
    out = {}
    for r in n.filter(pl.col("T_res") == "Year").iter_rows(named=True):
        out[r["start"].year] = dict(year=r["start"].year, observed=float(r["cases"]), basis="yearly report", source=r["UUID"])
    mm = n.filter(pl.col("T_res") == "Month").with_columns(pl.col("start").dt.year().alias("y")).group_by("y").agg(
        pl.col("cases").sum(), pl.len().alias("k"))
    for r in mm.filter(pl.col("k") == 12).iter_rows(named=True):
        out.setdefault(r["y"], dict(year=r["y"], observed=float(r["cases"]), basis="sum of 12 monthly reports", source="OpenDengue monthly"))
    ww = n.filter(pl.col("T_res") == "Week").with_columns(pl.col("start").dt.year().alias("y")).group_by("y").agg(
        pl.col("cases").sum(), pl.len().alias("k"))
    for r in ww.filter(pl.col("k") >= 52).iter_rows(named=True):
        out.setdefault(r["y"], dict(year=r["y"], observed=float(r["cases"]), basis="sum of weekly reports", source="OpenDengue weekly"))
    return [out[y] for y in sorted(out) if 2011 <= y <= 2024]


def dengue_weekly() -> pl.DataFrame:
    return model.national().filter(pl.col("T_res") == "Week").select("start", "cases").sort("start")


def model_year_sums() -> dict[int, dict]:
    """Sum over 34 provinces x 12 months of the fitted GLM (raw, before kappa): with and without the climate anomalies."""
    est = model.estimates().filter(pl.col("month").dt.year().is_between(2011, 2025))
    g = est.group_by(pl.col("month").dt.year().alias("y")).agg(pl.col("expected_raw").sum().alias("e"), pl.col("normal_raw").sum().alias("n"),
                                                                pl.len().alias("k")).filter(pl.col("k") == 408)
    return {r["y"]: dict(model=r["e"], normal=r["n"]) for r in g.iter_rows(named=True)}


# ================================================================================================ dengue: engine runs
def engine_year(year: int, *, scale: float = 1.0, shock_sd: float | None = None, trials: int = 24, mult: float = 1.0, noshock: bool = False,
                replay: bool = True, seed: int = 11, days: int = 365) -> np.ndarray:
    """Daily national reported dengue cases from the REAL sandbox engine, one column per trial: (days, trials)."""
    spec = sb.SandboxSpec(pathogen=sb.PathogenSpec(key="dengue", basis="literature", epidemic_scale=scale, r0=mult, shock_sd=shock_sd),
                          weather=sb.WeatherSpec(mode="observed" if replay else "normal", replay=replay), start_month=f"{year}-01-01",
                          trials=trials, seed=seed)
    s = sb.Session(spec)
    if noshock:
        s.shock[:] = 1.0
    out = np.zeros((days, trials))
    for d in range(days):
        s._advance()
        out[d] = s._new["rep"].sum(1)
    return out


def fit_scale(year: int, target: float, *, s0: float = 1.0, mult: float = 1.0) -> tuple[float, float]:
    """`epidemic_scale` for which the no-shock engine's annual total equals `target`. The response is sub-linear (susceptibles
    deplete in the provinces that carry an epidemic), so this iterates rather than dividing."""
    s_, tot = s0, 0.0
    for _ in range(10):
        tot = float(engine_year(year, scale=s_, trials=4, noshock=True, mult=mult).sum(0).mean())
        if abs(tot / target - 1) < 0.004:
            break
        s_ *= (target / tot) ** 1.25
    return s_, tot


def _weekly_from_daily(daily: np.ndarray, year: int, week_starts: list[date]) -> np.ndarray:
    """Sum daily (days, K) into the given 7-day windows (rows = windows)."""
    d0 = date(year, 1, 1)
    out = np.full((len(week_starts), daily.shape[1]), np.nan)
    for i, w in enumerate(week_starts):
        a = (w - d0).days
        if a >= 0 and a + 7 <= daily.shape[0]:
            out[i] = daily[a:a + 7].sum(0)
    return out


def _shape_metrics(obs: np.ndarray, sim: np.ndarray) -> dict:
    """Compare two weekly series after matching totals. r = Pearson on the weeks, nmae = mean |diff| / mean obs, centroid_offset_days =
    (centre of mass of sim) - (centre of mass of obs): negative = the simulated season is earlier than reported."""
    ok = np.isfinite(obs) & np.isfinite(sim)
    o, s_ = obs[ok], sim[ok]
    if len(o) < 8 or s_.sum() <= 0:
        return dict(weeks=int(ok.sum()), r=None, nmae=None, centroid_offset_days=None, log_rmse=None)
    s_ = s_ * (o.sum() / s_.sum())
    t = np.arange(len(o)) * 7.0
    r = float(np.corrcoef(o, s_)[0, 1]) if np.std(s_) > 1e-9 else None
    lr = float(np.sqrt(np.mean((np.log(o + 50) - np.log(s_ + 50)) ** 2)))
    return dict(weeks=int(ok.sum()), r=r, nmae=float(np.mean(np.abs(o - s_)) / o.mean()),
                centroid_offset_days=float((t * s_).sum() / s_.sum() - (t * o).sum() / o.sum()), log_rmse=lr)


def dengue_report(say=print, quick: bool = False) -> dict:
    K = 12 if quick else 24
    ann = dengue_annual()
    kappa = model.model_info()["kappa"]
    ms = model_year_sums()
    rows = []
    for a in ann:
        m = ms.get(a["year"])
        if not m:
            continue
        rows.append({**a, "model_raw": m["model"], "model_x_kappa": m["model"] * kappa, "normal_x_kappa": m["normal"] * kappa,
                     "ratio_obs_to_model": a["observed"] / (m["model"] * kappa)})
    lo = np.log([r["observed"] for r in rows])
    lm = np.log([r["model_x_kappa"] for r in rows])
    ln = np.log([r["normal_x_kappa"] for r in rows])
    var_o = float(np.var(lo))
    annual_stats = dict(
        years=[r["year"] for r in rows], n=len(rows),
        log_sd_observed=float(np.std(lo)), corr_log_obs_vs_model=float(np.corrcoef(lo, lm)[0, 1]) if np.std(lm) > 0 else None,
        r2_model_vs_constant=float(1 - np.var(lo - lm - np.mean(lo - lm)) / var_o),
        model_log_sd=float(np.std(lm)), normal_log_sd=float(np.std(ln)),
        reading="share of the year-to-year variance of the log annual total explained by the climate GLM (after fitting one constant level)")
    say(f"  dengue annual: obs log-SD {annual_stats['log_sd_observed']:.2f}, GLM log-SD {annual_stats['model_log_sd']:.2f}, "
        f"R² {annual_stats['r2_model_vs_constant']:.2f}")

    # ---- level (kappa) candidates, scored prospectively on the log annual error
    byy = {r["year"]: r for r in rows}
    years = sorted(byy)
    ratios = {y: byy[y]["ratio_obs_to_model"] / 1.0 for y in years}  # observed / (model x kappa_current)
    def pred_err(variant):
        errs = []
        for y in years:
            prior = [z for z in years if z < y]
            if variant == "last_2_years":
                use = prior[-2:] if len(prior) >= 2 else None
            elif variant == "all_prior_years":
                use = prior if len(prior) >= 4 else None
            elif variant == "last_5_years":
                use = prior[-5:] if len(prior) >= 5 else None
            if not use:
                continue
            k_hat = math.exp(np.mean([math.log(ratios[z]) for z in use]))
            errs.append((y, math.log(byy[y]["observed"]) - math.log(byy[y]["model_x_kappa"] * k_hat)))
        return errs
    ev = {}
    first = max(2016, years[0] + 5)
    for v in ("last_2_years", "last_5_years", "all_prior_years"):
        e = [x for yy, x in pred_err(v) if yy >= first]
        ev[v] = dict(n=len(e), mae_log=float(np.mean(np.abs(e))), median_ape=float(np.median(np.abs(np.expm1(e)))),
                     bias_log=float(np.mean(e)))
    best = min(ev, key=lambda k: ev[k]["mae_log"])
    cur = ev["last_2_years"]["mae_log"]
    switch = best != "last_2_years" and ev[best]["mae_log"] < 0.9 * cur
    kappa_eval = dict(scored_years=f"{first}-{years[-1]} (each predicted from earlier years only)", variants=ev, current_basis="latest 24 months (= last_2_years)",
                      best=best, relative_gain=1 - ev[best]["mae_log"] / cur, rule="switch only if the best variant cuts the mean |log error| by >= 10%",
                      switch_recommended=bool(switch))
    say(f"  kappa basis: {', '.join(f'{k} {v['mae_log']:.2f}' for k, v in ev.items())} -> {'SWITCH to ' + best if switch else 'keep latest 24 months'}")

    # ---- engine fidelity: noshock engine total vs fitted GLM total (does the sandbox reproduce the model it is built on?)
    fid = []
    for y in years:
        if y not in ms or y > 2024:
            continue
        sim = engine_year(y, trials=4, noshock=True).sum(0).mean()
        fid.append(dict(year=y, engine=float(sim), model_x_kappa=byy[y]["model_x_kappa"], ratio=float(sim / byy[y]["model_x_kappa"])))
    fidelity = dict(years=fid, median_ratio=float(np.median([f["ratio"] for f in fid])), range=[min(f["ratio"] for f in fid), max(f["ratio"] for f in fid)],
                    reading="no-shock engine total / (GLM x kappa) for the same year with that year's observed weather")

    # ---- amplitude per year and the 2022 target
    amp = []
    for y in years:
        base = float(np.mean(engine_year(y, trials=4, noshock=True).sum(0)))
        sy, tot = fit_scale(y, byy[y]["observed"], s0=byy[y]["observed"] / base)
        amp.append(dict(year=y, observed=byy[y]["observed"], engine_at_scale_1=base, scale=sy, engine_total_at_scale=tot))
    sc = np.array([a["scale"] for a in amp])
    ls = np.log(sc)
    # leave-one-year-out: typical-scale predictor vs scale 1
    loyo = []
    for i, a in enumerate(amp):
        others = np.delete(ls, i)
        loyo.append(dict(year=a["year"], err_typical=float(ls[i] - np.median(others)), err_scale1=float(ls[i])))
    amplitude = dict(
        per_year=amp, median_scale=float(np.median(sc)), geo_mean_scale=float(math.exp(np.mean(ls))), log_sd=float(np.std(ls)),
        p10=float(np.quantile(sc, 0.1)), p90=float(np.quantile(sc, 0.9)),
        loyo=dict(mae_log_typical=float(np.mean([abs(l["err_typical"]) for l in loyo])), mae_log_scale1=float(np.mean([abs(l["err_scale1"]) for l in loyo])),
                  per_year=loyo, reading="predicting a year's total from the other years' typical scale versus assuming scale 1"))
    say(f"  amplitude: median scale {amplitude['median_scale']:.2f}, log-SD {amplitude['log_sd']:.2f}; 2022 scale {[a['scale'] for a in amp if a['year']==2022][0]:.2f}")

    # ---- 2022 target: fit the scale so the engine's MEDIAN annual total equals the reported 367,729 (with the calibrated dispersion)
    target = byy[2022]["observed"] if 2022 in byy else None
    out2022 = None
    shock_sd = None
    # dispersion first: shock_sd so that the log-SD of annual totals across trials equals the empirical log-SD of the year amplitudes
    want = amplitude["log_sd"]
    lo_s, hi_s = 0.0, 0.08
    for _ in range(5 if quick else 7):
        mid = (lo_s + hi_s) / 2
        tot = engine_year(2019, shock_sd=mid, trials=48, seed=5).sum(0)
        (lo_s, hi_s) = (mid, hi_s) if np.std(np.log(tot)) < want else (lo_s, mid)
    shock_sd = (lo_s + hi_s) / 2
    dispersion = dict(shock_sd=shock_sd, target_log_sd=want, achieved_log_sd=float(np.std(np.log(engine_year(2019, shock_sd=shock_sd, trials=48, seed=5).sum(0)))),
                      engine_default=0.08, reading="per-trial lognormal transmission shock; the engine default 0.08 gives a far wider spread than Viet Nam's observed year-to-year amplitudes")
    if target:
        s_ = [a for a in amp if a["year"] == 2022][0]["scale"]   # fitted with the deterministic engine: total == the reported 2022 total
        tot = engine_year(2022, scale=s_, shock_sd=shock_sd, trials=64, seed=7).sum(0)
        lo90, med, hi90 = np.percentile(tot, [10, 50, 90])
        out2022 = dict(target=target, fitted_scale=float(s_), engine_noshock_total=[a for a in amp if a["year"] == 2022][0]["engine_total_at_scale"],
                       engine_median=float(med), engine_p10=float(lo90), engine_p90=float(hi90), trials=64,
                       glm_x_kappa=byy[2022]["model_x_kappa"], glm_underestimate_factor=float(target / byy[2022]["model_x_kappa"]),
                       semantics="CALIBRATED to one annual total: no monthly or weekly 2022 data exist in OpenDengue V1.3")
        say(f"  2022: reported {target:,.0f}; GLM x kappa {byy[2022]['model_x_kappa']:,.0f} (x{target/byy[2022]['model_x_kappa']:.2f}); "
            f"engine at scale {s_:.2f}: no-shock {out2022['engine_noshock_total']:,.0f}, ensemble P50 {med:,.0f} [{lo90:,.0f}-{hi90:,.0f}]")

    # ---- transfer: scale fitted on 2022 applied to the other years (out of sample) -- shows the scale is year specific
    transfer = []
    if out2022:
        for a in amp:
            if a["year"] == 2022:
                continue
            pred_t = float(engine_year(a["year"], scale=out2022["fitted_scale"], trials=4, noshock=True).sum(0).mean())
            transfer.append(dict(year=a["year"], observed=a["observed"], predicted=pred_t, ratio=float(pred_t / a["observed"])))
    # ---- shape on weekly years
    shape = []
    wk = dengue_weekly()
    mult_grid = [0.95, 0.96, 0.97, 0.98, 0.99, 1.0, 1.01, 1.02, 1.03, 1.04, 1.05]
    for y in sorted({d.year for d in wk["start"]}):
        w = wk.filter(pl.col("start").dt.year() == y)
        if w.height < 30:
            continue
        starts = list(w["start"])
        obs = np.array(w["cases"], float)
        sim_clim = _weekly_from_daily(engine_year(y, trials=4, noshock=True), y, starts).mean(1)
        sim_seas = _weekly_from_daily(engine_year(y, trials=4, noshock=True, replay=False), y, starts).mean(1)
        flat = np.ones_like(obs)
        res = dict(year=y, weeks=int(w.height),
                   climate_engine=_shape_metrics(obs, sim_clim), seasonal_only=_shape_metrics(obs, sim_seas), flat=_shape_metrics(obs, flat))
        res["flat"]["r"] = None
        # R multiplier: grid on log-RMSE after total matching
        gr = []
        for mlt in mult_grid:
            sm = _weekly_from_daily(engine_year(y, trials=4, noshock=True, mult=mlt), y, starts).mean(1)
            gr.append((mlt, _shape_metrics(obs, sm)["log_rmse"]))
        gr = [g for g in gr if g[1] is not None]
        if gr:
            res["mult_grid"] = [dict(mult=m_, log_rmse=v) for m_, v in gr]
            res["best_mult"] = min(gr, key=lambda g: g[1])[0]
        shape.append(res)
    bests = [s_["best_mult"] for s_ in shape if "best_mult" in s_]
    mult = dict(per_year=[dict(year=s_["year"], best=s_.get("best_mult")) for s_ in shape], median=float(np.median(bests)) if bests else None,
                range=[min(bests), max(bests)] if bests else None,
                excludes_one=bool(bests and (min(bests) > 1.0 or max(bests) < 1.0)), grid=mult_grid)
    cl = [s_["climate_engine"]["r"] for s_ in shape if s_["climate_engine"]["r"] is not None]
    se = [s_["seasonal_only"]["r"] for s_ in shape if s_["seasonal_only"]["r"] is not None]
    cen = [s_["climate_engine"]["centroid_offset_days"] for s_ in shape if s_["climate_engine"]["centroid_offset_days"] is not None]
    say(f"  weekly shape r: climate engine {np.mean(cl):.2f} vs seasonal-only {np.mean(se):.2f} over {len(cl)} years; season centroid offset "
        f"{np.mean(cen):+.0f} d (range {min(cen):+.0f}..{max(cen):+.0f}); R multiplier range {mult['range']}")
    return dict(centroid_offsets_days=cen, annual=rows, annual_stats=annual_stats, kappa=kappa_eval, engine_fidelity=fidelity, amplitude=amplitude, dispersion=dispersion,
                target_2022=out2022, transfer_from_2022=transfer, shape=shape, r_multiplier=mult)


# ================================================================================================ COVID-19: data and reduction
# Windows are decided from the data and documented in the artefact. Weeks are WHO weeks ending on the Sunday `week_end`.
WAVES: dict[str, dict] = {
    "covid": dict(
        label="Delta wave 2021 (+ tail to Jan 2022)", day0=date(2021, 5, 1),
        cases=(date(2021, 5, 9), date(2022, 1, 30)), deaths=(date(2021, 5, 16), date(2022, 2, 27)),
        mask_cases=[], mask_deaths=[date(2021, 7, 11) + timedelta(days=7 * i) for i in range(6)],
        mask_deaths_why="reported deaths step from 116 to 936 in one week (2021-07-25 -> 2021-08-01), consistent with batch reporting; "
                        "six weeks 2021-07-11..2021-08-15 are left out of the death fit",
        growth=(date(2021, 5, 2), date(2021, 5, 30)), growth_label="R_eff under containment (not the intrinsic Delta R0)",
        assumed=dict(latent=4.0, infectious=6.0, report=0.3, immune=0.2, cfr_hosp=0.12, admit_delay=6, los=9.0),
        sandbox_note="deaths before 2021-05-16 are zero in WHO; infections after 2022-01-30 are held at the last fitted R(t)"),
    "covid_omicron": dict(
        label="Omicron wave 2022", day0=date(2022, 2, 1),
        cases=(date(2022, 2, 13), date(2022, 6, 26)), deaths=(date(2022, 3, 6), date(2022, 7, 31)),
        mask_cases=[], mask_deaths=[],
        mask_deaths_why="deaths before 2022-03-06 are mostly Delta-tail infections and are left out; the 2022-08-07 case spike "
                        "(571,458, a one-week data dump) is outside every window",
        growth=(date(2022, 2, 13), date(2022, 3, 13)), growth_label="R_eff in the first month of the Omicron surge",
        assumed=dict(latent=3.0, infectious=4.0, report=0.3, immune=0.75, cfr_hosp=0.05, admit_delay=4, los=5.0),
        sandbox_note="the week ending 2022-02-06 (78,918, Lunar New Year) is before the case window; Jan-Feb infections are mixed Delta/Omicron, "
                     "so the Omicron fatality is an upper bound"),
}
KNOT_DAYS = 14
MIN_PLAUSIBLE_DEATH_DELAY = 7.0  # days; a plausibility floor (assumption), not a fitted value
REPLAY_SHOCK_SD = 0.02  # ensemble shock in back-test replays (the engine default 0.08 compounds into order-of-magnitude bands over an exponential phase)


def _pop_total() -> float:
    return float(sb._static()["N"].sum())


def _wave_data(key: str) -> dict:
    w = WAVES[key]
    cw = data.covid_weekly()
    if cw is None:
        raise RuntimeError("vn_who_covid not fetched (hp vn-calibrate --fetch, or hp vn-fetch)")
    d0 = w["day0"]
    ends, c_obs, d_obs = [], [], []
    for r in cw.iter_rows(named=True):
        e = r["week_end"]
        if not (w["cases"][0] <= e <= w["deaths"][1] or w["cases"][0] <= e <= w["cases"][1]):
            continue
        ends.append(e)
        c_obs.append(np.nan if r["cases"] is None else r["cases"])
        d_obs.append(np.nan if r["deaths"] is None else r["deaths"])
    ends = np.array(ends)
    c_obs, d_obs = np.array(c_obs, float), np.array(d_obs, float)
    idx = np.array([(e - d0).days for e in ends])
    in_c = np.array([w["cases"][0] <= e <= w["cases"][1] for e in ends]) & np.isfinite(c_obs) & ~np.isin(ends, w["mask_cases"])
    in_d = np.array([w["deaths"][0] <= e <= w["deaths"][1] for e in ends]) & np.isfinite(d_obs) & ~np.isin(ends, w["mask_deaths"])
    ndays = int(idx.max()) + 1
    return dict(ends=ends, idx=idx, cases=c_obs, deaths=d_obs, use_c=in_c, use_d=in_d, ndays=ndays, day0=d0, spec=w)


def _wk(arr: np.ndarray, idx: np.ndarray) -> np.ndarray:
    cs = np.concatenate([[0.0], np.cumsum(arr)])
    return cs[idx + 1] - cs[np.maximum(idx - 6, 0)]


def _simulate(Rd: np.ndarray, I0: float, par: dict, ndays: int, p_hosp: float, los: float) -> dict:
    """Deterministic (expected-value) version of the engine's daily S-E-I-R + admission queue + hospital chain for a respiratory
    pathogen with no climate term, no vaccination campaign and no capacity limit. Same equations and order as Session._advance."""
    N = par["N"]
    sg, gm = 1 / par["latent"], 1 / par["infectious"]
    es, eg = math.exp(-sg), math.exp(-gm)
    S = N * (1 - par["immune"]) - I0
    E, I = 0.0, I0
    delay = max(1, int(round(par["admit_delay"])))
    queue = np.zeros(delay)
    h = 1.0 / los
    q = min(1.0, par["cfr_hosp"] * h)
    keep = math.exp(-h)
    H = 0.0
    rep, ins, dead = np.zeros(ndays), np.zeros(ndays), np.zeros(ndays)
    for t in range(ndays):
        lam = Rd[t] * (1 - eg) * I / N
        inS = S * (1 - math.exp(-lam))
        newI = E * (1 - es)
        newR = I * (1 - eg)
        S -= inS
        E += inS - newI
        I += newI - newR
        rep[t] = par["report"] * newI
        ins[t] = inS
        adm = queue[t % delay]
        queue[t % delay] = p_hosp * inS
        H += adm
        d = H * q
        dis = (H - d) * (1 - keep)
        H = max(H - d - dis, 0.0)
        dead[t] = d
    return dict(rep=rep, inS=ins, dead=dead)


def _Rdaily(logR: np.ndarray, ndays: int) -> np.ndarray:
    kn = np.arange(len(logR)) * KNOT_DAYS
    return np.exp(np.interp(np.arange(ndays), kn, logR))


def _sd_counts(obs: np.ndarray, floor: float) -> np.ndarray:
    return np.sqrt(1.0 / (obs + 1.0) + floor ** 2)


def _fit_cases(wv: dict, par: dict, obs: np.ndarray | None = None, x0: np.ndarray | None = None, smooth: float = 2.0, nfev: int = 80) -> dict:
    obs = wv["cases"] if obs is None else obs
    use, idx, ndays = wv["use_c"], wv["idx"], wv["ndays"]
    nk = int(math.ceil((ndays - 1) / KNOT_DAYS)) + 1
    first = obs[use][:2].mean() if use.sum() else 100.0
    if x0 is None:
        x0 = np.concatenate([[math.log(max(first / 7 / par["report"] * 4, 10))], np.full(nk, math.log(1.5))])
    lo = np.concatenate([[math.log(1.0)], np.full(nk, math.log(0.15))])
    hi = np.concatenate([[math.log(5e6)], np.full(nk, math.log(14.0))])
    sd = _sd_counts(obs[use], 0.10)

    def res(x):
        sim = _simulate(_Rdaily(x[1:], ndays), math.exp(x[0]), par, ndays, 0.0, 5.0)
        pred = _wk(sim["rep"], idx)[use]
        r1 = (np.log(pred + 1) - np.log(obs[use] + 1)) / sd
        r2 = smooth * np.diff(x[1:], 2)
        return np.concatenate([r1, r2])

    f = least_squares(res, np.clip(x0, lo + 1e-6, hi - 1e-6), bounds=(lo, hi), loss="soft_l1", f_scale=2.0, max_nfev=nfev, x_scale=0.5)
    sim = _simulate(_Rdaily(f.x[1:], ndays), math.exp(f.x[0]), par, ndays, 0.0, 5.0)
    pred = _wk(sim["rep"], idx)
    r1 = ((np.log(pred[use] + 1) - np.log(obs[use] + 1)) / sd)
    return dict(x=f.x, I0=math.exp(f.x[0]), logR=f.x[1:], Rd=_Rdaily(f.x[1:], ndays), sim=sim, pred=pred, cost=float(np.sum(r1 ** 2)),
                rmse_log=float(np.sqrt(np.mean(((np.log(pred[use] + 1) - np.log(obs[use] + 1))) ** 2))))


def _death_res(wv: dict, par: dict, inS: np.ndarray, obs: np.ndarray):
    use, idx, ndays = wv["use_d"], wv["idx"], wv["ndays"]
    sd = _sd_counts(obs[use], 0.2)
    ndays_ = len(inS)

    def pred_for(ph, los):
        # hospital chain only (inS is fixed by the case fit)
        delay = max(1, int(round(par["admit_delay"])))
        queue = np.zeros(delay)
        h = 1.0 / los
        q = min(1.0, par["cfr_hosp"] * h)
        keep = math.exp(-h)
        H = 0.0
        dead = np.zeros(ndays_)
        for t in range(ndays_):
            adm = queue[t % delay]
            queue[t % delay] = ph * inS[t]
            H += adm
            d = H * q
            H = max(H - d - (H - d) * (1 - keep), 0.0)
            dead[t] = d
        return dead

    return use, idx, sd, pred_for


def _fit_deaths(wv: dict, par: dict, inS: np.ndarray, obs: np.ndarray | None = None, free_los: bool = True, x0=None) -> dict:
    obs = wv["deaths"] if obs is None else obs
    use, idx, sd, pred_for = _death_res(wv, par, inS, obs)
    los0 = par["los"]
    ph0 = 0.01 if x0 is None else math.exp(x0[0])

    def res(x):
        ph = math.exp(x[0])
        los = math.exp(x[1]) if free_los else los0
        pr = _wk(pred_for(ph, los), idx)[use]
        return (np.log(pr + 1) - np.log(obs[use] + 1)) / sd

    if free_los:
        f = least_squares(res, [math.log(ph0), math.log(los0)], bounds=([math.log(1e-5), math.log(1.0)], [math.log(0.5), math.log(40.0)]),
                          loss="soft_l1", f_scale=2.0, max_nfev=60)
        ph, los = math.exp(f.x[0]), math.exp(f.x[1])
    else:
        f = least_squares(lambda x: res([x[0], 0.0]), [math.log(ph0)], bounds=([math.log(1e-5)], [math.log(0.5)]), loss="soft_l1", f_scale=2.0, max_nfev=60)
        ph, los = math.exp(f.x[0]), los0
    dead = pred_for(ph, los)
    return dict(p_hosp=ph, los=los, x=[math.log(ph), math.log(los)], dead=dead, pred=_wk(dead, idx),
                rmse_log=float(np.sqrt(np.mean((np.log(_wk(dead, idx)[use] + 1) - np.log(obs[use] + 1)) ** 2))))


def _death_stats(par: dict, ph: float, los: float, inS: np.ndarray, rep: np.ndarray) -> dict:
    """Deaths per infection, per reported case, and mean infection-to-death delay implied by (p_hosp, los) under the engine's chain."""
    h = 1 / los
    q = min(1.0, par["cfr_hosp"] * h)
    keep = math.exp(-h)
    stay = (1 - q) * keep
    # per admitted patient: P(death) = q / (1 - stay); mean days in H until death
    p_die = q / (1 - stay) if stay < 1 else 0.0
    days = np.arange(1, 400)
    pm = q * stay ** (days - 1)
    mean_in_h = float((days * pm).sum() / max(pm.sum(), 1e-12))
    delay = max(1, int(round(par["admit_delay"])))
    return dict(deaths_per_infection=ph * p_die, deaths_per_reported_case=ph * p_die / par["report"], mean_infection_to_death_days=delay + mean_in_h - 1,
                mean_days_in_hospital_until_death=mean_in_h, p_die_given_admitted=p_die)


def _nb_sample(rng, mean: np.ndarray, phi: float) -> np.ndarray:
    lam = rng.gamma(1 / phi, mean * phi + 1e-9)
    return rng.poisson(lam).astype(float)


def _growth(wv: dict, par: dict, rng, B: int = 1500) -> dict:
    a, b = wv["spec"]["growth"]
    m = np.array([a <= e <= b for e in wv["ends"]]) & np.isfinite(wv["cases"])
    t = np.arange(m.sum()) * 7.0
    y = np.log(wv["cases"][m])
    X = np.column_stack([np.ones_like(t), t])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    res = y - X @ beta
    rs = []
    for _ in range(B):  # residual bootstrap over the weeks
        yy = X @ beta + rng.choice(res, size=len(res), replace=True)
        rs.append(np.linalg.lstsq(X, yy, rcond=None)[0][1])
    r = beta[1]
    out = dict(weeks=int(m.sum()), window=[str(a), str(b)], r_per_day=float(r), r_ci90=[float(np.percentile(rs, 5)), float(np.percentile(rs, 95))],
               doubling_days=float(math.log(2) / r) if r > 0 else None, label=wv["spec"]["growth_label"])
    sg, gm = 1 / par["latent"], 1 / par["infectious"]
    out["R_early"] = float(sb.growth_to_R(r, sg, gm))
    out["R_early_ci90"] = [float(sb.growth_to_R(x, sg, gm)) for x in out["r_ci90"]]
    # sensitivity to the assumed latent / infectious periods (+-1 day): the data fix r, not R
    alts = [float(sb.growth_to_R(r, 1 / (par["latent"] + dl), 1 / (par["infectious"] + di))) for dl in (-1, 1) for di in (-1, 1)]
    out["R_early_range_over_periods"] = [min(alts + [out["R_early_ci90"][0]]), max(alts + [out["R_early_ci90"][1]])]
    return out


def _engine_backtest(key: str, wv: dict, par: dict, fit: dict, ph: float, trials: int = 24, seed: int = 3) -> dict:
    """Replay the fitted R(t) path in the REAL 34-province engine (spatial mixing, binomial noise, ensemble shock)."""
    Rd = fit["Rd"]
    R0 = float(Rd.max())
    N = sb._static()["N"]
    seeds = [sb.Seed(province_id=p, infections=max(1, int(fit["I0"] * N[i] / N.sum()))) for i, p in enumerate(PIDS)]
    spec = sb.SandboxSpec(
        pathogen=sb.PathogenSpec(key=key, basis="literature", r0=min(max(R0, 0.5), 25.0), latent=par["latent"], infectious=par["infectious"], report=par["report"],
                                 immune=par["immune"], p_hosp=ph, cfr_hosp=par["cfr_hosp"], los=par["los"],
                                 climate_sensitivity=0.0, shock_sd=REPLAY_SHOCK_SD),
        weather=sb.WeatherSpec(mode="normal"), workforce=sb.WorkforceSpec(bed_share=0.8, nurse_share=0.8), seeds=seeds,
        start_month=str(wv["day0"]), trials=trials, seed=seed)
    s = sb.Session(spec)
    ndays = wv["ndays"]
    rep, dead = np.zeros((ndays, trials)), np.zeros((ndays, trials))
    for d in range(ndays):
        s.contact[:] = float(np.clip(Rd[d] / spec.pathogen.r0, 0.05, 1.0))
        s._advance()
        rep[d], dead[d] = s._new["rep"].sum(1), s._new["dead"].sum(1)
    idx = wv["idx"]
    cs = np.vstack([np.zeros((1, trials)), np.cumsum(rep, 0)])
    ds = np.vstack([np.zeros((1, trials)), np.cumsum(dead, 0)])
    wc = cs[idx + 1] - cs[np.maximum(idx - 6, 0)]
    wd = ds[idx + 1] - ds[np.maximum(idx - 6, 0)]
    q = lambda a: np.percentile(a, [10, 50, 90], axis=1)  # noqa: E731
    return dict(weeks=[str(e) for e in wv["ends"]], cases_p=q(wc), deaths_p=q(wd))


def _centroid_offset(obs, sim, use) -> float:
    t = np.arange(len(obs), dtype=float) * 7.0
    o, m = np.where(use, obs, 0.0), np.where(use, sim, 0.0)
    return float((t * m).sum() / max(m.sum(), 1) - (t * o).sum() / max(o.sum(), 1))


def _wape(obs, pred, use):
    return float(np.abs(obs[use] - pred[use]).sum() / max(obs[use].sum(), 1))


def _pin_immune(wv: dict, par: dict, quick: bool) -> dict:
    """Profile the weekly case fit over the immune share at the ASSUMED reporting fraction. The wave size fixes only
    report x (1 - immune): reported cases / report cannot exceed the susceptible pool. Immune is moved off its assumption only
    when the profile shows the assumed value fits clearly worse than the best one."""
    grid = [0.0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9] if not quick else [0.0, 0.3, 0.5, 0.65, 0.8]
    prof = []
    x0 = None
    for imm in grid:
        p2 = dict(par, immune=imm)
        f = _fit_cases(wv, p2, x0=x0, nfev=60)
        x0 = f["x"]
        att = float(f["sim"]["inS"].sum() / (p2["N"] * (1 - imm)))
        prof.append(dict(immune=imm, cost=f["cost"], rmse_log=f["rmse_log"], share_of_susceptibles=att, R_fit_first_3_weeks=float(f["Rd"][:21].mean()),
                         peak_R=float(f["Rd"].max())))
    base = _fit_cases(wv, par, nfev=80)
    best = min(prof, key=lambda r: r["cost"])
    within = [r["immune"] for r in prof if r["cost"] <= best["cost"] * 1.25 + 2]
    move = best["cost"] < 0.8 * base["cost"]
    # inside the set the data cannot tell apart, take the value NEAREST to the assumption (the smallest departure from it)
    nearest = min(within, key=lambda v: abs(v - par["immune"]))
    return dict(profile=prof, assumed=par["immune"], assumed_cost=base["cost"], best=best["immune"], best_cost=best["cost"],
                pinned=nearest if move else par["immune"], moved=bool(move), range_within_tolerance=[min(within), max(within)],
                tolerance="cost within 25% (+2) of the minimum",
                reading="wave size bounds report x (1 - immune) from below; the cost surface is flat along that ridge, so the split is an assumption")


def covid_wave(key: str, say=print, quick: bool = False, seed: int = 11) -> dict:
    wv = _wave_data(key)
    w = wv["spec"]
    par = dict(w["assumed"], N=_pop_total())
    rng = np.random.default_rng(seed)
    t0 = time.perf_counter()
    pin = _pin_immune(wv, par, quick)
    par = dict(par, immune=pin["pinned"])
    cases = _fit_cases(wv, par, nfev=100)
    sim_inS = cases["sim"]["inS"]
    # deaths: stage A free timing; stage B refit only p_hosp with the preset timing (the value the sandbox can take)
    dA = _fit_deaths(wv, par, sim_inS, free_los=True)
    dB = _fit_deaths(wv, par, sim_inS, free_los=False, x0=dA["x"])
    sA = _death_stats(par, dA["p_hosp"], dA["los"], sim_inS, cases["sim"]["rep"])
    sB = _death_stats(par, dB["p_hosp"], par["los"], sim_inS, cases["sim"]["rep"])
    timing_ok = bool(1.5 < dA["los"] < 35.0 and sA["mean_infection_to_death_days"] >= MIN_PLAUSIBLE_DEATH_DELAY)
    # inference of the death-fit parameters by parametric bootstrap on the weekly deaths (negative binomial around the fit)
    use_d = wv["use_d"]
    ob = wv["deaths"]
    pr = dA["pred"]
    phi = float(np.clip(np.mean(((ob[use_d] - pr[use_d]) ** 2 - pr[use_d]) / np.maximum(pr[use_d] ** 2, 1)), 0.005, 1.0)) if use_d.sum() > 3 else 0.05
    B = 60 if quick else 200
    bs = []
    for _ in range(B):
        o = ob.copy()
        o[use_d] = _nb_sample(rng, np.maximum(pr[use_d], 0.5), phi)
        a = _fit_deaths(wv, par, sim_inS, obs=o, free_los=True, x0=dA["x"])
        b = _fit_deaths(wv, par, sim_inS, obs=o, free_los=False, x0=dA["x"])
        st = _death_stats(par, a["p_hosp"], a["los"], sim_inS, None)
        bs.append((st["deaths_per_reported_case"], st["mean_infection_to_death_days"], b["p_hosp"], a["los"]))
    bs = np.array(bs)
    ci = lambda col: [float(np.percentile(bs[:, col], 5)), float(np.percentile(bs[:, col], 95))]  # noqa: E731

    # growth-rate R and its confidence interval
    growth = _growth(wv, par, rng)

    # bootstrap on the case series: R(t) path and I0 (negative binomial around the fit, refit with warm start)
    nb = 6 if quick else 14
    c_ob = wv["cases"]
    pc = cases["pred"]
    use_c = wv["use_c"]
    phic = float(np.clip(np.mean(((c_ob[use_c] - pc[use_c]) ** 2 - pc[use_c]) / np.maximum(pc[use_c] ** 2, 1)), 0.005, 1.0))
    paths = []
    for _ in range(nb):
        o = c_ob.copy()
        o[use_c] = _nb_sample(rng, np.maximum(pc[use_c], 0.5), phic)
        f = _fit_cases(wv, par, obs=o, x0=cases["x"], nfev=30)
        paths.append(f["Rd"])
    paths = np.array(paths)
    Rband = dict(p05=np.percentile(paths, 5, axis=0), p50=np.percentile(paths, 50, axis=0), p95=np.percentile(paths, 95, axis=0))

    # identifiability profiles
    prof_report = []
    rep_grid = [0.1, 0.2, 0.3, 0.5, 0.8, 1.0] if not quick else [0.1, 0.3, 1.0]
    base_cost = cases["cost"]
    for rho in rep_grid:
        p2 = dict(par, report=rho)
        f = _fit_cases(wv, p2, x0=cases["x"], nfev=50)
        dd = _fit_deaths(wv, p2, f["sim"]["inS"], free_los=False, x0=dA["x"])
        att = float(f["sim"]["inS"].sum() / (p2["N"] * (1 - p2["immune"])))
        cfr_h = p2["cfr_hosp"]
        prof_report.append(dict(report=rho, cost=f["cost"], infections=float(f["sim"]["inS"].sum()), share_of_susceptibles=att,
                                feasible=bool(att < 0.98), p_hosp_for_cfr_hosp_assumed=dd["p_hosp"], cfr_hosp=cfr_h,
                                deaths_per_reported_case=_death_stats(p2, dd["p_hosp"], p2["los"], f["sim"]["inS"], None)["deaths_per_reported_case"]))
    cost_spread_report = [p["cost"] for p in prof_report if p["feasible"]]
    flat = lambda c: float((max(c) - min(c)) / max(min(c), 1e-9)) if len(c) > 1 else None  # noqa: E731

    # real-engine replay with the sandbox-appliable parameters
    bt = _engine_backtest(key, wv, par, cases, dB["p_hosp"], trials=12 if quick else 24)
    ends = wv["ends"]
    in_c, in_d = wv["use_c"], wv["use_d"]
    eng_c, eng_d = np.array(bt["cases_p"]), np.array(bt["deaths_p"])
    metrics = dict(
        reduction=dict(wape_cases=_wape(wv["cases"], cases["pred"], in_c), wape_deaths=_wape(wv["deaths"], dB["pred"], in_d)),
        engine=dict(wape_cases=_wape(wv["cases"], eng_c[1], in_c), wape_deaths=_wape(wv["deaths"], eng_d[1], in_d),
                    cum_cases_ratio=float(eng_c[1][in_c].sum() / wv["cases"][in_c].sum()), cum_deaths_ratio=float(eng_d[1][in_d].sum() / wv["deaths"][in_d].sum()),
                    cases_in_p10_p90=float(np.mean((wv["cases"][in_c] >= eng_c[0][in_c]) & (wv["cases"][in_c] <= eng_c[2][in_c]))),
                    deaths_in_p10_p90=float(np.mean((wv["deaths"][in_d] >= eng_d[0][in_d]) & (wv["deaths"][in_d] <= eng_d[2][in_d]))),
                    centroid_offset_days=_centroid_offset(wv["cases"], eng_c[1], in_c)),
        naive_cfr=float(np.nansum(wv["deaths"][in_d]) / np.nansum(wv["cases"][in_c])))
    series = dict(weeks=[str(e) for e in ends], observed_cases=[None if not np.isfinite(x) else float(x) for x in wv["cases"]],
                  observed_deaths=[None if not np.isfinite(x) else float(x) for x in wv["deaths"]],
                  fitted_cases=[float(x) for x in cases["pred"]], fitted_deaths=[float(x) for x in dB["pred"]],
                  engine_cases=[[float(x) for x in row] for row in eng_c], engine_deaths=[[float(x) for x in row] for row in eng_d],
                  used_cases=[bool(x) for x in in_c], used_deaths=[bool(x) for x in in_d])
    Rpath = dict(day0=str(wv["day0"]), step_days=KNOT_DAYS, R=[float(x) for x in np.exp(cases["logR"])],
                 band_daily=dict(p05=[float(x) for x in Rband["p05"][::KNOT_DAYS]], p95=[float(x) for x in Rband["p95"][::KNOT_DAYS]]),
                 reading="reproduction number of a fully susceptible population (R0 x contact); multiply by the susceptible share for R_eff")
    say(f"  {key}: cases WAPE {metrics['reduction']['wape_cases']:.2f} (reduction) / {metrics['engine']['wape_cases']:.2f} (engine); deaths WAPE "
        f"{metrics['reduction']['wape_deaths']:.2f} / {metrics['engine']['wape_deaths']:.2f}; growth R {growth['R_early']:.2f}; "
        f"deaths/reported case {sA['deaths_per_reported_case']:.4f} [{ci(0)[0]:.4f}-{ci(0)[1]:.4f}]; {time.perf_counter() - t0:.0f}s")
    return dict(
        key=key, label=w["label"], windows=dict(cases=[str(w["cases"][0]), str(w["cases"][1])], deaths=[str(w["deaths"][0]), str(w["deaths"][1])],
                                                   masked_death_weeks=[str(x) for x in w["mask_deaths"]], masked_death_reason=w["mask_deaths_why"], day0=str(w["day0"]),
                                                   notes=w["sandbox_note"]),
        assumed={k: v for k, v in w["assumed"].items()}, population=par["N"], growth=growth,
        fit=dict(cases_rmse_log=cases["rmse_log"], deaths_rmse_log=dB["rmse_log"], I0=cases["I0"], R_path=Rpath,
                 infections_total=float(cases["sim"]["inS"].sum()), attack_share_of_population=float(cases["sim"]["inS"].sum() / par["N"]),
                 bootstrap=dict(cases_paths=nb, deaths=B, overdispersion_cases=phic, overdispersion_deaths=phi)),
        immune_pin=pin, assumed_used=dict(par, N=None),
        deaths=dict(timing_identified=timing_ok,
                    timing_note=("the fitted length of stay is inside its bounds" if timing_ok else
                                 "the free timing hit a bound or came out shorter than a physiologically plausible " + f"{MIN_PLAUSIBLE_DEATH_DELAY:.0f} days: weekly deaths decouple from the case "
                                 "surge (deaths fall while cases still rise; earlier infections dominate), so the infection-to-death delay is NOT identified for "
                                 "this wave and the preset timing is kept"),
                    free_timing=dict(p_hosp=dA["p_hosp"], los=dA["los"], **sA), preset_timing=dict(p_hosp=dB["p_hosp"], los=par["los"], **sB),
                    deaths_per_reported_case_ci90=ci(0), mean_infection_to_death_days_ci90=ci(1), p_hosp_preset_timing_ci90=ci(2), los_free_ci90=ci(3),
                    reading="p_hosp x P(death | admitted) / report is what the weekly deaths pin; the split between them is an assumption"),
        profiles=dict(report=prof_report, immune=pin["profile"], cost_relative_spread_over_report=flat(cost_spread_report),
                      cost_relative_spread_over_immune=flat([p["cost"] for p in pin["profile"] if p["share_of_susceptibles"] < 0.98]), base_cost=base_cost,
                      reading="cost = weighted sum of squared log errors of the weekly case fit; a small relative spread means the case series cannot tell these values apart"),
        metrics=metrics, series=series)


# ================================================================================================ overlay, orchestration
def _param(value, *, kind: str, applied: bool, source: str, window: str, interval=None, depends_on: dict | None = None, note: str = "") -> dict:
    """kind: identified (the data fix it) | pinned_combination (the data fix a combination; the other factors stay at their
    assumed values listed in depends_on) | scenario_reference (a documented range, not a fitted constant)."""
    return dict(value=value, interval=interval, kind=kind, applied=applied, source=source, window=window, depends_on=depends_on or {}, note=note)


def build_overlay(dengue: dict | None, covid: dict[str, dict]) -> dict:
    ov: dict = {}
    if dengue:
        d, amp = dengue["dispersion"], dengue["amplitude"]
        boots = []
        rng = np.random.default_rng(1)
        ls = np.log([a["scale"] for a in amp["per_year"]])
        for _ in range(500):
            boots.append(np.std(rng.choice(ls, size=len(ls), replace=True)))
        f = d["shock_sd"] / max(d["achieved_log_sd"], 1e-9)
        sd_ci = [float(np.percentile(boots, 10)) * f, float(np.percentile(boots, 90)) * f]
        t22 = dengue.get("target_2022")
        yrs = f"{dengue['annual_stats']['years'][0]}-{dengue['annual_stats']['years'][-1]}"
        ov["dengue"] = dict(
            params={
                "shock_sd": _param(round(d["shock_sd"], 4), kind="identified", applied=True, interval=[round(sd_ci[0], 4), round(sd_ci[1], 4)],
                                   source="OpenDengue national annual totals", window=yrs,
                                   note="spread of the ensemble set so that annual totals across trials vary like Viet Nam's year-to-year amplitudes (log-SD "
                                        f"{amp['log_sd']:.2f}); the engine default 0.08 is far wider"),
                "epidemic_scale": _param(1.0, kind="scenario_reference", applied=False, source="OpenDengue national annual totals", window=yrs,
                                         interval=[round(amp["p10"], 2), round(amp["p90"], 2)],
                                         note="year amplitude relative to the WHO-calibrated starting level; NOT predictable from climate (see report)"),
            },
            scenarios=[dict(label="Quiet year", value=round(amp["p10"], 2), basis="10th percentile of the yearly amplitudes"),
                       dict(label="Typical year", value=round(amp["median_scale"], 2), basis="median of the yearly amplitudes"),
                       *([dict(label="2022-like epidemic", value=round(t22["fitted_scale"], 2), basis="fitted so the engine median equals the reported 2022 total")] if t22 else []),
                       dict(label="Model level", value=1.0, basis="starting level from the climate model x WHO calibration")],
            status="PARTIAL",
            note=(f"Calibrated on OpenDengue national reports {yrs}"
                  + (f"; 2022 (annual total only, {t22['target']:,.0f}) is the amplitude target" if t22 else "")
                  + ". The climate model explains "
                  + f"{max(dengue['annual_stats']['r2_model_vs_constant'], 0):.0%} of the year-to-year amplitude, so epidemic years are scenarios, not forecasts. "
                    "Reporting fraction and immunity cancel out of reported counts and are NOT identified."))
    for key, c in covid.items():
        w = c["windows"]
        win = f"WHO weekly {w['cases'][0]}..{w['cases'][1]} (cases), {w['deaths'][0]}..{w['deaths'][1]} (deaths)"
        a = c["assumed"]
        pre = c["deaths"]["preset_timing"]
        params = {}
        params["p_hosp"] = _param(round(pre["p_hosp"], 5), kind="pinned_combination", applied=True, interval=[round(x, 5) for x in c["deaths"]["p_hosp_preset_timing_ci90"]],
                                  source="WHO weekly deaths and cases", window=win,
                                  depends_on=dict(report=a["report"], cfr_hosp=a["cfr_hosp"], los=a["los"], admit_delay=a["admit_delay"]),
                                  note=f"pinned so that deaths per reported case = {pre['deaths_per_reported_case']:.2%} "
                                       f"[{c['deaths']['deaths_per_reported_case_ci90'][0]:.2%}-{c['deaths']['deaths_per_reported_case_ci90'][1]:.2%}] "
                                       f"(infection-to-death timing {'identified' if c['deaths']['timing_identified'] else 'NOT identified'})")
        pin = c["immune_pin"]
        if pin["moved"]:
            params["immune"] = _param(pin["pinned"], kind="pinned_combination", applied=True, interval=pin["range_within_tolerance"],
                                      source="WHO weekly cases", window=win, depends_on=dict(report=a["report"]),
                                      note="the wave size bounds report x (1 - immune) from below; the assumed immune share could not reproduce the wave")
        if key == "covid_omicron":
            im = pin["pinned"]
            r0 = c["growth"]["R_early"] / (1 - im)
            lo, hi = [x / (1 - im) for x in c["growth"]["R_early_range_over_periods"]]
            params["r0"] = _param(round(r0, 2), kind="pinned_combination", applied=True, interval=[round(lo, 2), round(hi, 2)], source="WHO weekly cases (first month of growth)",
                                  window=f"{c['growth']['window'][0]}..{c['growth']['window'][1]}", depends_on=dict(immune=im, latent=a["latent"], infectious=a["infectious"]),
                                  note="R_eff in the first month of growth / (1 - immune); assumes no distancing in that month. Changing 'Already immune' alone breaks the pairing")
        else:
            params["r0"] = _param(None, kind="pinned_combination", applied=False, interval=c["growth"]["R_early_range_over_periods"], source="WHO weekly cases",
                                  window=f"{c['growth']['window'][0]}..{c['growth']['window'][1]}", depends_on=dict(latent=a["latent"], infectious=a["infectious"]),
                                  note=f"early R_eff ≈ {c['growth']['R_early']:.2f} was measured UNDER containment, so the intrinsic Delta R0 is not identified; the preset R0 is kept")
        ov[key] = dict(
            params=params, status="PARTIAL",
            note=(f"Calibrated on WHO weekly cases and deaths for Viet Nam, {c['label']}. Pinned: deaths per reported case "
                  f"{pre['deaths_per_reported_case']:.2%}" + (f"; immune share {pin['pinned']:.0%} (assumed {pin['assumed']:.0%} cannot reproduce the wave size)" if pin["moved"] else "")
                  + ". NOT identified: reporting fraction vs hospitalisation vs immunity separately, and the whole hospital layer "
                    "(length of stay, beds, no-bed death multiplier): there are no hospital data."),
            backtest=key)
    return ov


def run(say=print, quick: bool = False, fetch: bool = False) -> dict:
    """Run the full calibration and write data/gold/vn/calibration.json. Components without data are reported as
    NO_CALIBRATION and contribute nothing to the overlay."""
    if not data.is_built():
        raise RuntimeError("Viet Nam data not built (hp vn-fetch)")
    t0 = time.perf_counter()
    if fetch and data.covid_weekly() is None:
        data.fetch_covid(say=say)
    g = data.gold_dir()
    inputs = {"dengue_national": _sha_file(g / "dengue_national.parquet"), "estimates": _sha_file(g / "estimates.parquet"), "model_json": _sha_file(g / "model.json"),
              "weather_monthly": _sha_file(g / "weather_monthly.parquet"), "engine_sha256": sb.engine_sha()}
    cov_man = data._latest_bronze("vn_who_covid")
    if cov_man:
        inputs["vn_who_covid"] = dict(sha256=cov_man["sha256"], upstream_sha256=cov_man.get("upstream_sha256"), retrieved_at=cov_man["retrieved_at"], url=cov_man["source_url"])
    say("Dengue…")
    try:
        dengue = dengue_report(say=say, quick=quick)
        dengue_status = "OK"
    except Exception as e:  # noqa: BLE001
        dengue, dengue_status = None, f"NO_CALIBRATION ({type(e).__name__}: {e})"
    covid, covid_status = {}, {}
    for key in WAVES:
        say(f"COVID-19 {key}…")
        try:
            covid[key] = covid_wave(key, say=say, quick=quick)
            covid_status[key] = "OK"
        except Exception as e:  # noqa: BLE001
            covid_status[key] = f"NO_CALIBRATION ({type(e).__name__}: {e})"
            say(f"  {key}: {covid_status[key]}")
    backtests = {k: dict(label=c["label"], windows=c["windows"], **c["series"], metrics=c["metrics"], R_path=c["fit"]["R_path"]) for k, c in covid.items()}
    if dengue:
        backtests["dengue_annual"] = dict(years=[r["year"] for r in dengue["annual"]], observed=[r["observed"] for r in dengue["annual"]],
                                           glm_x_kappa=[r["model_x_kappa"] for r in dengue["annual"]], seasonal_only_x_kappa=[r["normal_x_kappa"] for r in dengue["annual"]],
                                           basis=[r["basis"] for r in dengue["annual"]], target_2022=dengue["target_2022"])
    art = dict(
        version=VERSION, created_at=datetime.now(timezone.utc).isoformat(), quick=quick, semantics="CALIBRATION (in-sample unless marked HOLDOUT)",
        status=dict(dengue=dengue_status, covid=covid_status), inputs=inputs,
        method=dict(
            engine="the real sandbox engine (vietnam/sandbox.py, 34 provinces, stochastic) for dengue; an exact deterministic reduction of its daily chain for the "
                   "COVID R(t) fit, then replayed in the real engine",
            discrete_time_fix="the engine's per-day transmission rate is R*(1-exp(-gamma)); the earlier R*gamma made the effective R ~9-10% higher than r0 for every pathogen",
            covid_fit="piecewise-linear log R(t) with knots every 14 days, soft-L1 loss on log weekly counts, second-difference smoothing, parametric negative-binomial bootstrap",
            replay_shock_sd=REPLAY_SHOCK_SD),
        dengue=dengue, covid=covid, overlay=build_overlay(dengue, covid), backtests=backtests,
        not_identified=["reporting fraction (cancels out of reported counts)", "share immune at the start, apart from the lower bound report x (1 - immune)",
                        "hospital layer: length of stay, beds usable, death multiplier without a bed (no hospital data)",
                        "province-level dynamics (all fits use national weekly series)", "intrinsic Delta R0 (early growth was under containment)",
                        "monthly or weekly dengue 2022 (OpenDengue V1.3 has the 2022 annual total only)"],
        seconds=round(time.perf_counter() - t0, 1))
    write_json_atomic(_path(), json.loads(json.dumps(art, default=_jsonable)))
    _load_cached.cache_clear()
    sb.apply_calibration()
    say(f"calibration written ({_path()}) in {art['seconds']}s")
    return art


def _jsonable(o):
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return [None if (isinstance(x, float) and not math.isfinite(x)) else x for x in o.tolist()]
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(type(o))
