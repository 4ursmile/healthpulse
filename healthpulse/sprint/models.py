"""Season-ahead dengue forecasts per UF for the IMDC protocol: data up to EW25 of year s -> weekly quantiles for
EW41 s .. EW40 s+1 (16 to 68 weeks ahead).

Models (all use only rows with epiweek <= s*100+25; nothing is fitted on the forecast season):
  climatology  per state and season week, empirical quantiles of the state's complete past seasons (the reference).
  scenario     HealthPulse season model: the log season total is predicted by a pooled ridge regression on what is
               known at EW25 (this year's first half = the tail and peak of the running season, last year's, the
               state's long-run level, ENSO); the weekly curve is the state's own past seasons' shapes. Scenarios =
               every (leave-one-season-out residual) x (past shape) pair, so peak timing and size uncertainty both
               show up in the intervals.
  blend        equal-weight quantile average of scenario and climatology (Vincentisation).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .data import QUANTILES, enso_weekly, season_dates

NK = 53  # season week slots (53-week seasons exist: 2014, 2020, 2025)
FEATURES = ("a_h1", "a_tail", "enso")


@dataclass
class Forecast:
    uf: str
    season: int
    dates: list
    q: np.ndarray  # (n_weeks, len(QUANTILES))


def _cutoff(season: int) -> int:
    return season * 100 + 25


def _matrix(d: pl.DataFrame) -> tuple[list[str], list[int], np.ndarray]:
    """cases[uf, season, k] with NaN where not observed; 52-week seasons copy slot 51 into 52 (shape padding only)."""
    ufs = sorted(d["uf"].unique().to_list())
    seasons = list(range(int(d["season"].min()), int(d["season"].max()) + 1))
    m = np.full((len(ufs), len(seasons), NK), np.nan)
    ui = {u: i for i, u in enumerate(ufs)}
    si = {s: i for i, s in enumerate(seasons)}
    for u, s, k, c in d.select("uf", "season", "k", "cases").iter_rows():
        m[ui[u], si[s], k] = c
    return ufs, seasons, m


def _complete(m_us: np.ndarray) -> bool:
    return np.isfinite(m_us[:52]).all()


def _year_sum(d: pl.DataFrame, year: int, w0: int, w1: int) -> dict[str, float]:
    r = d.filter((pl.col("year") == year) & pl.col("week").is_between(w0, w1)).group_by("uf").agg(pl.col("cases").sum())
    return dict(r.iter_rows())


def _enso(o: pl.DataFrame, year: int) -> float:
    v = o.filter(pl.col("epiweek").is_between(year * 100 + 14, year * 100 + 25))["enso"]
    return float(v.mean()) if len(v) else 0.0


def _wmean(v: list[float], hl: float | None) -> float:
    """Mean of past values (oldest first); with a half-life `hl` (seasons) recent ones weigh more."""
    if hl is None:
        return float(np.mean(v))
    w = 0.5 ** (np.arange(len(v))[::-1] / hl)
    return float(np.sum(w * np.array(v)) / w.sum())


def _features(d: pl.DataFrame, o: pl.DataFrame, season: int, logT: dict[str, dict[int, float]], ufs, first_year: int,
              hl: float | None) -> tuple[np.ndarray, np.ndarray]:
    """(level, X) for `season`; X are anomalies vs the state's own past, all observable at EW25 of `season`.
    level = (weighted) mean log season total over complete seasons <= season - 2.
    a_h1 = log cases EW1-25 this year minus its past mean (the running season's peak vs normal),
    a_tail = same for EW14-25 (how much is still circulating at the cut-off), enso = mean ENSO index EW14-25."""
    years = range(first_year, season + 1)
    h1 = {y: _year_sum(d, y, 1, 25) for y in years}
    tl = {y: _year_sum(d, y, 14, 25) for y in years}
    e = _enso(o, season)
    lev, X = [], []
    for u in ufs:
        past = [v for s, v in sorted(logT[u].items()) if s <= season - 2]
        ph1 = [np.log1p(h1[y].get(u, 0)) for y in years]
        ptl = [np.log1p(tl[y].get(u, 0)) for y in years]
        lev.append(_wmean(past, hl))
        X.append([ph1[-1] - _wmean(ph1[:-1], hl), ptl[-1] - _wmean(ptl[:-1], hl), e])
    return np.array(lev), np.array(X)


def _ridge(X: np.ndarray, y: np.ndarray, lam: float, grp: np.ndarray, min_groups: int = 4):
    """Ridge on raw features (all are log ratios or the ENSO index, so O(1); standardising them made ENSO, which is
    constant within a season, explode when only a few seasons exist). Intercept unpenalised. With fewer than
    `min_groups` seasons it is intercept-only. Predictions are clipped to the range of the training targets."""
    lo, hi = float(y.min()), float(y.max())
    if len(np.unique(grp)) < min_groups:
        c = float(y.mean())
        return lambda Xn: np.full(len(Xn), c)
    Z1 = np.c_[np.ones(len(X)), X]
    P = lam * np.eye(Z1.shape[1])
    P[0, 0] = 0
    b = np.linalg.solve(Z1.T @ Z1 + P, Z1.T @ y)
    return lambda Xn: np.clip(np.c_[np.ones(len(Xn)), Xn] @ b, lo, hi)


class SeasonForecaster:
    def __init__(self, d: pl.DataFrame, lam: float = 30.0, hl: float | None = None, shrink: float = 100.0, spread: float = 1.0, trend: bool = True):
        self.d, self.lam, self.hl, self.shrink, self.spread, self.trend = d, lam, hl, shrink, spread, trend
        self.o = enso_weekly()
        self.ufs, self.seasons, self.m = _matrix(d)

    # ------------------------------------------------------------------ helpers
    def _hist(self, season: int):
        """Data visible at the cut-off of `season`, and the complete seasons inside it."""
        dh = self.d.filter(pl.col("epiweek") <= _cutoff(season))
        _, seasons, m = _matrix(dh)
        done = [s for j, s in enumerate(seasons) if s <= season - 2 and all(_complete(m[i, j]) for i in range(len(self.ufs)))]
        return dh, seasons, m, done

    def _quant(self, samples: np.ndarray) -> np.ndarray:
        return np.quantile(samples, QUANTILES, axis=0).T  # (weeks, Q)

    # ------------------------------------------------------------------ models
    def climatology(self, season: int) -> list[Forecast]:
        _, seasons, m, done = self._hist(season)
        n = len(season_dates(season))
        out = []
        for i, u in enumerate(self.ufs):
            curves = np.array([m[i, seasons.index(s), :n] for s in done])
            curves = np.where(np.isfinite(curves), curves, curves[:, [51]])  # pad week 53 of 52-week seasons
            out.append(Forecast(u, season, season_dates(season), np.expm1(self._quant(np.log1p(curves)))))
        return out

    def _level_model(self, season: int):
        dh, seasons, m, done = self._hist(season)
        first_year = int(dh["year"].min())
        logT = {u: {s: float(np.log1p(np.nansum(m[i, seasons.index(s), :52]))) for s in done} for i, u in enumerate(self.ufs)}
        X, y, grp = [], [], []
        for s in done:
            if s - 2 < done[0]:  # needs at least one complete season before it
                continue
            lev, F = _features(dh, self.o, s, logT, self.ufs, first_year, self.hl)
            X.append(F)
            y.append(np.array([logT[u][s] for u in self.ufs]) - lev)
            grp += [s] * len(self.ufs)
        X, y, grp = np.vstack(X), np.concatenate(y), np.array(grp)
        # leave-one-season-out residuals = the level uncertainty (they carry the national year-to-year shock)
        res = []
        for s in np.unique(grp):
            f = _ridge(X[grp != s], y[grp != s], self.lam, grp[grp != s])
            res.append(y[grp == s] - f(X[grp == s]))
        res = np.concatenate(res)
        fit = _ridge(X, y, self.lam, grp)
        lev, Fs = _features(dh, self.o, season, logT, self.ufs, first_year, self.hl)
        return lev + fit(Fs), res, m, seasons, done

    def scenario(self, season: int) -> list[Forecast]:
        mu, res, m, seasons, done = self._level_model(season)
        n = len(season_dates(season))
        out = []
        for i, u in enumerate(self.ufs):
            shapes = []
            for s in done:
                c = m[i, seasons.index(s), :NK].copy()
                c[~np.isfinite(c)] = c[51]
                tot = c[:52].sum()
                if tot > 0:
                    shapes.append(c[:n] / tot)
            shapes = np.array(shapes)  # (S, n)
            levels = np.expm1(mu[i] + res)  # (R,)
            samples = (levels[:, None, None] * shapes[None, :, :]).reshape(-1, n)
            out.append(Forecast(u, season, season_dates(season), self._quant(np.log1p(samples))))
            out[-1].q = np.expm1(out[-1].q)
        return out

    # ------------------------------------------------------------------ weekly anomaly model
    def _anomaly_inputs(self, season: int):
        """Per training season s' (complete, <= season-2): leave-s'-out climatology of log1p cases per state and week,
        target r = log1p(y) - clim, features = this-year anomalies at EW25 vs the other years. Training seasons only
        ever see data up to the cut-off of `season`."""
        dh, seasons, m, done = self._hist(season)
        L = np.log1p(m[:, [seasons.index(s) for s in done], :])  # (U, S, NK)
        L[:, :, 52] = np.where(np.isfinite(L[:, :, 52]), L[:, :, 52], L[:, :, 51])
        years = list(range(int(dh["year"].min()), season + 1))
        def lsum(w0, w1):
            a = np.array([[np.log1p(_year_sum(dh, y, w0, w1).get(u, 0)) for y in years] for u in self.ufs])
            return a  # (U, Y)
        H1, TL, RC = lsum(1, 25), lsum(14, 25), lsum(20, 25)
        enso = np.array([_enso(self.o, y) for y in years])
        def feats(year, excl_years):
            j = years.index(year)
            keep = [i for i, y in enumerate(years) if y not in excl_years]
            cols = [H1[:, j] - H1[:, keep].mean(1), TL[:, j] - TL[:, keep].mean(1), RC[:, j] - RC[:, keep].mean(1),
                    np.full(len(self.ufs), enso[j] - enso[keep].mean())]
            if self.trend:  # recent regime: mean of the 3 calendar years before `year` vs all comparison years
                rec = [i for i in keep if years[i] < year][-3:]
                cols.append(H1[:, rec].mean(1) - H1[:, keep].mean(1) if rec else np.zeros(len(self.ufs)))
            return np.c_[tuple(cols)]
        return L, done, years, feats

    def anomaly(self, season: int) -> list[Forecast]:
        L, done, years, feats = self._anomaly_inputs(season)
        U, S, _ = L.shape
        n = len(season_dates(season))
        X, R, G = [], [], []
        for j, s in enumerate(done):
            if s not in years:
                continue
            clim = np.delete(L, j, axis=1).mean(1)  # (U, NK)
            X.append(feats(s, {s, s + 1}))  # the season's own calendar years are left out of the comparison
            R.append(L[:, j, :] - clim)
            G += [s] * U
        X, R, G = np.vstack(X), np.vstack(R), np.array(G)  # (N, F), (N, NK)
        clim_now = L.mean(1)
        Xn = feats(season, {season})
        q = np.zeros((U, n, len(QUANTILES)))
        res_k = []
        for k in range(n):
            res = []
            for s in np.unique(G):
                f = _ridge(X[G != s], R[G != s, k], self.lam, G[G != s])
                res.append(R[G == s, k] - f(X[G == s]))
            res_k.append(np.concatenate(res))
            mu = clim_now[:, k] + _ridge(X, R[:, k], self.lam, G)(Xn)
            q[:, k, :] = mu[:, None]
        res_k = np.array(res_k)  # (n, N); column order = training rows, state index = row % U
        # Residual spread differs a lot by state (small, volatile states vs large, stable ones): scale = each state's own
        # residual sd shrunk toward the pooled sd with `shrink` pseudo-observations; the shape of the distribution is the
        # pooled standardised residuals of weeks k-3..k+3.
        st = np.tile(np.arange(U), len(res_k[0]) // U)
        pooled = res_k.std()
        sd_u = np.array([res_k[:, st == i].std() for i in range(U)])
        n_u = (st == 0).sum() * n
        sd_u = np.sqrt((n_u * sd_u ** 2 + self.shrink * pooled ** 2) / (n_u + self.shrink))
        Z = res_k / sd_u[st][None, :]
        for k in range(n):
            zq = np.quantile(Z[max(0, k - 3):k + 4].ravel(), QUANTILES)
            q[:, k, :] += sd_u[:, None] * zq[None, :]
        q = np.maximum(np.expm1(q), 0)
        return [Forecast(u, season, season_dates(season), q[i]) for i, u in enumerate(self.ufs)]

    def _anomaly_shift(self, season: int) -> np.ndarray:
        """Predicted log anomaly vs climatology per (state, week), from the EW25 features; (U, n)."""
        L, done, years, feats = self._anomaly_inputs(season)
        U = L.shape[0]
        n = len(season_dates(season))
        X, R, G = [], [], []
        for j, s in enumerate(done):
            if s not in years:
                continue
            X.append(feats(s, {s, s + 1}))
            R.append(L[:, j, :] - np.delete(L, j, axis=1).mean(1))
            G += [s] * U
        X, R, G = np.vstack(X), np.vstack(R), np.array(G)
        Xn = feats(season, {season})
        return np.stack([_ridge(X, R[:, k], self.lam, G)(Xn) for k in range(n)], axis=1)

    def shifted(self, season: int) -> list[Forecast]:
        """HealthPulse IMDC model: the state's own climatological distribution for each season week (spread widened by
        `spread`, since 6-14 past seasons under-cover), moved by the anomaly predicted from what is known at EW25."""
        clim = self.climatology(season)
        shift = self._anomaly_shift(season)
        _, _, _, done = self._hist(season)
        out = []
        for i, c in enumerate(clim):
            lq = np.log1p(c.q)
            # centre on the mean log (what the anomaly is measured against), not on the median
            _, seasons, m, _ = self._hist(season)
            curves = np.array([m[i, seasons.index(s), :len(c.dates)] for s in done])
            curves = np.where(np.isfinite(curves), curves, curves[:, [51]])
            centre = np.log1p(curves).mean(0)
            lq = centre[:, None] + self.spread * (lq - centre[:, None]) + shift[i][:, None]
            out.append(Forecast(c.uf, season, c.dates, np.maximum(np.expm1(lq), 0)))
        return out

    def blend(self, season: int, w: float = 0.5) -> list[Forecast]:
        a, b = self.scenario(season), self.climatology(season)
        return [Forecast(x.uf, season, x.dates, np.expm1(w * np.log1p(x.q) + (1 - w) * np.log1p(y.q))) for x, y in zip(a, b)]

    def run(self, model: str, season: int) -> list[Forecast]:
        return getattr(self, model)(season)


MODELS = ("climatology", "scenario", "anomaly", "shifted", "blend")
