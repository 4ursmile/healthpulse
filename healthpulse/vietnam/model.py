"""Climate-driven dengue model for the 34 provinces, the current risk state, alerts and the monthly timeline.

Method (all fitted on real data; every output is labelled):
  * Training: province-month reported dengue 2004-2010 (OpenDengue / Tycho; 2004 is the first year in which every
    pre-2025 unit reports separately, so the GAUL->P-code aggregation is exact).
  * Poisson GLM:  log E[cases] = province + region x calendar-month + f(lagged climate anomalies, thermal curve).
    Climate = NASA POWER monthly temperature, rainfall, humidity at lag 1-2 months.
  * Holdout: fit 2004-2009, score 2010 (MAE, deviance explained) before refitting on all years.
  * Level calibration: the 2004-2010 era level is rescaled to the WHO national totals of the latest 24 reported months
    (kappa). Provincial estimates after 2010 are therefore MODEL ESTIMATES, not reports.
  * Risk ratio = estimate / seasonal norm (same model with zero climate anomaly).
  * Historical alerts (observed months) use a leave-one-year-out endemic channel: mean + 2 SD of the same calendar month.
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, timezone
from functools import lru_cache

import numpy as np
import polars as pl
from sklearn.linear_model import PoissonRegressor

from ..core.util import write_json_atomic
from .crosswalk import MERGER, REGION
from .data import gold_dir

PIDS = list(MERGER)
REGIONS = ["north", "central", "highlands", "south"]
_lock = threading.Lock()


# ---------------------------------------------------------------- loaders
@lru_cache(maxsize=1)
def provinces() -> list[dict]:
    return json.loads((gold_dir() / "provinces.json").read_text())


@lru_cache(maxsize=1)
def geometry_bytes() -> bytes:
    return (gold_dir() / "provinces.geojson").read_bytes()


@lru_cache(maxsize=1)
def manifest() -> dict:
    return json.loads((gold_dir() / "manifest.json").read_text())


@lru_cache(maxsize=1)
def workforce_national() -> dict:
    return json.loads((gold_dir() / "workforce_national.json").read_text())


@lru_cache(maxsize=1)
def dengue_monthly() -> pl.DataFrame:
    return pl.read_parquet(gold_dir() / "dengue_province_monthly.parquet")


@lru_cache(maxsize=1)
def national() -> pl.DataFrame:
    return pl.read_parquet(gold_dir() / "dengue_national.parquet")


@lru_cache(maxsize=1)
def weather_monthly() -> pl.DataFrame:
    return pl.read_parquet(gold_dir() / "weather_monthly.parquet")


@lru_cache(maxsize=1)
def weather_daily() -> pl.DataFrame:
    return pl.read_parquet(gold_dir() / "weather_daily.parquet")


@lru_cache(maxsize=1)
def model_info() -> dict:
    return json.loads((gold_dir() / "model.json").read_text())


@lru_cache(maxsize=1)
def estimates() -> pl.DataFrame:
    return pl.read_parquet(gold_dir() / "estimates.parquet")


def reset_cache() -> None:
    for f in (provinces, geometry_bytes, manifest, workforce_national, dengue_monthly, national, weather_monthly, weather_daily,
              model_info, estimates, _state_cached, timeline):
        f.cache_clear()


# ---------------------------------------------------------------- features
def _features() -> pl.DataFrame:
    w = weather_monthly().select("province_id", "month", "t2m", "rain_mm_day", "rh", "days")
    # a month needs >= 20 days of daily data to count (current partial month is otherwise excluded)
    w = w.with_columns(pl.when(pl.col("days").is_not_null() & (pl.col("days") < 20)).then(None).otherwise(pl.col("t2m")).alias("t2m"))
    w = w.with_columns(pl.col("month").dt.month().alias("moy"))
    clim = w.filter(pl.col("month").dt.year().is_between(1994, 2023)).group_by("province_id", "moy").agg(
        pl.col("t2m").mean().alias("t_c"), pl.col("rain_mm_day").mean().alias("r_c"), pl.col("rh").mean().alias("h_c"))
    w = w.join(clim, on=["province_id", "moy"]).sort("province_id", "month")
    w = w.with_columns((pl.col("t2m") - pl.col("t_c")).alias("t_a"), (pl.col("rain_mm_day") - pl.col("r_c")).alias("r_a"),
                       (pl.col("rh") - pl.col("h_c")).alias("h_a"))
    # add the next month (the one being estimated) so its lag-1 climate is available
    last = w.group_by("province_id").agg(pl.col("month").max())
    nxt = last.with_columns(pl.col("month").dt.offset_by("1mo")).with_columns(pl.col("month").dt.month().alias("moy"))
    nxt = nxt.join(clim, on=["province_id", "moy"])
    w = pl.concat([w, nxt], how="diagonal").sort("province_id", "month")
    lag = lambda c, k: pl.col(c).shift(k).over("province_id")  # noqa: E731
    w = w.with_columns(lag("t_a", 1).alias("t_a1"), lag("r_a", 1).alias("r_a1"), lag("r_a", 2).alias("r_a2"), lag("h_a", 1).alias("h_a1"),
                       lag("t2m", 1).alias("t1"), lag("t_c", 1).alias("t_c1"))
    return w.with_columns(pl.col("province_id").replace_strict(REGION).alias("region"))


def _design(df: pl.DataFrame, climate: bool = True) -> np.ndarray:
    n = df.height
    P = np.zeros((n, len(PIDS)))
    P[np.arange(n), [PIDS.index(p) for p in df["province_id"]]] = 1
    S = np.zeros((n, len(REGIONS) * 12))
    S[np.arange(n), [REGIONS.index(r) * 12 + m - 1 for r, m in zip(df["region"], df["moy"])]] = 1
    if climate:
        t1 = df["t1"].to_numpy()
        C = np.column_stack([df["t_a1"].to_numpy(), df["r_a1"].to_numpy(), df["r_a2"].to_numpy(), df["h_a1"].to_numpy(),
                             np.maximum(t1 - 27.0, 0) ** 2, np.maximum(20.0 - t1, 0)])
    else:
        tc = df["t_c1"].to_numpy()
        C = np.column_stack([np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n), np.maximum(tc - 27.0, 0) ** 2, np.maximum(20.0 - tc, 0)])
    return np.column_stack([P, S, C / C_SCALE])


C_SCALE = np.array([1.0, 3.0, 3.0, 5.0, 4.0, 3.0])  # fixed scales so lbfgs converges; coefficients are reported per raw unit
CLIMATE_TERMS = ["temp anomaly (lag 1)", "rain anomaly (lag 1)", "rain anomaly (lag 2)", "humidity anomaly (lag 1)",
                 "heat above 27 °C² (lag 1)", "cold below 20 °C (lag 1)"]


def fit_and_store() -> dict:
    feats = _features()
    obs = dengue_monthly().rename({"month": "month"})
    df = feats.join(obs, on=["province_id", "month"], how="left")
    train = df.filter(pl.col("month").dt.year().is_between(2004, 2010) & pl.col("cases").is_not_null()).drop_nulls(["t_a1", "r_a2", "h_a1", "t1"])
    tr, te = train.filter(pl.col("month").dt.year() < 2010), train.filter(pl.col("month").dt.year() == 2010)
    m0 = PoissonRegressor(alpha=1e-4, max_iter=8000).fit(_design(tr), tr["cases"].to_numpy())
    pred = m0.predict(_design(te))
    yte = te["cases"].to_numpy()
    base = PoissonRegressor(alpha=1e-4, max_iter=8000).fit(_design(tr, climate=False), tr["cases"].to_numpy())
    pred0 = base.predict(_design(te, climate=False))
    m = PoissonRegressor(alpha=1e-4, max_iter=8000).fit(_design(train), train["cases"].to_numpy())

    full = df.drop_nulls(["t_a1", "r_a2", "h_a1", "t1"])
    est = m.predict(_design(full))
    norm = m.predict(_design(full, climate=False))
    out = full.select("province_id", "month", "cases", "units_reporting", "t2m", "rain_mm_day", "rh", "t_a1", "r_a1").with_columns(
        pl.Series("expected_raw", est), pl.Series("normal_raw", norm))

    # national calibration (kappa) on the latest 24 WHO monthly reports; weekly reports are summed to months
    nat = national()
    natm = nat.filter(pl.col("T_res") == "Month").select(pl.col("start").dt.truncate("1mo").alias("month"), "cases")
    natw = (nat.filter(pl.col("T_res") == "Week").with_columns(pl.col("start").dt.truncate("1mo").alias("month"))
            .group_by("month").agg(pl.col("cases").sum(), pl.len().alias("n")).filter(pl.col("n") >= 4).select("month", "cases"))
    natm = pl.concat([natm, natw.filter(~pl.col("month").is_in(natm["month"].implode()))]).sort("month")
    sumE = out.group_by("month").agg(pl.col("expected_raw").sum().alias("E"), pl.len().alias("n")).filter(pl.col("n") == 34)
    cal = natm.join(sumE, on="month").sort("month")
    recent = cal.tail(24)
    kappa = float(recent["cases"].sum() / recent["E"].sum()) if recent.height else 1.0
    after = cal.filter(pl.col("month").dt.year() >= 2011)
    # yearly kappa: the shape test uses a level re-estimated each year, so it scores the seasonal/climate signal only
    yk = after.with_columns(pl.col("month").dt.year().alias("y")).group_by("y").agg((pl.col("cases").sum() / pl.col("E").sum()).alias("k"))
    after = after.with_columns(pl.col("month").dt.year().alias("y")).join(yk, on="y").with_columns((pl.col("E") * pl.col("k")).alias("fit"))
    corr = float(np.corrcoef(after["cases"].to_numpy(), after["fit"].to_numpy())[0, 1]) if after.height > 6 else None

    def dev_expl(y, mu, mu0):
        def d(mu_):
            mu_ = np.maximum(mu_, 1e-9)
            return 2 * np.sum(np.where(y > 0, y * np.log(np.maximum(y, 1e-9) / mu_), 0) - (y - mu_))
        return float(1 - d(mu) / d(np.full_like(y, y.mean(), dtype=float)))

    info = {
        "fitted_at": datetime.now(timezone.utc).isoformat(), "method": "Poisson GLM (scikit-learn), province + region×month + lagged climate",
        "train_period": "2004-01..2010-12", "rows": train.height, "kappa": kappa,
        "kappa_basis": f"WHO national reports {recent['month'].min()}..{recent['month'].max()} ÷ model sum" if recent.height else "none",
        "holdout_2010": {"mae": float(np.mean(np.abs(pred - yte))), "mae_seasonal_only": float(np.mean(np.abs(pred0 - yte))),
                         "deviance_explained": dev_expl(yte, pred, None), "deviance_explained_seasonal_only": dev_expl(yte, pred0, None)},
        "national_shape_corr_2011_on": corr, "national_months_compared": after.height,
        "climate_coefficients": dict(zip(CLIMATE_TERMS, [float(c) for c in m.coef_[-6:] / C_SCALE])),
        "latest_weather_month": str(feats.drop_nulls("t2m")["month"].max()),
        "estimate_month": str(out["month"].max()),
        "national_calibration": [{"month": str(r["month"]), "reported": r["cases"], "model_fit": r["fit"]} for r in after.iter_rows(named=True)],
    }
    with _lock:
        out.write_parquet(gold_dir() / "estimates.parquet")
        write_json_atomic(gold_dir() / "model.json", info)
    reset_cache()
    return info


# ---------------------------------------------------------------- state & alerts
LEVELS = ["normal", "watch", "warning", "alert"]


def classify(inc: float | None, ratio: float | None) -> str:
    if inc is None or ratio is None:
        return "no_data"
    if ratio >= 1.5 and inc >= 10:
        return "alert"
    if ratio >= 1.25 and inc >= 5:
        return "warning"
    if ratio >= 1.1 or inc >= 25:
        return "watch"
    return "normal"


def why(level: str, inc: float, ratio: float, t_a: float | None, r_a: float | None) -> str:
    parts = [f"{inc:.1f} est. cases per 100k this month", f"{ratio:.2f}× the seasonal norm"]
    if t_a is not None and abs(t_a) >= 0.5:
        parts.append(f"last month {abs(t_a):.1f} °C {'warmer' if t_a > 0 else 'cooler'} than normal")
    if r_a is not None and abs(r_a) >= 1:
        parts.append(f"rain {abs(r_a):.1f} mm/day {'above' if r_a > 0 else 'below'} normal")
    return "; ".join(parts)


@lru_cache(maxsize=4)
def _state_cached(month: str | None) -> dict:
    est = estimates()
    info = model_info()
    k = info["kappa"]
    target = date.fromisoformat(month) if month else est["month"].max()
    cur = est.filter(pl.col("month") == target)
    prev = est.filter(pl.col("month") == (pl.lit(target).dt.offset_by("-1mo")))
    prev_map = dict(zip(prev["province_id"], prev["expected_raw"] * k))
    pmap = {p["province_id"]: p for p in provinces()}
    rows = []
    for r in cur.iter_rows(named=True):
        p = pmap[r["province_id"]]
        e = r["expected_raw"] * k
        nrm = r["normal_raw"] * k
        inc = e / p["population"] * 1e5 if p["population"] else None
        ratio = e / nrm if nrm > 0 else None
        lvl = classify(inc, ratio)
        rows.append({"province_id": r["province_id"], "name": p["name"], "region": p["region"], "lat": p["lat"], "lon": p["lon"],
                     "population": p["population"], "estimate": e, "normal": nrm, "ratio": ratio, "incidence_per_100k": inc,
                     "trend": (e - prev_map[r["province_id"]]) / prev_map[r["province_id"]] if prev_map.get(r["province_id"]) else None,
                     "level": lvl, "reason": why(lvl, inc or 0, ratio or 0, r["t_a1"], r["r_a1"]),
                     "temp_anomaly_lag1": r["t_a1"], "rain_anomaly_lag1": r["r_a1"],
                     "observed": r["cases"]})
    rows.sort(key=lambda x: (-LEVELS.index(x["level"]) if x["level"] in LEVELS else 1, -(x["incidence_per_100k"] or 0)))
    counts = {lv: sum(1 for x in rows if x["level"] == lv) for lv in LEVELS}
    return {"month": str(target), "semantics": "MODEL_ESTIMATE" if target.year > 2010 else "OBSERVED+MODEL", "provinces": rows,
            "counts": counts, "national_estimate": sum(x["estimate"] for x in rows), "national_normal": sum(x["normal"] for x in rows),
            "kappa": k}


def state(month: str | None = None) -> dict:
    return _state_cached(month)


@lru_cache(maxsize=1)
def timeline() -> dict:
    """Monthly frames: observed 1997-2010 with endemic-channel alerts, then model estimates 2011..current."""
    est = estimates()
    k = model_info()["kappa"]
    pmap = {p["province_id"]: p for p in provinces()}
    obs = dengue_monthly().filter(pl.col("month").dt.year().is_between(1997, 2010)).with_columns(pl.col("month").dt.month().alias("moy"),
                                                                                                   pl.col("month").dt.year().alias("y"))
    # leave-one-year-out endemic channel
    agg = obs.group_by("province_id", "moy").agg(pl.col("cases").sum().alias("s"), (pl.col("cases") ** 2).sum().alias("s2"),
                                                 pl.col("cases").is_not_null().sum().alias("n"))
    o = obs.join(agg, on=["province_id", "moy"]).with_columns(
        ((pl.col("s") - pl.col("cases").fill_null(0)) / (pl.col("n") - 1)).alias("mu"),
    ).with_columns(
        (((pl.col("s2") - pl.col("cases").fill_null(0) ** 2) / (pl.col("n") - 1) - pl.col("mu") ** 2).clip(0).sqrt()).alias("sd"))
    frames = {}
    for r in o.sort("month").iter_rows(named=True):
        f = frames.setdefault(str(r["month"]), {"month": str(r["month"]), "kind": "OBSERVED", "v": {}, "lv": {}})
        if r["cases"] is None:
            continue
        f["v"][r["province_id"]] = r["cases"]
        thr2, thr1 = r["mu"] + 2 * r["sd"], r["mu"] + r["sd"]
        f["lv"][r["province_id"]] = "alert" if r["cases"] > thr2 and r["cases"] >= 20 else "warning" if r["cases"] > thr1 and r["cases"] >= 10 else "normal"
    for r in est.filter(pl.col("month").dt.year() >= 2011).sort("month").iter_rows(named=True):
        f = frames.setdefault(str(r["month"]), {"month": str(r["month"]), "kind": "MODEL_ESTIMATE", "v": {}, "lv": {}})
        e = r["expected_raw"] * k
        pop = pmap[r["province_id"]]["population"]
        f["v"][r["province_id"]] = round(e, 1)
        f["lv"][r["province_id"]] = classify(e / pop * 1e5 if pop else None, r["expected_raw"] / r["normal_raw"] if r["normal_raw"] > 0 else None)
    out = sorted(frames.values(), key=lambda x: x["month"])
    return {"frames": out, "population": {p: pmap[p]["population"] for p in PIDS}}


def province_series(pid: str) -> dict:
    est = estimates().filter(pl.col("province_id") == pid).sort("month")
    k = model_info()["kappa"]
    obs = dengue_monthly().filter(pl.col("province_id") == pid).sort("month")
    w = weather_monthly().filter(pl.col("province_id") == pid).sort("month").tail(36)
    return {
        "observed": [{"t": str(r["month"]), "y": r["cases"]} for r in obs.iter_rows(named=True)],
        "estimate": [{"t": str(r["month"]), "p50": r["expected_raw"] * k, "normal": r["normal_raw"] * k} for r in est.filter(pl.col("month").dt.year() >= 2011).iter_rows(named=True)],
        "weather": [{"t": str(r["month"]), "t2m": r["t2m"], "rain": r["rain_mm_day"], "rh": r["rh"]} for r in w.iter_rows(named=True)],
    }


def national_series() -> dict:
    nat = national().sort("start")
    return {"weekly": [{"t": str(r["start"]), "y": r["cases"]} for r in nat.filter(pl.col("T_res") == "Week").iter_rows(named=True)],
            "monthly": [{"t": str(r["start"]), "y": r["cases"]} for r in nat.filter(pl.col("T_res") == "Month").iter_rows(named=True)],
            "yearly": [{"t": str(r["start"]), "y": r["cases"]} for r in nat.filter(pl.col("T_res") == "Year").iter_rows(named=True)]}
