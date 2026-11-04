"""Feature definitions, immutable versions, feature sets and validation (PIT, cardinality, dtype, privacy)."""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from ..config import get_settings
from ..core import db
from ..core.util import dumps, iso, loads, stable_hash, utcnow
from . import compute, pit

GROUPS = ["epidemiology", "climate", "holiday", "calendar", "population", "mobility"]

BUILTIN: list[dict] = [
    # epidemiology (case series of the feature set's disease)
    {"feature_id": "cases_lag_1w", "group": "epidemiology", "template": "lag", "unit": "log(1+cases)",
     "description": "Reported cases in the last completed week before issue (log1p).",
     "params": {"table": "cases", "column": "observed_cases", "lag_weeks": 1, "transform": "log1p"}},
    {"feature_id": "cases_lag_2w", "group": "epidemiology", "template": "lag", "unit": "log(1+cases)",
     "description": "Reported cases two weeks before issue (log1p).",
     "params": {"table": "cases", "column": "observed_cases", "lag_weeks": 2, "transform": "log1p"}},
    {"feature_id": "cases_lag_3w", "group": "epidemiology", "template": "lag", "unit": "log(1+cases)",
     "description": "Reported cases three weeks before issue (log1p).",
     "params": {"table": "cases", "column": "observed_cases", "lag_weeks": 3, "transform": "log1p"}},
    {"feature_id": "cases_lag_4w", "group": "epidemiology", "template": "lag", "unit": "log(1+cases)",
     "description": "Reported cases four weeks before issue (log1p).",
     "params": {"table": "cases", "column": "observed_cases", "lag_weeks": 4, "transform": "log1p"}},
    {"feature_id": "cases_mean_8w", "group": "epidemiology", "template": "rolling", "unit": "log(1+cases)",
     "description": "Mean weekly reported cases over the 8 completed weeks before issue (log1p).",
     "params": {"table": "cases", "column": "observed_cases", "end_lag_weeks": 1, "window_weeks": 8, "agg": "mean",
                "min_periods": 6, "transform": "log1p"}},
    {"feature_id": "cases_growth_2w", "group": "epidemiology", "template": "growth", "unit": "log ratio",
     "description": "Log growth of reported cases between lag 3 and lag 1.",
     "params": {"table": "cases", "column": "observed_cases", "lag_weeks": 1, "span_weeks": 2}},
    {"feature_id": "cases_same_week_last_year", "group": "epidemiology", "template": "seasonal_lag", "unit": "log(1+cases)",
     "description": "Reported cases in the same epi-week one year before the target week (log1p).",
     "params": {"table": "cases", "column": "observed_cases", "transform": "log1p"}},
    {"feature_id": "incidence_lag_1w", "group": "epidemiology", "template": "incidence", "unit": "cases per 100k",
     "description": "Last completed week's reported cases per 100,000 residents (population as of the prior year).",
     "params": {"table": "cases", "lag_weeks": 1}},
    # climate (reanalysis; released ~6 days after week end -> lag >= 2 weeks at a Wednesday issue)
    {"feature_id": "rain_sum_4w", "group": "climate", "template": "rolling", "unit": "mm",
     "description": "Total rainfall over the four fully released weeks ending two weeks before issue.",
     "params": {"table": "weather_weekly", "column": "rain_mm_sum", "end_lag_weeks": 2, "window_weeks": 4, "agg": "sum", "min_periods": 3}},
    {"feature_id": "rain_sum_8w_lag6", "group": "climate", "template": "rolling", "unit": "mm",
     "description": "Total rainfall over 8 weeks ending six weeks before issue (lagged vector response).",
     "params": {"table": "weather_weekly", "column": "rain_mm_sum", "end_lag_weeks": 6, "window_weeks": 8, "agg": "sum", "min_periods": 6}},
    {"feature_id": "temp_mean_4w", "group": "climate", "template": "rolling", "unit": "°C",
     "description": "Mean 2 m temperature over four released weeks ending two weeks before issue.",
     "params": {"table": "weather_weekly", "column": "t2m_mean", "end_lag_weeks": 2, "window_weeks": 4, "agg": "mean", "min_periods": 3}},
    {"feature_id": "temp_max_4w", "group": "climate", "template": "rolling", "unit": "°C",
     "description": "Maximum daily 2 m temperature over four released weeks ending two weeks before issue.",
     "params": {"table": "weather_weekly", "column": "t2m_max", "end_lag_weeks": 2, "window_weeks": 4, "agg": "max", "min_periods": 3}},
    {"feature_id": "rh_mean_4w", "group": "climate", "template": "rolling", "unit": "%",
     "description": "Mean relative humidity over four released weeks ending two weeks before issue.",
     "params": {"table": "weather_weekly", "column": "rh_mean", "end_lag_weeks": 2, "window_weeks": 4, "agg": "mean", "min_periods": 3}},
    # holiday (scheduled, known ahead)
    {"feature_id": "holiday_in_target_week", "group": "holiday", "template": "holiday_window", "unit": "count",
     "description": "National public holidays falling in the target week (known in advance).",
     "params": {"relative_to": "target", "start_offset_days": 0, "end_offset_days": 7}},
    {"feature_id": "holiday_prev_2w_of_target", "group": "holiday", "template": "holiday_window", "unit": "count",
     "description": "National public holidays in the two weeks before the target week.",
     "params": {"relative_to": "target", "start_offset_days": -14, "end_offset_days": 0}},
    # calendar
    {"feature_id": "season_sin", "group": "calendar", "template": "calendar", "unit": "unitless",
     "description": "Annual seasonality of the target week (sine).", "params": {"fn": "sin"}},
    {"feature_id": "season_cos", "group": "calendar", "template": "calendar", "unit": "unitless",
     "description": "Annual seasonality of the target week (cosine).", "params": {"fn": "cos"}},
    # population
    {"feature_id": "log_population", "group": "population", "template": "static_asof", "unit": "log(residents)",
     "description": "Resident population as of the year before issue (log).",
     "params": {"table": "population_annual", "column": "population", "transform": "log"}},
    # mobility — IBGE Census 2010 commuting integration (arranjos populacionais)
    {"feature_id": "commuter_pressure_lag_1w", "group": "mobility", "template": "neighbor_pressure", "unit": "weighted cases per 100k",
     "description": "Σ over the other municipalities j of the IBGE arranjo: commuting integration index_j × prior-week incidence_j "
                    "(per 100k). Proxy of exposure connectivity (Census 2010), not imported infections.",
     "params": {"lag_weeks": 1}},
]

# A deliberately unsafe draft shipped with the catalog so the PIT validator's refusal is visible in the UI.
UNSAFE_DRAFT = {"feature_id": "rain_sum_1w_lag1", "group": "climate", "template": "rolling", "unit": "mm",
                "description": "Rain in the week just before issue — reanalysis is not released yet at issue time (PIT test).",
                "params": {"table": "weather_weekly", "column": "rain_mm_sum", "end_lag_weeks": 1, "window_weeks": 1, "agg": "sum",
                           "min_periods": 1}}

ABLATION = {"cases_only": ["epidemiology", "calendar", "population"], "+climate": ["epidemiology", "calendar", "population", "climate"],
            "+holiday": ["epidemiology", "calendar", "population", "climate", "holiday"],
            "+mobility": ["epidemiology", "calendar", "population", "climate", "holiday", "mobility"]}


def _source_refs(template: str, params: dict) -> list[str]:
    t = params.get("table")
    if template in ("holiday_window",):
        return ["holiday_daily"]
    if template == "calendar":
        return []
    if template == "incidence":
        return ["cases", "population_annual"]
    if template == "neighbor_pressure":
        return ["commuting_arranjo", "cases", "population_annual"]
    return [t] if t else []


def definition_row(d: dict, version: int, state: str, owner: str) -> dict:
    lag_tables = _source_refs(d["template"], d["params"])
    now = iso(utcnow())
    body = {"template": d["template"], "params": d["params"]}
    return {
        "feature_id": d["feature_id"], "version": version, "owner_id": owner, "description": d["description"],
        "feature_group": d["group"], "template": d["template"], "params_json": dumps(d["params"]),
        "entity_keys": dumps(["municipality_id"]), "grain": "municipality × issue week" + (" × horizon" if compute.TEMPLATES[d["template"]]["level"] == "target" else ""),
        "dtype": "Float64", "unit": d.get("unit"), "source_refs": dumps(lag_tables),
        "availability_json": dumps({"valid_time": "event week", "available_at": "source release (vintage) or documented publication lag",
                                    "known_ahead": d["template"] in ("holiday_window", "calendar"),
                                    "lag_weeks": d["params"].get("lag_weeks", d["params"].get("end_lag_weeks"))}),
        "null_policy": "keep_null", "privacy_class": "aggregated", "diseases": dumps(["*"]), "state": state,
        "leakage_status": "unchecked", "validation_json": None, "hash": stable_hash(body), "created_at": now, "updated_at": now,
    }


def seed_features() -> None:
    if db.q1("SELECT 1 FROM feature_definition LIMIT 1"):
        return
    for d in BUILTIN:
        db.insert("feature_definition", definition_row(d, 1, "approved", "u_scientist"))
    db.insert("feature_definition", definition_row(UNSAFE_DRAFT, 1, "draft", "u_scientist"))


def get_def(feature_id: str, version: int | None = None) -> dict | None:
    if version is None:
        r = db.q1("SELECT * FROM feature_definition WHERE feature_id=? ORDER BY version DESC LIMIT 1", feature_id)
    else:
        r = db.q1("SELECT * FROM feature_definition WHERE feature_id=? AND version=?", feature_id, version)
    return _decode(r)


def _decode(r: dict | None) -> dict | None:
    if not r:
        return None
    r = db.jcol(r, "params_json", "entity_keys", "source_refs", "availability_json", "diseases", "validation_json")
    r["params"] = r.pop("params_json")
    return r


def list_defs(q: str | None = None, group: str | None = None, state: str | None = None) -> list[dict]:
    rows = [_decode(r) for r in db.q("SELECT * FROM feature_definition ORDER BY feature_group, feature_id, version")]
    latest: dict[str, dict] = {}
    for r in rows:
        latest[r["feature_id"]] = r
    out = list(latest.values())
    if q:
        ql = q.lower()
        out = [r for r in out if ql in r["feature_id"].lower() or ql in (r["description"] or "").lower()]
    if group:
        out = [r for r in out if r["feature_group"] == group]
    if state:
        out = [r for r in out if r["state"] == state]
    for r in out:
        r["versions"] = [x["version"] for x in rows if x["feature_id"] == r["feature_id"]]
        r["referenced_by"] = referenced_by(r["feature_id"])
    return out


def referenced_by(feature_id: str) -> list[str]:
    refs = []
    for fs in db.q("SELECT feature_set_id, version, member_versions_json FROM feature_set"):
        if any(m["feature_id"] == feature_id for m in loads(fs["member_versions_json"])):
            refs.append(f"{fs['feature_set_id']}@{fs['version']}")
    return refs


def validate_def(d: dict, disease_id: str = "dengue", weeks: int = 104) -> dict:
    """PIT, cardinality, dtype, null and privacy checks on a capped recent window (safe aggregated data only)."""
    s = get_settings()
    from ..sources import silver
    munis = s.pilot
    dw = silver.latest(compute.case_table(disease_id))
    if not dw.height:
        return {"status": "blocked", "checks": [{"check": "source_data", "passed": False, "detail": "no case data published"}]}
    last = dw["week_start"].max()
    start = last - timedelta(weeks=weeks)
    checks = []
    tpl = d["template"]
    if tpl not in compute.TEMPLATES:
        return {"status": "blocked", "checks": [{"check": "template_allowlisted", "passed": False, "detail": tpl}]}
    checks.append({"check": "template_allowlisted", "passed": True, "detail": tpl})
    # naive-join leak probe: would the declared lag read data that was not released at issue time?
    p = d["params"]
    table = p.get("table")
    if table and table != "population_annual" and tpl in ("lag", "rolling", "growth", "incidence"):
        t = compute._resolve_table(p, disease_id)
        lag_w = int(p.get("lag_weeks", p.get("end_lag_weeks", 1)))
        df = silver.all_vintages(t)
        if "disease_id" in df.columns:
            df = df.filter(pl.col("disease_id") == disease_id)
        df = df.filter(pl.col("municipality_id").is_in(munis))
        col = p.get("column", "observed_cases")
        c = pit.candidates(df, ["municipality_id"], "week_start", [col], "publication_lag")
        sp = compute.build_spine(munis, start, last).with_columns((pl.col("issue_week") - pl.duration(weeks=lag_w)).alias("_ev"))
        v = pit.naive_violations(sp, c, keys=["municipality_id"], spine_event="_ev", event_col="week_start")
        share = v / max(1, sp.height)
        checks.append({"check": "pit_available_at_le_issue_at", "passed": v == 0,
                       "detail": f"{v:,} of {sp.height:,} rows ({share:.0%}) would read a value released after the issue instant "
                                 f"(source publication lag ≈ {compute.TABLE_LAG_DAYS.get(t, '?')} days; declared lag {lag_w} week(s))",
                       "violations": v})
    try:
        frame, rep = compute.evaluate([{"feature_id": d["feature_id"], "template": tpl, "params": p}], disease_id, munis,
                                      start, last, [1, 2, 3, 4])
        fr = rep["features"][d["feature_id"]]
        if fr["status"] == "blocked":
            checks.append({"check": "source_available", "passed": False, "detail": fr["reason"]})
        else:
            checks.append({"check": "source_available", "passed": True, "detail": ", ".join(rep["sources"]) or "derived"})
            checks.append({"check": "cardinality_many_to_one", "passed": True, "detail": "join keys unique at municipality × week"})
            checks.append({"check": "dtype", "passed": frame[d["feature_id"]].dtype == pl.Float64, "detail": str(frame[d["feature_id"]].dtype)})
            checks.append({"check": "null_rate", "passed": fr["null_rate"] <= 0.5, "detail": f"{fr['null_rate']:.1%} null after PIT filtering",
                           "value": fr["null_rate"]})
            checks.append({"check": "approximate_availability", "passed": True,
                           "detail": f"{fr['approx_rows']:,} rows use the publication-lag approximation (PIT_UNVERIFIED)"})
    except pit.CardinalityError as e:
        checks.append({"check": "cardinality_many_to_one", "passed": False, "detail": str(e)})
    except compute.FeatureUnavailable as e:
        checks.append({"check": "source_available", "passed": False, "detail": str(e)})
    checks.append({"check": "privacy_class", "passed": True, "detail": "aggregated municipality-level inputs only"})
    hard = [c for c in checks if not c["passed"] and c["check"] not in ("null_rate",)]
    leak = any(c["check"] == "pit_available_at_le_issue_at" and not c["passed"] for c in checks)
    blocked_src = any(c["check"] == "source_available" and not c["passed"] for c in checks)
    status = "leak_blocked" if leak else ("source_missing" if blocked_src else ("failed" if hard else "pass"))
    return {"status": status, "checks": checks, "window": {"start": str(start), "end": str(last)}, "disease_id": disease_id,
            "validated_at": iso(utcnow())}


def record_validation(feature_id: str, version: int, res: dict) -> None:
    db.update("feature_definition", {"feature_id": feature_id, "version": version},
              {"leakage_status": res["status"], "validation_json": dumps(res), "updated_at": iso(utcnow())})


# ---------------------------------------------------------------- feature sets
def create_feature_set(feature_set_id: str, disease_id: str, members: list[str], description: str, actor: str) -> dict:
    defs = []
    for fid in members:
        d = get_def(fid)
        if not d:
            raise ValueError(f"unknown feature {fid}")
        defs.append({"feature_id": fid, "version": d["version"], "hash": d["hash"], "group": d["feature_group"]})
    prev = db.q1("SELECT MAX(version) v FROM feature_set WHERE feature_set_id=?", feature_set_id)
    version = (prev["v"] or 0) + 1
    h = stable_hash({"disease": disease_id, "members": [(m["feature_id"], m["version"], m["hash"]) for m in defs]})
    same = db.q1("SELECT version FROM feature_set WHERE feature_set_id=? AND hash=?", feature_set_id, h)
    if same:
        return get_feature_set(feature_set_id, same["version"])
    groups = {m["group"] for m in defs}
    ablation = {k: [m["feature_id"] for m in defs if m["group"] in v] for k, v in ABLATION.items() if set(v) <= groups | {"mobility"}}
    db.insert("feature_set", {"feature_set_id": feature_set_id, "version": version, "description": description,
                              "disease_id": disease_id, "member_versions_json": dumps(defs), "ablation_groups_json": dumps(ablation),
                              "status": "draft", "hash": h, "created_by": actor, "created_at": iso(utcnow())})
    return get_feature_set(feature_set_id, version)


def get_feature_set(fsid: str, version: int | None = None) -> dict | None:
    if version is None:
        r = db.q1("SELECT * FROM feature_set WHERE feature_set_id=? ORDER BY version DESC LIMIT 1", fsid)
    else:
        r = db.q1("SELECT * FROM feature_set WHERE feature_set_id=? AND version=?", fsid, version)
    if not r:
        return None
    r = db.jcol(r, "member_versions_json", "ablation_groups_json")
    r["members"] = r.pop("member_versions_json")
    r["ablation_groups"] = r.pop("ablation_groups_json")
    return r


def readiness(fs: dict) -> dict:
    """Per-feature readiness: published / blocked (source missing) / leak_blocked / draft / unchecked."""
    rows = []
    for m in fs["members"]:
        d = get_def(m["feature_id"], m["version"])
        state = d["state"]
        ls = d["leakage_status"]
        status = ("blocked" if ls in ("source_missing",) else "leak_blocked" if ls == "leak_blocked" else
                  "draft" if state != "approved" else "pit_safe" if ls == "pass" else "unchecked")
        rows.append({"feature_id": m["feature_id"], "version": m["version"], "group": m["group"], "status": status,
                     "leakage_status": ls, "state": state})
    return {"members": rows, "ready": all(r["status"] in ("pit_safe", "blocked") for r in rows),
            "blocked": [r["feature_id"] for r in rows if r["status"] == "blocked"],
            "unsafe": [r["feature_id"] for r in rows if r["status"] in ("leak_blocked", "draft", "unchecked")]}


def seed_feature_sets(actor: str = "u_scientist") -> list[dict]:
    out = []
    base = [d["feature_id"] for d in BUILTIN]
    for disease, fsid in (("dengue", "br_dengue"), ("chikungunya", "br_chikungunya")):
        if not get_feature_set(fsid):
            out.append(create_feature_set(fsid, disease, base, f"Pilot {disease} feature set (cases, climate, holidays, calendar, population, mobility)", actor))
    return out


def iso_week_bounds(df: pl.DataFrame) -> tuple[date, date]:
    return df["week_start"].min(), df["week_start"].max()
