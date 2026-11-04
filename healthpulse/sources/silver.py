"""Silver canonical tables: enforced schemas, natural keys and vintage-aware readers.

`vintaged` tables keep every source release (needed for point-in-time replay); readers pick the latest vintage per key
or the latest vintage released by a given instant. `replace` tables (dimensions) are served from their latest snapshot.
"""
from __future__ import annotations

import threading
from datetime import datetime

import polars as pl

from ..core import db, lake

DT = pl.Datetime("us", "UTC")

SCHEMAS: dict[str, dict[str, pl.DataType]] = {
    "municipality_dim": {"municipality_id": pl.Utf8, "name": pl.Utf8, "state_code": pl.Int32, "state_abbr": pl.Utf8,
                         "centroid_lat": pl.Float64, "centroid_lon": pl.Float64, "is_capital": pl.Boolean, "timezone": pl.Utf8},
    "geometry": {"municipality_id": pl.Utf8, "name_in_source": pl.Utf8, "vertices": pl.Int64, "min_lon": pl.Float64,
                 "min_lat": pl.Float64, "max_lon": pl.Float64, "max_lat": pl.Float64},
    "disease_weekly": {"municipality_id": pl.Utf8, "week_start": pl.Date, "epi_week": pl.Int32, "disease_id": pl.Utf8,
                       "observed_cases": pl.Int64, "estimated_cases": pl.Float64, "estimated_min": pl.Float64,
                       "estimated_max": pl.Float64, "population": pl.Int64, "infodengue_rt": pl.Float64,
                       "infodengue_alert_level": pl.Int64, "row_model_version": pl.Utf8, "signal_type": pl.Utf8,
                       "available_at": DT, "release_approx_at": DT, "source_id": pl.Utf8},
    "population_annual": {"municipality_id": pl.Utf8, "reference_year": pl.Int32, "population": pl.Int64,
                          "source_note": pl.Utf8, "available_at": DT, "source_id": pl.Utf8},
    "weather_weekly": {"municipality_id": pl.Utf8, "week_start": pl.Date, "rain_mm_sum": pl.Float64, "t2m_mean": pl.Float64,
                       "t2m_max": pl.Float64, "rh_mean": pl.Float64, "days_observed": pl.UInt32, "coverage": pl.Float64,
                       "source_kind": pl.Utf8, "available_at": DT, "release_approx_at": DT, "source_id": pl.Utf8},
    "holiday_daily": {"date": pl.Date, "country_code": pl.Utf8, "subdivision": pl.Utf8, "holiday_type": pl.Utf8,
                      "holiday_name": pl.Utf8, "local_name": pl.Utf8, "is_national": pl.Boolean, "known_at": DT,
                      "available_at": DT, "source_id": pl.Utf8},
    "facility": {"facility_id": pl.Utf8, "municipality_id": pl.Utf8, "municipality_code6": pl.Utf8, "name": pl.Utf8,
                 "facility_type_code": pl.Int64, "facility_type": pl.Utf8, "lat": pl.Float64, "lon": pl.Float64,
                 "has_inpatient": pl.Boolean, "sus_ambulatory": pl.Boolean, "admin_sphere": pl.Utf8,
                 "updated_in_source": pl.Utf8, "disabled_reason_code": pl.Utf8, "snapshot_month": pl.Utf8,
                 "available_at": DT, "source_id": pl.Utf8},
    "facility_beds": {"facility_id": pl.Utf8, "competence": pl.Utf8, "hospital_name": pl.Utf8, "state_abbr": pl.Utf8,
                      "municipality_id": pl.Utf8, "registered_beds": pl.Float64, "registered_beds_sus": pl.Float64,
                      "registered_icu_beds": pl.Float64, "registered_icu_beds_sus": pl.Float64,
                      "available_at": DT, "source_id": pl.Utf8},
    "sari_weekly": {"week_start": pl.Date, "disease_id": pl.Utf8, "observed_cases": pl.Int64, "municipality_id": pl.Utf8,
                    "signal_type": pl.Utf8, "available_at": DT, "release_approx_at": DT, "source_id": pl.Utf8,
                    "is_synthetic": pl.Boolean},
    "workforce_sector": {"municipality_id": pl.Utf8, "year": pl.Int64, "cnae_section": pl.Utf8, "cnae_label": pl.Utf8,
                         "formal_workers": pl.Int64, "available_at": DT, "source_id": pl.Utf8},
    "commuting_arranjo": {"municipality_id": pl.Utf8, "arranjo_id": pl.Int64, "arranjo_name": pl.Utf8, "is_core": pl.Boolean,
                          "population_2010": pl.Int64, "commuters_in_arranjo": pl.Int64, "integration_index": pl.Float64,
                          "reference_year": pl.Int32, "available_at": DT, "source_id": pl.Utf8},
    "population_grid_annual": {"municipality_id": pl.Utf8, "reference_year": pl.Int32, "worldpop_population": pl.Float64,
                               "populated_cells": pl.Int64, "populated_area_km2_approx": pl.Float64, "available_at": DT,
                               "source_id": pl.Utf8},
    "environment_weekly": {"municipality_id": pl.Utf8, "week_start": pl.Date, "ndvi_mean": pl.Float64, "evi_mean": pl.Float64,
                           "lst_day_c": pl.Float64, "lst_night_c": pl.Float64, "nino34": pl.Float64, "soi": pl.Float64,
                           "available_at": DT, "release_approx_at": DT, "source_id": pl.Utf8},
    "gadm_unit": {"gadm_version": pl.Utf8, "gadm_id": pl.Utf8, "country": pl.Utf8, "level": pl.Int32, "name_in_source": pl.Utf8, "parent_gid": pl.Utf8,
                  "code_in_source": pl.Utf8, "vertices": pl.Int64, "min_lon": pl.Float64, "min_lat": pl.Float64,
                  "max_lon": pl.Float64, "max_lat": pl.Float64, "available_at": DT, "source_id": pl.Utf8},
    "mobility_polygon": {"gadm_id": pl.Utf8, "country": pl.Utf8, "polygon_level": pl.Int32, "ds": pl.Date, "share_0km": pl.Float64,
                         "share_0_10km": pl.Float64, "share_10_100km": pl.Float64, "share_100plus_km": pl.Float64,
                         "available_at": DT, "source_id": pl.Utf8},
    "municipality_profile": {"municipality_id": pl.Utf8, "area_km2": pl.Float64, "population_density": pl.Float64,
                             "water_supply_pct": pl.Float64, "sewage_pct": pl.Float64, "garbage_collection_pct": pl.Float64,
                             "pct_builtup": pl.Float64, "pct_tree_cover": pl.Float64, "elev_mean": pl.Float64,
                             "dominant_landcover": pl.Utf8, "available_at": DT, "source_id": pl.Utf8},
}

TABLES: dict[str, dict] = {
    "municipality_dim": {"keys": ["municipality_id"], "mode": "replace", "grain": "municipality"},
    "geometry": {"keys": ["municipality_id"], "mode": "replace", "grain": "municipality polygon"},
    "disease_weekly": {"keys": ["municipality_id", "week_start", "disease_id"], "mode": "vintaged", "grain": "municipality-week"},
    "population_annual": {"keys": ["municipality_id", "reference_year"], "mode": "vintaged", "grain": "municipality-year"},
    "weather_weekly": {"keys": ["municipality_id", "week_start"], "mode": "vintaged", "grain": "municipality-week"},
    "holiday_daily": {"keys": ["date", "subdivision", "holiday_name"], "mode": "replace", "grain": "country-date"},
    "facility": {"keys": ["facility_id"], "mode": "replace", "grain": "establishment"},
    "facility_beds": {"keys": ["facility_id"], "mode": "replace", "grain": "hospital (CNES code)"},
    "sari_weekly": {"keys": ["municipality_id", "week_start", "disease_id"], "mode": "vintaged", "grain": "municipality-week"},
    "workforce_sector": {"keys": ["municipality_id", "year", "cnae_section"], "mode": "vintaged", "grain": "municipality-year-sector"},
    "commuting_arranjo": {"keys": ["municipality_id"], "mode": "replace", "grain": "municipality (census 2010 arranjo)"},
    "population_grid_annual": {"keys": ["municipality_id", "reference_year"], "mode": "vintaged", "grain": "municipality-year (raster sum)"},
    "environment_weekly": {"keys": ["municipality_id", "week_start"], "mode": "vintaged", "grain": "municipality-week"},
    "municipality_profile": {"keys": ["municipality_id"], "mode": "replace", "grain": "municipality"},
    "gadm_unit": {"keys": ["gadm_version", "gadm_id"], "mode": "replace", "grain": "GADM polygon"},
    # vintaged so every ingest ADDS days: HDX keeps ~90 days, the history we hold is the history we collected.
    # NOT a point-in-time training feature: no history before our first ingest (see docs/LIMITATIONS.md)
    "mobility_polygon": {"keys": ["gadm_id", "ds"], "mode": "vintaged", "grain": "GADM polygon-day (distance-from-home bands)"},
}


def conform(table: str, df: pl.DataFrame) -> pl.DataFrame:
    """Select and cast to the canonical schema; missing columns become typed nulls (never zeros)."""
    schema = SCHEMAS[table]
    cols = []
    for name, dtype in schema.items():
        if name in df.columns:
            cols.append(pl.col(name).cast(dtype, strict=False))
        else:
            cols.append(pl.lit(None, dtype=dtype).alias(name))
    return df.select(cols) if df.height or df.width else pl.DataFrame(schema=schema)


# ---------------------------------------------------------------- readers with a version-keyed cache
_cache: dict[tuple, pl.DataFrame] = {}
_lock = threading.Lock()


def data_version() -> str:
    r = db.q1("SELECT COUNT(*) n, MAX(created_at) t FROM source_snapshot WHERE published=1")
    return f"{r['n']}:{r['t']}"


def snapshots(table: str, published_only: bool = True) -> list[dict]:
    w = "AND published=1" if published_only else ""
    return db.q(f"SELECT * FROM source_snapshot WHERE silver_table=? {w} ORDER BY retrieved_at, created_at", table)


def _scan(table: str, snaps: list[dict]) -> pl.DataFrame:
    frames = []
    for s in snaps:
        if not s["silver_uri"]:
            continue
        p = lake.data_path(s["silver_uri"])
        if p.exists():
            frames.append(pl.scan_parquet(p).with_columns(pl.lit(s["snapshot_id"]).alias("vintage_id"),
                                                          pl.lit(bool(s["is_synthetic"])).alias("_synthetic_snapshot")))
    if not frames:
        return pl.DataFrame(schema={**SCHEMAS[table], "vintage_id": pl.Utf8, "_synthetic_snapshot": pl.Boolean})
    return pl.concat(frames, how="diagonal_relaxed").collect()


def all_vintages(table: str) -> pl.DataFrame:
    key = ("all", table, data_version())
    with _lock:
        if key in _cache:
            return _cache[key]
    df = _scan(table, snapshots(table))
    with _lock:
        _cache[key] = df
    return df


def latest(table: str, include_synthetic: bool | None = None) -> pl.DataFrame:
    """Current view: replace tables from the newest snapshot; vintaged tables keep the newest vintage per key.
    Synthetic fixture snapshots are only mixed in when no real snapshot exists, and are flagged."""
    key = ("latest", table, data_version(), include_synthetic)
    with _lock:
        if key in _cache:
            return _cache[key]
    snaps = snapshots(table)
    real = [s for s in snaps if not s["is_synthetic"]]
    use = snaps if include_synthetic else (real or (snaps if include_synthetic is None else []))
    meta = TABLES[table]
    if meta["mode"] == "replace":
        use = use[-1:]
    df = _scan(table, use)
    if meta["mode"] == "vintaged" and df.height:
        order = {s["snapshot_id"]: i for i, s in enumerate(use)}
        df = (df.with_columns(pl.col("vintage_id").replace_strict(order, default=-1, return_dtype=pl.Int64).alias("_vo"))
              .sort("_vo").unique(meta["keys"], keep="last").drop("_vo"))
    with _lock:
        _cache[key] = df
        if len(_cache) > 64:
            for k in list(_cache)[:16]:
                _cache.pop(k, None)
    return df


def as_of(table: str, issue_at: datetime, policy: str = "vintage") -> pl.DataFrame:
    """Rows as they were knowable at `issue_at`.
    policy='vintage': only source releases with available_at <= issue_at (release-accurate, PIT_VERIFIED).
    policy='publication_lag': for weeks with no historical vintage, assume first release at release_approx_at, using the
    earliest stored vintage of that week (closest to the first release). Marked PIT_UNVERIFIED by callers."""
    df = all_vintages(table)
    if not df.height:
        return df
    keys = TABLES[table]["keys"]
    ts = pl.lit(issue_at).cast(DT)
    if policy == "vintage":
        df = df.filter(pl.col("available_at") <= ts).sort("available_at").unique(keys, keep="last")
        return df.with_columns(pl.col("available_at").alias("effective_available_at"))
    strict = df.filter(pl.col("available_at") <= ts).sort("available_at").unique(keys, keep="last")
    approx = (df.filter((pl.col("available_at") > ts) & (pl.col("release_approx_at") <= ts))
              .sort("available_at").unique(keys, keep="first"))
    approx = approx.join(strict.select(keys), on=keys, how="anti")
    return pl.concat([strict.with_columns(pl.col("available_at").alias("effective_available_at")),
                      approx.with_columns(pl.col("release_approx_at").alias("effective_available_at"))], how="diagonal_relaxed")


def clear_cache() -> None:
    with _lock:
        _cache.clear()
