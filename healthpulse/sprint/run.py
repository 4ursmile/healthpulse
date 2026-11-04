"""`hp sprint`: replay the IMDC 2026 validation tests, score them like the sprint (weekly WIS on the 50/80/90/95 intervals
and the median), and write submission files in the template's CSV format.

Outputs (data/sprint/):
  report.json                       leaderboard per test, per state scores, peak / season-total errors, protocol notes
  submission/<model>/<season>/<UF>.csv   date,pred,lower_50,upper_50,...,upper_95  (one file per state = one upload)

Model choices (lam, spread, features) were made on seasons 2015-2021 only; the four tests were scored once afterwards.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone

import numpy as np
import polars as pl

from . import data as D
from . import models as M
from . import score as S

REPORT_MODELS = ("climatology", "shifted", "scenario", "blend")
CHAMPION = "shifted"
LABELS = {"climatology": "Climatology (reference)", "shifted": "HealthPulse shifted climatology",
          "scenario": "Season-total x past shapes", "blend": "Scenario + climatology blend"}
DEV_SEASONS = range(2015, 2022)


def _uf_codes() -> dict[str, int]:
    p = D.root() / "raw" / "dengue_update_2026.csv.gz"
    u = pl.read_csv(p, columns=["uf", "uf_code"]).unique()
    return {a: int(b) for a, b in u.iter_rows()}


def _write_csv(fc: M.Forecast, path) -> None:
    q = fc.q
    idx = {p: i for i, p in enumerate(D.QUANTILES)}
    df = pl.DataFrame({"date": [d.isoformat() for d in fc.dates], "pred": q[:, idx[0.5]],
                       **{f"{side}_{lvl}": q[:, idx[round(p, 4)]]
                          for lvl, lo in ((50, 0.25), (80, 0.1), (90, 0.05), (95, 0.025))
                          for side, p in (("lower", lo), ("upper", 1 - lo))}})
    path.parent.mkdir(parents=True, exist_ok=True)
    df.select("date", "pred", *[f"{s}_{l}" for l in (50, 80, 90, 95) for s in ("lower", "upper")]).write_csv(path)


def _gm(x) -> float:
    x = [v for v in x if v and v > 0]
    return float(math.exp(np.mean(np.log(x)))) if x else float("nan")


def run(fetch: bool = False, write_submissions: bool = True) -> dict:
    t0 = time.time()
    if fetch:
        D.fetch()
    d = D.uf_weekly(rebuild=fetch)
    f = M.SeasonForecaster(d)
    test_seasons = list(D.TESTS.values())
    fcs = {m: [x for s in test_seasons for x in f.run(m, s)] for m in REPORT_MODELS}
    sc = {m: S.score(v, d) for m, v in fcs.items()}
    summ = S.summary(sc)
    pk = {m: S.peak_errors(v, d) for m, v in fcs.items()}
    # development seasons (where the choices were made) for transparency
    dev = {m: S.score([x for s in DEV_SEASONS for x in f.run(m, s)], d) for m in ("climatology", CHAMPION)}
    dev_summ = S.summary(dev)

    season_test = {v: k for k, v in D.TESTS.items()}
    board = []
    for m in REPORT_MODELS:
        r = summ.filter(pl.col("model") == m).sort("season")
        p = pk[m]
        board.append({
            "model": m, "label": LABELS[m], "champion": m == CHAMPION, "reference": m == "climatology",
            "rel_wis": _gm(r["rel_wis"].to_list()),
            "mean_wis": float(sc[m]["wis"].mean()), "mae": float(sc[m]["ae"].mean()),
            "cov50": float(sc[m]["in50"].mean()), "cov90": float(sc[m]["in90"].mean()), "cov95": float(sc[m]["in95"].mean()),
            "peak_week_abs_median": float(p["peak_week_err"].abs().median()) if len(p) else None,
            "season_total_abs_log_ratio_median": float(p["total_log_ratio"].abs().median()) if len(p) else None,
            "by_test": [{"test": season_test[s], "season": f"{s}-{s + 1}", "n": n, "rel_wis": rw, "mean_wis": mw, "cov90": c9}
                        for s, n, rw, mw, c9 in r.select("season", "n", "rel_wis", "mean_wis", "cov90").iter_rows()],
        })
    board.sort(key=lambda b: b["mean_wis"])
    per_state = (sc[CHAMPION].group_by("uf", "season").agg(pl.col("wis").mean().alias("wis"))
                 .join(sc["climatology"].group_by("uf", "season").agg(pl.col("wis").mean().alias("ref")), on=["uf", "season"])
                 .with_columns((pl.col("wis") / pl.col("ref")).alias("rel_wis")).sort("season", "uf"))
    dev_rel = dev_summ.filter(pl.col("model") == CHAMPION)["rel_wis"].to_list()

    files = []
    if write_submissions:
        codes = _uf_codes()
        for m in ("climatology", CHAMPION):
            for s in [*test_seasons, D.TARGET_SEASON]:
                for fc in (fcs[m] if s in test_seasons else []) or f.run(m, s):
                    if fc.season != s:
                        continue
                    p = D.root() / "submission" / m / f"{s}-{s + 1}" / f"{fc.uf}.csv"
                    _write_csv(fc, p)
                    files.append(str(p.relative_to(D.root())))
        target = f.run(CHAMPION, D.TARGET_SEASON)
        target_summary = sorted(({"uf": x.uf, "adm_1": codes.get(x.uf), "season_total_median": float(x.q[:, 4].sum()),
                                  "peak_week": x.dates[int(np.argmax(x.q[:, 4]))].isoformat(),
                                  "peak_median": float(x.q[:, 4].max())} for x in target), key=lambda r: -r["season_total_median"])
    else:
        target_summary = []

    rep = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "challenge": "Infodengue-Mosqlimate Dengue Challenge (IMDC) 2026, state level (all UFs except ES), probable cases",
        "protocol": {
            "train_until": "EW25 of the season's start year", "forecast_window": "EW41 .. EW40 of the next year",
            "tests": {k: f"{v}-{v + 1}" for k, v in D.TESTS.items()}, "target": f"{D.TARGET_SEASON}-{D.TARGET_SEASON + 1}",
            "metric": "weekly WIS on the median + 50/80/90/95% intervals; rel_wis = geometric mean over states of WIS / "
                      "climatology WIS (each state counts equally); mean_wis = plain mean in cases (large states dominate)",
            "tuning": f"model choices made on seasons {DEV_SEASONS.start}-{DEV_SEASONS.stop - 1} only; tests scored once",
            "test4_partial": "test 4 (2025-2026) is scored through EW25 2026, the last week in the sprint files",
            "data": "official sprint files, ftp://info.dengue.mat.br/data_imdc_2026 (dengue.csv.gz + dengue_update_2026.csv.gz)",
        },
        "leaderboard": board,
        "dev": {"seasons": f"{DEV_SEASONS.start}-{DEV_SEASONS.stop - 1}", "champion_rel_wis": _gm(dev_rel),
                "champion_mean_wis": float(dev[CHAMPION]["wis"].mean()), "climatology_mean_wis": float(dev["climatology"]["wis"].mean())},
        "per_state": per_state.to_dicts(),
        "target_forecast": target_summary,
        "submission_files": len(files),
        "elapsed_s": round(time.time() - t0, 1),
        "caveats": [
            "Not an official ranking: other teams' submissions need a Mosqlimate API key to download, so the comparison is "
            "against our own climatology reference only.",
            "The shifted model improves case-weighted WIS (large states) but not every small state; see rel_wis per test.",
            "The 2026-27 target forecast was made after the sprint deadline (2026-09-10) and was not submitted.",
        ],
    }
    (D.root() / "report.json").write_text(json.dumps(rep, indent=1, default=str))
    return rep


def load_report() -> dict | None:
    p = D.root() / "report.json"
    return json.loads(p.read_text()) if p.exists() else None
