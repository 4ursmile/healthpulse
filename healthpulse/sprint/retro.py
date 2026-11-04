"""Retrospective early-warning case study: the 2023-24 dengue season (Brazil's record), replayed week by week per state.

Event (public, data-defined): the Ministry of Health epidemic level, cumulative season incidence > 300 probable cases per
100k inhabitants (season = EW41 .. EW40, as in the IMDC). `c` = the onset week in which the cumulative incidence first exceeds it.

Who knows what, when (decision week t, reporting delay `delay` weeks; values are the final vintage, so PIT_UNVERIFIED):
  status quo   threshold monitoring on reported data: the crossing is seen at c + delay.
  HealthPulse  at t the data runs to t - delay; a damped log-linear growth projection of the last 3 visible weeks fills the
               unseen weeks and the next `horizon`; alert when the projected cumulative incidence through t + horizon
               exceeds 300/100k.
  June outlook the IMDC season forecast made at EW25 (`models.shifted`): is the season expected above the state's normal?

lead time = (c + delay) - (HealthPulse alert week): weeks gained over the status quo. False alarms are counted on the
same rule replayed over seasons 2015-2022 (an alert in a state-season that never crosses).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import polars as pl

from . import data as D
from . import models as M

THRESHOLD = 300.0  # per 100k, cumulative over the season
SLOPE_CLIP = (-0.5, 0.7)
DAMP = 0.85


def uf_population() -> dict[tuple[str, int], float]:
    p = pl.read_csv(D.root() / "raw" / "datasus_population_2001_2025.csv.gz")
    codes = pl.read_csv(D.root() / "raw" / "dengue_update_2026.csv.gz", columns=["uf", "uf_code"]).unique()
    p = (p.with_columns((pl.col("geocode") // 100000).alias("uf_code")).join(codes, on="uf_code")
         .group_by("uf", "year").agg(pl.col("population").sum()))
    return {(u, y): float(v) for u, y, v in p.iter_rows()}


def _pop(pop, uf: str, season: int) -> float:
    for y in (season + 1, season, 2025, 2024):
        if (uf, y) in pop:
            return pop[(uf, y)]
    raise KeyError(uf)


def project(visible: np.ndarray, steps: int, growth: bool = True) -> np.ndarray:
    """Damped log-linear growth from the last 3 visible weeks -> the next `steps` weekly counts.
    growth=False is the naive comparator: carry the last visible week forward flat."""
    if not growth:
        return np.full(steps, visible[-1] if len(visible) else 0.0)
    if len(visible) < 3:
        return np.full(steps, visible[-1] if len(visible) else 0.0)
    y = np.log1p(visible[-3:])
    g = float(np.clip(np.polyfit(np.arange(3), y, 1)[0], *SLOPE_CLIP))
    out, lv = [], y[-1]
    for h in range(steps):
        lv = lv + g * DAMP ** (h + 1)
        out.append(np.expm1(lv))
    return np.maximum(np.array(out), 0)


def replay_season(cases: np.ndarray, pop: float, delay: int, horizon: int, growth: bool = True) -> dict:
    """cases = weekly counts of one state-season (k = 0..). Returns crossing week c, alert week a (or None)."""
    thr = THRESHOLD * pop / 1e5
    cum = np.cumsum(cases)
    c = int(np.argmax(cum > thr)) if (cum > thr).any() else None
    a = None
    for t in range(delay + 3, len(cases)):
        vis = cases[: t - delay + 1]
        proj = project(vis, delay + horizon, growth)
        if vis.sum() + proj.sum() > thr:
            a = t
            break
    return {"c": c, "a": a}


def run(delay: int = 2, horizon: int = 4) -> dict:
    d = D.uf_weekly()
    pop = uf_population()
    ufs = sorted(d["uf"].unique().to_list())
    rows = []
    for s in range(2015, 2025):
        for u in ufs:
            x = d.filter((pl.col("uf") == u) & (pl.col("season") == s)).sort("date")
            if len(x) < 52:
                continue
            r = replay_season(x["cases"].to_numpy(), _pop(pop, u, s), delay, horizon)
            flat = replay_season(x["cases"].to_numpy(), _pop(pop, u, s), delay, horizon, growth=False)
            dates = x["date"].to_list()
            r["a_flat"] = flat["a"]
            r.update(uf=u, season=s, pop=_pop(pop, u, s),
                     incidence=float(x["cases"].sum() / _pop(pop, u, s) * 1e5),
                     crossing_date=dates[r["c"]].isoformat() if r["c"] is not None else None,
                     seen_by_status_quo=dates[min(r["c"] + delay, len(dates) - 1)].isoformat() if r["c"] is not None else None,
                     alert_date=dates[r["a"]].isoformat() if r["a"] is not None else None,
                     lead_weeks=(r["c"] + delay - r["a"]) if (r["c"] is not None and r["a"] is not None) else None,
                     lead_weeks_flat=(r["c"] + delay - flat["a"]) if (r["c"] is not None and flat["a"] is not None) else None,
                     peak_k=int(np.argmax(x["cases"].to_numpy())),
                     peak_date=dates[int(np.argmax(x["cases"].to_numpy()))].isoformat())
            rows.append(r)
    df = pl.DataFrame(rows)
    focus = df.filter(pl.col("season") == 2023)
    hist = df.filter(pl.col("season") < 2023)
    crossed = focus.filter(pl.col("c").is_not_null())

    def stats(x: pl.DataFrame) -> dict:
        cr = x.filter(pl.col("c").is_not_null())
        never = x.filter(pl.col("c").is_null())
        leads = cr["lead_weeks"].drop_nulls()
        return {"state_seasons": len(x), "epidemic": len(cr), "detected_before_status_quo": int((leads > 0).sum()),
                "missed": int(cr["a"].is_null().sum()),
                "lead_weeks_median": float(leads.median()) if len(leads) else None,
                "lead_weeks_mean": float(leads.mean()) if len(leads) else None,
                "false_alarms": int(never["a"].is_not_null().sum()), "non_epidemic": len(never),
                "false_alarm_rate": float(never["a"].is_not_null().mean()) if len(never) else None,
                "alerts_more_than_8_weeks_early": int((leads > 8).sum()),
                # the operationally useful subset: epidemics that crossed the level before their peak
                "lead_weeks_median_crossed_before_peak": (float(cr.filter(pl.col("c") <= pl.col("peak_k"))["lead_weeks"].median())
                                                          if len(cr.filter(pl.col("c") <= pl.col("peak_k"))) else None),
                "crossed_before_peak": len(cr.filter(pl.col("c") <= pl.col("peak_k"))),
                "flat_comparator": {
                    "lead_weeks_median": float(cr["lead_weeks_flat"].drop_nulls().median()) if len(cr["lead_weeks_flat"].drop_nulls()) else None,
                    "lead_weeks_median_crossed_before_peak": (float(cr.filter(pl.col("c") <= pl.col("peak_k"))["lead_weeks_flat"].median())
                                                              if len(cr.filter(pl.col("c") <= pl.col("peak_k"))) else None),
                    "missed": int(cr["a_flat"].is_null().sum()),
                    "false_alarms": int(never["a_flat"].is_not_null().sum())}}

    # June outlook (IMDC season forecast at EW25 2023): median season total vs the state's climatological median
    f = M.SeasonForecaster(d)
    sh, cl = f.shifted(2023), f.climatology(2023)
    outlook = []
    for a, b in zip(sh, cl):
        obs = focus.filter(pl.col("uf") == a.uf)
        outlook.append({"uf": a.uf, "forecast_total": float(a.q[:, 4].sum()), "climatology_total": float(b.q[:, 4].sum()),
                        "observed_total": float(obs["incidence"][0] * obs["pop"][0] / 1e5) if len(obs) else None})
    up = [o for o in outlook if o["forecast_total"] > o["climatology_total"]]
    obs_up = [o for o in outlook if o["observed_total"] and o["observed_total"] > o["climatology_total"]]
    both = [o for o in up if o in obs_up]

    rep = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question": "In the record 2023-24 dengue season, how many weeks before threshold monitoring would HealthPulse "
                    "have warned each state that it was heading past the epidemic level?",
        "definitions": {"event": f"cumulative season incidence > {THRESHOLD:.0f} probable cases / 100k (MoH epidemic level)",
                        "delay_weeks": delay, "horizon_weeks": horizon,
                        "status_quo": "the crossing is seen when its week's data arrives (crossing week + delay)",
                        "healthpulse": "damped log-linear growth of the last 3 visible weeks, projected over the unseen "
                                       "weeks + horizon; alert when projected cumulative incidence > threshold",
                        "lead_weeks": "status-quo detection week - HealthPulse alert week",
                        "flat_comparator": "same rule, but the last visible week is carried forward flat (no growth model)"},
        "season_2023_24": stats(focus),
        "history_2015_2022": stats(hist),
        "states_2023_24": focus.sort("crossing_date", nulls_last=True).select(
            "uf", "incidence", "crossing_date", "seen_by_status_quo", "alert_date", "lead_weeks", "lead_weeks_flat", "peak_date").to_dicts(),
        "june_outlook": {"made_at": "EW25 2023 (June)", "states_forecast_above_normal": len(up),
                         "states_observed_above_normal": len(obs_up), "both": len(both),
                         "national_forecast_total": sum(o["forecast_total"] for o in outlook),
                         "national_climatology_total": sum(o["climatology_total"] for o in outlook),
                         "national_observed_total": sum(o["observed_total"] or 0 for o in outlook),
                         "states": outlook},
        "caveats": [
            "Final-vintage counts replayed with a fixed reporting delay; real-time counts are lower and revised (PIT_UNVERIFIED).",
            "The event is the incidence threshold, not an official emergency decree; decree dates were not used.",
            "The alert rule was not tuned on 2023-24; the same rule and settings are replayed over 2015-2022 for false alarms.",
            "A projection crossing the threshold is a warning for planners, not a forecast of hospital load.",
        ],
    }
    (D.root() / "retro_2023_24.json").write_text(json.dumps(rep, indent=1, default=str))
    return rep
