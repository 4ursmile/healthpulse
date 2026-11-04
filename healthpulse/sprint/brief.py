"""Grounded brief for the Story screen (one state): the 2023-24 replay, the 2026-27 season forecast and the benchmark,
as one compact CONTEXT JSON. The LLM only sees that JSON; `rule_based` writes the same brief without an LLM."""
from __future__ import annotations

import json

from . import run as sr
from . import data as D

SYSTEM = """You are the decision-support analyst inside HealthPulse, a dengue early-warning and preparedness tool for \
Brazil's state health departments. A state planner reads your brief and then approves or rejects a preparedness proposal.

Ground rules:
- Use only the numbers in the CONTEXT JSON. If something is not in it, say it is not available; do not guess.
- Label every number: OBSERVED (reported probable cases), REPLAY (what the alert rule would have done on past data), \
FORECAST (season model with intervals). Never present a forecast as a report.
- The season forecast is weak at predicting the size of a season (CONTEXT.benchmark): say so when you use it, and give its \
interval (P10-P90 or P5-P95), not only the median.
- Do not claim lives saved or cases averted.
- Recommendations are options for human review, not orders. Tie each to a trigger from the context (for example, the \
week the alert rule fires) and name the trade-off (cost of a false alarm vs a late response).
- Short markdown sections: **Situation**, **What the 2023-24 replay shows**, **2026-27 outlook**, **Proposal for approval**, \
**Caveats**. At most ~300 words. Answer in CONTEXT.lang (en = English, pt = Portuguese)."""


def _f(x, d=0):
    return None if x is None else round(float(x), d)


def context(uf: str, lang: str = "en") -> dict:
    uf = uf.upper()
    rep = sr.load_report() or {}
    retro_p = D.root() / "retro_2023_24.json"
    retro = json.loads(retro_p.read_text()) if retro_p.exists() else {}
    st = next((x for x in retro.get("states_2023_24", []) if x["uf"] == uf), None)
    tgt = next((x for x in rep.get("target_forecast", []) if x["uf"] == uf), None)
    band = None
    p = D.root() / "submission" / sr.CHAMPION / f"{D.TARGET_SEASON}-{D.TARGET_SEASON + 1}" / f"{uf}.csv"
    if p.exists():
        import polars as pl
        fc = pl.read_csv(p)
        band = {"season_total_p50": _f(fc["pred"].sum()), "weekly_80pct_interval_at_highest": [_f(fc["lower_80"].max()), _f(fc["upper_80"].max())]}
    board = {b["model"]: b for b in rep.get("leaderboard", [])}
    champ, ref = board.get(sr.CHAMPION, {}), board.get("climatology", {})
    hist = retro.get("history_2015_2022", {})
    return {
        "screen": "story", "lang": lang, "state": uf,
        "replay_2023_24": None if not st else {
            "label": "REPLAY", "incidence_per_100k_observed": _f(st["incidence"]),
            "crossed_epidemic_level_week": st["crossing_date"], "status_quo_would_see_it": st["seen_by_status_quo"],
            "healthpulse_alert_week": st["alert_date"], "weeks_gained": st["lead_weeks"],
            "weeks_gained_flat_rule": st.get("lead_weeks_flat"), "peak_week_observed": st["peak_date"]},
        "alert_rule_track_record_2015_2022": {
            "label": "REPLAY", "epidemics": hist.get("epidemic"), "missed": hist.get("missed"),
            "median_weeks_gained": hist.get("lead_weeks_median"), "false_alarm_rate": _f(hist.get("false_alarm_rate"), 3)},
        "outlook_2026_27": None if not tgt else {
            "label": "FORECAST", "made_with_data_to": "EW25 2026", "season_total_median": _f(tgt["season_total_median"]),
            "peak_week_median": tgt["peak_week"], "peak_weekly_cases_median": _f(tgt["peak_median"]), **(band or {})},
        "benchmark": {"imdc_validation_tests": "2022-23 .. 2025-26, all states except ES",
                      "champion_rel_wis_vs_climatology": _f(champ.get("rel_wis"), 2),
                      "champion_mean_wis": _f(champ.get("mean_wis")), "climatology_mean_wis": _f(ref.get("mean_wis")),
                      "note": "beats climatology on case-weighted WIS, not when every state counts equally; season size is "
                              "largely unpredictable in June"},
        "definitions": retro.get("definitions", {}),
    }


def rule_based(ctx: dict) -> str:
    uf, r, o, h = ctx["state"], ctx["replay_2023_24"], ctx["outlook_2026_27"], ctx["alert_rule_track_record_2015_2022"]
    out = ["_Resumo por regras disponível apenas em inglês; o analista de IA responde em português._"] if ctx.get("lang") == "pt" else []
    out.append(f"**Situation** — {uf}, dengue (probable cases).")
    if r:
        if r["crossed_epidemic_level_week"]:
            out.append(f"**What the 2023-24 replay shows** — {uf} reached {r['incidence_per_100k_observed']:,.0f}/100k (OBSERVED) and "
                       f"passed the epidemic level in the week of {r['crossed_epidemic_level_week']}. Threshold monitoring would have seen it "
                       f"on {r['status_quo_would_see_it']}; the HealthPulse alert rule fired on {r['healthpulse_alert_week']}, "
                       f"**{r['weeks_gained']} weeks earlier** (REPLAY; a flat carry-forward rule: {r['weeks_gained_flat_rule']}).")
        else:
            out.append(f"**What the 2023-24 replay shows** — {uf} stayed below the epidemic level "
                       f"({r['incidence_per_100k_observed']:,.0f}/100k OBSERVED)"
                       + (f"; the rule raised a false alarm on {r['healthpulse_alert_week']}." if r["healthpulse_alert_week"] else "; no alert was raised."))
    if h.get("epidemics"):
        out.append(f"Over 2015-2022 the same rule caught {h['epidemics'] - h['missed']} of {h['epidemics']} epidemics, a median "
                   f"{h['median_weeks_gained']:.0f} weeks early, with a {h['false_alarm_rate']:.0%} false-alarm rate.")
    if o:
        pk = o.get("weekly_80pct_interval_at_highest") or [None, None]
        out.append(f"**2026-27 outlook** (FORECAST, data to EW25 2026) — median season total {o['season_total_median']:,.0f} cases, "
                   f"peak around {o['peak_week_median']} at about {o['peak_weekly_cases_median']:,.0f}/week"
                   + (f" (the weekly 80% interval reaches {pk[0]:,.0f}-{pk[1]:,.0f} at its highest)." if pk[0] is not None else ".")
                   + " Season size is weakly predictable in June, so treat this as the planning baseline, not the trigger.")
    out.append("**Proposal for approval** — switch on the weekly alert rule for this state from EW41 and pre-agree the response "
               "package (vector-control teams, surge staffing, hydration units) to be activated the week it fires. "
               + (f"Trade-off: about one false mobilisation every {round(1 / h['false_alarm_rate'])} non-epidemic seasons "
                  "(2015-2022 replay), against weeks of lead time." if h.get("false_alarm_rate") else
                  "Trade-off: an occasional false mobilisation against weeks of lead time."))
    out.append("**Caveats** — replay on final-vintage counts with a 2-week reporting delay; the event is the 300/100k incidence "
               "level, not a decree; no hospital-load forecast.\n\n_Rule-based brief — AI not configured._")
    return "\n\n".join(out)


def user_prompt(ctx: dict, question: str | None) -> str:
    q = question or "Write the brief and the proposal for approval."
    return f"CONTEXT:\n```json\n{json.dumps(ctx, ensure_ascii=False, default=str)}\n```\n\nQUESTION: {q}"
