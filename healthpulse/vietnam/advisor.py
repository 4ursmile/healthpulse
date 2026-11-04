"""Sandbox advisor: proposes interventions from the live sandbox state.

`rule_actions` is the transparent rule-based advisor (used when no LLM is configured or the LLM fails, and by the
autopilot). `context` is the compact, verified state the LLM advisor sees. Every proposal is validated as an `Action`
before it reaches the user, so an LLM can never invent a lever or a province.
"""
from __future__ import annotations

import json
import re

import numpy as np

from .sandbox import ACTION_HELP, PIDS, Action, Session


def _f(s: Session):
    return s.history[-1]


def rule_actions(s: Session) -> list[Action]:
    f = _f(s)
    p, pa = f["prov"], s.path
    N = np.maximum(s.N, 1)
    act = np.array(p["active"], float)
    per100k = act / N * 1e5
    rt = np.array(p["rt"], float)
    load = np.array(p["hosp"], float) / np.maximum(np.array(p["cap"], float), 1)
    out: list[Action] = []
    name = lambda i: s.provs[PIDS[i]]["name"]  # noqa: E731
    idle = sum(1 for t in s.teams if not t.deployed)
    # 1. hospital overload -> teams, then staff from a quiet province
    over = [i for i in np.argsort(-load) if load[i] >= 0.9]
    for i in over[:2]:
        if idle:
            n = min(idle, 2 if load[i] >= 1.2 else 1)
            out.append(Action(type="team", province_id=PIDS[i], value=n,
                              why=f"{name(i)} hospitals at {load[i]:.0%} of capacity; each team adds {s.spec.workforce.team_capacity} slots"))
            idle -= n
        quiet = [j for j in np.argsort(s.st["D"][i]) if j != i and load[j] < 0.3 and s.nurses[j] > 500][:1]
        for j in quiet:
            n = round(float(s.nurses[j] * 0.04), -1)
            if n >= 20:
                out.append(Action(type="staff", province_id=PIDS[j], to_id=PIDS[i], value=n,
                                  why=f"{name(j)} is at {load[j]:.0%} load and is the nearest quiet province"))
        if s.surge[i] < 0.3:
            out.append(Action(type="surge", province_id=PIDS[i], value=0.3, why=f"overtime buys capacity in {name(i)} within days"))
    # 2. fast local growth -> distancing; lift where the outbreak has turned
    for i in np.argsort(-per100k)[:3]:
        if rt[i] > 1.15 and per100k[i] > 30 and s.contact[i] > 0.75:
            out.append(Action(type="lockdown", province_id=PIDS[i], value=0.35,
                              why=f"R ≈ {rt[i]:.2f} and {per100k[i]:.0f} active infections per 100k in {name(i)}"))
    for i in range(len(PIDS)):
        if s.contact[i] < 1 and rt[i] < 0.85 and per100k[i] < 10:
            out.append(Action(type="lockdown", province_id=PIDS[i], value=0,
                              why=f"R ≈ {rt[i]:.2f} and few active infections in {name(i)}: restrictions cost more than they save"))
    # 3. vaccination where growth is (if a vaccine exists)
    if pa["vax_inf"] > 0 or pa["vax_sev"] > 0:
        running = {int(i) for c in s.campaigns for i in c[0]}
        cand = [i for i in np.argsort(-(per100k * np.maximum(rt, 0.5))) if i not in running and p["susc"][i] > 0.15][:2]
        for i in cand:
            if per100k[i] > 5:
                out.append(Action(type="vaccinate", province_id=PIDS[i], value=5, days=30,
                                  why=f"{p['susc'][i]:.0%} still susceptible and R ≈ {rt[i]:.2f}; protection builds over weeks"))
    # 4. dengue: vector control at hotspots
    if pa["route"] == "vector":
        on = {v[0] for v in s.vector}
        for i in np.argsort(-per100k)[:2]:
            if i not in on and rt[i] > 1.0 and per100k[i] > 20:
                out.append(Action(type="vector_control", province_id=PIDS[i], value=0.25, days=30,
                                  why=f"Aedes control while R ≈ {rt[i]:.2f} in {name(i)}"))
    # 5. early containment: a few provinces only -> slow export
    if 0 < f["nat"]["reached"] <= 3 and f["nat"]["active"][1] > 1000:
        for i in np.where(s.reached)[0][:2]:
            if s.border[i] < 0.5:
                out.append(Action(type="border", province_id=PIDS[i], value=0.6,
                                  why=f"outbreak still concentrated in {f['nat']['reached']} province(s); slowing export from {name(i)} buys time"))
    return out[:8]


def summary_rules(s: Session, acts: list[Action], vi: bool = False) -> str:
    f = _f(s)
    n = f["nat"]
    head = (f"**Ngày {s.day}** — {n['active'][1]:,.0f} ca nhiễm đang hoạt động, R toàn quốc ≈ {n['rt']:.2f}, "
            f"{n['overloaded']} tỉnh quá tải, {n['deaths'][1]:,.0f} tử vong (trung vị)." if vi else
            f"**Day {s.day}** — {n['active'][1]:,.0f} active infections, national R ≈ {n['rt']:.2f}, "
            f"{n['overloaded']} province(s) over capacity, {n['deaths'][1]:,.0f} deaths (median).")
    if not acts:
        return head + ("\n\nKhông có hành động nào được đề xuất lúc này." if vi else "\n\nNo action stands out right now.")
    return head + "\n\n" + "\n".join(f"- **{a.type}** {s._name(a.province_id)}: {a.why}" for a in acts)


def context(s: Session, lang: str) -> dict:
    f = _f(s)
    p = f["prov"]
    N = np.maximum(s.N, 1)
    rows = []
    for i, pid in enumerate(PIDS):
        rows.append({"id": pid, "name": s.provs[pid]["name"], "pop": int(s.provs[pid]["population"] or 0),
                     "active_per100k": round(p["active"][i] / N[i] * 1e5, 1), "rt": p["rt"][i],
                     "hosp": p["hosp"][i], "cap": p["cap"][i], "unmet": p["unmet"][i], "deaths": p["deaths"][i],
                     "vaccinated": p["vacc"][i], "susceptible": p["susc"][i], "contact_level": p["contact"][i],
                     "border_closed": p["border"][i], "nurses": p["nurses"][i]})
    rows.sort(key=lambda r: -(r["active_per100k"] + 50 * (r["unmet"] > 0)))
    hist = s.history[::max(1, len(s.history) // 12)][-12:]
    return {
        "lang": lang, "screen": "sandbox", "semantics": "SCENARIO", "day": s.day, "date": f["date"],
        "pathogen": {k: s.path[k] for k in ("name", "route", "r0", "latent", "infectious", "p_hosp", "cfr_hosp", "untreated",
                                             "immune", "vax_inf", "vax_sev")},
        "national": f["nat"], "score_so_far": s.score(),
        "trend": [{"day": h["day"], "active": h["nat"]["active"][1], "hosp": h["nat"]["hosp"][1], "rt": h["nat"]["rt"]} for h in hist],
        "provinces_top": rows[:14],
        "teams": {"total": len(s.teams), "idle": sum(1 for t in s.teams if not t.deployed),
                  "deployed": [t.at for t in s.teams if t.deployed]},
        "campaigns_running": [{"provinces": [PIDS[int(i)] for i in c[0]][:5], "rate_per_1k_day": c[1], "until_day": c[2]} for c in s.campaigns],
        "recent_actions": s.actions[-12:], "recent_events": [e["text"] for e in s.events[-15:]],
        "weather_mode": s.spec.weather.mode, "basis": s.public(frames=[])["basis"],
    }


SYSTEM = """You are the outbreak-response advisor inside HealthPulse's Viet Nam outbreak sandbox (34 provinces, units in force \
since 1 July 2025). A planner is running a live SCENARIO and asks what to do next.

Rules:
- Use only the numbers in CONTEXT. Everything is a SCENARIO under stated assumptions; never present it as a forecast or a report.
- Propose at most 6 concrete actions using ONLY these action types (field `value` meaning in brackets):
{actions}
- `province_id` must be one of the ids in CONTEXT (e.g. "VN79") or "ALL" where national makes sense. Staff and corridor actions also need `to_id`.
- Prefer actions whose trade-off is clear: say the expected effect and the cost (doses, staff pulled from elsewhere, \
person-days under restriction). Do not claim lives saved as fact.
- Answer in the language in CONTEXT.lang (vi = Vietnamese, en = English); keep province names in Vietnamese.

Reply with one JSON object only, no prose outside it:
{{"summary": "<3-6 sentence situation read and strategy, markdown allowed>", \
"actions": [{{"type": "...", "province_id": "...", "to_id": null, "value": 0, "days": null, "kind": null, "why": "<one sentence>"}}]}}"""


def system_prompt() -> str:
    return SYSTEM.format(actions="\n".join(f"  - {k}: {v}" for k, v in ACTION_HELP.items() if k != "autopilot"))


def parse(text: str) -> tuple[str, list[Action], list[str]]:
    """Pull the JSON object out of the model reply and validate each action. Returns (summary, actions, rejected)."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
    if not m:
        raise ValueError("no JSON object in the reply")
    obj = json.loads(m.group(1))
    acts, bad = [], []
    for a in (obj.get("actions") or [])[:8]:
        try:
            a = {k: v for k, v in a.items() if v is not None}
            if a.get("type") == "autopilot":
                raise ValueError("autopilot is the user's choice")
            acts.append(Action(**a, source="advisor"))
        except Exception as e:  # noqa: BLE001 - any invalid proposal is dropped and reported
            bad.append(f"{a.get('type', '?')}: {str(e)[:120]}")
    return str(obj.get("summary") or ""), acts, bad
