"""Point-in-time join library (spec §19.3). Every Gold feature row is built through these functions.

Invariants enforced here and asserted by tests:
  * a value is used only if its availability instant <= the row's forecast issue instant;
  * among eligible vintages the most recently released one wins (a later correction never leaks backwards);
  * joins are many-to-one on the declared keys — fan-out raises CardinalityError instead of multiplying counts.
"""
from __future__ import annotations

from datetime import datetime

import polars as pl

DT = pl.Datetime("us", "UTC")


class CardinalityError(ValueError):
    pass


class LeakageError(ValueError):
    pass


def assert_unique(df: pl.DataFrame, keys: list[str], what: str) -> None:
    if df.height == 0:
        return
    dup = df.height - df.select(keys).unique().height
    if dup:
        raise CardinalityError(f"{what}: {dup} duplicate rows on {keys}; aggregate before joining (join would fan out)")


def safe_join(left: pl.DataFrame, right: pl.DataFrame, on: list[str], how: str = "left", what: str = "join") -> pl.DataFrame:
    """Many-to-one join that refuses to duplicate left rows."""
    assert_unique(right, on, what)
    out = left.join(right, on=on, how=how)
    if how == "left" and out.height != left.height:
        raise CardinalityError(f"{what}: row count changed {left.height} -> {out.height}")
    return out


def candidates(vintaged: pl.DataFrame, keys: list[str], event_col: str, value_cols: list[str], policy: str) -> pl.DataFrame:
    """All releases of each (keys, event) with the instant each became knowable.
    policy 'vintage': only actual ingestion instants (release-accurate).
    policy 'publication_lag': additionally assume the earliest stored vintage was first released at release_approx_at
    (documented publication lag) — rows produced this way are flagged `approx=True` and the dataset is PIT_UNVERIFIED."""
    base = vintaged.select(keys + [event_col] + value_cols + ["available_at", "release_approx_at"])
    strict = base.select(keys + [event_col] + value_cols + [pl.col("available_at").alias("known_at"), pl.lit(False).alias("approx")])
    if policy == "vintage" or "release_approx_at" not in vintaged.columns:
        return strict
    first = (base.sort("available_at").unique(keys + [event_col], keep="first")
             .filter(pl.col("release_approx_at") < pl.col("available_at"))
             .select(keys + [event_col] + value_cols + [pl.col("release_approx_at").alias("known_at"), pl.lit(True).alias("approx")]))
    return pl.concat([strict, first], how="vertical_relaxed")


def lookup(spine: pl.DataFrame, cands: pl.DataFrame, *, keys: list[str], spine_event: str, event_col: str,
           value_cols: list[str], issue_col: str = "issue_at") -> pl.DataFrame:
    """For each spine row pick the latest release of (keys, spine_event) with known_at <= issue. Unmatched -> nulls."""
    sp = spine.select(keys + [spine_event, issue_col]).unique()
    j = (sp.join(cands.rename({event_col: spine_event}), on=keys + [spine_event], how="inner")
         .filter(pl.col("known_at") <= pl.col(issue_col))
         .sort("known_at")
         .unique(keys + [spine_event, issue_col], keep="last"))
    out = spine.join(j.select(keys + [spine_event, issue_col] + value_cols + ["known_at", "approx"]),
                     on=keys + [spine_event, issue_col], how="left")
    if out.height != spine.height:
        raise CardinalityError("PIT lookup fanned out")
    return out


def naive_violations(spine: pl.DataFrame, cands: pl.DataFrame, *, keys: list[str], spine_event: str, event_col: str,
                     issue_col: str = "issue_at") -> int:
    """How many spine rows would read a value released after the issue instant if joined naively on event time.
    A non-zero count means the feature's declared lag is shorter than the source's publication lag."""
    first = cands.group_by(keys + [event_col]).agg(pl.col("known_at").min().alias("first_known"))
    j = spine.select(keys + [spine_event, issue_col]).join(first.rename({event_col: spine_event}), on=keys + [spine_event], how="inner")
    return j.filter(pl.col("first_known") > pl.col(issue_col)).height


def assert_no_leak(df: pl.DataFrame, avail_col: str = "max_input_available_at", issue_col: str = "issue_at") -> None:
    bad = df.filter(pl.col(avail_col).is_not_null() & (pl.col(avail_col) > pl.col(issue_col))).height
    if bad:
        raise LeakageError(f"{bad} rows use inputs released after their forecast issue instant")


def issue_instant(week_start_col: str = "issue_week", hour: int = 12, weekday_offset_days: int = 3) -> pl.Expr:
    """Forecasts are issued on Wednesday 12:00 UTC of the issue week (after the Monday surveillance release)."""
    return (pl.col(week_start_col).cast(DT) + pl.duration(days=weekday_offset_days, hours=hour)).alias("issue_at")


def to_dt(d: datetime) -> pl.Expr:
    return pl.lit(d).cast(DT)
