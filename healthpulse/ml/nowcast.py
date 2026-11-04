"""Nowcasting of right-truncated SARI counts (COVID-19 and influenza) from a reconstructed reporting triangle.

WHY. The count shown for the most recent onset weeks is incomplete: a case only counts toward a disease once its record
has been entered (DT_DIGITA) AND closed with a final classification (DT_ENCERRA). Measured on the SIVEP-Gripe files, the
median onset-to-classified delay is 17 days (7 days for entry alone), the 90th percentile 67 days. The latest weeks therefore
look like a decline that is not real. A nowcast estimates the count those weeks will eventually have.

DATA. A reporting triangle is rebuilt from the row-level Bronze files and reduced to counts immediately. Only five columns
are read: onset date, entry date, closure date, final classification, residence municipality. Nothing row-level or personal is
written anywhere. The triangle cell n[s, k] is the number of cases with onset week s whose disease-specific record became
available k weeks later (k = 0 is the onset week itself). A case is "available" at max(DT_DIGITA, DT_ENCERRA).

   Honest limit: this is reconstructed from FIRST ENTRY and CLOSURE DATES in a single snapshot. It cannot see edits,
   reclassifications or deletions made after those dates, so it is not a true vintage archive. Every backtest result carries
   the label PIT_RECONSTRUCTED, never "verified".

METHOD (all choices documented; defaults picked on the tuning half of the backtest only).
  1. Delay distribution. For as-of week T the PMF p_k (k = 0..D) is estimated ONLY from onset weeks s <= T - D, whose delays
     0..D are all already observed at T (strict point-in-time), from a window of the W most recent such weeks with optional
     exponential recency weights. Delays beyond D (about 2% of cases) are not modelled and show up as a small negative bias.
  2. Hierarchy. State PMF = Dirichlet posterior mean with the national PMF as prior (strength kappa cases); a municipality
     uses its state's PMF as prior. So small places borrow strength and never produce a zero or a wild factor.
  3. Posterior. With r cases seen so far for an onset week of age a, completeness F_a = sum_{k<=a} p_k and a flat prior on the
     eventual count, the number still to come is Negative-Binomial(r + 1, F_a). The delay PMF itself is uncertain, so each of
     S draws takes its own PMF from a Dirichlet and its own NB sample; the quantiles are taken over the mixture. `ess_scale`
     (<= 1) discounts the Dirichlet counts to allow for batch entry (over-dispersion).
  4. When F_a < min_cdf the week is NOT nowcast (null, never zero): the data say too little about it.

IGNORED on purpose: day-of-week entry effects (the unit is the epi week), the seasonal drift of delays inside the window,
reclassification after closure, and the tail beyond D weeks. Viet Nam has no line list, so no nowcast exists there.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl

DISEASE_CLASS = {"covid19_sari": 5, "influenza_sari": 1}   # CLASSI_FIN codes, same mapping as sources.connectors_extra.SIVEP_CLASS
CLASS_DISEASE = {v: k for k, v in DISEASE_CLASS.items()}
K_STORE = 52           # delays 0..52 weeks stored; the column K_STORE + 1 holds "longer than a year"
LABEL = "PIT_RECONSTRUCTED"

UF_ABBR = {"11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO", "21": "MA", "22": "PI", "23": "CE",
           "24": "RN", "25": "PB", "26": "PE", "27": "AL", "28": "SE", "29": "BA", "31": "MG", "32": "ES", "33": "RJ", "35": "SP",
           "41": "PR", "42": "SC", "43": "RS", "50": "MS", "51": "MT", "52": "GO", "53": "DF"}


@dataclass(frozen=True)
class Config:
    max_delay: int = 26          # D: weeks of delay modelled; a PMF needs onset weeks at least D weeks old
    window: int = 52             # W: most recent complete onset weeks used for the PMF (26 was worse on the tuning half; 80 = 52 with this history)
    min_window: int = 10         # fewer complete weeks than this: no nowcast for the as-of week at all
    halflife: float | None = None  # recency half-life in weeks (None = equal weights)
    kappa_state: float = 60.0    # prior strength (cases) of the national PMF in a state's PMF
    kappa_muni: float = 60.0     # prior strength of the state PMF in a municipality's PMF
    ess_scale: float = 1.0       # discount on the Dirichlet counts (over-dispersion)
    ess_cap: float | None = 100.0  # cap on the effective Dirichlet sample size in cases: entry comes in batches, so 5,000 cases are not 5,000 draws
    draws: int = 400
    min_cdf: float = 0.2         # completeness below which a week is withheld
    horizon: int = 12            # how many most-recent onset weeks are nowcast
    seed: int = 20260211

    def hash(self) -> str:
        return hashlib.sha256(repr(sorted(asdict(self).items())).encode()).hexdigest()[:16]


# ====================================================================================================================
# 1. reporting triangle
# ====================================================================================================================
def week_index(d: date, origin: date) -> int:
    return (d - origin).days // 7


def _week_start(col: str) -> pl.Expr:
    return (pl.col(col) - pl.duration(days=(pl.col(col).dt.weekday() % 7))).cast(pl.Date)


def _scan_srag(path: Path, delimiter: str = ";") -> pl.LazyFrame:
    """Only the five columns the nowcast needs. Dates parse from parquet datetimes or dd/mm/yyyy | yyyy-mm-dd text."""
    need = ["DT_SIN_PRI", "DT_DIGITA", "DT_ENCERRA", "CLASSI_FIN", "CO_MUN_RES"]
    if path.suffix == ".parquet":
        lf = pl.scan_parquet(path)
        cols = set(lf.collect_schema().names())
        if not set(need) <= cols:
            raise ValueError(f"{path.name} lacks {sorted(set(need) - cols)}")
        return lf.select(pl.col("DT_SIN_PRI").cast(pl.Date, strict=False).alias("onset"),
                         pl.col("DT_DIGITA").cast(pl.Date, strict=False).alias("entered"),
                         pl.col("DT_ENCERRA").cast(pl.Date, strict=False).alias("closed"),
                         pl.col("CLASSI_FIN").cast(pl.Int64, strict=False).alias("cls"),
                         pl.col("CO_MUN_RES").cast(pl.Utf8).str.strip_chars().str.slice(0, 6).alias("m6"))
    lf = pl.scan_csv(path, separator=delimiter, infer_schema=False, encoding="utf8-lossy", ignore_errors=True)
    cols = set(lf.collect_schema().names())
    if not set(need) <= cols:
        raise ValueError(f"{path.name} lacks {sorted(set(need) - cols)}")

    def dt(c: str, alias: str) -> pl.Expr:
        return pl.coalesce(pl.col(c).str.to_date("%d/%m/%Y", strict=False), pl.col(c).str.to_date("%Y-%m-%d", strict=False)).alias(alias)
    return lf.select(dt("DT_SIN_PRI", "onset"), dt("DT_DIGITA", "entered"), dt("DT_ENCERRA", "closed"),
                     pl.col("CLASSI_FIN").str.strip_chars().cast(pl.Int64, strict=False).alias("cls"),
                     pl.col("CO_MUN_RES").str.strip_chars().str.slice(0, 6).alias("m6"))


def build_triangle(paths: list[Path], municipalities: dict[str, str] | None = None, delimiter: str = ";") -> tuple[pl.DataFrame, dict]:
    """Row-level files -> long count table (level, unit_id, disease_id, onset_week, delay_w, n) and a meta dict.

    `municipalities` maps the 6-digit residence code to the 7-digit municipality_id for the places that get their own series.
    delay_w runs 0..K_STORE, and K_STORE + 1 means "longer". Negative delays (entry dated before onset, data-entry errors) are
    clipped to 0 and counted in meta. Disease = CLASSI_FIN 5 (COVID-19) or 1 (influenza); other classes are not nowcast here."""
    municipalities = municipalities or {}
    lf = pl.concat([_scan_srag(Path(p), delimiter) for p in paths], how="vertical_relaxed")
    lf = (lf.filter(pl.col("onset").is_not_null() & pl.col("entered").is_not_null() & pl.col("cls").is_in(list(CLASS_DISEASE))
                    & (pl.col("onset") >= date(2019, 1, 1)))
          .with_columns(pl.max_horizontal("entered", "closed").alias("available"))   # max ignores a null closure date
          .with_columns(_week_start("onset").alias("onset_week"), _week_start("available").alias("avail_week"))
          .with_columns(((pl.col("avail_week") - pl.col("onset_week")).dt.total_days() // 7).alias("raw_delay"))
          .with_columns(pl.col("raw_delay").clip(lower_bound=0, upper_bound=K_STORE + 1).alias("delay_w"),
                        pl.col("cls").replace_strict(CLASS_DISEASE, return_dtype=pl.Utf8).alias("disease_id"),
                        pl.col("m6").str.slice(0, 2).replace_strict(UF_ABBR, default=None, return_dtype=pl.Utf8).alias("uf")))
    cells = (lf.group_by("uf", "m6", "disease_id", "onset_week", "delay_w").agg(pl.len().alias("n")).collect(engine="streaming"))
    stats = lf.select(pl.len().alias("rows"), (pl.col("raw_delay") < 0).sum().alias("neg"), (pl.col("raw_delay") > K_STORE).sum().alias("tail"),
                      pl.col("onset_week").min().alias("w0"), pl.col("onset_week").max().alias("w1"), pl.col("available").max().alias("last_available"),
                      ).collect().row(0, named=True)
    nat = cells.group_by("disease_id", "onset_week", "delay_w").agg(pl.col("n").sum()).with_columns(pl.lit("national").alias("level"), pl.lit("BR").alias("unit_id"))
    st = (cells.filter(pl.col("uf").is_not_null()).group_by("uf", "disease_id", "onset_week", "delay_w").agg(pl.col("n").sum())
          .rename({"uf": "unit_id"}).with_columns(pl.lit("state").alias("level")))
    parts = [nat, st]
    if municipalities:
        mu = (cells.filter(pl.col("m6").is_in(list(municipalities))).group_by("m6", "disease_id", "onset_week", "delay_w").agg(pl.col("n").sum())
              .with_columns(pl.col("m6").replace_strict(municipalities, return_dtype=pl.Utf8).alias("unit_id"), pl.lit("municipality").alias("level")).drop("m6"))
        parts.append(mu)
    cols = ["level", "unit_id", "disease_id", "onset_week", "delay_w", "n"]
    tri = pl.concat([p.select(cols) for p in parts]).with_columns(pl.col("n").cast(pl.Int64), pl.col("delay_w").cast(pl.Int64)).sort(cols[:5])
    meta = {"rows_used": int(stats["rows"]), "negative_delay_clipped": int(stats["neg"]), "beyond_one_year": int(stats["tail"]),
            "first_onset_week": str(stats["w0"]), "last_onset_week": str(stats["w1"]), "last_availability_date": str(stats["last_available"]),
            "columns_read": ["DT_SIN_PRI", "DT_DIGITA", "DT_ENCERRA", "CLASSI_FIN", "CO_MUN_RES"],
            "availability_definition": "max(DT_DIGITA, DT_ENCERRA)", "label": LABEL}
    return tri, meta


class Triangle:
    """Dense view of the long table: one (weeks x (K_STORE + 2)) array per (level, unit, disease). Week 0 is `origin`."""

    def __init__(self, long: pl.DataFrame):
        self.long = long
        if not long.height:
            raise ValueError("empty reporting triangle")
        self.origin: date = long["onset_week"].min()
        last: date = long["onset_week"].max()
        self.n_weeks = week_index(last, self.origin) + 1
        self.arr: dict[tuple[str, str, str], np.ndarray] = {}
        i = long.select((pl.col("onset_week") - pl.lit(self.origin)).dt.total_days())["onset_week"].to_numpy() // 7
        d = long["delay_w"].to_numpy()
        n = long["n"].to_numpy()
        keys = list(zip(long["level"].to_list(), long["unit_id"].to_list(), long["disease_id"].to_list()))
        for key in set(keys):
            self.arr[key] = np.zeros((self.n_weeks, K_STORE + 2))
        for key, ii, dd, nn in zip(keys, i, d, n):
            self.arr[key][ii, dd] += nn

    def week(self, idx: int) -> date:
        return self.origin + timedelta(weeks=int(idx))

    def get(self, level: str, unit: str, disease: str) -> np.ndarray | None:
        return self.arr.get((level, unit, disease))

    def units(self, level: str, disease: str) -> list[str]:
        return sorted(u for (lv, u, ds) in self.arr if lv == level and ds == disease)


# ====================================================================================================================
# 2. point-in-time choke point
# ====================================================================================================================
def asof(arr: np.ndarray, T: int) -> np.ndarray:
    """The triangle as it stood at the end of week T: only cells with s + k <= T survive. Every estimate goes through this
    function, so no delay estimate or observed count can see anything entered after T. The 'longer than a year' column is
    dropped (its entry week is unknown), which only matters for onset weeks more than a year old."""
    s = np.arange(arr.shape[0])[:, None]
    k = np.arange(arr.shape[1])[None, :]
    m = (s + k <= T) & (k <= K_STORE) & (s <= T)
    return np.where(m, arr, 0.0)


def pit_ok(masked: np.ndarray, T: int) -> bool:
    s, k = np.nonzero(masked)
    return bool(np.all(s + k <= T))


# ====================================================================================================================
# 3. delay model
# ====================================================================================================================
def delay_counts(masked: np.ndarray, T: int, cfg: Config, valid_from: int) -> tuple[np.ndarray, float, int]:
    """Weighted delay counts c_k (k = 0..D) from complete onset weeks s in (T - D - W, T - D]; returns (c, ess, n_weeks)."""
    D, W = cfg.max_delay, cfg.window
    hi = T - D
    lo = max(valid_from, hi - W + 1)
    if hi < lo:
        return np.zeros(D + 1), 0.0, 0
    s = np.arange(lo, hi + 1)
    w = np.ones(len(s)) if cfg.halflife is None else 0.5 ** ((hi - s) / cfg.halflife)
    c = (w[:, None] * masked[s, : D + 1]).sum(0)
    return c, float(c.sum()), len(s)


def pmf_with_prior(c: np.ndarray, prior: np.ndarray, kappa: float, cfg: Config) -> np.ndarray:
    """Dirichlet parameters alpha = scale * counts + kappa * parent PMF. scale = ess_scale, and the counts are further shrunk so
    that their total never exceeds ess_cap (over-dispersion: neighbouring cases are entered together, not independently)."""
    n = float(c.sum())
    scale = cfg.ess_scale if cfg.ess_cap is None or n <= 0 else min(cfg.ess_scale, cfg.ess_cap / n)
    return scale * c + kappa * prior


def national_pmf(c: np.ndarray) -> np.ndarray:
    a = c + 1.0 / len(c)                        # one pseudo-case spread evenly: avoids zero cells, negligible next to real data
    return a / a.sum()


@dataclass
class Posterior:
    alpha: np.ndarray            # Dirichlet parameters of this unit's delay PMF
    ess: float
    n_weeks: int
    source: str                  # own | borrowed (state/national dominates) | none

    @property
    def mean(self) -> np.ndarray:
        return self.alpha / self.alpha.sum()


def fit_posteriors(tri: Triangle, disease: str, T: int, cfg: Config, valid_from: int, units: dict[str, list[str]]) -> dict[tuple[str, str], Posterior]:
    """Delay posteriors for the national series, every state and the listed municipalities, as of week T."""
    out: dict[tuple[str, str], Posterior] = {}
    nat = tri.get("national", "BR", disease)
    if nat is None:
        return out
    m = asof(nat, T)
    c, ess, nw = delay_counts(m, T, cfg, valid_from)
    if nw < cfg.min_window or ess < 20:
        return out
    p_nat = national_pmf(c)
    out[("national", "BR")] = Posterior(pmf_with_prior(c, p_nat, 1.0, cfg), ess, nw, "own")
    state_of = units.get("state_of", {})
    for st in units.get("state", []):
        a = tri.get("state", st, disease)
        if a is None:
            continue
        cs, es, nws = delay_counts(asof(a, T), T, cfg, valid_from)
        out[("state", st)] = Posterior(pmf_with_prior(cs, p_nat, cfg.kappa_state, cfg), es, nws,
                                       "own" if es >= cfg.kappa_state else "borrowed")
    for mu in units.get("municipality", []):
        a = tri.get("municipality", mu, disease)
        parent = out.get(("state", state_of.get(mu, "")), out[("national", "BR")])
        if a is None:
            continue
        cm, em, nwm = delay_counts(asof(a, T), T, cfg, valid_from)
        out[("municipality", mu)] = Posterior(pmf_with_prior(cm, parent.mean, cfg.kappa_muni, cfg), em, nwm,
                                              "own" if em >= cfg.kappa_muni else "borrowed")
    return out


def nowcast_unit(masked: np.ndarray, T: int, post: Posterior, cfg: Config, rng: np.random.Generator, ages: list[int] | None = None) -> list[dict]:
    """Nowcast the last `horizon` onset weeks of one unit. Returns one dict per age a = 0 .. horizon-1 (s = T - a)."""
    D = cfg.max_delay
    ages = list(range(cfg.horizon)) if ages is None else ages
    P = rng.dirichlet(post.alpha, size=cfg.draws)              # (S, D+1) draws of the delay PMF
    F = np.clip(np.cumsum(P, axis=1), 1e-9, 1.0)                 # (S, D+1)
    Fm = np.cumsum(post.mean)
    rows = []
    for a in ages:
        s = T - a
        if s < 0 or s >= masked.shape[0]:
            continue
        r = float(masked[s, : a + 1].sum()) if a <= K_STORE else float(masked[s].sum())
        f_a = F[:, min(a, D)]
        fmean = float(Fm[min(a, D)])
        row = {"age_w": a, "onset_idx": s, "observed": r, "completeness": fmean, "ess": post.ess, "delay_source": post.source}
        if a >= D:
            row.update(nowcast_p50=r, p025=r, p10=r, p90=r, p975=r, status="complete")        # delays past D are not modelled
        elif fmean < cfg.min_cdf:
            row.update(nowcast_p50=None, p025=None, p10=None, p90=None, p975=None, status="insufficient_data")
        else:
            m = rng.negative_binomial(r + 1.0, f_a)
            tot = r + m
            q = np.quantile(tot, [0.025, 0.10, 0.50, 0.90, 0.975])
            row.update(nowcast_p50=float(q[2]), p025=float(q[0]), p10=float(q[1]), p90=float(q[3]), p975=float(q[4]), status="nowcast")
        rows.append(row)
    return rows


def inflate_baseline(r: float, a: int, pooled_cdf: np.ndarray, cfg: Config) -> float | None:
    """Static inflation baseline: raw count divided by one pooled national completeness curve (no state, no recency, no NB)."""
    f = pooled_cdf[min(a, cfg.max_delay)]
    return None if f < cfg.min_cdf else r / f


# ====================================================================================================================
# 4. running a nowcast for an as-of week
# ====================================================================================================================
def unit_layout(tri: Triangle, disease: str, municipality_state: dict[str, str] | None = None) -> dict:
    return {"state": tri.units("state", disease), "municipality": tri.units("municipality", disease),
            "state_of": municipality_state or {}}


def valid_from_index(tri: Triangle) -> int:
    """First onset week with coverage. Files are partitioned by onset epi-year, so the first week of the first file is complete."""
    return 0


def run_asof(tri: Triangle, disease: str, T: int, cfg: Config, layout: dict, display_weeks: int = 52, seed_extra: int = 0) -> pl.DataFrame:
    """Live-style output for as-of week T: for every unit, `display_weeks` weeks of as-of observed counts with the nowcast on the
    recent `horizon` weeks. Weeks older than `max_delay` are shown as complete (no band)."""
    rng = np.random.default_rng(cfg.seed + seed_extra)
    post = fit_posteriors(tri, disease, T, cfg, valid_from_index(tri), layout)
    rows = []
    for (level, unit), pz in post.items():
        arr = tri.get(level, unit, disease)
        m = asof(arr, T)
        assert pit_ok(m, T)
        ages = list(range(min(display_weeks, T + 1)))
        res = {x["age_w"]: x for x in nowcast_unit(m, T, pz, cfg, rng, ages=[a for a in ages if a < cfg.horizon] or [0])}
        for a in ages:
            s = T - a
            if a in res:
                x = res[a]
            else:
                r = float(m[s, : min(a, K_STORE) + 1].sum())
                x = {"age_w": a, "onset_idx": s, "observed": r, "completeness": 1.0, "ess": pz.ess, "delay_source": pz.source,
                     "nowcast_p50": r, "p025": r, "p10": r, "p90": r, "p975": r, "status": "complete" if a >= cfg.max_delay else "unmodelled"}
            x.update(level=level, unit_id=unit, disease_id=disease, week_start=tri.week(s))
            rows.append(x)
    cols = ["level", "unit_id", "disease_id", "week_start", "age_w", "observed", "nowcast_p50", "p025", "p10", "p90", "p975", "completeness", "status", "ess", "delay_source"]
    schema = {"level": pl.Utf8, "unit_id": pl.Utf8, "disease_id": pl.Utf8, "week_start": pl.Date, "age_w": pl.Int64, "observed": pl.Float64,
              "nowcast_p50": pl.Float64, "p025": pl.Float64, "p10": pl.Float64, "p90": pl.Float64, "p975": pl.Float64,
              "completeness": pl.Float64, "status": pl.Utf8, "ess": pl.Float64, "delay_source": pl.Utf8}
    return pl.DataFrame([{k: x.get(k) for k in cols} for x in rows], schema=schema).sort("level", "unit_id", "week_start")


# ====================================================================================================================
# 5. pseudo-prospective backtest
# ====================================================================================================================
BUCKETS = [("0", 0, 0), ("1", 1, 1), ("2", 2, 2), ("3-4", 3, 4), ("5-8", 5, 8), ("9-11", 9, 11)]


def bucket_of(a: int) -> str:
    for name, lo, hi in BUCKETS:
        if lo <= a <= hi:
            return name
    return "12+"


def backtest(tri: Triangle, disease: str, cfg: Config, layout: dict, t_first: int | None = None, t_last: int | None = None,
             maturity: int | None = None, levels: tuple[str, ...] = ("national", "state", "municipality")) -> pl.DataFrame:
    """For each as-of week T: fit delay posteriors from data entered by T only, nowcast the last `horizon` onset weeks, and compare
    with the count those weeks have in the snapshot (the 'mature' truth, only weeks at least `maturity` weeks before the end of
    the data). Rows hold the method, the naive raw count and the static-inflation baseline, side by side."""
    D = cfg.max_delay
    maturity = D if maturity is None else maturity
    last_idx = tri.n_weeks - 1
    t_first = D + cfg.min_window if t_first is None else t_first
    t_last = last_idx - maturity if t_last is None else t_last
    rng = np.random.default_rng(cfg.seed)
    out = []
    nat_full = tri.get("national", "BR", disease)
    for T in range(t_first, t_last + 1):
        post = fit_posteriors(tri, disease, T, cfg, 0, layout)
        if ("national", "BR") not in post:
            continue
        # static baseline: one pooled national completeness curve from the same complete weeks, equal weights, all of them
        cp, _, _ = delay_counts(asof(nat_full, T), T, Config(**{**asdict(cfg), "window": 10_000, "halflife": None}), 0)
        pooled = np.cumsum(national_pmf(cp))
        for (level, unit), pz in post.items():
            if level not in levels:
                continue
            arr = tri.get(level, unit, disease)
            m = asof(arr, T)
            assert pit_ok(m, T)
            for x in nowcast_unit(m, T, pz, cfg, rng):
                truth = float(arr[x["onset_idx"]].sum())
                a = x["age_w"]
                infl = inflate_baseline(x["observed"], a, pooled, cfg)
                out.append({"disease_id": disease, "level": level, "unit_id": unit, "asof_idx": T, "asof_week": tri.week(T), "age_w": a,
                            "bucket": bucket_of(a), "truth": truth, "observed": x["observed"], "completeness": x["completeness"], "ess": x["ess"],
                            "est": x["nowcast_p50"], "p025": x["p025"], "p10": x["p10"], "p90": x["p90"], "p975": x["p975"],
                            "inflate": infl, "status": x["status"]})
    return pl.DataFrame(out, infer_schema_length=None) if out else pl.DataFrame()


def summarise(bt: pl.DataFrame, by: list[str]) -> pl.DataFrame:
    """MAE, WAPE, bias, interval coverage and width of the method, the naive raw count and static inflation, on rows where the
    method produced an estimate (status == nowcast); `withheld` is the share of rows it declined."""
    if not bt.height:
        return pl.DataFrame()
    ev = bt.filter(pl.col("status") == "nowcast")
    g = ev.group_by(by).agg(
        pl.len().alias("n"), pl.col("truth").sum().alias("truth_sum"),
        (pl.col("est") - pl.col("truth")).abs().mean().alias("mae"),
        (pl.col("observed") - pl.col("truth")).abs().mean().alias("mae_naive"),
        (pl.col("inflate") - pl.col("truth")).abs().mean().alias("mae_inflate"),
        ((pl.col("est") - pl.col("truth")).abs().sum() / pl.col("truth").sum()).alias("wape"),
        ((pl.col("observed") - pl.col("truth")).abs().sum() / pl.col("truth").sum()).alias("wape_naive"),
        ((pl.col("inflate") - pl.col("truth")).abs().sum() / pl.col("truth").sum()).alias("wape_inflate"),
        ((pl.col("est") - pl.col("truth")).sum() / pl.col("truth").sum()).alias("bias"),
        ((pl.col("observed") - pl.col("truth")).sum() / pl.col("truth").sum()).alias("bias_naive"),
        (((pl.col("truth") >= pl.col("p10")) & (pl.col("truth") <= pl.col("p90"))).cast(pl.Float64).mean()).alias("cov80"),
        (((pl.col("truth") >= pl.col("p025")) & (pl.col("truth") <= pl.col("p975"))).cast(pl.Float64).mean()).alias("cov95"),
        ((pl.col("p90") - pl.col("p10")).mean()).alias("width80"),
    )
    tot = bt.group_by(by).agg(pl.len().alias("n_all"), (pl.col("status") != "nowcast").mean().alias("withheld"))
    g = g.join(tot, on=by, how="full", coalesce=True).sort(by)
    return g.with_columns((1 - pl.col("mae") / pl.col("mae_naive")).alias("skill_vs_naive"),
                          (1 - pl.col("mae") / pl.col("mae_inflate")).alias("skill_vs_inflate"))


def split_halves(bt: pl.DataFrame) -> pl.DataFrame:
    """Label each as-of week 'tuning' (first half) or 'holdout' (second half). Defaults were chosen on the tuning half only."""
    if not bt.height:
        return bt
    mid = (bt["asof_idx"].min() + bt["asof_idx"].max()) / 2
    return bt.with_columns(pl.when(pl.col("asof_idx") <= mid).then(pl.lit("tuning")).otherwise(pl.lit("holdout")).alias("period"))


# ====================================================================================================================
# 6. release gates
# ====================================================================================================================
def gates(bt: pl.DataFrame) -> dict:
    """Automatic release gates on the HOLD-OUT half, national + state series, delays 0..8 weeks, rows the method produced.
    G1 PIT audit passed (asserted inside every run); G2 beats the raw count on WAPE; G3 80% coverage within [0.65, 0.92]."""
    out = {"pit_audit": True, "label": LABEL}
    h = bt.filter((pl.col("period") == "holdout") & pl.col("level").is_in(["national", "state"]) & (pl.col("age_w") <= 8) & (pl.col("status") == "nowcast")) if bt.height else bt
    if not h.height:
        return {**out, "passed": False, "reason": "no hold-out rows"}
    wape = float((h["est"] - h["truth"]).abs().sum() / h["truth"].sum())
    wape_n = float((h["observed"] - h["truth"]).abs().sum() / h["truth"].sum())
    cov = float(((h["truth"] >= h["p10"]) & (h["truth"] <= h["p90"])).mean())
    out.update(holdout_wape=wape, holdout_wape_naive=wape_n, holdout_cov80=cov, beats_naive=wape < wape_n, coverage_ok=0.65 <= cov <= 0.92)
    out["passed"] = bool(out["beats_naive"] and out["coverage_ok"])
    return out
