"""SYNTHETIC fixture snapshots for offline mode and tests. Published through the same Silver path, flagged
`is_synthetic=1` and source `synthetic_fixture`, so every read model labels them SYNTHETIC. Never a data product."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import polars as pl

from ..core import db, lake
from ..core.util import dumps, iso, new_id, stamp, utcnow
from . import quality, silver

FIXTURE_MUNIS = [("3304557", "Rio de Janeiro", 33, "RJ", -22.9129, -43.2003, 6_625_849),
                 ("3550308", "São Paulo", 35, "SP", -23.5329, -46.6395, 12_325_232),
                 ("3106200", "Belo Horizonte", 31, "MG", -19.9102, -43.9266, 2_521_564)]


def publish(table: str, df: pl.DataFrame, available_at: datetime, source_id: str = "synthetic_fixture") -> str:
    df = silver.conform(table, df)
    sid = f"{source_id}__{table}__{stamp(available_at)}_{new_id('v')[-6:]}"
    uri, digest = lake.silver_publish(table, source_id, sid, df)
    rep = quality.check(table, df)
    db.insert("source_snapshot", {
        "snapshot_id": sid, "source_id": source_id, "job_id": None, "bronze_uri": None, "silver_uri": uri, "checksum": digest,
        "retrieved_at": iso(available_at), "data_available_at": iso(available_at), "coverage_start": None, "coverage_end": None,
        "schema_version": "1.0", "dq_status": rep["status"], "dq_report_json": dumps(rep), "row_count": df.height, "manifest_uri": None,
        "silver_table": table, "is_synthetic": 1, "published": 1, "created_at": iso(utcnow())})
    silver.clear_cache()
    return sid


def install(weeks: int = 260, seed: int = 11, end: date | None = None, available_at: datetime | None = None) -> dict:
    """Seasonal dengue-like counts for three municipalities, weather, holidays and population."""
    rng = np.random.default_rng(seed)
    end = end or (utcnow().date() - timedelta(days=(utcnow().date().weekday() + 1) % 7) - timedelta(weeks=2))
    start = end - timedelta(weeks=weeks - 1)
    avail = available_at or utcnow()
    wk = pl.date_range(start, end, interval="1w", eager=True)
    rows, wrows = [], []
    for i, (mid, name, uf, ab, lat, lon, pop) in enumerate(FIXTURE_MUNIS):
        t = np.arange(len(wk))
        season = np.exp(1.2 * np.sin(2 * np.pi * (t / 52.18) - 1.0 + i * 0.3))
        base = pop / 1e5 * (2 + 3 * season)
        rain = np.clip(rng.gamma(2, 15 + 25 * (season / season.max()), len(wk)), 0, None)
        lam = base * np.exp(0.004 * np.roll(rain, 6))
        y = rng.poisson(lam)
        for w, c, r in zip(wk, y, rain):
            rows.append({"municipality_id": mid, "week_start": w, "disease_id": "dengue", "observed_cases": int(c),
                         "estimated_cases": float(c) * 1.05, "population": pop, "signal_type": "notification"})
            wrows.append({"municipality_id": mid, "week_start": w, "rain_mm_sum": float(r), "t2m_mean": 24 + 3 * np.sin(2 * np.pi * len(wrows) / 52.18),
                          "t2m_max": 30.0, "rh_mean": 75.0, "days_observed": 7, "coverage": 1.0, "source_kind": "observed_reanalysis"})
    dw = pl.DataFrame(rows).with_columns(
        pl.col("week_start").dt.strftime("%Y%U").cast(pl.Int32).alias("epi_week"),
        pl.lit(avail).cast(silver.DT).alias("available_at"),
        (pl.col("week_start").cast(silver.DT) + pl.duration(days=10)).alias("release_approx_at"),
        pl.lit("synthetic_fixture").alias("source_id"))
    ww = pl.DataFrame(wrows).with_columns(pl.lit(avail).cast(silver.DT).alias("available_at"),
                                          (pl.col("week_start").cast(silver.DT) + pl.duration(days=13)).alias("release_approx_at"),
                                          pl.lit("synthetic_fixture").alias("source_id"))
    dim = pl.DataFrame([{"municipality_id": m, "name": n, "state_code": uf, "state_abbr": ab, "centroid_lat": la, "centroid_lon": lo,
                         "is_capital": True, "timezone": "America/Sao_Paulo"} for m, n, uf, ab, la, lo, _ in FIXTURE_MUNIS])
    years = sorted({w.year for w in wk})
    pop = pl.DataFrame([{"municipality_id": m, "reference_year": y, "population": p, "source_note": "fixture"} for m, *_, p in FIXTURE_MUNIS for y in years]).with_columns(
        pl.lit(avail).cast(silver.DT).alias("available_at"), pl.lit("synthetic_fixture").alias("source_id"))
    hol = []
    for y in range(years[0], years[-1] + 2):
        for md, nm in (("01-01", "New Year's Day"), ("04-21", "Tiradentes"), ("05-01", "Labour Day"), ("09-07", "Independence Day"),
                       ("10-12", "Our Lady of Aparecida"), ("11-02", "All Souls' Day"), ("11-15", "Republic Day"), ("12-25", "Christmas Day")):
            hol.append({"date": date.fromisoformat(f"{y}-{md}"), "country_code": "BR", "subdivision": None, "holiday_type": "Public",
                        "holiday_name": nm, "local_name": nm, "is_national": True})
    hdf = pl.DataFrame(hol).with_columns((pl.col("date").dt.truncate("1y").cast(silver.DT) - pl.duration(days=365)).alias("known_at"),
                                         pl.lit(avail).cast(silver.DT).alias("available_at"), pl.lit("synthetic_fixture").alias("source_id"))
    ids = {"municipality_dim": publish("municipality_dim", dim, avail), "disease_weekly": publish("disease_weekly", dw, avail),
           "weather_weekly": publish("weather_weekly", ww, avail), "population_annual": publish("population_annual", pop, avail),
           "holiday_daily": publish("holiday_daily", hdf, avail)}
    return {"snapshots": ids, "start": start, "end": end, "rows": dw.height}


def publish_revision(week: date, municipality_id: str, new_count: int, available_at: datetime) -> str:
    """A later corrected release of one week (to test that it never leaks into earlier issue times)."""
    df = pl.DataFrame([{"municipality_id": municipality_id, "week_start": week, "disease_id": "dengue", "observed_cases": new_count,
                        "signal_type": "notification", "epi_week": 0}]).with_columns(
        pl.lit(available_at).cast(silver.DT).alias("available_at"),
        (pl.col("week_start").cast(silver.DT) + pl.duration(days=10)).alias("release_approx_at"),
        pl.lit("synthetic_fixture").alias("source_id"))
    return publish("disease_weekly", df, available_at)
