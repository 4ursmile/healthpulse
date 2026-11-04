"""Meta Movement Distribution: file schema and the long -> wide transformation (pure, no I/O)."""
from __future__ import annotations

import polars as pl

META_COLUMNS = ["gadm_id", "gadm_name", "country", "polygon_level", "home_to_ping_distance_category",
                "distance_category_ping_fraction", "ds"]
# Meta's "home_to_ping_distance_category": distance between the device's home location and where the ping was seen
BANDS = {"0": "share_0km", "(0, 10)": "share_0_10km", "[10, 100)": "share_10_100km", "100+": "share_100plus_km"}


def pivot_bands(df: pl.DataFrame) -> pl.DataFrame:
    """Long Meta rows (one per polygon-day-band, all text) -> one row per polygon-day with the four band fractions.
    A band that is absent stays null (never 0); an unknown band label stops the ingest (schema drift)."""
    unknown = set(df["home_to_ping_distance_category"].unique().to_list()) - set(BANDS)
    if unknown:
        raise RuntimeError(f"unknown distance categories {sorted(unknown)} (schema drift)")
    long = df.select("gadm_id", "country", pl.col("polygon_level").cast(pl.Int32), pl.col("ds").str.to_date(),
                     pl.col("home_to_ping_distance_category").replace_strict(BANDS).alias("band"),
                     pl.col("distance_category_ping_fraction").cast(pl.Float64, strict=False).alias("f"))
    return long.group_by("gadm_id", "country", "polygon_level", "ds").agg(
        [pl.col("f").filter(pl.col("band") == b).mean().alias(b) for b in BANDS.values()])


SUM_TOL = 0.02


def sanitize(wide: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    """Meta adds noise to the published fractions (privacy protection), most visibly in small polygons: some polygon-days have
    negative fractions or bands that do not sum to 1. Those polygon-days are noise-dominated, so ALL their bands are set to null.
    Nothing is clipped or renormalised (that would manufacture values), and missing is null, never 0."""
    b = list(BANDS.values())
    nn = pl.all_horizontal(*[pl.col(c).is_not_null() for c in b])
    bad = nn & (pl.any_horizontal(*[(pl.col(c) < 0) | (pl.col(c) > 1) for c in b]) | ((pl.sum_horizontal(b) - 1).abs() > SUM_TOL))
    n_bad = wide.select(bad.sum()).item()
    out = wide.with_columns([pl.when(bad).then(None).otherwise(pl.col(c)).alias(c) for c in b])
    stats = {"polygon_days": wide.height, "noise_nulled": int(n_bad), "noise_nulled_share": n_bad / wide.height if wide.height else 0.0,
             "polygons_with_noise_nulled": int(wide.filter(bad)["gadm_id"].n_unique()), "rule": f"any band outside [0,1] or |sum-1| > {SUM_TOL}"}
    return out, stats
