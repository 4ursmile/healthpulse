"""Allowlisted feature templates evaluated point-in-time on an issue-week spine. No user code is executed."""
from __future__ import annotations

from datetime import date
from typing import Callable

import numpy as np
import polars as pl

from ..sources import silver
from . import pit

KEYS = ["municipality_id"]
SPINE_KEYS = ["municipality_id", "issue_week", "issue_at"]


class FeatureUnavailable(RuntimeError):
    """A required upstream source has no published data (e.g. mobility needs an authorization)."""


def case_table(disease_id: str) -> str:
    return "sari_weekly" if disease_id.endswith("_sari") else "disease_weekly"


def _resolve_table(params: dict, disease_id: str) -> str:
    t = params.get("table", "cases")
    return case_table(disease_id) if t == "cases" else t


class Ctx:
    """Shared, cached candidate frames for one materialization (one disease, one policy)."""

    def __init__(self, disease_id: str, policy: str, munis: list[str] | None = None):
        self.disease_id, self.policy, self.munis = disease_id, policy, munis
        self._c: dict[tuple, pl.DataFrame] = {}
        self.sources_used: set[str] = set()
        self.vintages_used: set[str] = set()

    def cands(self, table: str, column: str) -> pl.DataFrame:
        k = (table, column)
        if k in self._c:
            return self._c[k]
        df = silver.all_vintages(table)
        if not df.height:
            raise FeatureUnavailable(f"no published data in Silver table {table}")
        if "disease_id" in df.columns:
            df = df.filter(pl.col("disease_id") == self.disease_id)
            if not df.height:
                raise FeatureUnavailable(f"{table} has no rows for {self.disease_id}")
        if self.munis and "municipality_id" in df.columns:
            df = df.filter(pl.col("municipality_id").is_in(self.munis))
        self.vintages_used |= set(df["vintage_id"].unique().to_list())
        self.sources_used |= set(df["source_id"].unique().to_list())
        if table == "population_annual":
            df = df.with_columns(pl.date(pl.col("reference_year"), 8, 31).cast(pit.DT).alias("release_approx_at"))
        c = pit.candidates(df, KEYS, _event_col(table), [column], self.policy)
        self._c[k] = c
        return c


def _event_col(table: str) -> str:
    return {"population_annual": "reference_year"}.get(table, "week_start")


def _lookup_offsets(spine: pl.DataFrame, cands: pl.DataFrame, column: str, offsets_weeks: list[int],
                    base_col: str = "issue_week") -> pl.DataFrame:
    """Values at base - k weeks for each k, long format with an `_k` column."""
    sp = pl.concat([spine.select(SPINE_KEYS + ([base_col] if base_col not in SPINE_KEYS else []))
                    .with_columns(pl.lit(k).alias("_k"), (pl.col(base_col) - pl.duration(weeks=k)).alias("_ev"))
                    for k in offsets_weeks])
    out = pit.lookup(sp, cands, keys=KEYS, spine_event="_ev", event_col="week_start", value_cols=[column])
    return out


def _finish(spine: pl.DataFrame, df: pl.DataFrame, name: str, value: pl.Expr, by: list[str] | None = None) -> pl.DataFrame:
    by = by or SPINE_KEYS
    return df.group_by(by).agg(value.alias(name), pl.col("known_at").max().alias(f"{name}__known_at"),
                               pl.col("approx").any().alias(f"{name}__approx"))


def t_lag(spine, ctx: Ctx, p: dict, name: str):
    table, col = _resolve_table(p, ctx.disease_id), p["column"]
    long = _lookup_offsets(spine, ctx.cands(table, col), col, [int(p["lag_weeks"])])
    v = pl.col(col).first().cast(pl.Float64)
    return _finish(spine, long, name, _transform(v, p.get("transform")))


def t_rolling(spine, ctx: Ctx, p: dict, name: str):
    table, col = _resolve_table(p, ctx.disease_id), p["column"]
    end, win = int(p["end_lag_weeks"]), int(p["window_weeks"])
    long = _lookup_offsets(spine, ctx.cands(table, col), col, list(range(end, end + win)))
    agg = {"sum": pl.col(col).sum(), "mean": pl.col(col).mean(), "max": pl.col(col).max()}[p.get("agg", "mean")]
    minp = int(p.get("min_periods", win))
    v = pl.when(pl.col(col).is_not_null().sum() >= minp).then(agg.cast(pl.Float64)).otherwise(None)
    return _finish(spine, long, name, _transform(v, p.get("transform")))


def t_growth(spine, ctx: Ctx, p: dict, name: str):
    table, col = _resolve_table(p, ctx.disease_id), p["column"]
    lag, span = int(p.get("lag_weeks", 1)), int(p.get("span_weeks", 2))
    long = _lookup_offsets(spine, ctx.cands(table, col), col, [lag, lag + span])
    a = pl.col(col).filter(pl.col("_k") == lag).first().cast(pl.Float64)
    b = pl.col(col).filter(pl.col("_k") == lag + span).first().cast(pl.Float64)
    return _finish(spine, long, name, ((a + 1).log() - (b + 1).log()))


def t_incidence(spine, ctx: Ctx, p: dict, name: str):
    table = _resolve_table(p, ctx.disease_id)
    lag = int(p.get("lag_weeks", 1))
    cases = _lookup_offsets(spine, ctx.cands(table, "observed_cases"), "observed_cases", [lag])
    cases = cases.select(SPINE_KEYS + ["observed_cases", "known_at", "approx"])
    pop = t_static(spine, ctx, {"table": "population_annual", "column": "population"}, "_pop")
    j = pit.safe_join(cases, pop, on=SPINE_KEYS, what="incidence population")
    j = j.with_columns(pl.max_horizontal("known_at", "_pop__known_at").alias("known_at"),
                       (pl.col("approx") | pl.col("_pop__approx").fill_null(False)).alias("approx"))
    return _finish(spine, j, name, (pl.col("observed_cases").first().cast(pl.Float64) / pl.col("_pop").first() * 1e5))


def t_static(spine, ctx: Ctx, p: dict, name: str):
    """As-of value of a slow-moving table (annual population): reference year = issue year - 1."""
    table, col = p["table"], p["column"]
    if table not in ("population_annual",):
        # facility / sector tables are not unique per municipality: refuse rather than fan out
        df = silver.latest(table)
        pit.assert_unique(df, KEYS, f"static_asof on {table}")
    c = ctx.cands(table, col)
    sp = spine.select(SPINE_KEYS).with_columns((pl.col("issue_week").dt.year() - 1).cast(pl.Int32).alias("_ev"))
    out = pit.lookup(sp, c.with_columns(pl.col("reference_year").cast(pl.Int32)), keys=KEYS, spine_event="_ev",
                     event_col="reference_year", value_cols=[col])
    return _finish(spine, out, name, _transform(pl.col(col).first().cast(pl.Float64), p.get("transform")))


def t_seasonal_lag(spine_h, ctx: Ctx, p: dict, name: str):
    """Value of the same epi-week one year before the target week (target-relative; needs the horizon spine)."""
    table, col = _resolve_table(p, ctx.disease_id), p["column"]
    sp = spine_h.select(SPINE_KEYS + ["horizon", "target_week"]).with_columns((pl.col("target_week") - pl.duration(weeks=52)).alias("_ev"))
    out = pit.lookup(sp, ctx.cands(table, col), keys=KEYS, spine_event="_ev", event_col="week_start", value_cols=[col])
    return _finish(spine_h, out, name, _transform(pl.col(col).first().cast(pl.Float64), p.get("transform")),
                   by=SPINE_KEYS + ["horizon"])


def t_holiday_window(spine_h, ctx: Ctx, p: dict, name: str):
    """National public holidays in a window relative to the target week. Scheduled holidays are known ahead."""
    hol = silver.latest("holiday_daily")
    if not hol.height:
        raise FeatureUnavailable("no holiday calendar published")
    hol = hol.filter(pl.col("is_national")).select("date", "known_at").unique("date")
    a, b = int(p.get("start_offset_days", 0)), int(p.get("end_offset_days", 7))
    rel = p.get("relative_to", "target")
    base = spine_h.select(SPINE_KEYS + ["horizon", "target_week"])
    ref = pl.col("target_week") if rel == "target" else pl.col("issue_at").dt.date()
    base = base.with_columns((ref + pl.duration(days=a)).alias("_a"), (ref + pl.duration(days=b)).alias("_b"))
    j = base.join(hol, how="cross").filter((pl.col("date") >= pl.col("_a")) & (pl.col("date") < pl.col("_b"))
                                           & (pl.col("known_at") <= pl.col("issue_at")))
    cnt = j.group_by(SPINE_KEYS + ["horizon"]).agg(pl.len().cast(pl.Float64).alias(name),
                                                    pl.col("known_at").max().alias(f"{name}__known_at"))
    out = base.select(SPINE_KEYS + ["horizon"]).join(cnt, on=SPINE_KEYS + ["horizon"], how="left").with_columns(
        pl.col(name).fill_null(0.0), pl.lit(False).alias(f"{name}__approx"))
    return out


def t_calendar(spine_h, ctx: Ctx, p: dict, name: str):
    wk = pl.col("target_week").dt.ordinal_day() / 365.25 * 2 * np.pi
    f = wk.sin() if p.get("fn", "sin") == "sin" else wk.cos()
    return spine_h.select(SPINE_KEYS + ["horizon", f.alias(name)]).with_columns(
        pl.lit(None, dtype=pit.DT).alias(f"{name}__known_at"), pl.lit(False).alias(f"{name}__approx"))


# IBGE published the 2nd edition of "Arranjos Populacionais" (Census 2010 commuting) in 2016: weights known from then on
COMMUTING_KNOWN_AT = "2016-01-01T00:00:00Z"


def t_neighbor_pressure(spine, ctx: Ctx, p: dict, name: str):
    """Σ_j w_j × incidence_j(issue − lag) over the other municipalities j of i's arranjo populacional, where w_j is IBGE's
    commuting integration index of j (Census 2010). A connectivity-weighted exposure proxy, not imported infections."""
    ca = silver.latest("commuting_arranjo")
    if not ca.height:
        raise FeatureUnavailable("commuter-weighted pressure needs the IBGE commuting table (ingest source ibge_commuting); "
                                 "no movement data is fabricated")
    lag = int(p.get("lag_weeks", 1))
    targets = spine["municipality_id"].unique().to_list()
    mem = ca.select("municipality_id", "arranjo_id", "integration_index")
    pairs = (mem.filter(pl.col("municipality_id").is_in(targets)).select(pl.col("municipality_id").alias("_i"), "arranjo_id")
             .join(mem.rename({"municipality_id": "_j"}), on="arranjo_id").filter(pl.col("_i") != pl.col("_j"))
             .select("_i", "_j", pl.col("integration_index").alias("_w")))
    out_cols = SPINE_KEYS
    if not pairs.height:
        return spine.select(out_cols).with_columns(pl.lit(None, dtype=pl.Float64).alias(name),
                                                   pl.lit(None, dtype=pit.DT).alias(f"{name}__known_at"), pl.lit(False).alias(f"{name}__approx"))
    js = pairs["_j"].unique().to_list()
    table = case_table(ctx.disease_id)
    cases = silver.all_vintages(table)
    cases = cases.filter((pl.col("disease_id") == ctx.disease_id) & pl.col("municipality_id").is_in(js))
    pop = silver.all_vintages("population_annual").filter(pl.col("municipality_id").is_in(js))
    if not cases.height or not pop.height:
        raise FeatureUnavailable("no case or population data for the arranjo neighbours (re-ingest InfoDengue after ibge_commuting)")
    ctx.vintages_used |= set(cases["vintage_id"].unique().to_list()) | set(pop["vintage_id"].unique().to_list())
    ctx.sources_used |= set(cases["source_id"].unique().to_list()) | {"ibge_commuting"}
    cc = pit.candidates(cases, KEYS, "week_start", ["observed_cases"], ctx.policy)
    pc = pit.candidates(pop.with_columns(pl.date(pl.col("reference_year"), 8, 31).cast(pit.DT).alias("release_approx_at")),
                        KEYS, "reference_year", ["population"], ctx.policy)
    sp = (spine.select(SPINE_KEYS).rename({"municipality_id": "_i"}).join(pairs, on="_i")
          .rename({"_j": "municipality_id"})
          .with_columns((pl.col("issue_week") - pl.duration(weeks=lag)).alias("_ev"),
                        (pl.col("issue_week").dt.year() - 1).cast(pl.Int32).alias("_yr")))
    lc = pit.lookup(sp, cc, keys=KEYS, spine_event="_ev", event_col="week_start", value_cols=["observed_cases"])
    lp = pit.lookup(sp.select(KEYS + ["_yr", "issue_at"]).unique(), pc.with_columns(pl.col("reference_year").cast(pl.Int32)),
                    keys=KEYS, spine_event="_yr", event_col="reference_year", value_cols=["population"])
    lp = lp.rename({"known_at": "_pk", "approx": "_pa"})
    j = lc.join(lp, on=KEYS + ["_yr", "issue_at"], how="left")
    j = j.with_columns((pl.col("observed_cases").cast(pl.Float64) / pl.col("population") * 1e5 * pl.col("_w")).alias("_v"),
                       pl.max_horizontal("known_at", "_pk", pl.lit(COMMUTING_KNOWN_AT).str.to_datetime(time_zone="UTC").cast(pit.DT)).alias("_k"),
                       (pl.col("approx").fill_null(False) | pl.col("_pa").fill_null(False)).alias("_a"))
    agg = (j.filter(pl.col("_v").is_not_null()).group_by(["_i", "issue_week", "issue_at"])
           .agg(pl.col("_v").sum().alias(name), pl.col("_k").max().alias(f"{name}__known_at"), pl.col("_a").any().alias(f"{name}__approx"))
           .rename({"_i": "municipality_id"}))
    return spine.select(out_cols).join(agg, on=SPINE_KEYS, how="left").with_columns(pl.col(f"{name}__approx").fill_null(False))


def _transform(v: pl.Expr, t: str | None) -> pl.Expr:
    if t == "log1p":
        return (v + 1).log()
    if t == "log":
        return v.log()
    return v


TEMPLATES: dict[str, dict] = {
    "lag": {"fn": t_lag, "level": "issue", "params": ["table", "column", "lag_weeks", "transform"]},
    "rolling": {"fn": t_rolling, "level": "issue", "params": ["table", "column", "end_lag_weeks", "window_weeks", "agg", "min_periods", "transform"]},
    "growth": {"fn": t_growth, "level": "issue", "params": ["table", "column", "lag_weeks", "span_weeks"]},
    "incidence": {"fn": t_incidence, "level": "issue", "params": ["table", "lag_weeks"]},
    "static_asof": {"fn": t_static, "level": "issue", "params": ["table", "column", "transform"]},
    "seasonal_lag": {"fn": t_seasonal_lag, "level": "target", "params": ["table", "column", "transform"]},
    "holiday_window": {"fn": t_holiday_window, "level": "target", "params": ["relative_to", "start_offset_days", "end_offset_days"]},
    "calendar": {"fn": t_calendar, "level": "target", "params": ["fn"]},
    "neighbor_pressure": {"fn": t_neighbor_pressure, "level": "issue", "params": ["lag_weeks"]},
}

# publication lag per Silver table used to flag definitions whose lag is shorter than the source's release delay
TABLE_LAG_DAYS = {"disease_weekly": 3, "sari_weekly": 14, "weather_weekly": 6, "population_annual": 240, "environment_weekly": 28}


def build_spine(munis: list[str], start: date, end: date, issue_at=None) -> pl.DataFrame:
    weeks = pl.date_range(start, end, interval="1w", eager=True).alias("issue_week")
    sp = pl.DataFrame({"municipality_id": munis}).join(pl.DataFrame(weeks), how="cross")
    if issue_at is not None:  # live issuance: the real instant, not the nominal Wednesday of the issue week
        return sp.with_columns(pl.lit(issue_at).cast(pit.DT).alias("issue_at"))
    return sp.with_columns(pit.issue_instant("issue_week"))


def expand_horizons(spine: pl.DataFrame, horizons: list[int]) -> pl.DataFrame:
    h = pl.DataFrame({"horizon": horizons}, schema={"horizon": pl.Int32})
    return spine.join(h, how="cross").with_columns(
        (pl.col("issue_week") - pl.duration(weeks=1) + pl.duration(weeks=pl.col("horizon"))).alias("target_week"),
        (pl.col("issue_week") - pl.duration(weeks=1)).alias("anchor_week"))


def evaluate(defs: list[dict], disease_id: str, munis: list[str], start: date, end: date, horizons: list[int],
             policy: str = "publication_lag", on_feature: Callable[[str], None] | None = None,
             issue_at=None) -> tuple[pl.DataFrame, dict]:
    """Materialize a list of feature definitions; returns (frame keyed by spine+horizon, per-feature report)."""
    ctx = Ctx(disease_id, policy, munis)
    spine = build_spine(munis, start, end, issue_at)
    spine_h = expand_horizons(spine, horizons)
    out = spine_h
    report: dict[str, dict] = {}
    for d in defs:
        name = d["feature_id"]
        tpl = TEMPLATES[d["template"]]
        if on_feature:
            on_feature(name)
        try:
            f = tpl["fn"](spine if tpl["level"] == "issue" else spine_h, ctx, d["params"], name)
        except FeatureUnavailable as e:
            report[name] = {"status": "blocked", "reason": str(e)}
            continue
        join_keys = SPINE_KEYS if tpl["level"] == "issue" else SPINE_KEYS + ["horizon"]
        out = pit.safe_join(out, f, on=join_keys, what=f"feature {name}")
        col = out[name]
        report[name] = {"status": "ok", "null_rate": round(col.null_count() / max(1, out.height), 4),
                        "approx_rows": int(out[f"{name}__approx"].fill_null(False).sum()),
                        "mean": None if col.null_count() == out.height else float(col.drop_nulls().mean()),
                        "min": None if col.null_count() == out.height else float(col.drop_nulls().min()),
                        "max": None if col.null_count() == out.height else float(col.drop_nulls().max())}
    known = [c for c in out.columns if c.endswith("__known_at")]
    approx = [c for c in out.columns if c.endswith("__approx")]
    out = out.with_columns(
        (pl.max_horizontal(known) if known else pl.lit(None, dtype=pit.DT)).alias("max_input_available_at"),
        (pl.any_horizontal([pl.col(c).fill_null(False) for c in approx]) if approx else pl.lit(False)).alias("pit_approx"))
    pit.assert_no_leak(out)
    out = out.drop(known + approx)
    meta = {"sources": sorted(ctx.sources_used), "vintages": sorted(ctx.vintages_used), "policy": policy}
    return out, {"features": report, **meta}
