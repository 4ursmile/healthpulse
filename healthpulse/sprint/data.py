"""Infodengue-Mosqlimate Dengue Challenge (IMDC 2026) data: official sprint files -> state (UF) weekly table.

Source: anonymous FTP info.dengue.mat.br/data_imdc_2026 (the files the sprint itself distributes). `casos` are probable
cases by municipality and epidemiological week; the sprint target is their sum per UF. Espirito Santo is excluded by the
sprint rules (reporting problems). The `*_update_2026` file extends the series to EW25 2026, the training cut-off of the
2026-27 forecast target; where weeks overlap the update wins (later vintage).
"""
from __future__ import annotations

import urllib.request
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ..config import get_settings

FTP = "ftp://anonymous:anonymous@info.dengue.mat.br/data_imdc_2026/"
FILES = ("dengue.csv.gz", "dengue_update_2026.csv.gz", "ocean_climate_oscillations.csv.gz",
         "ocean_climate_oscillations_update_2026.csv.gz")
EXCLUDED_UF = ("ES",)
# validation tests of the sprint: (test, season start year). Training data end EW25 of that year,
# the forecast window is EW41 of that year to EW40 of the next.
TESTS = {1: 2022, 2: 2023, 3: 2024, 4: 2025}
TARGET_SEASON = 2026
QUANTILES = (0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.975)


def root() -> Path:
    p = get_settings().data_dir / "sprint"
    p.mkdir(parents=True, exist_ok=True)
    return p


def fetch(force: bool = False) -> list[Path]:
    raw = root() / "raw"
    raw.mkdir(exist_ok=True)
    out = []
    for f in FILES:
        p = raw / f
        if force or not p.exists() or p.stat().st_size == 0:
            tmp = p.with_suffix(".part")
            with urllib.request.urlopen(FTP + f, timeout=600) as r, open(tmp, "wb") as w:
                while chunk := r.read(1 << 20):
                    w.write(chunk)
            tmp.rename(p)
        out.append(p)
    return out


def season_of(year: int, week: int) -> int:
    """Sprint season label = the year it starts in (EW41 of `year` to EW40 of `year + 1`)."""
    return year if week >= 41 else year - 1


def uf_weekly(rebuild: bool = False) -> pl.DataFrame:
    """date, epiweek, uf, cases, season, k (0-based week within season). Cached as parquet."""
    cache = root() / "uf_weekly.parquet"
    if cache.exists() and not rebuild:
        return pl.read_parquet(cache)
    raw = root() / "raw"
    if not (raw / "dengue.csv.gz").exists():
        fetch()
    cols = ["date", "epiweek", "uf", "casos"]
    base = pl.read_csv(raw / "dengue.csv.gz", columns=cols, infer_schema_length=0)
    parts = [base]
    upd = raw / "dengue_update_2026.csv.gz"
    if upd.exists():
        u = pl.read_csv(upd, columns=cols, infer_schema_length=0)
        parts = [base.filter(~pl.col("epiweek").is_in(u["epiweek"].unique().implode())), u]
    d = (pl.concat(parts)
         .with_columns(pl.col("casos").cast(pl.Float64), pl.col("epiweek").cast(pl.Int64), pl.col("date").str.to_date())
         .filter(~pl.col("uf").is_in(EXCLUDED_UF))
         .group_by("date", "epiweek", "uf").agg(pl.col("casos").sum().alias("cases"))
         .with_columns(year=pl.col("epiweek") // 100, week=pl.col("epiweek") % 100))
    d = d.with_columns(season=pl.when(pl.col("week") >= 41).then(pl.col("year")).otherwise(pl.col("year") - 1))
    d = d.sort("uf", "date").with_columns(k=pl.col("date").rank("dense").over("uf", "season").cast(pl.Int64) - 1)
    d = d.select("date", "epiweek", "year", "week", "uf", "cases", "season", "k")
    d.write_parquet(cache)
    return d


def enso_weekly() -> pl.DataFrame:
    raw = root() / "raw"
    parts = [pl.read_csv(raw / f).select("epiweek", "enso", "iod", "pdo") for f in ("ocean_climate_oscillations.csv.gz", "ocean_climate_oscillations_update_2026.csv.gz")
             if (raw / f).exists()]
    o = pl.concat(parts).unique("epiweek", keep="last").sort("epiweek")
    return o


def season_dates(season: int) -> list[date]:
    """Sunday start dates of EW41 `season` .. EW40 `season + 1` (52 or 53 weeks), CDC/MMWR epiweek convention."""
    def ew1(y: int) -> date:
        jan4 = date(y, 1, 4)
        return jan4 - timedelta(days=(jan4.weekday() + 1) % 7)  # Sunday on or before Jan 4

    start = ew1(season) + timedelta(weeks=40)
    end = ew1(season + 1) + timedelta(weeks=39)
    n = (end - start).days // 7 + 1
    return [start + timedelta(weeks=i) for i in range(n)]
