"""Live response: growth fit, R conversion, projection levers, and (with Viet Nam data) the incident workflow + RBAC."""
import math

import numpy as np
import pytest

from healthpulse.ops import analysis, project
from healthpulse.vietnam import sandbox as sb

PS = sb.PATHOGENS["covid"]


def test_poisson_trend_recovers_growth():
    rng = np.random.default_rng(0)
    y = rng.poisson(5 * np.exp(0.12 * np.arange(14))).astype(float)
    f = analysis.poisson_trend(y)
    assert f and f["lo"] < 0.12 < f["hi"] and abs(f["r"] - 0.12) < 0.04


def test_poisson_trend_refuses_thin_data():
    assert analysis.poisson_trend(np.array([0, 1, 0, 2.0])) is None


def test_R_r_roundtrip():
    L, D = analysis.gen_params(PS)
    for R in (0.6, 1.0, 2.5, 6.0):
        assert math.isclose(float(analysis.R_from_r(analysis.r_from_R(R, L, D), L, D)), R, rel_tol=1e-6)
    assert abs(float(analysis.r_from_R(1.0, L, D))) < 1e-9


def _unit(R=2.5):
    return [{"id": "C1", "label": "C1", "lat": 10.8, "lon": 106.7, "province_id": "VN79", "I0": 20.0, "R": R, "R_lo": R * 0.8, "R_hi": R * 1.2,
             "N": 200000.0, "cum_inf": 500.0, "radius_km": 3.0}]


def test_levers_reduce_cases_with_diminishing_returns():
    us = _unit()
    zm = np.ones((1, analysis.HORIZON))
    z = lambda k, v: {kk: np.array([v if kk == k else 0.0]) for kk in ("team", "vaccine", "test_kit")}  # noqa: E731
    base = project.simulate(us, PS, 0.75, zm).sum()
    q = project.simulate(us, PS, 0.75, zm * 0.5).sum()
    assert q < base
    t1 = project.simulate(us, PS, 0.75, zm, z("team", 1), z("team", 0)).sum()
    t2 = project.simulate(us, PS, 0.75, zm, z("team", 2), z("team", 0)).sum()
    t3 = project.simulate(us, PS, 0.75, zm, z("team", 3), z("team", 0)).sum()
    assert base > t1 > t2 > t3
    late = project.simulate(us, PS, 0.75, zm, z("team", 1), z("team", 10)).sum()
    assert t1 < late <= base + 1e-6          # a far depot (late arrival) is worth less
    assert project.simulate(us, PS, 0.75, zm, z("vaccine", 50000), z("vaccine", 0)).sum() < base
    lo, hi = (project.simulate(us, PS, 0.75, zm, band=b).sum() for b in ("lo", "hi"))
    assert lo < base < hi


def test_real_zone_already_in_data_is_not_counted_twice():
    from datetime import timedelta

    from healthpulse.core.util import iso, utcnow
    z = lambda days, basis="real": {"basis": basis, "params": {"activated_at": iso(utcnow() - timedelta(days=days))}}  # noqa: E731
    assert analysis.zone_active_weight(z(0)) == pytest.approx(1, abs=0.01)
    assert analysis.zone_active_weight(z(30)) == 0
    assert 0 < analysis.zone_active_weight(z(3)) < 1
    assert analysis.zone_active_weight(z(30, "simulated")) == 1


# ---------------------------------------------------------------- integration (needs `hp vn-fetch`)
def _built():
    from healthpulse.vietnam import data
    if not data.is_built():
        pytest.skip("Viet Nam data not built (hp vn-fetch)")


def test_drill_workflow_and_optimizer():
    _built()
    from healthpulse.ops import drill, optimize, store
    inc = store.create_incident(store.IncidentIn(name="t1", pathogen="covid", mode="drill"), "u_admin")
    iid = inc["incident_id"]
    drill.seed(iid, "u_admin")
    an = analysis.analyze(iid)
    assert an["clusters"] and an["suggestions"] and an["kpis"]["r_basis"] == "fitted"
    s = next(x for x in an["suggestions"] if x["kind"] == "quarantine")
    store.add_zone(iid, store.ZoneIn(kind="quarantine", geometry=s["geometry"], basis="simulated"), "u_admin")
    an = analysis.analyze(iid)
    pr = an["projection"]
    assert pr["plan"]["cum_cases"] < pr["current"]["cum_cases"] == pr["baseline"]["cum_cases"]
    o = optimize.optimize(an)
    assert o["allocations"] and o["summary"]["cases_after"] < o["summary"]["cases_before"]
    assert all(a["qty"] > 0 for a in o["allocations"])


def test_live_incident_refuses_synthetic_data():
    _built()
    from healthpulse.ops import drill, store
    inc = store.create_incident(store.IncidentIn(name="real", pathogen="measles", mode="live"), "u_admin")
    with pytest.raises(PermissionError):
        drill.seed(inc["incident_id"], "u_admin")


def test_rbac_real_zone_needs_command():
    _built()
    from fastapi.testclient import TestClient

    from healthpulse.api.main import create_app
    from healthpulse.core.security import demo_token
    c = TestClient(create_app())
    op = {"Authorization": f"Bearer {demo_token('u_operator')}"}
    rv = {"Authorization": f"Bearer {demo_token('u_reviewer')}"}
    assert c.post("/api/v1/ops/incidents", json={"name": "live", "mode": "live"}, headers=op).status_code == 403
    iid = c.post("/api/v1/ops/incidents", json={"name": "drill", "mode": "drill"}, headers=op).json()["data"]["incident_id"]
    from healthpulse.ops.geo import circle
    body = {"kind": "quarantine", "geometry": circle(10.78, 106.70, 2), "basis": "real"}
    assert c.post(f"/api/v1/ops/incidents/{iid}/zones", json=body, headers=op).status_code == 403
    assert c.post(f"/api/v1/ops/incidents/{iid}/zones", json={**body, "basis": "simulated"}, headers=op).status_code == 200
    r = c.post(f"/api/v1/ops/incidents/{iid}/zones", json=body, headers=rv)
    assert r.status_code == 200 and r.json()["data"]["params"]["people_est"] > 1000
    assert c.delete(f"/api/v1/ops/incidents/{iid}/zones/{r.json()['data']['zone_id']}", headers=rv).status_code == 409
    assert c.post(f"/api/v1/ops/incidents/{iid}/reports", json={"reports": [{"kind": "case"}]}, headers=op).status_code == 422
    assert c.post(f"/api/v1/ops/incidents/{iid}/reports", json={"reports": [{"kind": "case", "lat": 10.78, "lon": 106.7}]}).status_code == 401
