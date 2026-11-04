"""Allowlisted model recipes. Each recipe fits on a labeled frame for one horizon and predicts non-negative counts.
Users choose recipe ids and bounded hyper-parameters; no arbitrary code is accepted."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl

RECIPES: dict[str, dict[str, Any]] = {
    "persistence": {"label": "Persistence (last completed week)", "family": "baseline", "params": {},
                    "description": "Predicts the last reported week's count for every horizon."},
    "seasonal_naive": {"label": "Seasonal naive", "family": "baseline", "params": {},
                       "description": "Same epi-week one year earlier; falls back to persistence when missing."},
    "historical_median": {"label": "Historical median", "family": "baseline", "params": {},
                          "description": "Median label of the same epi-week across training years per municipality."},
    "glm_poisson": {"label": "Poisson GLM", "family": "glm",
                    "params": {"alpha": {"type": "float", "default": 1e-3, "min": 0.0, "max": 10.0}},
                    "description": "Regularised log-link Poisson regression on standardised PIT features."},
    "hgb_poisson": {"label": "Gradient boosting (Poisson)", "family": "gbm",
                    "params": {"max_iter": {"type": "int", "default": 150, "min": 50, "max": 1500},
                               "learning_rate": {"type": "float", "default": 0.08, "min": 0.005, "max": 0.3},
                               "max_leaf_nodes": {"type": "int", "default": 15, "min": 4, "max": 63},
                               "min_samples_leaf": {"type": "int", "default": 20, "min": 5, "max": 200},
                               "l2_regularization": {"type": "float", "default": 1.0, "min": 0.0, "max": 10.0}},
                    "description": "sklearn HistGradientBoostingRegressor(loss='poisson'), one model per horizon."},
    "damped_trend": {"label": "Damped trend", "family": "baseline",
                     "params": {"phi": {"type": "float", "default": 0.5, "min": 0.0, "max": 1.0}},
                     "description": "Last week × exp(φ·h·recent weekly log-growth). A strong naive benchmark during epidemic waves."},
    "ridge_growth": {"label": "Growth regression (ridge)", "family": "glm",
                     "params": {"alpha": {"type": "float", "default": 10.0, "min": 0.0, "max": 1000.0}},
                     "description": "Ridge regression on log growth vs the last observed week; scale-free so it extrapolates to record waves."},
    "hgb_growth": {"label": "Gradient boosting (growth)", "family": "gbm",
                   "params": {"max_iter": {"type": "int", "default": 200, "min": 50, "max": 1500},
                              "learning_rate": {"type": "float", "default": 0.04, "min": 0.005, "max": 0.3},
                              "max_leaf_nodes": {"type": "int", "default": 7, "min": 4, "max": 63},
                              "min_samples_leaf": {"type": "int", "default": 40, "min": 5, "max": 200},
                              "l2_regularization": {"type": "float", "default": 1.0, "min": 0.0, "max": 10.0}},
                   "description": "Gradient boosting (absolute-error loss) on log growth vs the last observed week; scale-free features."},
    "ensemble": {"label": "Ensemble (recommended)", "family": "ensemble", "params": {},
                 "description": "Weighted geometric mean of growth boosting (0.7) and growth ridge (0.3). Weights fixed from "
                                "rolling-origin CV on the dengue pilot; the test window was not used to pick them."},
}
ENSEMBLE_MEMBERS = {"hgb_growth": 0.7, "ridge_growth": 0.3}
GROWTH = ("ridge_growth", "hgb_growth")
BASELINES = ("persistence", "seasonal_naive", "historical_median", "damped_trend")


def clamp_params(recipe: str, params: dict | None) -> dict:
    spec = RECIPES[recipe]["params"]
    out = {}
    for k, s in spec.items():
        v = (params or {}).get(k, s["default"])
        v = int(v) if s["type"] == "int" else float(v)
        out[k] = min(max(v, s["min"]), s["max"])
    return out


@dataclass
class Fitted:
    recipe: str
    horizon: int
    features: list[str]
    params: dict
    model: Any = None
    impute: dict = field(default_factory=dict)
    scale: dict = field(default_factory=dict)
    lookup: dict = field(default_factory=dict)
    members: list = field(default_factory=list)

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        if self.recipe == "persistence":
            return _expm1(df["cases_lag_1w"])
        if self.recipe == "seasonal_naive":
            s = df["cases_same_week_last_year"] if "cases_same_week_last_year" in df.columns else df["cases_lag_1w"]
            return np.where(np.isnan(_expm1(s)), _expm1(df["cases_lag_1w"]), _expm1(s))
        if self.recipe == "historical_median":
            wk = df["target_week"].dt.week().to_numpy()
            mids = df["municipality_id"].to_list()
            fb = _expm1(df["cases_lag_1w"])
            return np.array([self.lookup.get((m, int(w)), f) for m, w, f in zip(mids, wk, fb)], dtype=float)
        if self.recipe == "damped_trend":
            base = df["cases_lag_1w"].cast(pl.Float64).to_numpy()
            g = df["cases_growth_2w"].cast(pl.Float64).to_numpy() / 2 if "cases_growth_2w" in df.columns else np.zeros(len(base))
            g = np.clip(np.nan_to_num(g), -0.7, 0.7)
            return np.clip(np.expm1(base + self.params["phi"] * self.horizon * g), 0, None)
        if self.recipe == "ensemble":
            w = np.array([ENSEMBLE_MEMBERS.get(m.recipe, 0.0) for m in self.members])
            logs = np.vstack([np.log1p(m.predict(df)) for m in self.members])
            return np.clip(np.expm1((w[:, None] * logs).sum(0) / w.sum()), 0, None)
        if self.recipe in GROWTH:
            base = df["cases_lag_1w"].cast(pl.Float64).to_numpy()
            X = self._growth_matrix(df)
            return np.clip(np.expm1(base + self.model.predict(X)), 0, None)
        X = self._matrix(df)
        return np.clip(self.model.predict(X), 0, None)

    def _growth_matrix(self, df: pl.DataFrame) -> np.ndarray:
        """Scale-free design: log-count features become differences from the last observed week (log1p scale)."""
        base = df["cases_lag_1w"].cast(pl.Float64).to_numpy()
        cols = []
        for f in self.features:
            v = df[f].cast(pl.Float64).to_numpy().copy()
            if _is_log_count(f):
                v = v - base
            elif f.startswith("incidence"):
                v = np.log1p(np.clip(v, 0, None))
            cols.append(v)
        X = np.column_stack(cols) if cols else np.zeros((df.height, 0))
        if self.recipe == "ridge_growth":
            for j, f in enumerate(self.features):
                col = X[:, j]
                col[np.isnan(col)] = self.impute.get(f, 0.0)
                X[:, j] = (col - self.scale[f][0]) / self.scale[f][1]
        return X

    def _matrix(self, df: pl.DataFrame) -> np.ndarray:
        X = df.select(self.features).to_numpy().astype(float)
        if self.recipe == "glm_poisson":
            for j, f in enumerate(self.features):
                col = X[:, j]
                col[np.isnan(col)] = self.impute.get(f, 0.0)
                X[:, j] = (col - self.scale[f][0]) / self.scale[f][1]
        return X


def _is_log_count(f: str) -> bool:
    """Case-history features stored on the log1p scale (lags, rolling means, same week last year)."""
    return f.startswith("cases_") and not f.startswith("cases_growth") and f != "cases_lag_1w"


def growth_features(features: list[str]) -> list[str]:
    """Growth recipes drop absolute-size features (population, commuter totals) that only encode which city it is."""
    return [f for f in features if not (f.startswith("log_population") or f.startswith("commuter_"))]


def _expm1(s: pl.Series) -> np.ndarray:
    a = s.cast(pl.Float64).to_numpy()
    return np.clip(np.expm1(a), 0, None)


def fit(recipe: str, train: pl.DataFrame, features: list[str], horizon: int, params: dict | None = None, seed: int = 42) -> Fitted:
    params = clamp_params(recipe, params)
    f = Fitted(recipe, horizon, list(features), params)
    if recipe in ("persistence", "seasonal_naive", "damped_trend"):
        return f
    if recipe == "ensemble":
        f.members = [fit(m, train, features, horizon, None, seed) for m in ENSEMBLE_MEMBERS]
        return f
    if recipe in GROWTH:
        f.features = growth_features(features)
        tr = train.filter(pl.col("cases_lag_1w").is_not_null())
        z = np.log1p(tr["label"].cast(pl.Float64).to_numpy()) - tr["cases_lag_1w"].cast(pl.Float64).to_numpy()
        if recipe == "ridge_growth":
            from sklearn.linear_model import Ridge
            f.scale = {k: (0.0, 1.0) for k in f.features}
            X = f._growth_matrix(tr)  # unscaled pass (identity scale) to learn impute/scale
            for j, name in enumerate(f.features):
                col = X[:, j]
                med = float(np.nanmedian(col)) if np.isfinite(np.nanmedian(col)) else 0.0
                col[np.isnan(col)] = med
                f.impute[name], f.scale[name] = med, (float(col.mean()), float(col.std()) or 1.0)
            X = f._growth_matrix(tr)
            f.model = Ridge(alpha=params["alpha"]).fit(X, z)
            return f
        from sklearn.ensemble import HistGradientBoostingRegressor
        m = HistGradientBoostingRegressor(loss="absolute_error", random_state=seed, max_iter=params["max_iter"],
                                          learning_rate=params["learning_rate"], max_leaf_nodes=params["max_leaf_nodes"],
                                          min_samples_leaf=params["min_samples_leaf"], l2_regularization=params["l2_regularization"],
                                          early_stopping=False)
        f.model = m.fit(f._growth_matrix(tr), z)
        return f
    y = train["label"].cast(pl.Float64).to_numpy()
    if recipe == "historical_median":
        g = (train.with_columns(pl.col("target_week").dt.week().alias("_w"))
             .group_by(["municipality_id", "_w"]).agg(pl.col("label").median().alias("_m")))
        f.lookup = {(r["municipality_id"], int(r["_w"])): float(r["_m"]) for r in g.iter_rows(named=True)}
        return f
    X = train.select(features).to_numpy().astype(float)
    if recipe == "glm_poisson":
        from sklearn.linear_model import PoissonRegressor
        for j, name in enumerate(features):
            col = X[:, j]
            med = float(np.nanmedian(col)) if np.isfinite(np.nanmedian(col)) else 0.0
            col[np.isnan(col)] = med
            sd = float(col.std()) or 1.0
            f.impute[name], f.scale[name] = med, (float(col.mean()), sd)
            X[:, j] = (col - col.mean()) / sd
        m = PoissonRegressor(alpha=params["alpha"], max_iter=300, solver="newton-cholesky")
        m.fit(X, y)
        f.model = m
        return f
    if recipe == "hgb_poisson":
        from sklearn.ensemble import HistGradientBoostingRegressor
        m = HistGradientBoostingRegressor(loss="poisson", random_state=seed, max_iter=params["max_iter"],
                                          learning_rate=params["learning_rate"], max_leaf_nodes=params["max_leaf_nodes"],
                                          min_samples_leaf=params["min_samples_leaf"], l2_regularization=params["l2_regularization"],
                                          early_stopping=False)
        m.fit(X, y)
        f.model = m
        return f
    raise ValueError(f"unknown recipe {recipe}")
