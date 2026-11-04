"""Operational simulation: forecast-driven demand draws, capacity, constrained team allocation, baseline vs proposal.

Scale design: arrays are (trials × regions × weeks) float32, processed in region chunks so thousands of municipalities
× tens of thousands of trials fit in memory. Baseline and proposal share the same random draws (common random
numbers), so their difference is attributable to the allocation, not to sampling noise. Deterministic for a seed.
"""
from __future__ import annotations

import math
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

import numpy as np
import polars as pl

from .schemas import ScenarioSpec

Z90 = 1.2815515655446004
OPTIMIZER_VERSION = "hp-opt 1.2 (HiGHS MILP / marginal greedy)"
ENGINE_VERSION = "hp-sim 1.2"


@dataclass
class Inputs:
    regions: list[str]
    weeks: list[str]
    p10: np.ndarray  # (R, W)
    p50: np.ndarray
    p90: np.ndarray
    population: np.ndarray  # (R,)
    lat: np.ndarray
    lon: np.ndarray
    facilities: np.ndarray  # (R,) count of registered facilities (context only)
    signal_type: str


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def lognormal_params(p10, p50, p90):
    lo, mid, hi = np.log1p(np.maximum(p10, 0)), np.log1p(np.maximum(p50, 0)), np.log1p(np.maximum(p90, 0))
    sigma = np.maximum((hi - lo) / (2 * Z90), 1e-3)
    return mid.astype(np.float32), sigma.astype(np.float32)


class Sampler:
    """Reproducible chunked sampling: the per-trial common shock and each region's own noise come from independent
    streams keyed by (seed, region index), so results do not depend on the chunk size."""

    def __init__(self, seed: int, K: int, W: int, rho: float):
        self.seed, self.K, self.W, self.rho = seed, K, W, rho
        self.common = np.random.default_rng([seed, 0]).standard_normal((K, 1, W)).astype(np.float32)

    def cases(self, mu: np.ndarray, sigma: np.ndarray, idx: np.ndarray, mult: float) -> np.ndarray:
        r = len(idx)
        ind = np.empty((self.K, r, self.W), np.float32)
        for j, i in enumerate(idx):
            ind[:, j, :] = np.random.default_rng([self.seed, 1, int(i)]).standard_normal((self.K, self.W))
        z = math.sqrt(self.rho) * self.common + math.sqrt(1 - self.rho) * ind
        c = np.expm1(mu[idx][None] + sigma[idx][None] * z)
        np.maximum(c, 0, out=c)
        return c * np.float32(mult)


def capacity(spec: ScenarioSpec, inp: Inputs) -> tuple[np.ndarray, list[str]]:
    cap = spec.capacity
    warn = []
    if cap.basis == "explicit":
        base = np.array([cap.explicit_visits_per_week.get(r, np.nan) for r in inp.regions], np.float32)
        miss = [r for r, b in zip(inp.regions, base) if np.isnan(b)]
        if miss:
            warn.append(f"no planner capacity for {len(miss)} region(s); per-10k default applied")
            base = np.where(np.isnan(base), inp.population / 1e4 * cap.visits_per_10k_residents_per_week, base)
    else:
        base = (inp.population / 1e4 * cap.visits_per_10k_residents_per_week).astype(np.float32)
    teams = np.array([cap.existing_teams.get(r, 0) for r in inp.regions], np.float32)
    removed = spec.resources.teams_removed
    if removed:
        # withdraw existing teams from the largest holders first
        order = np.argsort(-teams)
        left = removed
        for i in order:
            take = min(left, teams[i])
            teams[i] -= take
            left -= take
            if left <= 0:
                break
        if left > 0:
            warn.append(f"{int(left)} withdrawn team(s) had no existing team to remove")
    base = base + teams * spec.resources.visits_per_team_per_week
    closure = np.array([cap.site_closure_share.get(r, 0.0) for r in inp.regions], np.float32)
    return np.maximum(base * (1 - closure), 0).astype(np.float32), warn


def access_gap(inp: Inputs) -> np.ndarray:
    """0..1 rank of facilities per 100k residents (1 = least access). Context indicator, documented proxy."""
    per = np.where(inp.population > 0, inp.facilities / np.maximum(inp.population, 1) * 1e5, np.nan)
    if np.all(np.isnan(per)) or len(per) == 1:
        return np.zeros(len(per), np.float32)
    ranks = np.argsort(np.argsort(np.nan_to_num(per, nan=np.nanmedian(per) if np.isfinite(np.nanmedian(per)) else 0)))
    return (1 - ranks / max(1, len(per) - 1)).astype(np.float32)


def run_operational(spec: ScenarioSpec, inp: Inputs, progress: Callable[[float, str], None] | None = None,
                    chunk_regions: int = 256) -> dict:
    prog = progress or (lambda *_: None)
    R, W, K = len(inp.regions), len(inp.weeks), spec.simulation.draws
    mult = (1 + spec.stressors.holiday_surge_pct / 100) * (1 + spec.stressors.weather_stress_pct / 100)
    sar = inp.signal_type == "sari_admission"
    rate = 1.0 if sar else spec.assumptions.visit_rate
    mu, sigma = lognormal_params(inp.p10, inp.p50, inp.p90)
    cap_rw, warnings = capacity(spec, inp)
    cap = np.broadcast_to(cap_rw[:, None], (R, W)).astype(np.float32)
    v = np.float32(spec.resources.visits_per_team_per_week)
    nmax = int(spec.resources.max_teams_per_region)
    sampler = Sampler(spec.simulation.random_seed, K, W, spec.stressors.trial_correlation)
    # ---- pass 1: expected unmet as a function of teams per region, g[r, n]
    g = np.zeros((R, nmax + 1), np.float64)
    chunks = [np.arange(s, min(R, s + chunk_regions)) for s in range(0, R, chunk_regions)]
    pool = ThreadPoolExecutor(max_workers=max(1, min(8, (os.cpu_count() or 2) // 2)))  # numpy releases the GIL
    done = [0]

    def curve(idx):
        V = sampler.cases(mu, sigma, idx, mult) * np.float32(rate)  # (K, r, W)
        excess = V - cap[idx][None]
        for n in range(nmax + 1):
            g[idx, n] = np.maximum(excess - n * v, 0).sum(axis=2).mean(axis=0)
        return len(idx)
    for n_done in pool.map(curve, chunks):
        done[0] += n_done
        prog(0.1 + 0.35 * done[0] / R, f"expected-shortfall curves {done[0]:,}/{R:,} regions")
    # ---- allocation
    gap = access_gap(inp)
    weight = 1 + spec.assumptions.equity_alpha * gap
    alloc, opt = allocate(spec, inp, g, weight, gap, W)
    warnings += opt.pop("warnings", [])
    prog(0.6, f"allocation solved with {opt['method']}")
    n_r = alloc.sum(axis=0) if alloc.size else np.zeros(R)
    # ---- pass 2: evaluate baseline and proposal on the same draws
    q = [10, 50, 90]
    rows = {k: np.zeros((R, W, 3), np.float32) for k in ("demand", "unmet_base", "unmet_prop")}
    over_b = np.zeros((R, W), np.float32)
    over_p = np.zeros((R, W), np.float32)
    tot_b = np.zeros(K, np.float64)
    tot_p = np.zeros(K, np.float64)
    used_team_visits = 0.0

    def evaluate_chunk(idx):
        V = sampler.cases(mu, sigma, idx, mult) * np.float32(rate)
        ub = np.maximum(V - cap[idx][None], 0)
        up = np.maximum(V - cap[idx][None] - (n_r[idx] * v)[None, :, None].astype(np.float32), 0)
        rows["demand"][idx] = np.percentile(V, q, axis=0).transpose(1, 2, 0)
        rows["unmet_base"][idx] = np.percentile(ub, q, axis=0).transpose(1, 2, 0)
        rows["unmet_prop"][idx] = np.percentile(up, q, axis=0).transpose(1, 2, 0)
        over_b[idx] = (ub > 0).mean(axis=0)
        over_p[idx] = (up > 0).mean(axis=0)
        sb, sp_ = ub.sum(axis=(1, 2), dtype=np.float64), up.sum(axis=(1, 2), dtype=np.float64)
        return len(idx), sb, sp_, float((sb - sp_).mean())
    done[0] = 0
    for n_done, sb, sp_, used in pool.map(evaluate_chunk, chunks):
        tot_b += sb
        tot_p += sp_
        used_team_visits += used
        done[0] += n_done
        prog(0.6 + 0.35 * done[0] / R, f"evaluated {done[0]:,}/{R:,} regions")
    pool.shutdown()
    added = float(n_r.sum() * v * W)
    cost = opt.get("cost_brl", 0.0)
    per_cap_b = rows["unmet_base"][:, :, 1].sum(axis=1) / np.maximum(inp.population, 1) * 1e4
    per_cap_p = rows["unmet_prop"][:, :, 1].sum(axis=1) / np.maximum(inp.population, 1) * 1e4
    summary = {
        "regions": R, "weeks": W, "draws": K, "case_multiplier": mult,
        "demand_unit": "SARI cases" if sar else "health-service visits",
        "unmet_visits_baseline": dict(zip(("p10", "p50", "p90"), map(float, np.percentile(tot_b, q)))),
        "unmet_visits_proposed": dict(zip(("p10", "p50", "p90"), map(float, np.percentile(tot_p, q)))),
        "avoided_shortfall": dict(zip(("p10", "p50", "p90"), map(float, np.percentile(tot_b - tot_p, q)))),
        "prob_any_overload_baseline": float((tot_b > 0).mean()), "prob_any_overload_proposed": float((tot_p > 0).mean()),
        "regions_over_capacity_baseline": int((rows["unmet_base"][:, :, 1].sum(axis=1) > 0).sum()),
        "regions_over_capacity_proposed": int((rows["unmet_prop"][:, :, 1].sum(axis=1) > 0).sum()),
        "teams_allocated": int(n_r.sum()), "teams_available": int(spec.resources.mobile_teams_available),
        "team_visit_capacity_added": added,
        "expected_team_utilization": (used_team_visits / added) if added else None,
        "cost_brl": cost, "budget_brl": spec.resources.total_budget_brl,
        "worst_region_unmet_per_10k_baseline": float(per_cap_b.max()) if R else 0.0,
        "worst_region_unmet_per_10k_proposed": float(per_cap_p.max()) if R else 0.0,
        "optimizer": opt,
    }
    region_rows = []
    for i, r in enumerate(inp.regions):
        for w in range(W):
            region_rows.append({
                "municipality_id": r, "target_week": inp.weeks[w],
                "cases_p10": float(inp.p10[i, w]), "cases_p50": float(inp.p50[i, w]), "cases_p90": float(inp.p90[i, w]),
                "demand_p10": float(rows["demand"][i, w, 0]), "demand_p50": float(rows["demand"][i, w, 1]), "demand_p90": float(rows["demand"][i, w, 2]),
                "capacity_baseline": float(cap[i, w]), "capacity_proposed": float(cap[i, w] + n_r[i] * v),
                "unmet_baseline_p10": float(rows["unmet_base"][i, w, 0]), "unmet_baseline_p50": float(rows["unmet_base"][i, w, 1]),
                "unmet_baseline_p90": float(rows["unmet_base"][i, w, 2]),
                "unmet_proposed_p10": float(rows["unmet_prop"][i, w, 0]), "unmet_proposed_p50": float(rows["unmet_prop"][i, w, 1]),
                "unmet_proposed_p90": float(rows["unmet_prop"][i, w, 2]),
                "overload_prob_baseline": float(over_b[i, w]), "overload_prob_proposed": float(over_p[i, w]),
                "teams": int(n_r[i]), "access_gap": float(gap[i]), "equity_weight": float(weight[i]),
            })
    assign = []
    if alloc.size:
        bases = opt["bases"]
        for b, r in zip(*np.nonzero(alloc)):
            assign.append({"base": bases[b], "municipality_id": inp.regions[r], "teams": int(alloc[b, r]),
                           "distance_km": float(opt["_dist"][b, r]), "travel_minutes": float(opt["_dist"][b, r] / spec.resources.speed_kmh * 60)})
    opt.pop("_dist", None)
    return {"summary": summary, "regions": region_rows, "assignments": assign, "warnings": warnings}


def allocate(spec: ScenarioSpec, inp: Inputs, g: np.ndarray, weight: np.ndarray, gap: np.ndarray, W: int):
    res = spec.resources
    R = len(inp.regions)
    T = res.mobile_teams_available
    warnings: list[str] = []
    bases = res.team_bases or [inp.regions[int(np.argmax(inp.population))]]
    idx = {r: i for i, r in enumerate(inp.regions)}
    blat = np.array([inp.lat[idx[b]] if b in idx else np.nan for b in bases])
    blon = np.array([inp.lon[idx[b]] if b in idx else np.nan for b in bases])
    if np.isnan(blat).any():
        unknown = [b for b, x in zip(bases, blat) if np.isnan(x)]
        raise ValueError(f"team bases must be regions in scope: {unknown}")
    dist = haversine_km(blat[:, None], blon[:, None], inp.lat[None, :], inp.lon[None, :]).astype(np.float64)
    minutes = dist / res.speed_kmh * 60
    feasible = minutes <= res.max_travel_minutes
    unit_cost = res.cost_per_team_week_brl * W + 2 * dist * res.cost_per_km_brl * W
    B = len(bases)
    per_base = np.full(B, T // B)
    per_base[: T % B] += 1
    base_info = {"method": "none", "bases": bases, "teams_per_base": per_base.tolist(), "_dist": dist, "cost_brl": 0.0,
                 "optimality": None, "binding_constraints": []}
    if T == 0:
        base_info["binding_constraints"].append("no mobile teams available")
        return np.zeros((B, R), int), {**base_info, "warnings": warnings}
    unreachable = [inp.regions[r] for r in range(R) if not feasible[:, r].any() and g[r, 0] > 0]
    if unreachable:
        warnings.append(f"{len(unreachable)} region(s) with expected shortfall are beyond {res.max_travel_minutes:.0f} min of every base")
    nmax = g.shape[1] - 1
    method = spec.simulation.solver
    pairs = int(feasible.sum())
    if method == "auto":
        method = "milp" if pairs * nmax <= 40_000 else "greedy"
    if method == "milp":
        try:
            alloc, info = _milp(spec, g, weight, gap, feasible, unit_cost, per_base)
            return alloc, {**base_info, **info, "warnings": warnings + info.pop("warnings", [])}
        except _Infeasible as e:
            warnings.append(str(e))
            base_info["binding_constraints"].append(str(e))
        except Exception as e:  # noqa: BLE001
            warnings.append(f"MILP unavailable ({type(e).__name__}); fell back to greedy")
    alloc, info = _greedy(spec, g, weight, gap, feasible, unit_cost, per_base)
    return alloc, {**base_info, **info, "warnings": warnings + info.pop("warnings", [])}


class _Infeasible(Exception):
    pass


def _milp(spec, g, weight, gap, feasible, unit_cost, per_base):
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import lil_matrix
    B, R = feasible.shape
    nmax = g.shape[1] - 1
    lam = spec.assumptions.travel_cost_weight
    pairs = [(b, r) for b in range(B) for r in range(R) if feasible[b, r]]
    P = len(pairs)
    nv = P + R  # x_pairs (integer), u_r (continuous)
    c = np.zeros(nv)
    for k, (b, r) in enumerate(pairs):
        c[k] = lam * unit_cost[b, r] * 1e-3
    c[P:] = weight
    by_r: dict[int, list[int]] = {}
    by_b: dict[int, list[int]] = {}
    for k, (b, r) in enumerate(pairs):
        by_r.setdefault(r, []).append(k)
        by_b.setdefault(b, []).append(k)
    budget = spec.resources.total_budget_brl
    nf = spec.assumptions.equity_floor_regions
    floor_regions = [int(r) for r in np.argsort(-gap) if g[r, 0] > 0][:nf] if nf else []
    for r in floor_regions:
        if not by_r.get(r):
            raise _Infeasible(f"equity floor: region index {r} has no base within the travel limit")
    nrows = B + R * nmax + (1 if budget is not None else 0) + R + len(floor_regions)
    A = lil_matrix((nrows, nv))
    lb, ub = [], []
    ri = 0
    for b in range(B):  # team availability per base
        for k in by_b.get(b, []):
            A[ri, k] = 1
        lb.append(0)
        ub.append(per_base[b])
        ri += 1
    for r in range(R):  # u_r >= g_r(m) + d_m (n_r - m)  <=>  u_r - d_m n_r >= g_r(m) - d_m m
        ks = by_r.get(r, [])
        for m in range(nmax):
            d = g[r, m + 1] - g[r, m]
            A[ri, P + r] = 1
            for k in ks:
                A[ri, k] = -d
            lb.append(g[r, m] - d * m)
            ub.append(np.inf)
            ri += 1
    if budget is not None:
        for k, (b, r) in enumerate(pairs):
            A[ri, k] = unit_cost[b, r]
        lb.append(0)
        ub.append(budget)
        ri += 1
    for r in range(R):  # cap per region
        for k in by_r.get(r, []):
            A[ri, k] = 1
        lb.append(0)
        ub.append(nmax)
        ri += 1
    for r in floor_regions:
        for k in by_r[r]:
            A[ri, k] = 1
        lb.append(1)
        ub.append(np.inf)
        ri += 1
    A = A[:ri].tocsr()
    integrality = np.r_[np.ones(P), np.zeros(R)]
    bounds = Bounds(np.zeros(nv), np.r_[np.full(P, nmax), np.full(R, np.inf)])
    res = milp(c, constraints=LinearConstraint(A, lb, ub), integrality=integrality, bounds=bounds,
               options={"time_limit": 60, "mip_rel_gap": 1e-4})
    if res.status not in (0, 1) or res.x is None:
        raise _Infeasible(f"no feasible allocation: {res.message}"
                          + (" (budget below the cost of the equity floor?)" if floor_regions else ""))
    x = np.rint(res.x[:P]).astype(int)
    alloc = np.zeros((B, R), int)
    for k, (b, r) in enumerate(pairs):
        alloc[b, r] = x[k]
    cost = float((alloc * unit_cost).sum())
    binding = []
    if budget is not None and cost > budget * 0.97:
        binding.append("budget")
    for b in range(B):
        if alloc[b].sum() >= per_base[b]:
            binding.append(f"team availability at base {b}")
    return alloc, {"method": "milp", "solver": "HiGHS", "status": res.message, "optimality": "proven within 0.01% gap" if res.status == 0 else "time limit",
                   "cost_brl": cost, "binding_constraints": binding, "equity_floor_regions": floor_regions, "warnings": []}


def _greedy(spec, g, weight, gap, feasible, unit_cost, per_base):
    """Marginal-gain greedy: exact for separable convex shortfall with one resource type and equal costs; a
    documented heuristic otherwise (reported as such)."""
    B, R = feasible.shape
    nmax = g.shape[1] - 1
    lam = spec.assumptions.travel_cost_weight
    budget = spec.resources.total_budget_brl
    left = per_base.astype(int).copy()
    n = np.zeros(R, int)
    alloc = np.zeros((B, R), int)
    spent = 0.0
    big_cost = np.where(feasible, unit_cost, np.inf)
    floor = [r for r in np.argsort(-gap) if g[r, 0] > 0][: spec.assumptions.equity_floor_regions]
    warnings = []
    for r in floor:
        bs = [b for b in np.argsort(big_cost[:, r]) if feasible[b, r] and left[b] > 0]
        if not bs or (budget is not None and spent + big_cost[bs[0], r] > budget):
            warnings.append(f"equity floor not met for region index {r} (no base or budget)")
            continue
        b = bs[0]
        alloc[b, r] += 1
        n[r] += 1
        left[b] -= 1
        spent += big_cost[b, r]
    while left.sum() > 0:
        gain = np.where(n < nmax, weight * (g[np.arange(R), n] - g[np.arange(R), np.minimum(n + 1, nmax)]), 0.0)
        # best base per region: cheapest feasible with teams left
        cost_avail = np.where(left[:, None] > 0, big_cost, np.inf)
        bbest = np.argmin(cost_avail, axis=0)
        cbest = cost_avail[bbest, np.arange(R)]
        if budget is not None:
            gain = np.where(spent + cbest <= budget, gain, 0.0)
        score = gain - lam * np.where(np.isfinite(cbest), cbest, 0) * 1e-3
        score = np.where(np.isfinite(cbest), score, -np.inf)
        r = int(np.argmax(score))
        if not np.isfinite(score[r]) or gain[r] <= 1e-9 or score[r] <= 0:
            break
        b = int(bbest[r])
        alloc[b, r] += 1
        n[r] += 1
        left[b] -= 1
        spent += float(cbest[r])
    binding = []
    if left.sum() == 0:
        binding.append("all available teams assigned")
    elif budget is not None and spent > 0.9 * budget:
        binding.append("budget")
    else:
        binding.append("no further expected shortfall reduction worth the travel cost")
    return alloc, {"method": "greedy", "solver": "marginal-gain greedy", "optimality": "heuristic (exact for equal costs)",
                   "cost_brl": float(spent), "binding_constraints": binding, "warnings": warnings}


def compare_runs(a: dict, b: dict) -> dict:
    """Baseline/proposal of two runs on identical scales. Flags incompatibility instead of comparing apples to pears."""
    sa, sb = a["summary_json"], b["summary_json"]
    pa, pb = a["provenance_json"], b["provenance_json"]
    issues = []
    if pa.get("disease_id") != pb.get("disease_id"):
        issues.append("different diseases")
    if pa.get("signal_type") != pb.get("signal_type"):
        issues.append("different case signals")
    if pa.get("weeks") != pb.get("weeks"):
        issues.append("different target weeks")
    if set(pa.get("regions", [])) != set(pb.get("regions", [])):
        issues.append("different region scope")
    kind = "different resource scenarios" if pa.get("forecast_id") == pb.get("forecast_id") else "different model forecasts"
    keys = ("unmet_visits_baseline", "unmet_visits_proposed", "avoided_shortfall")
    return {"comparable": not issues, "issues": issues, "comparison_kind": kind,
            "a": {k: sa.get(k) for k in keys + ("cost_brl", "teams_allocated", "regions_over_capacity_proposed")},
            "b": {k: sb.get(k) for k in keys + ("cost_brl", "teams_allocated", "regions_over_capacity_proposed")},
            "delta_unmet_proposed_p50": (sb["unmet_visits_proposed"]["p50"] - sa["unmet_visits_proposed"]["p50"]) if not issues else None,
            "interpretation": "Scenario estimates under listed assumptions, not observed impact."}
