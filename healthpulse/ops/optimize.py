"""Resource allocation: where to send teams, vaccine, test kits, beds and ambulances.

Greedy marginal allocation, which is optimal for separable objectives with diminishing returns (each extra team on a
cluster helps less than the previous one, because tracing coverage saturates):
  Stage 1 — transmission levers (teams, vaccine, test kits): each chunk goes to the cluster where it averts the most
            projected cases over 28 days, coming from the nearest depot that still has stock; the travel time delays
            its effect, so a far depot is worth less.
  Stage 2 — care levers (beds, ambulances): each chunk goes to the province where it averts the most deaths, given
            the admissions left after stage 1.
Chunks that avert less than a threshold stay in reserve (sending them would cost logistics for no projected gain).
Everything is a SCENARIO under `project.ASSUMPTIONS`.
"""
from __future__ import annotations

import numpy as np

from . import geo, project, store
from .analysis import HORIZON

SPEED_KM_DAY = {"team": 450.0, "vaccine": 500.0, "test_kit": 600.0, "bed": 350.0, "ambulance": 500.0}
SETUP_DAYS = {"team": 0.5, "vaccine": 1.0, "test_kit": 0.3, "bed": 1.5, "ambulance": 0.3}
MIN_GAIN_CASES = 0.5
MIN_GAIN_DEATHS = 0.01


def _chunks(kind: str, total: float) -> float:
    if kind in ("team", "ambulance"):
        return 1.0
    if kind == "vaccine":
        return max(500.0, round(total / 40, -2))
    if kind == "test_kit":
        return max(200.0, round(total / 40, -1))
    return max(5.0, round(total / 30))


def optimize(an: dict, basis: str = "simulated", resources: list[dict] | None = None) -> dict:
    inc = an["incident"]
    ps, S = inc["pathogen_spec"], inc["settings"]
    iid = inc["incident_id"]
    res = resources if resources is not None else store.resources(iid)
    us = project.units(an)
    which = "plan" if basis == "simulated" else "current"
    zm = project.zone_effects(us, an["zones"], which)
    U = len(us)
    stock = {r["resource_id"]: float(r["qty"]) for r in res}
    by_kind = {}
    for r in res:
        by_kind.setdefault(r["kind"], []).append(r)
    allocs: list[dict] = []
    log = []

    def nearest(kind, lat, lon):
        best, bd = None, 1e18
        for r in by_kind.get(kind, []):
            if stock[r["resource_id"]] <= 1e-9:
                continue
            d = float(geo.haversine(lat, lon, r["lat"], r["lon"]))
            if d < bd:
                best, bd = r, d
        return best, bd

    def delay(kind, km):
        return SETUP_DAYS[kind] + km / SPEED_KM_DAY[kind]

    # ---------------------------------------------------------------- stage 1: transmission
    T_KINDS = [k for k in ("team", "vaccine", "test_kit") if by_kind.get(k)]
    if (ps.get("vax_inf") or 0) <= 0 and "vaccine" in T_KINDS:
        T_KINDS.remove("vaccine")
        log.append("No vaccine efficacy for this pathogen: vaccine stock is kept in reserve.")
    A = {k: np.zeros(U) for k in ("team", "vaccine", "test_kit")}
    Dl = {k: np.zeros(U) for k in A}

    def batch(rows):
        """Total projected cases for many (unit, allocation) candidates in ONE vectorised simulation."""
        if not rows:
            return []
        idx = [r[0] for r in rows]
        Ab = {k: np.array([r[1][k] for r in rows], float) for k in A}
        Db = {k: np.array([r[2][k] for r in rows], float) for k in A}
        return project.simulate([us[i] for i in idx], ps, S["compliance"], zm[idx], Ab, Db).sum(axis=1).tolist()

    cur_i = batch([(i, {k: 0.0 for k in A}, {k: 0.0 for k in A}) for i in range(U)])
    chunk = {k: _chunks(k, sum(stock[r["resource_id"]] for r in by_kind[k])) for k in T_KINDS}

    def gains(pairs):
        """Marginal cases averted by the next chunk of `kind` at unit `i`, for every (i, kind) in pairs."""
        rows, meta = [], []
        for i, kind in pairs:
            src, km = nearest(kind, us[i]["lat"], us[i]["lon"])
            if not src:
                meta.append((i, kind, None))
                continue
            q = min(chunk[kind], stock[src["resource_id"]])
            Ai = {k: A[k][i] for k in A}
            Di = {k: Dl[k][i] for k in A}
            dl = delay(kind, km)
            Di[kind] = (Di[kind] * Ai[kind] + dl * q) / (Ai[kind] + q)
            Ai[kind] += q
            rows.append((i, Ai, Di))
            meta.append((i, kind, (src, (q, dl, km, Ai, Di))))
        vals = iter(batch(rows))
        for i, kind, m in meta:
            G[(i, kind)] = (0.0, None, None) if m is None else (cur_i[i] - next(vals), m[0], m[1])

    G: dict = {}
    gains([(i, k) for i in range(U) for k in T_KINDS])
    steps = 0
    while G and steps < 600:
        steps += 1
        # each kind draws on its own stock, so the best chunk overall is simply the one that averts the most cases
        (i, k), (g, src, info) = max(G.items(), key=lambda kv: kv[1][0])
        if g < MIN_GAIN_CASES or src is None:
            break
        q, dl, km, Ai, Di = info
        stock[src["resource_id"]] -= q
        for kk in A:
            A[kk][i], Dl[kk][i] = Ai[kk], Di[kk]
        cur_i[i] -= g
        allocs.append({"kind": k, "qty": q, "from": src["resource_id"], "from_name": src["name"], "from_lat": src["lat"], "from_lon": src["lon"],
                       "to": us[i]["id"], "to_name": us[i]["label"], "to_lat": us[i]["lat"], "to_lon": us[i]["lon"], "to_province": us[i]["province_id"],
                       "km": round(km), "delay_days": round(dl, 1), "gain_cases": g})
        # this unit's marginals changed; if the depot ran dry, every unit's best source for this kind may have too
        pairs = {(i, kk) for kk in T_KINDS}
        if stock[src["resource_id"]] <= 1e-9:
            pairs |= {(j, k) for j in range(U)}
        gains(sorted(pairs))

    # ---------------------------------------------------------------- stage 2: care (beds, ambulances)
    C_KINDS = [k for k in ("bed", "ambulance") if by_kind.get(k)]
    bed_allocs: list[dict] = []
    if C_KINDS and U:
        inc_mid = project.simulate(us, ps, S["compliance"], zm, A, Dl, "mid")
        pids = sorted({u["province_id"] for u in us})
        attrs = geo.provinces()["attrs"]
        chunk2 = {k: _chunks(k, sum(stock[r["resource_id"]] for r in by_kind[k])) for k in C_KINDS}

        def deaths(extra):
            beds = {}
            for a in bed_allocs + extra:
                beds.setdefault(a["to_province"], []).append((a["kind"], a["qty"], a["delay_days"]))
            return project.hospital(an, us, inc_mid, beds)

        def per_prov(h):
            return {p["province_id"]: p["deaths"] for p in h["provinces"]}

        cur = per_prov(deaths([]))
        for _ in range(400):
            best = None
            for pid in pids:
                la, lo = attrs[pid]["lat"], attrs[pid]["lon"]
                for k in C_KINDS:
                    src, km = nearest(k, la, lo)
                    if not src:
                        continue
                    q = min(chunk2[k], stock[src["resource_id"]])
                    cand = {"kind": k, "qty": q, "to_province": pid, "delay_days": round(delay(k, km), 1)}
                    g = cur.get(pid, 0) - per_prov(deaths([cand])).get(pid, 0)
                    if not best or g > best[0]:
                        best = (g, src, km, cand)
            if not best or best[0] < MIN_GAIN_DEATHS:
                break
            g, src, km, cand = best
            stock[src["resource_id"]] -= cand["qty"]
            a = {**cand, "from": src["resource_id"], "from_name": src["name"], "from_lat": src["lat"], "from_lon": src["lon"],
                 "to": "P:" + cand["to_province"], "to_name": attrs[cand["to_province"]]["name"], "to_lat": attrs[cand["to_province"]]["lat"],
                 "to_lon": attrs[cand["to_province"]]["lon"], "km": round(km), "gain_deaths": round(g, 3)}
            bed_allocs.append(a)
            cur = per_prov(deaths([]))

    # ---------------------------------------------------------------- merge chunks → moves
    merged: dict[tuple, dict] = {}
    for a in allocs + bed_allocs:
        key = (a["kind"], a["from"], a["to"])
        m = merged.get(key)
        if m:
            m["delay_days"] = round((m["delay_days"] * m["qty"] + a["delay_days"] * a["qty"]) / (m["qty"] + a["qty"]), 1)
            m["qty"] += a["qty"]
            m["gain_cases"] = m.get("gain_cases", 0) + a.get("gain_cases", 0)
            m["gain_deaths"] = m.get("gain_deaths", 0) + a.get("gain_deaths", 0)
        else:
            merged[key] = dict(a)
    moves = sorted(merged.values(), key=lambda a: -(a.get("gain_cases", 0) + 200 * a.get("gain_deaths", 0)))
    for m in moves:
        m["qty"] = round(m["qty"], 0)
        m["gain_cases"] = round(m.get("gain_cases", 0), 1)
        m["gain_deaths"] = round(m.get("gain_deaths", 0), 2)
        m["why"] = _why(m, ps)
    reserve = [{"resource_id": r["resource_id"], "name": r["name"], "kind": r["kind"], "left": round(stock[r["resource_id"]]),
                "of": r["qty"]} for r in res if stock[r["resource_id"]] > 1e-6]
    before = project.run(an, which, [], None, us)
    after = project.run(an, which, moves, None, us)
    return {"basis": basis, "allocations": moves, "reserve": reserve, "log": log,
            "summary": {"horizon_days": HORIZON, "moves": len(moves), "cases_before": before["cum_cases"], "cases_after": after["cum_cases"],
                        "cases_averted": before["cum_cases"] - after["cum_cases"], "deaths_before": before["deaths"], "deaths_after": after["deaths"],
                        "deaths_averted": round(before["deaths"] - after["deaths"], 1), "peak_unmet_before": before["peak_unmet"],
                        "peak_unmet_after": after["peak_unmet"]},
            "before": {"cases": before["cases"]["mid"], "beds_unmet": before["beds_unmet"]},
            "after": {"cases": after["cases"]["mid"], "beds_unmet": after["beds_unmet"]},
            "assumptions": project.ASSUMPTIONS, "semantics": "SCENARIO",
            "method": "greedy marginal allocation (stage 1: cases averted per chunk; stage 2: deaths averted per chunk)"}


def _why(m: dict, ps: dict) -> str:
    unit = store.RESOURCE_UNITS[m["kind"]]
    head = f"{m['qty']:,.0f} {unit} {m['from_name']} → {m['to_name']} ({m['km']} km, effective in ~{m['delay_days']} d)"
    if m["kind"] in ("bed", "ambulance"):
        return f"{head}: ~{m['gain_deaths']:.2f} deaths averted where beds run short"
    return f"{head}: ~{m['gain_cases']:,.0f} cases averted over {HORIZON} days"
