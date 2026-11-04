"""Data-quality gates per Silver table. Hard failures quarantine the release; soft gates produce warnings."""
from __future__ import annotations

import polars as pl

from . import silver


def _gate(name: str, ok: bool, severity: str, detail: str, value=None) -> dict:
    return {"gate": name, "passed": bool(ok), "severity": severity, "detail": detail, "value": value}


def check(table: str, df: pl.DataFrame, *, known_munis: set[str] | None = None, prev: pl.DataFrame | None = None) -> dict:
    gates: list[dict] = []
    keys = silver.TABLES[table]["keys"]
    n = df.height
    gates.append(_gate("non_empty", n > 0, "hard", f"{n:,} rows", n))
    if n:
        dup = n - df.select(keys).unique().height
        gates.append(_gate("unique_natural_key", dup == 0, "hard", f"{dup} duplicate {'+'.join(keys)} rows", dup))
    if "municipality_id" in df.columns and n:
        bad_fmt = df.filter(~pl.col("municipality_id").fill_null("").str.contains(r"^\d{7}$")).height
        if table == "facility_beds":
            # registered beds without an IBGE code cannot be joined to a municipality (no name-based crosswalk)
            share = bad_fmt / n
            gates.append(_gate("ibge_code_present", share == 0, "hard",
                               f"{bad_fmt:,} of {n:,} hospital rows carry no IBGE municipality code ({share:.0%}); "
                               "name-based crosswalk refused", bad_fmt))
        else:
            gates.append(_gate("municipality_id_format", bad_fmt == 0, "hard", f"{bad_fmt} rows without a 7-digit IBGE code", bad_fmt))
            if known_munis is not None and table not in ("municipality_dim",):
                unknown = df.filter(pl.col("municipality_id").is_not_null() & ~pl.col("municipality_id").is_in(list(known_munis))).height
                gates.append(_gate("geo_crosswalk", unknown == 0, "hard", f"{unknown} rows with codes absent from the IBGE register", unknown))
    for col in ("observed_cases", "estimated_cases", "population", "registered_beds", "formal_workers"):
        if col in df.columns and n:
            neg = df.filter(pl.col(col) < 0).height
            gates.append(_gate(f"nonnegative_{col}", neg == 0, "hard", f"{neg} negative values", neg))
            miss = df[col].null_count() / n
            gates.append(_gate(f"missingness_{col}", miss <= 0.2, "soft", f"{miss:.1%} missing", round(miss, 4)))
    if "week_start" in df.columns and n:
        not_sunday = df.filter(pl.col("week_start").dt.weekday() != 7).height
        gates.append(_gate("epi_week_sunday_start", not_sunday == 0, "hard", f"{not_sunday} weeks not starting on Sunday", not_sunday))
    if table == "weather_weekly" and n:
        low = df.filter(pl.col("coverage") < 0.85).height
        gates.append(_gate("weekly_coverage", low / n <= 0.05, "soft", f"{low} weeks below coverage threshold (set to null, not zero)", low))
    if table == "disease_weekly" and n:
        sig = df["signal_type"].unique().to_list()
        gates.append(_gate("single_signal_type", len(sig) == 1, "hard", f"signal types {sig}", sig))
        # large jumps are reviewable, never automatically invalid
        j = (df.sort(["municipality_id", "disease_id", "week_start"])
             .with_columns(pl.col("observed_cases").shift(1).over(["municipality_id", "disease_id"]).alias("_p"))
             .filter((pl.col("_p") >= 20) & (pl.col("observed_cases") > pl.col("_p") * 5))).height
        gates.append(_gate("anomaly_jumps", j == 0, "soft", f"{j} week-on-week jumps above 5×", j))
    if table == "mobility_polygon" and n:
        bands = ["share_0km", "share_0_10km", "share_10_100km", "share_100plus_km"]
        tot = df.select(pl.sum_horizontal(bands).alias("t"), pl.any_horizontal(*[pl.col(b).is_null() for b in bands]).alias("m"))
        bad = tot.filter(pl.col("m").not_() & ((pl.col("t") - 1).abs() > 0.02)).height
        gates.append(_gate("band_fractions_sum_to_one", bad / n <= 0.01, "hard", f"{bad} polygon-days whose four bands do not sum to 1 (±0.02)", bad))
        rng = df.filter((pl.col("share_0km") < 0) | (pl.col("share_100plus_km") > 1)).height
        gates.append(_gate("fractions_in_range", rng == 0, "hard", f"{rng} out-of-range fractions", rng))
        miss = float(tot["m"].mean())
        # Meta's privacy noise leaves a few percent of polygon-days unusable (nulled upstream of Silver); far more means the format changed
        gates.append(_gate("unusable_polygon_days_soft", miss <= 0.10, "soft", f"{miss:.1%} polygon-days have null bands (noise-dominated or absent; never 0)", round(miss, 4)))
        gates.append(_gate("unusable_polygon_days_hard", miss <= 0.25, "hard", f"{miss:.1%} polygon-days have null bands (limit 25%)", round(miss, 4)))
    if table == "sari_weekly" and n:
        unk = df.filter(pl.col("municipality_id").is_null()).height
        gates.append(_gate("residence_code_mapped", unk / n <= 0.02, "soft", f"{unk} cells with an unmapped residence code", unk))
    if table == "geometry" and known_munis is not None and n:
        cover = len(set(df["municipality_id"].to_list()) & known_munis) / max(1, len(known_munis))
        gates.append(_gate("register_coverage", cover >= 0.99, "hard", f"{cover:.2%} of IBGE municipalities have a polygon", round(cover, 4)))
    if prev is not None and prev.height and n and table in ("disease_weekly", "weather_weekly"):
        gates.append(_gate("schema_drift", set(prev.columns) <= set(df.columns) | {"vintage_id", "_synthetic_snapshot"}, "soft",
                           "columns compared with the previous vintage"))
    hard_fail = [g for g in gates if g["severity"] == "hard" and not g["passed"]]
    soft_fail = [g for g in gates if g["severity"] == "soft" and not g["passed"]]
    status = "quarantined" if hard_fail else ("passed_with_warnings" if soft_fail else "passed")
    return {"table": table, "status": status, "rows": n, "gates": gates,
            "hard_failures": len(hard_fail), "warnings": len(soft_fail)}
