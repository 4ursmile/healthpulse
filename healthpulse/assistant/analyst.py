"""Grounded analyst (spec §6). Every number it states comes from a typed tool reading verified outputs; it never
computes a forecast, a capacity or an efficacy itself. This implementation is deterministic (rule-based composition
over tool results) so it runs offline; an LLM can be placed behind the same tool gate and output policy.
"""
from __future__ import annotations

import re

import polars as pl

from ..core import db, lake
from ..core.util import loads
from ..ml import registry as mreg
from ..simulation import jobs as sim
from ..sources import catalog, silver

OUTCOME_CLAIMS = re.compile(r"\b(lives?|deaths?|infections?|cases?)\b.*\b(sav|prevent|avert)", re.I)
ALLOCATE = re.compile(r"\b(allocate|add|deploy|send)\b.*\b(\d+)\b.*\bteams?\b", re.I)
BUDGET = re.compile(r"(?:brl|r\$)\s*([\d.,]+)|([\d.,]+)\s*(?:brl|reais)", re.I)


# ---------------------------------------------------------------- tools (read-only, typed)
def get_forecast(forecast_id: str, municipality_id: str | None = None) -> dict:
    f = sim.get_forecast(forecast_id)
    if not f:
        return {"error": "unknown forecast"}
    F = sim.forecast_frame(forecast_id)
    if municipality_id:
        F = F.filter(pl.col("municipality_id") == municipality_id)
    return {"forecast_id": forecast_id, "quality": f["quality_status"], "model_version": f["provenance_json"].get("model_version"),
            "rows": F.select("municipality_id", "target_week", "horizon", "p10", "p50", "p90", "quality_status").to_dicts(),
            "signal_type": F["signal_type"][0] if F.height else None, "disease_id": f["disease_id"]}


def get_region_context(municipality_id: str, disease_id: str) -> dict:
    t = "sari_weekly" if disease_id.endswith("_sari") else "disease_weekly"
    df = silver.latest(t)
    df = df.filter((pl.col("disease_id") == disease_id) & (pl.col("municipality_id") == municipality_id)).sort("week_start") if df.height else df
    dim = silver.latest("municipality_dim").filter(pl.col("municipality_id") == municipality_id)
    w = silver.latest("weather_weekly")
    w = w.filter(pl.col("municipality_id") == municipality_id).sort("week_start") if w.height else w
    last8 = df.tail(8).select("week_start", "observed_cases").to_dicts()
    same_ly = None
    if df.height:
        lw = df["week_start"].max()
        ly = df.filter(pl.col("week_start") == lw - pl.duration(weeks=52))
        same_ly = int(ly["observed_cases"][0]) if ly.height and ly["observed_cases"][0] is not None else None
    rain = w.tail(6).select("week_start", "rain_mm_sum", "t2m_mean").to_dicts() if w.height else []
    return {"name": dim["name"][0] if dim.height else municipality_id, "recent_weeks": last8, "same_week_last_year": same_ly,
            "recent_climate": rain, "source_vintages": sorted(set(df["vintage_id"].to_list())) if df.height else []}


def get_feature_attribution(forecast_id: str) -> dict:
    f = sim.get_forecast(forecast_id)
    m = db.q1("SELECT run_id FROM model_registry WHERE model_id=? AND version=?", f["model_id"], f["model_version"])
    rep = mreg.run_report(m["run_id"]) if m else None
    imp = (rep or {}).get("importance")
    return imp or {"features": [], "caveat": "no attribution recorded for this model"}


def get_scenario_comparison(run_id: str) -> dict:
    r = sim.get_run(run_id)
    if not r or r["status"] != "completed":
        return {"error": "run not completed"}
    return {"run_id": run_id, "summary": r["summary_json"], "provenance": r["provenance_json"], "warnings": r["warnings_json"]}


def get_data_provenance(forecast_id: str) -> dict:
    f = sim.get_forecast(forecast_id)
    return f["provenance_json"] if f else {}


def get_nowcast(municipality_id: str, disease_id: str) -> dict:
    """Latest published nowcast (SARI diseases only) for the place, or {} — never computed here, only read."""
    from ..ml import nowcast_jobs as nj
    if not disease_id.endswith("_sari"):
        return {}
    run = nj.latest_published(disease_id)
    if not run:
        return {}
    ser = nj.series(run["nowcast_id"], disease_id, municipality_id=municipality_id, weeks=12)
    est = [r for r in ser["rows"] if r["status"] == "nowcast"]
    if not est:
        return {}
    return {"nowcast_id": run["nowcast_id"], "asof_cutoff": run["asof_cutoff"], "unit_level": ser["unit_level"], "unit_id": ser["unit_id"],
            "row": est[-1], "label": run["label"]}


def _name(mid: str) -> str:
    dim = silver.latest("municipality_dim")
    r = dim.filter(pl.col("municipality_id") == mid) if dim.height else dim
    return r["name"][0] if r.height else mid


# ---------------------------------------------------------------- composition
def _fmt(x: float | None) -> str:
    return "—" if x is None else f"{x:,.0f}"


def explain(question: str, *, disease_id: str = "dengue", forecast_id: str | None = None, municipality_id: str | None = None,
            run_id: str | None = None) -> dict:
    q = question.strip()
    cites: list[dict] = []
    caveats: list[str] = []
    recs: list[dict] = []
    tools_used: list[str] = []
    # policy: outcome claims are refused and redirected to measurable indicators
    if OUTCOME_CLAIMS.search(q):
        return {"answer": ("That outcome can't be supported by the current data. HealthPulse forecasts reported notifications and "
                           "simulates service shortfall under stated assumptions; it has no validated model of how resources change "
                           "illness or deaths. I can instead report the simulated change in unmet visits, the number of regions over "
                           "assumed capacity, and the cost under the scenario's assumptions."),
                "intent": "refused_outcome_claim", "citations": [], "recommendations": [], "caveats": [], "tools": [],
                "confidence": "not_applicable"}
    m = ALLOCATE.search(q)
    if m:
        teams = int(m.group(2))
        b = BUDGET.search(q)
        budget = float((b.group(1) or b.group(2)).replace(".", "").replace(",", "")) if b else None
        draft = {"name": f"Draft: allocate {teams} additional teams", "mode": "operational", "forecast_id": forecast_id,
                 "disease_id": disease_id, "region_ids": [municipality_id] if municipality_id else [],
                 "resources": {"mobile_teams_available": teams, **({"total_budget_brl": budget} if budget is not None else {})}}
        return {"answer": (f"I drafted a scenario with {teams} mobile teams" + (f" and a BRL {budget:,.0f} budget" if budget else "")
                           + ". Nothing has been computed yet: review the constraints and assumptions in the Simulation Studio and press "
                             "Run — only the optimizer computes allocations."),
                "intent": "draft_scenario", "draft_scenario": draft, "citations": [{"forecast_id": forecast_id}] if forecast_id else [],
                "recommendations": [], "caveats": ["Visit rates and capacities are planner assumptions until verified."],
                "tools": [], "confidence": "draft_requires_review"}
    if run_id:
        tools_used.append("get_scenario_comparison")
        c = get_scenario_comparison(run_id)
        if "error" in c:
            return {"answer": "That simulation has not completed, so there is nothing verified to explain yet.", "intent": "tradeoff",
                    "citations": [], "recommendations": [], "caveats": [], "tools": tools_used, "confidence": "unavailable"}
        s = c["summary"]
        ub, up = s["unmet_visits_baseline"], s["unmet_visits_proposed"]
        av = s["avoided_shortfall"]
        moved = sorted(s.get("assignments") or [], key=lambda x: -x["teams"])[:3]
        where = "; ".join(f"{a['teams']} to {_name(a['municipality_id'])} ({a['travel_minutes']:.0f} min from the {_name(a['base'])} base)" for a in moved) or "no teams were assigned"
        binding = ", ".join(s["optimizer"].get("binding_constraints") or []) or "none reported"
        ans = (f"Under this scenario's assumptions the baseline leaves a median of {_fmt(ub['p50'])} unmet {s['demand_unit']} "
               f"(P10–P90 {_fmt(ub['p10'])}–{_fmt(ub['p90'])}) over {s['weeks']} weeks; the proposal leaves {_fmt(up['p50'])} "
               f"({_fmt(up['p10'])}–{_fmt(up['p90'])}), a simulated reduction of {_fmt(av['p50'])} unmet visits at BRL {s['cost_brl']:,.0f}. "
               f"The optimizer ({s['optimizer']['method']}) placed teams where the expected shortfall per added team, weighted by the access "
               f"gap, was largest: {where}. Binding constraints: {binding}. Regions over assumed capacity: "
               f"{s['regions_over_capacity_baseline']} → {s['regions_over_capacity_proposed']}.")
        cites = [{"run_id": run_id}, {"forecast_id": c["provenance"].get("forecast_id")}]
        caveats = list(c["warnings"] or []) + ["Scenario estimate, not observed impact."]
        recs.append({"scope": {"run_id": run_id}, "observation": f"median unmet {_fmt(ub['p50'])} → {_fmt(up['p50'])}",
                     "supporting_evidence": [run_id, c["provenance"].get("forecast_id")],
                     "suggested_action": "Review readiness of the assigned mobile teams with the regional coordinator",
                     "rationale": "largest weighted shortfall reduction per team under the listed constraints",
                     "estimated_effect": {"metric": "unmet_visits", "value": av["p50"], "status": "scenario_estimate"},
                     "uncertainties": ["visit rate and capacity are planner inputs", "forecast interval calibration"], "requires_review": True})
        return {"answer": ans, "intent": "tradeoff", "citations": cites, "recommendations": recs, "caveats": caveats,
                "tools": tools_used, "confidence": "grounded_in_run"}
    if forecast_id and municipality_id:
        tools_used += ["get_forecast", "get_region_context", "get_feature_attribution", "get_data_provenance"]
        fc = get_forecast(forecast_id, municipality_id)
        ctx = get_region_context(municipality_id, fc["disease_id"])
        imp = get_feature_attribution(forecast_id)
        prov = get_data_provenance(forecast_id)
        label = catalog.DISEASES[fc["disease_id"]]["metric_label"].lower()
        rows = sorted(fc["rows"], key=lambda r: r["horizon"])
        if not rows:
            return {"answer": "This forecast has no rows for the selected municipality.", "intent": "why", "citations": [],
                    "recommendations": [], "caveats": [], "tools": tools_used, "confidence": "unavailable"}
        r1, r4 = rows[0], rows[-1]
        recent = [w["observed_cases"] for w in ctx["recent_weeks"] if w["observed_cases"] is not None]
        trend = ""
        if len(recent) >= 4:
            a, b = sum(recent[-2:]) / 2, sum(recent[-4:-2]) / 2
            trend = (f" The last two released weeks averaged {a:,.0f} {label}, against {b:,.0f} in the two weeks before.")
        ly = f" The same week last year had {ctx['same_week_last_year']:,} reported cases." if ctx["same_week_last_year"] is not None else ""
        top = [f["feature"] for f in (imp.get("features") or [])[:3]]
        drivers = (f" The model's strongest predictive associations are {', '.join(top)} — associations, not causes.") if top else ""
        rain = ctx["recent_climate"]
        rtxt = ""
        if rain:
            rr = [x["rain_mm_sum"] for x in rain if x["rain_mm_sum"] is not None]
            if rr:
                rtxt = f" Observed rainfall over the last {len(rr)} released weeks totalled {sum(rr):,.0f} mm (reanalysis)."
        if r1["quality_status"] != "ok":
            head = f"The model returned insufficient data for {ctx['name']}; no point estimate is shown."
        else:
            head = (f"Model estimate for {ctx['name']}: {_fmt(r1['p50'])} {label} in the week of {r1['target_week']} "
                    f"(P10–P90 {_fmt(r1['p10'])}–{_fmt(r1['p90'])}), and {_fmt(r4['p50'])} ({_fmt(r4['p10'])}–{_fmt(r4['p90'])}) "
                    f"{r4['horizon']} weeks ahead.")
        nowc = get_nowcast(municipality_id, fc["disease_id"])
        ntxt = ""
        if nowc:
            tools_used.append("get_nowcast")
            nr = nowc["row"]
            where = "" if nowc["unit_level"] == "municipality" else f" (at {nowc['unit_level']} level, {nowc['unit_id']})"
            ntxt = (f" The most recent weeks are still being filled in: for the week of {nr['week_start']} {nr['observed']:,.0f} cases are recorded so far and the "
                    f"nowcast of the eventual count is {nr['nowcast_p50']:,.0f} (80% interval {nr['p10']:,.0f}–{nr['p90']:,.0f}){where}.")
        ans = head + trend + ntxt + ly + rtxt + drivers
        cites = [{"forecast_id": forecast_id}, {"model_version": fc["model_version"]}] + [{"source_vintage_id": v} for v in prov.get("source_vintage_ids", [])[:4]]
        if nowc:
            cites.append({"nowcast_id": nowc["nowcast_id"]})
        caveats = [f"Forecast quality {fc['quality']}", "Reported notifications are revised after publication.",
                   imp.get("caveat") or "Feature importance is not causal attribution."]
        if nowc:
            caveats.append(f"Nowcast {nowc['label']}: delay reconstructed from entry/closure dates; it corrects an observed count, it is not a forecast.")
        recs.append({"scope": {"region_id": municipality_id, "disease": fc["disease_id"]},
                     "observation": head, "supporting_evidence": [forecast_id] + prov.get("source_vintage_ids", [])[:2],
                     "suggested_action": "Review field-team readiness and confirm operational capacity for the coming weeks",
                     "rationale": "forecast demand compared with planner-confirmed capacity", "estimated_effect":
                     {"metric": "unmet_visits", "value": None, "status": "not_estimated"},
                     "uncertainties": ["interval calibration on held-out weeks", "reporting delays"], "requires_review": True})
        return {"answer": ans, "intent": "why", "citations": cites, "recommendations": recs, "caveats": caveats,
                "tools": tools_used, "confidence": "grounded_in_forecast"}
    return {"answer": ("Select a municipality with a published forecast, or a completed simulation run, and ask about it. I only "
                       "report numbers from verified forecasts and simulation outputs."),
            "intent": "need_context", "citations": [], "recommendations": [], "caveats": [], "tools": [], "confidence": "unavailable"}
