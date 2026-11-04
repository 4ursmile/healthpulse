"""Viet Nam dengue preparedness simulator: stochastic metapopulation SEIR over the 34 provinces, hospital and
workforce capacity, and mobile rapid-response teams that physically travel between provinces.

Everything here is a SCENARIO under stated assumptions. The inputs that come from data are labelled in `basis`:
population (WorldPop via HeiGIT), province points and distances (COD-AB), seasonal transmission shape and the starting
level (fitted climate model, calibrated to WHO national reports), beds and staff (WHO national densities x provincial
population - an estimate, not a facility census). Rates (reporting, hospitalisation, length of stay, team effect) are
planner assumptions with defaults from the dengue literature and can be changed in the request.

Both arms (baseline without teams, plan with teams) use the same seed and trial count (common random numbers).
"""
from __future__ import annotations

import math
import time
from datetime import date

import numpy as np
from pydantic import BaseModel, Field, field_validator

from . import model
from .crosswalk import BASES, MERGER

PIDS = list(MERGER)


class ManualDispatch(BaseModel):
    province_id: str
    day: int = Field(0, ge=0, le=365)
    teams: int = Field(1, ge=1, le=50)
    base_id: str | None = None


class SimSpec(BaseModel):
    days: int = Field(150, ge=14, le=365)
    trials: int = Field(120, ge=10, le=1000)
    seed: int = 7
    start_month: str | None = None              # default: the model's current estimate month
    # transmission
    transmission: float = Field(1.35, ge=0.2, le=4.0)  # multiplier on the fitted provincial R(t) (1 = as fitted)
    seasonality: float = Field(1.0, ge=0, le=2)        # 0 = hold the start-month R, 1 = follow the fitted seasonal curve
    mobility: float = Field(0.04, ge=0, le=0.3)  # share of contacts made outside the home province
    mobility_reduction: float = Field(0.0, ge=0, le=1)
    immune_share: float = Field(0.65, ge=0, le=0.95)  # share immune to the circulating serotypes (assumption)
    outbreak_province: str | None = None
    outbreak_infections: int = Field(0, ge=0, le=200000)
    # clinical
    reporting_rate: float = Field(0.15, gt=0, le=1)   # reported cases per infection
    hosp_rate: float = Field(0.30, ge=0, le=1)        # admitted per reported case
    los_days: float = Field(4.0, ge=1, le=20)
    # capacity / workforce
    dengue_bed_share: float = Field(0.08, ge=0.005, le=0.6)    # share of estimated beds usable for dengue
    dengue_nurse_share: float = Field(0.06, ge=0.005, le=0.6)  # share of estimated nurses on dengue wards
    patients_per_nurse: float = Field(6.0, ge=1, le=20)         # per shift; 3 shifts a day
    surge_pct: float = Field(0.0, ge=0, le=1)                   # overtime / cancelled leave
    # teams
    teams: int = Field(12, ge=0, le=200)
    team_capacity: int = Field(25, ge=1, le=500)   # extra patients a deployed team can manage (field beds + staff)
    team_staff: int = Field(10, ge=1, le=100)
    team_vector_effect: float = Field(0.10, ge=0, le=0.9)  # transmission reduction per team on site (after 7 days)
    max_teams_per_province: int = Field(6, ge=1, le=50)
    speed_kmh: float = Field(45, ge=10, le=800)
    mobilize_hours: float = Field(6, ge=0, le=72)
    dispatch: str = Field("auto", pattern="^(auto|manual|none)$")
    review_every_days: int = Field(7, ge=1, le=30)
    trigger_load: float = Field(0.85, ge=0.1, le=3)
    manual: list[ManualDispatch] = []
    bases: list[str] | None = None

    @field_validator("outbreak_province")
    @classmethod
    def _pid(cls, v):
        if v and v not in MERGER:
            raise ValueError(f"unknown province {v}")
        return v


def _haversine(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def seasonal_shape() -> np.ndarray:
    """(34, 12) fitted climate-normal case curve per province, mean 1 (2015+ months of the fitted model)."""
    import polars as pl
    nrm = model.estimates()
    sh = (nrm.filter(pl.col("month").dt.year() >= 2015).with_columns(pl.col("month").dt.month().alias("moy"))
          .group_by("province_id", "moy").agg(pl.col("normal_raw").mean()))
    S = np.ones((len(PIDS), 12))
    for r in sh.iter_rows(named=True):
        S[PIDS.index(r["province_id"]), r["moy"] - 1] = r["normal_raw"]
    return S / S.mean(1, keepdims=True)


def seasonal_rm() -> np.ndarray:
    """(34, 12) month-on-month transmission multiplier: cases(t+1)/cases(t) ≈ R^(30/Tg), Tg ≈ 15 days."""
    S = seasonal_shape()
    return np.clip((np.roll(S, -1, axis=1) / np.maximum(S, 1e-6)) ** (15 / 30), 0.6, 1.6)


def _setup(spec: SimSpec):
    provs = {p["province_id"]: p for p in model.provinces()}
    P = len(PIDS)
    N = np.array([provs[p]["population"] or 0 for p in PIDS], float)
    xy = [(provs[p]["lat"], provs[p]["lon"]) for p in PIDS]
    D = np.array([[_haversine(a, b) for b in xy] for a in xy])
    # gravity mixing: off-diagonal share `mobility`, distributed by pop_j / d^2
    m = spec.mobility * (1 - spec.mobility_reduction)
    G = np.where(D > 0, N[None, :] / np.maximum(D, 30) ** 2, 0)
    G = G / G.sum(1, keepdims=True)
    M = (1 - m) * np.eye(P) + m * G
    st = model.state(spec.start_month)
    est = {r["province_id"]: r for r in st["provinces"]}
    Rm = seasonal_rm()
    start = date.fromisoformat(st["month"])
    beds = np.array([provs[p]["beds_est"] or 0 for p in PIDS], float)
    nurses = np.array([provs[p]["nurses_est"] or 0 for p in PIDS], float)
    cap_beds = beds * spec.dengue_bed_share
    cap_staff = nurses * spec.dengue_nurse_share / 3 * spec.patients_per_nurse
    base_cap = np.minimum(cap_beds, cap_staff) * (1 + spec.surge_pct)
    binding = np.where(cap_beds <= cap_staff, "beds", "staff")
    month_cases = np.array([est[p]["estimate"] if p in est else 0 for p in PIDS])
    ratio = np.array([est[p]["ratio"] or 1 if p in est else 1 for p in PIDS])
    return dict(provs=provs, N=N, D=D, M=M, Rm=Rm, start=start, base_cap=base_cap, cap_beds=cap_beds, cap_staff=cap_staff,
                binding=binding, month_cases=month_cases, ratio=ratio, state=st, xy=xy)


class Team:
    __slots__ = ("tid", "base", "at", "dest", "depart", "arrive", "since", "log")

    def __init__(self, tid, base):
        self.tid, self.base = tid, base
        self.at = base["province_id"]
        self.dest = None
        self.depart = self.arrive = None
        self.since = None  # day it arrived on site
        self.log = []      # movement segments for animation


def _travel_days(env, spec, a: str, b: str) -> float:
    if a == b:
        return spec.mobilize_hours / 24
    d = env["D"][PIDS.index(a), PIDS.index(b)] * 1.3  # road factor
    return (spec.mobilize_hours + d / spec.speed_kmh) / 24


def run_arm(spec: SimSpec, env: dict, with_teams: bool) -> dict:
    rng = np.random.default_rng(spec.seed)
    K, P, T = spec.trials, len(PIDS), spec.days
    N = env["N"]
    sigma, gamma = 1 / 5.5, 1 / 5.0
    # initial state from this month's estimated reported cases
    daily_inf = env["month_cases"] / 30 / spec.reporting_rate
    # daily discrete chain: a person stays 1/(1-exp(-rate)) counted days in E or I, so steady pools = flow x that duration
    p_sigma, p_gamma = 1 - math.exp(-sigma), 1 - math.exp(-gamma)
    I0 = daily_inf / p_gamma
    E0 = daily_inf / p_sigma
    imm = spec.immune_share
    S0 = N * (1 - imm) - I0 - E0
    if spec.outbreak_province and spec.outbreak_infections:
        I0 = I0.copy()
        I0[PIDS.index(spec.outbreak_province)] += spec.outbreak_infections
    s0 = S0 / np.maximum(N, 1)
    # R_p(t) = transmission x fitted seasonal R; beta = R * (1-exp(-gamma)) / s0 (frequency-dependent, s0 = start
    # susceptible share). Same convention as sandbox.py; the old R * gamma made the effective R ~10% above the input.
    beta0 = spec.transmission * p_gamma / np.maximum(s0, 1e-6)
    Sx = np.tile(np.maximum(S0, 0), (K, 1)).round()
    Ex = rng.poisson(np.tile(E0, (K, 1))).astype(float)
    Ix = rng.poisson(np.tile(I0, (K, 1))).astype(float)
    H = np.tile(env["month_cases"] / 30 * spec.hosp_rate * spec.los_days, (K, 1)).round()
    # extra trial-level heterogeneity in transmission (shared across provinces within a trial)
    shock = rng.lognormal(0, 0.08, (K, 1))
    teams = []
    if with_teams and spec.teams and spec.dispatch != "none":
        bases = [b for b in BASES if not spec.bases or b["base_id"] in spec.bases] or BASES
        teams = [Team(i, bases[i % len(bases)]) for i in range(spec.teams)]
    cap_base = env["base_cap"]
    out = {k: np.zeros((T, P)) for k in ("cases", "occ", "unmet", "cap", "teams", "r")}
    band = {k: np.zeros((T, 3)) for k in ("cases", "occ", "unmet")}
    over_any = np.zeros((K, P), bool)
    events = []
    on_site = np.zeros(P)
    vec_on = np.zeros(P)
    manual = sorted(spec.manual, key=lambda m: m.day)
    pending_manual = list(manual) if spec.dispatch == "manual" else []
    last_adm = np.zeros((K, P))
    seasonal_day = lambda d: (env["start"].month - 1 + d / 30.44)  # noqa: E731
    over_prev = np.zeros(P, bool)
    for t in range(T):
        # --- team movement: arrivals
        for tm in teams:
            if tm.dest and t >= tm.arrive:
                tm.at, tm.dest, tm.since = tm.dest, None, t
                tm.log.append({"state": "deployed", "at": tm.at, "t0": t, "t1": None})
                if len(events) < 400:
                    events.append({"day": t, "kind": "arrive", "team": tm.tid, "province_id": tm.at,
                                   "text": f"Team {tm.tid + 1} on site in {env['provs'][tm.at]['name']}"})
        on_site[:] = 0
        vec_on[:] = 0
        for tm in teams:
            if tm.dest is None and tm.since is not None:
                j = PIDS.index(tm.at)
                on_site[j] += 1
                if t - tm.since >= 7:
                    vec_on[j] += 1
        cap = cap_base + on_site * spec.team_capacity
        # --- dispatch decisions on the ensemble mean (one plan, evaluated on every trial)
        if teams:
            orders = []
            if spec.dispatch == "manual":
                while pending_manual and pending_manual[0].day <= t:
                    m = pending_manual.pop(0)
                    orders += [(m.province_id, m.base_id)] * m.teams
            elif t % spec.review_every_days == 0:
                proj = (H.mean(0) + spec.review_every_days * last_adm.mean(0) * 0.5) / np.maximum(cap, 1)
                # release teams where load is low
                for tm in teams:
                    if tm.dest is None and tm.since is not None and tm.at != tm.base["province_id"]:
                        j = PIDS.index(tm.at)
                        if proj[j] < 0.5 and t - tm.since >= 14:
                            tm.since = None
                            _move(tm, tm.base["province_id"], t, env, spec, events, "return")
                pending = proj.copy()
                for _ in range(len(teams)):
                    j = int(np.argmax(pending))
                    if pending[j] < spec.trigger_load:
                        break
                    if on_site[j] + sum(1 for tm in teams if tm.dest == PIDS[j]) >= spec.max_teams_per_province:
                        pending[j] = -1
                        continue
                    orders.append((PIDS[j], None))
                    pending[j] -= spec.team_capacity / max(cap[j], 1)
            for pid, base_id in orders:
                idle = [tm for tm in teams if tm.dest is None and (tm.since is None or tm.at == tm.base["province_id"])
                        and (base_id is None or tm.base["base_id"] == base_id)]
                if not idle:
                    continue
                tm = min(idle, key=lambda x: _travel_days(env, spec, x.at, pid))
                tm.since = None
                _move(tm, pid, t, env, spec, events, "dispatch")
        # --- transmission
        month = seasonal_day(t)
        mi = int(month) % 12
        frac = month - int(month)
        Rt = env["Rm"][:, mi] * (1 - frac) + env["Rm"][:, (mi + 1) % 12] * frac
        if t == 0:
            R0 = Rt.copy()
        Rt = R0 * (Rt / R0) ** spec.seasonality
        r_now = Rt * spec.transmission
        beta = beta0 * Rt * (1 - spec.team_vector_effect) ** vec_on
        prev = Ix / np.maximum(N, 1)
        lam = beta[None, :] * shock * (prev @ env["M"].T)
        newE = rng.binomial(Sx.astype(np.int64), 1 - np.exp(-lam)).astype(float)
        newI = rng.binomial(Ex.astype(np.int64), 1 - math.exp(-sigma)).astype(float)
        newR = rng.binomial(Ix.astype(np.int64), 1 - math.exp(-gamma)).astype(float)
        Sx -= newE
        Ex += newE - newI
        Ix += newI - newR
        rep = rng.binomial(newI.astype(np.int64), spec.reporting_rate).astype(float)
        adm = rng.binomial(rep.astype(np.int64), spec.hosp_rate).astype(float)
        dis = rng.binomial(H.astype(np.int64), 1 - math.exp(-1 / spec.los_days)).astype(float)
        H += adm - dis
        last_adm = adm
        un = np.maximum(H - cap[None, :], 0)
        over_any |= un > 0
        out["cases"][t] = np.median(rep, 0)
        out["occ"][t] = np.median(H, 0)
        out["unmet"][t] = np.median(un, 0)
        out["cap"][t] = cap
        out["teams"][t] = on_site
        out["r"][t] = r_now * (Sx.mean(0) / np.maximum(S0, 1)) if t else r_now
        for k, arr in (("cases", rep.sum(1)), ("occ", H.sum(1)), ("unmet", un.sum(1))):
            band[k][t] = np.percentile(arr, [10, 50, 90])
        over_now = out["unmet"][t] > 0
        for j in np.where(over_now & ~over_prev)[0]:
            if len(events) < 400:
                events.append({"day": t, "kind": "overload", "province_id": PIDS[j],
                               "text": f"{env['provs'][PIDS[j]]['name']} over capacity ({out['occ'][t][j]:.0f} patients vs {cap[j]:.0f} slots)"})
        for j in np.where(~over_now & over_prev)[0]:
            if len(events) < 400:
                events.append({"day": t, "kind": "recover", "province_id": PIDS[j], "text": f"{env['provs'][PIDS[j]]['name']} back under capacity"})
        over_prev = over_now
        if t == 0:
            cum_unmet = un.copy()
            cum_rep = rep.copy()
        else:
            cum_unmet += un
            cum_rep += rep
    for tm in teams:
        if tm.log and tm.log[-1]["t1"] is None:
            tm.log[-1]["t1"] = T
    tot_unmet = cum_unmet.sum(1)
    tot_rep = cum_rep.sum(1)
    staff_days = sum((min(T, s["t1"] or T) - s["t0"]) for tm in teams for s in tm.log if s["state"] == "deployed") * spec.team_staff
    return {
        "series": {k: v.round(1).tolist() for k, v in out.items()},
        "band": {k: v.round(1).tolist() for k, v in band.items()},
        "p_overload": over_any.mean(0).round(3).tolist(),
        "cum_unmet_by_province": np.median(cum_unmet, 0).round(0).tolist(),
        "cum_cases_by_province": np.median(cum_rep, 0).round(0).tolist(),
        "kpi": {"reported_cases": _q(tot_rep), "unmet_bed_days": _q(tot_unmet),
                "peak_occupancy": {"p50": float(np.max(band["occ"][:, 1])), "day": int(np.argmax(band["occ"][:, 1]))},
                "provinces_overloaded": int((over_any.mean(0) >= 0.5).sum()), "team_staff_days": staff_days},
        "teams": [{"team_id": tm.tid, "base_id": tm.base["base_id"], "base_name": tm.base["name"], "base_province": tm.base["province_id"],
                   "log": tm.log} for tm in teams],
        "events": sorted(events, key=lambda e: e["day"]),
        "_unmet_trials": tot_unmet,
    }


def _move(tm: Team, dest: str, t: int, env, spec, events, why: str):
    if tm.log and tm.log[-1]["t1"] is None:
        tm.log[-1]["t1"] = t
    dt = _travel_days(env, spec, tm.at, dest)
    tm.dest, tm.depart, tm.arrive = dest, t, t + max(1, math.ceil(dt))
    tm.log.append({"state": "travel", "from": tm.at, "to": dest, "t0": t, "t1": tm.arrive, "hours": round(dt * 24, 1)})
    if len(events) < 400:
        a, b = env["provs"][tm.at]["name"], env["provs"][dest]["name"]
        events.append({"day": t, "kind": why, "team": tm.tid, "province_id": dest,
                       "text": (f"Team {tm.tid + 1} dispatched {a} → {b} (≈{dt * 24:.0f} h)" if why == "dispatch"
                                else f"Team {tm.tid + 1} released, returning {a} → {b}")})


def _q(a: np.ndarray) -> dict:
    p = np.percentile(a, [10, 50, 90])
    return {"p10": float(p[0]), "p50": float(p[1]), "p90": float(p[2])}


def run(spec: SimSpec) -> dict:
    t0 = time.perf_counter()
    env = _setup(spec)
    base = run_arm(spec, env, with_teams=False)
    plan = run_arm(spec, env, with_teams=True) if spec.teams and spec.dispatch != "none" else base
    bu = base.pop("_unmet_trials")
    pu = plan.pop("_unmet_trials") if plan is not base else bu
    avoided = bu - pu
    provs = env["provs"]
    wf = model.workforce_national()
    return {
        "spec": spec.model_dump(),
        "semantics": "SCENARIO",
        "start_month": env["state"]["month"],
        "days": spec.days,
        "dates": [str(np.datetime64(env["start"]) + np.timedelta64(d, "D")) for d in range(spec.days)],
        "provinces": [{"province_id": p, "name": provs[p]["name"], "lat": provs[p]["lat"], "lon": provs[p]["lon"],
                       "population": provs[p]["population"], "base_capacity": round(float(env["base_cap"][i]), 1),
                       "beds_dengue": round(float(env["cap_beds"][i]), 1), "staff_capacity": round(float(env["cap_staff"][i]), 1),
                       "binding": str(env["binding"][i]), "doctors_est": provs[p]["doctors_est"], "nurses_est": provs[p]["nurses_est"],
                       "start_month_cases": round(float(env["month_cases"][i]), 1)}
                      for i, p in enumerate(PIDS)],
        "bases": BASES,
        "baseline": base,
        "plan": plan,
        "avoided_unmet_bed_days": _q(avoided),
        "basis": {
            "population": "WorldPop (HeiGIT/HDX), 34 provinces",
            "start_level": f"climate model estimate for {env['state']['month']}, calibrated to WHO national reports (MODEL ESTIMATE)",
            "seasonality": "fitted provincial climate-normal curve (Poisson GLM, 2004-2010 Tycho data)",
            "beds_staff": f"WHO national densities ({wf.get('beds_per_10k')} beds, {wf.get('nurses_per_10k')} nurses per 10k) × population — ESTIMATE",
            "assumptions": "reporting, hospitalisation, length of stay, immunity, team effect: planner assumptions (editable)",
            "bases": "MoH regional institutes: NIHE, Pasteur Nha Trang, Tây Nguyên Institute, Pasteur HCMC",
        },
        "elapsed_ms": round((time.perf_counter() - t0) * 1000),
    }
