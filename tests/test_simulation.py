"""Operational simulation: reproducibility, constraints, honesty (spec §5, §11.2, acceptance scenario 7)."""
import numpy as np
import pytest
from pydantic import ValidationError

from healthpulse.core import jobs
from healthpulse.simulation import engine
from healthpulse.simulation import jobs as sim
from healthpulse.simulation.schemas import ScenarioSpec


def _inputs(R=12, W=4, seed=3, signal="notification"):
    rng = np.random.default_rng(seed)
    p50 = rng.gamma(2, 80, (R, W)).astype(np.float32)
    return engine.Inputs([f"{3300000 + i:07d}" for i in range(R)], [f"2026-11-{1 + 7 * w:02d}" for w in range(W)], p50 * 0.6, p50, p50 * 1.7,
                         rng.lognormal(11, 1, R).astype(np.float32), rng.uniform(-23, -20, R), rng.uniform(-45, -42, R),
                         rng.integers(1, 40, R).astype(np.float32), signal)


def _spec(**kw):
    base = dict(name="test scenario", forecast_id="f", resources={"mobile_teams_available": 8, "max_travel_minutes": 600,
                                                                 "team_bases": ["3300000"], "total_budget_brl": 60000},
                capacity={"visits_per_10k_residents_per_week": 0.05}, simulation={"draws": 400, "random_seed": 9})
    base.update(kw)
    return ScenarioSpec(**base)


def test_same_seed_same_result_and_crn():
    inp = _inputs()
    a = engine.run_operational(_spec(), inp)
    b = engine.run_operational(_spec(), inp, chunk_regions=3)  # chunking must not change the draws
    assert a["summary"]["unmet_visits_baseline"] == b["summary"]["unmet_visits_baseline"]
    assert a["regions"] == b["regions"]
    c = engine.run_operational(_spec(simulation={"draws": 400, "random_seed": 10}), inp)
    assert c["summary"]["unmet_visits_baseline"] != a["summary"]["unmet_visits_baseline"]
    # common random numbers: proposal never worse than baseline in any region-week
    for r in a["regions"]:
        assert r["unmet_proposed_p50"] <= r["unmet_baseline_p50"] + 1e-6
        assert r["unmet_baseline_p10"] >= 0


@pytest.mark.parametrize("solver", ["milp", "greedy"])
def test_budget_and_team_limits_hold(solver):
    inp = _inputs()
    out = engine.run_operational(_spec(simulation={"draws": 300, "random_seed": 1, "solver": solver}), inp)
    s = out["summary"]
    assert s["teams_allocated"] <= 8
    assert s["cost_brl"] <= 60000 + 1e-6
    assert s["optimizer"]["method"] == solver


def test_reduce_teams_to_zero_gives_no_allocation():
    out = engine.run_operational(_spec(resources={"mobile_teams_available": 0}), _inputs())
    assert out["summary"]["teams_allocated"] == 0
    assert "no mobile teams available" in out["summary"]["optimizer"]["binding_constraints"]


def test_sari_signal_is_not_multiplied_by_visit_rate():
    inp = _inputs(signal="sari_admission")
    out = engine.run_operational(_spec(assumptions={"visit_rate": 0.3}), inp)
    r = out["regions"][0]
    assert r["demand_p50"] == pytest.approx(r["cases_p50"], rel=0.25)


def test_unsafe_specs_rejected():
    with pytest.raises(ValidationError):
        ScenarioSpec(name="lives saved by teams", forecast_id="x")
    with pytest.raises(ValidationError):
        ScenarioSpec(name="neg cap", forecast_id="x", capacity={"explicit_visits_per_week": {"1": -5}})
    with pytest.raises(ValidationError):
        ScenarioSpec(name="bad rate", forecast_id="x", assumptions={"visit_rate": 1.5})


def test_pinned_scenario_reproduces_after_champion_change(champion, trained):
    """Scenario 7: rerunning an old scenario after the champion alias changes returns the identical result hash."""
    spec = ScenarioSpec(name="pinned", forecast_id=champion["forecast_id"], resources={"mobile_teams_available": 3, "max_travel_minutes": 800},
                        capacity={"visits_per_10k_residents_per_week": 0.05}, simulation={"draws": 300})
    sc = sim.save_scenario(spec, "u_scientist")
    r1 = sim.start_run(sc["scenario_id"], "u_scientist")
    jobs.run_job_inline(r1["job_id"])
    h1 = sim.get_run(r1["run_id"])["result_sha256"]
    from healthpulse.ml import registry as mreg
    m = mreg.register(trained, "hgb_poisson", "u_scientist")
    mreg.review(m["model_id"], m["version"], "clinical", "approve", "newer model", "u_reviewer", "epi_reviewer")
    mreg.review(m["model_id"], m["version"], "platform", "approve", "newer model", "u_admin", "platform_admin")
    d = mreg.deploy(m["model_id"], m["version"], "champion", "u_admin")
    jobs.run_job_inline(d["inference_job_id"])
    r2 = sim.start_run(sc["scenario_id"], "u_scientist")
    jobs.run_job_inline(r2["job_id"])
    run2 = sim.get_run(r2["run_id"])
    assert run2["result_sha256"] == h1
    assert run2["provenance_json"]["forecast_id"] == champion["forecast_id"]


def test_transmission_research_mode_is_labelled(fixture_data):
    spec = ScenarioSpec(name="research what-if", mode="transmission_research", region_ids=["3304557", "3550308"],
                        transmission={"weeks": 3, "contact_reduction_pct": 20}, simulation={"draws": 20})
    sc = sim.save_scenario(spec, "u_scientist")
    r = sim.start_run(sc["scenario_id"], "u_scientist")
    j = jobs.run_job_inline(r["job_id"])
    assert j["status"] == "succeeded", j["error"]
    s = sim.get_run(r["run_id"])["summary_json"]
    assert s["label"].startswith("HYPOTHETICAL") and s["individuals_simulated"] > 10_000_000
    assert s["infections_intervention"]["p50"] <= s["infections_no_intervention"]["p50"]
