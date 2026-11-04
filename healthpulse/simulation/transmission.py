"""Transmission what-if — RESEARCH MODE ONLY (spec §5.5, P2).

A stochastic metapopulation SEIR (chain-binomial, daily steps) over every region's full resident population — millions
of individuals per municipality — vectorised over trials × regions. Inter-region mixing uses a gravity kernel because
no authorised origin-destination data exist; that kernel, R0, latent/infectious periods, reporting fraction and the
intervention's contact reduction are explicit, uncalibrated assumptions. Results are labelled HYPOTHETICAL and are
never shown as expected infections averted by a real programme.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

from .engine import haversine_km
from .schemas import ScenarioSpec

VERSION = "hp-seir 1.0 (research)"


def gravity(pop: np.ndarray, lat: np.ndarray, lon: np.ndarray, coupling: float) -> np.ndarray:
    R = len(pop)
    if R == 1:
        return np.ones((1, 1))
    d = haversine_km(lat[:, None], lon[:, None], lat[None, :], lon[None, :]) + 10.0
    k = (pop[None, :] / d ** 2)
    np.fill_diagonal(k, 0)
    k = k / np.maximum(k.sum(axis=1, keepdims=True), 1e-12) * coupling
    np.fill_diagonal(k, 1 - coupling)
    return k


def run(spec: ScenarioSpec, regions: list[str], pop: np.ndarray, lat: np.ndarray, lon: np.ndarray, recent_cases: np.ndarray,
        progress: Callable[[float, str], None] | None = None) -> dict:
    t = spec.transmission
    prog = progress or (lambda *_: None)
    K, R = spec.simulation.draws, len(regions)
    days = t.weeks * 7
    rng = np.random.default_rng([spec.simulation.random_seed, 99])
    r0 = rng.uniform(t.r0_low, t.r0_high, size=(K, 1))
    sigma, gamma = 1 / t.latent_days, 1 / t.infectious_days
    beta = r0 * gamma
    M = gravity(pop.astype(float), lat, lon, t.coupling)
    N = np.broadcast_to(pop.astype(np.int64), (K, R)).copy()
    # initial infectious from last week's reported cases scaled by the reporting fraction (assumption)
    pop_i = pop.astype(np.int64)
    I0 = np.minimum(np.round(recent_cases / t.reporting_fraction * (t.infectious_days / 7)).astype(np.int64), pop_i // 10)
    E0 = np.minimum(np.round(I0 * t.latent_days / t.infectious_days).astype(np.int64), pop_i // 10)
    Rm = np.round(N * t.immune_share).astype(np.int64)
    out = {}
    for arm, red in (("no_intervention", 0.0), ("intervention", t.contact_reduction_pct / 100)):
        arng = np.random.default_rng([spec.simulation.random_seed, 7])  # same stream for both arms: CRN
        S = N - I0[None] - E0[None] - Rm
        E = np.broadcast_to(E0, (K, R)).copy()
        I = np.broadcast_to(I0, (K, R)).copy()
        Rc = Rm.copy()
        weekly = np.zeros((K, R, t.weeks), np.int64)
        for d in range(days):
            w = d // 7
            red_now = red if (w + 1) >= t.intervention_start_week else 0.0
            prev = I / np.maximum(N, 1)
            force = beta * (1 - red_now) * (prev @ M.T)
            p_inf = 1 - np.exp(-np.clip(force, 0, 50))
            new_e = arng.binomial(S, p_inf)
            new_i = arng.binomial(E, 1 - np.exp(-sigma))
            new_r = arng.binomial(I, 1 - np.exp(-gamma))
            S -= new_e
            E += new_e - new_i
            I += new_i - new_r
            Rc += new_r
            weekly[:, :, w] += new_i
            if d % 7 == 6:
                prog((0.1 if arm == "no_intervention" else 0.55) + 0.45 * (d + 1) / days, f"{arm}: day {d + 1}/{days}")
        reported = arng.binomial(weekly, t.reporting_fraction)
        out[arm] = {"infections": weekly, "reported": reported}
    q = [10, 50, 90]
    rows = []
    for i, r in enumerate(regions):
        for w in range(t.weeks):
            a = out["no_intervention"]["reported"][:, i, w]
            b = out["intervention"]["reported"][:, i, w]
            pa, pb = np.percentile(a, q), np.percentile(b, q)
            rows.append({"municipality_id": r, "week": w + 1, "reported_p10": float(pa[0]), "reported_p50": float(pa[1]),
                         "reported_p90": float(pa[2]), "reported_int_p10": float(pb[0]), "reported_int_p50": float(pb[1]),
                         "reported_int_p90": float(pb[2])})
    tot_a = out["no_intervention"]["infections"].sum(axis=(1, 2))
    tot_b = out["intervention"]["infections"].sum(axis=(1, 2))
    summary = {
        "label": "HYPOTHETICAL — transmission research mode; parameters are uncalibrated assumptions",
        "individuals_simulated": int(pop.sum()), "trials": K, "regions": R, "days": days,
        "infections_no_intervention": dict(zip(("p10", "p50", "p90"), map(float, np.percentile(tot_a, q)))),
        "infections_intervention": dict(zip(("p10", "p50", "p90"), map(float, np.percentile(tot_b, q)))),
        "relative_change_p50": float(np.median((tot_b - tot_a) / np.maximum(tot_a, 1))),
        "assumptions": t.model_dump(), "engine": VERSION,
        "interpretation_status": "hypothetical_model_output_not_programme_effect",
    }
    return {"summary": summary, "regions": rows}
