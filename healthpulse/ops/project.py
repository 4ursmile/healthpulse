"""28-day SCENARIO projection per outbreak unit (cluster, or the sporadic cases of one province).

`units()` turns an analysis into vectors; `simulate()` steps all units at once; `scenarios()` runs baseline / current
/ plan with the growth-rate interval as a band and adds hospital beds and deaths at province level. Assumptions are
listed in `ASSUMPTIONS` and returned with every result so the screen can show them.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from . import geo, store
from .analysis import (AMBULANCE_TRANSFERS, HORIZON, TEST_EFFECT, TEST_PER_CASE, TRACE_EFFECT, TRACE_PER_TEAM, VAX_LAG,
                       gen_params, r_from_R, zone_active_weight)

ASSUMPTIONS = [
    f"A response team traces and isolates ~{TRACE_PER_TEAM} new cases/day; full tracing cuts R by up to {TRACE_EFFECT:.0%}.",
    f"Testing uses ~{TEST_PER_CASE} kits per new case; full testing cuts R by up to {TEST_EFFECT:.0%} (earlier isolation).",
    f"Vaccine doses are given over 7 days; protection starts {VAX_LAG} days after a dose, at the pathogen's efficacy against infection.",
    "A zone cuts transmission from people inside it by contact reduction × compliance; real zones active ≥ 7 days are already in the observed growth.",
    f"Each ambulance moves ~{AMBULANCE_TRANSFERS} patients/day to provinces with free beds.",
    "Hospital deaths rise by the pathogen's 'untreated' multiplier for patients without a bed.",
    "Population inside zones is province density × area (ESTIMATE). A cluster can reach 10× the people in its quarantine area (≥ 100k, ≤ its province).",
]


def _pool(c: dict, attrs: dict) -> float:
    """People a cluster can still reach. Not a closed box: the quarantine area's residents ×10 (their contacts reach well
    beyond it), at least 100k, at most the province's population."""
    prov = float(attrs.get(c["province_id"], {}).get("population") or 1e6)
    return float(min(prov, max(10 * c["quarantine_people_est"], 1e5)))


def units(an: dict) -> list[dict]:
    inc = an["incident"]
    ps, S = inc["pathogen_spec"], inc["settings"]
    rep = max(float(ps["report"]), 1e-3)
    delay = S["report_delay_days"]
    attrs = geo.provinces()["attrs"]
    out = []
    for c in an["clusters"]:
        g = c["growth"]
        lvl = g.get("level") or (sum(c["series"][-7:]) / 7)
        if g["basis"] == "fitted":
            lvl = lvl * np.exp(g["r"] * delay)
        out.append({"id": c["id"], "label": c["label"], "lat": c["lat"], "lon": c["lon"], "province_id": c["province_id"], "I0": float(max(lvl, 0.05)),
                    "R": g["R"], "R_lo": g["R_lo"], "R_hi": g["R_hi"], "N": _pool(c, attrs),
                    "cum_inf": c["cases"] / rep, "radius_km": c["radius_km"] + c["quarantine_margin_km"]})
    k = an["kpis"]
    for s in an["sporadic"]:
        if s["cases"] <= 0:
            continue
        lvl = max(s["last7"] / 7, 0.05)
        out.append({"id": "P:" + s["province_id"], "label": f"Sporadic · {s['province']}", "lat": s["lat"], "lon": s["lon"], "province_id": s["province_id"],
                    "I0": lvl, "R": k["R"], "R_lo": k["R_lo"], "R_hi": k["R_hi"], "N": float(attrs.get(s["province_id"], {}).get("population") or 1e5),
                    "cum_inf": s["cases"] / rep, "radius_km": 25.0})
    return out


def zone_effects(us: list[dict], zones: list[dict], which: str) -> np.ndarray:
    """R multiplier [U, H] from zones. which: baseline | current | plan."""
    m = np.ones((len(us), HORIZON))
    if not us:
        return m
    lat = [u["lat"] for u in us]
    lon = [u["lon"] for u in us]
    for z in zones:
        if z["status"] != "active" or z["kind"] in ("safe", "vaccination"):
            continue
        if which == "baseline" or (which == "current" and z["basis"] != "real"):
            continue
        w = zone_active_weight(z)
        if w <= 0:
            continue
        inside = geo.contains(z["geometry"], lat, lon)
        if not inside.any():
            continue
        c = float(z["params"].get("contact_reduction") or 0)
        k = z["params"].get("compliance")
        k = float(k if k is not None else z.get("_compliance", 0.75))
        start = 1 if z["basis"] == "simulated" else 0
        m[inside, start:] *= (1 - c * k * w)
    return m


def _alloc_arrays(us: list[dict], allocs: list[dict]):
    U = len(us)
    A = {k: np.zeros(U) for k in ("team", "vaccine", "test_kit")}
    Dl = {k: np.zeros(U) for k in A}
    beds = {}
    if not allocs or not U:
        return A, Dl, beds
    ids = {u["id"]: i for i, u in enumerate(us)}
    for a in allocs:
        if a["kind"] in ("bed", "ambulance"):
            pid = a.get("to_province") or a.get("province_id")
            b = beds.setdefault(pid, [])
            b.append((a["kind"], float(a["qty"]), float(a.get("delay_days") or 0)))
            continue
        i = ids.get(a.get("to"))
        if i is None or (a.get("to_lat") is not None and geo.haversine(us[i]["lat"], us[i]["lon"], a["to_lat"], a["to_lon"]) > us[i]["radius_km"] + 5):
            # clusters are re-numbered as reports arrive: match by position instead
            if a.get("to_lat") is None:
                continue
            d = [float(geo.haversine(u["lat"], u["lon"], a["to_lat"], a["to_lon"])) for u in us]
            i = int(np.argmin(d))
            if d[i] > us[i]["radius_km"] + 10:
                continue
        q = float(a["qty"])
        prev = A[a["kind"]][i]
        Dl[a["kind"]][i] = (Dl[a["kind"]][i] * prev + float(a.get("delay_days") or 0) * q) / max(prev + q, 1e-9)
        A[a["kind"]][i] += q
    return A, Dl, beds


def simulate(us: list[dict], ps: dict, compliance: float, zm: np.ndarray, A=None, Dl=None, band: str = "mid") -> np.ndarray:
    """Daily reported cases [U, H]."""
    U = len(us)
    if not U:
        return np.zeros((0, HORIZON))
    L, D = gen_params(ps)
    rep = max(float(ps["report"]), 1e-3)
    key = {"mid": "R", "lo": "R_lo", "hi": "R_hi"}[band]
    R0 = np.array([u[key] for u in us], float)
    I = np.array([u["I0"] for u in us], float)
    N = np.array([u["N"] for u in us], float)
    Sref = np.maximum(N * (1 - float(ps.get("immune") or 0)) - np.array([u["cum_inf"] for u in us]), 0.05 * N)
    Sx = Sref.copy()
    A = A or {k: np.zeros(U) for k in ("team", "vaccine", "test_kit")}
    Dl = Dl or {k: np.zeros(U) for k in A}
    kits = A["test_kit"].copy()
    admin = np.zeros((U, HORIZON + VAX_LAG + 1))
    for t in range(HORIZON):
        vt = (t >= Dl["vaccine"]) & (t < Dl["vaccine"] + 7)
        admin[:, t] = np.where(vt, A["vaccine"] / 7.0, 0)
    vinf = float(ps.get("vax_inf") or 0)
    out = np.zeros((U, HORIZON))
    for t in range(HORIZON):
        Ip = np.maximum(I, 1e-6)
        trace = np.where(t >= Dl["team"], np.minimum(1.0, A["team"] * TRACE_PER_TEAM / np.maximum(Ip, 1.0)), 0.0)
        need = TEST_PER_CASE * Ip
        use = np.where(t >= Dl["test_kit"], np.minimum(kits, need), 0.0)
        kits -= use
        test = use / need
        R = R0 * zm[:, t] * (1 - TRACE_EFFECT * trace * compliance) * (1 - TEST_EFFECT * test) * (Sx / Sref)
        r = r_from_R(R, L, D)
        I = I * np.exp(r)
        I = np.minimum(I, np.maximum(Sx, 0) * rep)
        out[:, t] = I
        prot = admin[:, t - VAX_LAG] * vinf * np.clip(Sx / N, 0, 1) if t >= VAX_LAG else 0.0
        Sx = np.maximum(Sx - I / rep - prot, 1e-3 * N)
    return out


def hospital(an: dict, us: list[dict], inc_: np.ndarray, beds: dict) -> dict:
    """Province beds needed vs free, and deaths (with the untreated penalty when beds run out)."""
    ps, S = an["incident"]["pathogen_spec"], an["incident"]["settings"]
    h = min(1.0, float(ps["p_hosp"]) / max(float(ps["report"]), 1e-3))
    los = max(1, int(round(float(ps["los"]))))
    cfr, unt = float(ps["cfr_hosp"]), float(ps["untreated"])
    attrs = geo.provinces()["attrs"]
    cur = {p["province_id"]: p["hosp_est"] for p in an["provinces"]}
    pids = sorted({u["province_id"] for u in us} | set(beds))
    occ_tot, need_tot, deaths = np.zeros(HORIZON), np.zeros(HORIZON), np.zeros(HORIZON)
    per = []
    for pid in pids:
        adm = np.zeros(HORIZON)
        for i, u in enumerate(us):
            if u["province_id"] == pid:
                adm += inc_[i] * h
        hist = cur.get(pid, 0) * np.clip(1 - (np.arange(HORIZON) + 1) / los, 0, 1)
        occ = hist + np.convolve(adm, np.ones(los))[:HORIZON]
        cap = np.full(HORIZON, (attrs.get(pid, {}).get("beds_est") or 0) * S["free_bed_share"])
        for kind, q, dl in beds.get(pid, []):
            add = q if kind == "bed" else q * AMBULANCE_TRANSFERS * los
            cap[int(np.ceil(dl)):] += add
        unmet = np.maximum(occ - cap, 0)
        share = np.where(occ > 0, unmet / np.maximum(occ, 1e-9), 0)
        d = adm * cfr * (1 + (unt - 1) * share)
        occ_tot += occ
        need_tot += unmet
        deaths += d
        per.append({"province_id": pid, "name": attrs.get(pid, {}).get("name", pid), "peak_beds": round(float(occ.max())),
                    "free_beds": round(float(cap[0])), "peak_unmet": round(float(unmet.max())), "deaths": round(float(d.sum()), 1)})
    per.sort(key=lambda r: -r["peak_unmet"] - 0.001 * r["peak_beds"])
    return {"occupied": occ_tot, "unmet": need_tot, "deaths": deaths, "provinces": per}


def run(an: dict, which: str, allocs: list[dict] | None = None, extra_zones: list[dict] | None = None, us: list[dict] | None = None) -> dict:
    inc = an["incident"]
    ps, S = inc["pathogen_spec"], inc["settings"]
    us = us if us is not None else units(an)
    zones = [z for z in an["zones"]] + (extra_zones or [])
    for z in zones:
        z["_compliance"] = S["compliance"]
    zm = zone_effects(us, zones, which)
    A, Dl, beds = _alloc_arrays(us, allocs or [])
    res = {}
    for band in ("lo", "mid", "hi"):
        res[band] = simulate(us, ps, S["compliance"], zm, A, Dl, band)
    hp = hospital(an, us, res["mid"], beds)
    tot = {b: res[b].sum(axis=0) for b in res}
    return {"cases": {b: np.round(tot[b], 1).tolist() for b in tot}, "cum_cases": round(float(tot["mid"].sum())),
            "cum_lo": round(float(tot["lo"].sum())), "cum_hi": round(float(tot["hi"].sum())),
            "next14": round(float(tot["mid"][:14].sum())), "beds_occupied": np.round(hp["occupied"]).tolist(),
            "beds_unmet": np.round(hp["unmet"]).tolist(), "peak_unmet": round(float(hp["unmet"].max())) if len(hp["unmet"]) else 0,
            "deaths": round(float(hp["deaths"].sum()), 1), "provinces": hp["provinces"],
            "units": [{"id": u["id"], "label": u["label"], "next14": round(float(res["mid"][i, :14].sum())), "R_eff_end": None,
                       "cum": round(float(res["mid"][i].sum()))} for i, u in enumerate(us)]}


def scenarios(an: dict, plan: dict | None = None, extra_zones: list[dict] | None = None) -> dict:
    iid = an["incident"]["incident_id"]
    approved = store.approved_plan(iid)
    real_alloc = approved["plan"]["allocations"] if approved else []
    if plan is None:
        latest = next((p for p in store.plans(iid) if p["status"] == "proposed"), None)
        if latest and (not approved or latest["created_at"] > approved["approved_at"]):
            plan = latest
    plan_alloc = real_alloc + ((plan or {}).get("plan", plan or {}).get("allocations") or [])
    us = units(an)
    start = date.fromisoformat(an["as_of"]) + timedelta(days=1)
    out = {"days": [(start + timedelta(days=i)).isoformat() for i in range(HORIZON)], "horizon_days": HORIZON,
           "baseline": run(an, "baseline", [], None, us), "current": run(an, "current", real_alloc, None, us),
           "plan": run(an, "plan", plan_alloc, extra_zones, us),
           "plan_id": (plan or {}).get("plan_id"), "approved_plan_id": approved["plan_id"] if approved else None,
           "assumptions": ASSUMPTIONS, "semantics": "SCENARIO", "n_units": len(us)}
    b = out["baseline"]
    for k in ("current", "plan"):
        out[k]["averted_vs_baseline"] = round(b["cum_cases"] - out[k]["cum_cases"])
        out[k]["deaths_averted_vs_baseline"] = round(b["deaths"] - out[k]["deaths"], 1)
    return out
