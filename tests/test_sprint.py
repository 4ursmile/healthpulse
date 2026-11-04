"""IMDC benchmark: WIS against hand-computed values, epiweek season dates, no look-ahead past EW25. Offline."""
from datetime import date

import numpy as np
import polars as pl

from healthpulse.sprint import data as D
from healthpulse.sprint import models as M
from healthpulse.sprint import score as S


def test_wis_point_forecast_is_abs_error():
    # all quantiles equal to the median -> intervals have zero width, WIS = (0.5|y-m| + sum a/2 * 2/a |y-m|)/(K+0.5) = |y-m|
    q = np.full((1, len(D.QUANTILES)), 10.0)
    assert np.isclose(S.wis(q, np.array([14.0]))[0], 4.0)


def test_wis_inside_all_intervals_is_width_term():
    q = np.array([[0, 1, 2, 3, 5, 7, 8, 9, 10]], float)  # QUANTILES order
    y = np.array([5.0])
    widths = {0.5: 4, 0.2: 6, 0.1: 8, 0.05: 10}
    expect = sum(a / 2 * w for a, w in widths.items()) / 4.5
    assert np.isclose(S.wis(q, y)[0], expect)


def test_season_dates_mmwr():
    s = D.season_dates(2025)  # 2025 has an EW53
    assert s[0] == date(2025, 10, 5) and s[-1] == date(2026, 10, 4) and len(s) == 53
    assert len(D.season_dates(2023)) == 52 and D.season_dates(2023)[0] == date(2023, 10, 8)


def _synthetic(seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for uf in ("AA", "BB", "CC"):
        for s in range(2010, 2024):
            amp = rng.lognormal(6, 0.7)
            for k, dt in enumerate(D.season_dates(s)):
                y, w = (dt.year, int(dt.strftime("%U")))
                lam = 5 + amp * np.exp(-((k - 22) / 6) ** 2)
                rows.append({"date": dt, "uf": uf, "cases": float(rng.poisson(lam)), "season": s, "k": k})
    d = pl.DataFrame(rows)
    # epiweek from season position: EW41 + k, wrapping at the year's week count
    ew = []
    for s, k in d.select("season", "k").iter_rows():
        n1 = len(D.season_dates(s)) - 40  # weeks of year s from EW41 on
        ew.append(s * 100 + 41 + k if k < n1 else (s + 1) * 100 + (k - n1 + 1))
    d = d.with_columns(pl.Series("epiweek", ew)).with_columns(year=pl.col("epiweek") // 100, week=pl.col("epiweek") % 100)
    return d


def test_no_lookahead(monkeypatch):
    monkeypatch.setattr(M, "enso_weekly", lambda: pl.DataFrame({"epiweek": [201001], "enso": [0.0], "iod": [0.0], "pdo": [0.0]}))
    d = _synthetic()
    f1 = M.SeasonForecaster(d)
    a = f1.shifted(2021)
    # changing every value after the 2021 cut-off (EW25 2021) must not change the forecast
    d2 = d.with_columns(pl.when(pl.col("epiweek") > 202125).then(pl.col("cases") * 50).otherwise(pl.col("cases")).alias("cases"))
    b = M.SeasonForecaster(d2).shifted(2021)
    for x, y in zip(a, b):
        assert np.allclose(x.q, y.q)
    q = a[0].q
    assert (np.diff(q, axis=1) >= -1e-9).all()  # quantiles are ordered
