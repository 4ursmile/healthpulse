"""Metrics, interval calibration from out-of-fold residuals, and outbreak-warning scores."""
from __future__ import annotations

import numpy as np
import polars as pl


def interval_factors(y: np.ndarray, yhat: np.ndarray, coverage: float = 0.8) -> tuple[float, float]:
    """Multiplicative residual quantiles on the (count+1) scale: p_lo = (ŷ+1)·q_lo − 1."""
    ok = np.isfinite(y) & np.isfinite(yhat)
    if ok.sum() < 10:
        return float("nan"), float("nan")
    r = (y[ok] + 1.0) / (yhat[ok] + 1.0)
    a = (1 - coverage) / 2
    return float(np.quantile(r, a)), float(np.quantile(r, 1 - a))


def apply_interval(yhat: np.ndarray, qlo: float, qhi: float) -> tuple[np.ndarray, np.ndarray]:
    if not np.isfinite(qlo):
        return np.full_like(yhat, np.nan), np.full_like(yhat, np.nan)
    return np.clip((yhat + 1) * qlo - 1, 0, None), np.clip((yhat + 1) * qhi - 1, 0, None)


# Central-interval levels scored by the Weighted Interval Score (FluSight / Infodengue-Mosqlimate sprint convention).
WIS_ALPHAS = (0.02, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


def wis_factors(y: np.ndarray, yhat: np.ndarray) -> dict | None:
    """Out-of-fold multiplicative residual quantiles for every WIS interval level."""
    ok = np.isfinite(y) & np.isfinite(yhat)
    if ok.sum() < 10:
        return None
    r = (y[ok] + 1.0) / (yhat[ok] + 1.0)
    return {"median": float(np.median(r)),
            "intervals": [[a, float(np.quantile(r, a / 2)), float(np.quantile(r, 1 - a / 2))] for a in WIS_ALPHAS]}


def wis(y: np.ndarray, yhat: np.ndarray, factors: dict | None) -> np.ndarray:
    """Per-row Weighted Interval Score (lower is better; equals absolute error for a point forecast with K=0)."""
    if not factors:
        return np.full(len(y), np.nan)
    m = np.clip((yhat + 1) * factors["median"] - 1, 0, None)
    tot = 0.5 * np.abs(y - m)
    for a, qlo, qhi in factors["intervals"]:
        lo, hi = np.clip((yhat + 1) * qlo - 1, 0, None), np.clip((yhat + 1) * qhi - 1, 0, None)
        tot = tot + (a / 2) * ((hi - lo) + (2 / a) * np.clip(lo - y, 0, None) + (2 / a) * np.clip(y - hi, 0, None))
    return tot / (len(factors["intervals"]) + 0.5)


def metrics(y: np.ndarray, yhat: np.ndarray, lo: np.ndarray | None = None, hi: np.ndarray | None = None,
            threshold: np.ndarray | None = None, wis_rows: np.ndarray | None = None) -> dict:
    ok = np.isfinite(y) & np.isfinite(yhat)
    if wis_rows is not None:
        w = wis_rows[ok]
        wis_mean = float(np.nanmean(w)) if np.isfinite(w).any() else None
    else:
        wis_mean = None
    y, yhat = y[ok], yhat[ok]
    n = int(ok.sum())
    if n == 0:
        return {"n": 0}
    err = yhat - y
    out = {"n": n, "mae": float(np.mean(np.abs(err))), "bias": float(np.mean(err)),
           "wape": float(np.sum(np.abs(err)) / np.sum(y)) if np.sum(y) > 0 else None,
           "rmse": float(np.sqrt(np.mean(err ** 2)))}
    if wis_mean is not None:
        out["wis"] = wis_mean
    mu = np.clip(yhat, 1e-6, None)
    with np.errstate(divide="ignore", invalid="ignore"):
        term = np.where(y > 0, y * np.log(y / mu), 0.0) - (y - mu)
    out["poisson_deviance"] = float(2 * np.mean(term))
    if lo is not None and hi is not None:
        lo, hi = lo[ok], hi[ok]
        okk = np.isfinite(lo) & np.isfinite(hi)
        if okk.any():
            out["coverage80"] = float(np.mean((y[okk] >= lo[okk]) & (y[okk] <= hi[okk])))
            out["interval_width"] = float(np.mean(hi[okk] - lo[okk]))
    if threshold is not None:
        th = threshold[ok]
        okt = np.isfinite(th)
        if okt.any():
            actual = y[okt] > th[okt]
            pred = yhat[okt] > th[okt]
            tp = int((actual & pred).sum())
            out["outbreak_precision"] = (tp / int(pred.sum())) if pred.sum() else None
            out["outbreak_recall"] = (tp / int(actual.sum())) if actual.sum() else None
            out["outbreak_events"] = int(actual.sum())
    return out


def thresholds(train: pl.DataFrame, q: float = 0.9) -> dict[str, float]:
    """Provisional outbreak threshold per municipality: q-quantile of training labels (to be co-defined with epidemiologists)."""
    g = train.group_by("municipality_id").agg(pl.col("label").quantile(q).alias("t"))
    return {r["municipality_id"]: float(r["t"]) for r in g.iter_rows(named=True)}


def slices(df: pl.DataFrame) -> dict[str, pl.Expr]:
    s = {"all": pl.lit(True), "low_counts_lt10": pl.col("label") < 10}
    if "holiday_in_target_week" in df.columns:
        s["holiday_weeks"] = pl.col("holiday_in_target_week") > 0
    if "incidence_lag_1w" in df.columns:
        s["high_incidence"] = pl.col("incidence_lag_1w") > df["incidence_lag_1w"].quantile(0.9)
    return s
