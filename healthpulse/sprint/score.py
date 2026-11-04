"""IMDC scoring: weighted interval score (WIS) on the median and the 50/80/90/95% central intervals, weekly, per state.

WIS = 1/(K + 1/2) * (1/2 |y - m| + sum_k alpha_k/2 * IS_alpha_k), IS_alpha = (u - l) + 2/alpha (l - y)+ + 2/alpha (y - u)+
(Bracher et al. 2021, the forecasting-hub convention). WIS is in case units, so large states dominate a plain mean;
`summary` also reports the geometric mean over states of WIS relative to the climatology reference (scale-free).
"""
from __future__ import annotations

import numpy as np
import polars as pl

from .data import QUANTILES
from .models import Forecast

ALPHAS = (0.5, 0.2, 0.1, 0.05)


def _qi(p: float) -> int:
    return QUANTILES.index(round(p, 4))


def wis(q: np.ndarray, y: np.ndarray) -> np.ndarray:
    """q (weeks, Q) in QUANTILES order, y (weeks,) -> WIS per week."""
    m = q[:, _qi(0.5)]
    tot = 0.5 * np.abs(y - m)
    for a in ALPHAS:
        lo, hi = q[:, _qi(a / 2)], q[:, _qi(1 - a / 2)]
        is_ = (hi - lo) + 2 / a * np.maximum(lo - y, 0) + 2 / a * np.maximum(y - hi, 0)
        tot = tot + a / 2 * is_
    return tot / (len(ALPHAS) + 0.5)


def score(fcs: list[Forecast], d: pl.DataFrame) -> pl.DataFrame:
    """One row per (uf, season, date) that has an observation: wis, abs error of the median, in-50/90/95 flags."""
    obs = {(u, dt): c for u, dt, c in d.select("uf", "date", "cases").iter_rows()}
    rows = []
    for f in fcs:
        idx = [i for i, dt in enumerate(f.dates) if (f.uf, dt) in obs]
        if not idx:
            continue
        y = np.array([obs[(f.uf, f.dates[i])] for i in idx])
        q = f.q[idx]
        w = wis(q, y)
        for j, i in enumerate(idx):
            rows.append({"uf": f.uf, "season": f.season, "date": f.dates[i], "y": y[j], "median": q[j, _qi(0.5)], "wis": w[j],
                         "ae": abs(y[j] - q[j, _qi(0.5)]),
                         "in50": q[j, _qi(0.25)] <= y[j] <= q[j, _qi(0.75)],
                         "in90": q[j, _qi(0.05)] <= y[j] <= q[j, _qi(0.95)],
                         "in95": q[j, _qi(0.025)] <= y[j] <= q[j, _qi(0.975)]})
    return pl.DataFrame(rows)


def peak_errors(fcs: list[Forecast], d: pl.DataFrame) -> pl.DataFrame:
    """Peak week (weeks off) and peak size (log ratio) of the median curve vs observed; only for complete seasons."""
    rows = []
    for f in fcs:
        o = d.filter((pl.col("uf") == f.uf) & (pl.col("season") == f.season)).sort("date")
        if len(o) < len(f.dates):
            continue
        y = o["cases"].to_numpy()
        m = f.q[:, _qi(0.5)]
        rows.append({"uf": f.uf, "season": f.season, "peak_week_err": int(np.argmax(m) - np.argmax(y)),
                     "peak_log_ratio": float(np.log((m.max() + 1) / (y.max() + 1))),
                     "total_log_ratio": float(np.log((m.sum() + 1) / (y.sum() + 1)))})
    return pl.DataFrame(rows)


def summary(scores: dict[str, pl.DataFrame], reference: str = "climatology") -> pl.DataFrame:
    """Per model and season: mean WIS, MAE, coverage, and relative WIS vs `reference` (geometric mean over states)."""
    ref = scores[reference].group_by("uf", "season").agg(pl.col("wis").mean().alias("ref"))
    out = []
    for name, s in scores.items():
        per = s.group_by("uf", "season").agg(pl.col("wis").mean().alias("w")).join(ref, on=["uf", "season"])
        rel = per.group_by("season").agg((pl.col("w") / pl.col("ref")).log().mean().exp().alias("rel_wis"))
        agg = s.group_by("season").agg(pl.col("wis").mean().alias("mean_wis"), pl.col("ae").mean().alias("mae"),
                                       pl.col("in50").mean().alias("cov50"), pl.col("in90").mean().alias("cov90"),
                                       pl.col("in95").mean().alias("cov95"), pl.len().alias("n"))
        out.append(agg.join(rel, on="season").with_columns(pl.lit(name).alias("model")))
    return pl.concat(out).select("model", "season", "n", "rel_wis", "mean_wis", "mae", "cov50", "cov90", "cov95").sort("season", "rel_wis")
