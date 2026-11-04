"""Grounded prompts for the Viet Nam dashboard and simulator. The model only sees a compact JSON of verified numbers
from this app; the system prompt fixes the labelling and decision-support rules. A rule-based brief is used when no
LLM key is configured."""
from __future__ import annotations

import json

SYSTEM = """You are the decision-support analyst inside HealthPulse, a dengue preparedness tool for Viet Nam's 34 provinces \
(the provincial units in force since 1 July 2025). Public-health planners read your briefs to decide where to send rapid-response \
teams, staff and vector-control effort.

Ground rules:
- Use only the numbers in the CONTEXT JSON. If something is not in it, say it is not available; do not guess.
- Keep each number's label: OBSERVED (reported), MODEL ESTIMATE (climate model, calibrated to WHO national totals), \
SCENARIO (simulation under assumptions), ESTIMATE (beds and staff from national WHO densities × population). Never present an \
estimate or a scenario as a report.
- Give uncertainty where the context has it (P10–P90).
- Do not claim lives saved, deaths averted or programme effects. Simulated differences come from the stated assumptions.
- Recommendations are options for human review, not orders. For each one, name the province(s), the lever (teams, surge staff, \
beds, vector control, mobility), the expected effect from the context, and the trade-off.
- Be concise and concrete. Use short markdown sections: **Situation**, **Where the pressure is**, **Workforce & capacity**, \
**Options to consider**, **Data caveats**. Use bullet points, at most ~350 words unless the user asks for more.
- On the outbreak sandbox screen (CONTEXT.screen = "sandbox") the pathogen is whatever CONTEXT.pathogen says, not \
necessarily dengue; brief on its spread, hospital pressure, interventions so far and what to try next.
- Answer in the language requested in CONTEXT.lang (vi = Vietnamese, en = English). Keep province names in Vietnamese."""


def _r(x, d=1):
    return None if x is None else round(float(x), d)


def dashboard_context(state: dict, info: dict, nat_recent: list[dict], lang: str) -> dict:
    provs = state["provinces"]
    top = [p for p in provs if p["level"] in ("alert", "warning", "watch")][:10] or provs[:6]
    return {
        "lang": lang, "screen": "dashboard", "month": state["month"], "semantics": "MODEL ESTIMATE",
        "alert_counts": state["counts"],
        "national": {"estimate_cases_month": _r(state["national_estimate"], 0), "seasonal_norm": _r(state["national_normal"], 0)},
        "provinces_flagged": [{"name": p["name"], "level": p["level"], "est_cases": _r(p["estimate"], 0),
                               "per_100k": _r(p["incidence_per_100k"]), "ratio_to_norm": _r(p["ratio"], 2),
                               "trend_vs_last_month": _r(p["trend"], 2), "why": p["reason"]} for p in top],
        "model": {"holdout_2010": {k: _r(v, 2) for k, v in info["holdout_2010"].items()},
                  "national_shape_corr": _r(info.get("national_shape_corr_2011_on"), 2), "kappa": _r(info["kappa"], 2),
                  "kappa_basis": info["kappa_basis"], "latest_weather_month": info["latest_weather_month"]},
        "latest_national_reports_OBSERVED": nat_recent[-6:],
        "caveats": ["Province-level reports in this app end in 2010 (Project Tycho via OpenDengue); later provincial values are "
                    "model estimates calibrated to WHO national totals.", "National WHO reports end " +
                    (nat_recent[-1]["t"] if nat_recent else "unknown") + "."],
    }


def sim_context(res: dict, lang: str) -> dict:
    provs = res["provinces"]
    b, p = res["baseline"], res["plan"]
    names = [x["name"] for x in provs]
    order = sorted(range(len(provs)), key=lambda i: -b["cum_unmet_by_province"][i])[:8]
    teams = p.get("teams", [])
    dest = {}
    for t in teams:
        for seg in t["log"]:
            if seg["state"] == "deployed":
                n = names[[x["province_id"] for x in provs].index(seg["at"])]
                dest[n] = dest.get(n, 0) + 1
    s = res["spec"]
    return {
        "lang": lang, "screen": "simulation", "semantics": "SCENARIO", "start_month": res["start_month"], "days": res["days"],
        "assumptions": {k: s[k] for k in ("transmission", "seasonality", "mobility", "mobility_reduction", "reporting_rate", "hosp_rate",
                                          "los_days", "dengue_bed_share", "dengue_nurse_share", "surge_pct", "teams", "team_capacity",
                                          "team_vector_effect", "dispatch", "outbreak_province", "outbreak_infections")},
        "baseline_no_teams": b["kpi"], "plan_with_teams": p["kpi"], "avoided_unmet_bed_days": res["avoided_unmet_bed_days"],
        "most_pressured_provinces": [{"name": names[i], "unmet_bed_days_baseline": b["cum_unmet_by_province"][i],
                                      "unmet_bed_days_plan": p["cum_unmet_by_province"][i], "p_overload_baseline": b["p_overload"][i],
                                      "p_overload_plan": p["p_overload"][i], "capacity_slots": provs[i]["base_capacity"],
                                      "binding_constraint": provs[i]["binding"]} for i in order],
        "team_deployments_by_province": dest,
        "first_events": [e["text"] for e in p.get("events", [])[:25]],
        "basis": res["basis"],
    }


def user_prompt(ctx: dict, question: str | None) -> str:
    q = question.strip() if question else ("Write the planning brief for this screen." if ctx["screen"] == "dashboard"
                                          else "Explain this scenario and the trade-offs of the team plan.")
    return f"CONTEXT:\n```json\n{json.dumps(ctx, ensure_ascii=False, default=str)}\n```\n\nQUESTION: {q}"


def rule_based(ctx: dict) -> str:
    vi = ctx["lang"] == "vi"
    if ctx["screen"] == "dashboard":
        c = ctx["alert_counts"]
        fl = ctx["provinces_flagged"][:5]
        head = (f"**Tình hình ({ctx['month']}, ƯỚC TÍNH MÔ HÌNH)**: {c.get('alert', 0)} tỉnh báo động, {c.get('warning', 0)} cảnh báo, "
                f"{c.get('watch', 0)} theo dõi. Cả nước ước tính {ctx['national']['estimate_cases_month']:,} ca/tháng "
                f"(mức mùa thông thường {ctx['national']['seasonal_norm']:,})." if vi else
                f"**Situation ({ctx['month']}, MODEL ESTIMATE)**: {c.get('alert', 0)} provinces on alert, {c.get('warning', 0)} warning, "
                f"{c.get('watch', 0)} watch. National estimate {ctx['national']['estimate_cases_month']:,} cases this month "
                f"(seasonal norm {ctx['national']['seasonal_norm']:,}).")
        lines = [f"- **{p['name']}** — {p['level']}: {p['why']}" for p in fl]
        tail = ("\n\n_Bản tóm tắt theo quy tắc — chưa cấu hình khoá LLM trong .env._" if vi else
                "\n\n_Rule-based brief — no LLM key configured in .env._")
        return head + "\n\n" + "\n".join(lines) + tail
    b, p = ctx["baseline_no_teams"], ctx["plan_with_teams"]
    av = ctx["avoided_unmet_bed_days"]
    top = ctx["most_pressured_provinces"][:4]
    if vi:
        s = (f"**Kịch bản {ctx['days']} ngày từ {ctx['start_month']} (KỊCH BẢN)**: không có đội phản ứng nhanh, thiếu "
             f"{b['unmet_bed_days']['p50']:,.0f} ngày-giường (P10–P90 {b['unmet_bed_days']['p10']:,.0f}–{b['unmet_bed_days']['p90']:,.0f}); "
             f"với {ctx['assumptions']['teams']} đội: {p['unmet_bed_days']['p50']:,.0f}. Giảm ước tính {av['p50']:,.0f} ngày-giường.\n\n")
        s += "\n".join(f"- **{t['name']}**: thiếu {t['unmet_bed_days_baseline']:,.0f} → {t['unmet_bed_days_plan']:,.0f} ngày-giường, ràng buộc: {t['binding_constraint']}" for t in top)
        return s + "\n\n_Bản tóm tắt theo quy tắc — chưa cấu hình khoá LLM trong .env._"
    s = (f"**{ctx['days']}-day scenario from {ctx['start_month']} (SCENARIO)**: without teams, "
         f"{b['unmet_bed_days']['p50']:,.0f} unmet bed-days (P10–P90 {b['unmet_bed_days']['p10']:,.0f}–{b['unmet_bed_days']['p90']:,.0f}); "
         f"with {ctx['assumptions']['teams']} teams, {p['unmet_bed_days']['p50']:,.0f}. Simulated reduction {av['p50']:,.0f} bed-days "
         f"(P10–P90 {av['p10']:,.0f}–{av['p90']:,.0f}).\n\n")
    s += "\n".join(f"- **{t['name']}**: {t['unmet_bed_days_baseline']:,.0f} → {t['unmet_bed_days_plan']:,.0f} unmet bed-days; binding: {t['binding_constraint']}" for t in top)
    return s + "\n\n_Rule-based brief — no LLM key configured in .env._"
