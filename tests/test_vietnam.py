"""Viet Nam module: crosswalk integrity, geometry simplification, alert classification, simulator smoke test."""
import pytest

from healthpulse.vietnam import crosswalk
from healthpulse.vietnam.data import simplify_geometry
from healthpulse.vietnam.model import classify


def test_crosswalk_is_complete_and_code_only():
    crosswalk.check()
    assert crosswalk.GAUL_TO_NEW["1012370"] == "VN01"  # Hà Tây -> Hà Nội
    assert crosswalk.GAUL_TO_NEW["1012419"] == "VN52"  # Gia Lai (shares ISO VN-30 with Hà Tây in OpenDengue)
    assert crosswalk.GAUL_TO_NEW["1012357"] == "VN68"  # Bình Thuận -> Lâm Đồng


def test_simplify_keeps_valid_rings():
    import math
    ring = [[math.cos(t / 100 * 2 * math.pi), math.sin(t / 100 * 2 * math.pi)] for t in range(100)] + [[1.0, 0.0]]
    g = simplify_geometry({"type": "Polygon", "coordinates": [ring]}, tol=0.01)
    assert g["type"] == "MultiPolygon" and 4 <= len(g["coordinates"][0][0]) < 100


@pytest.mark.parametrize("inc,ratio,lvl", [(30, 1.6, "alert"), (6, 1.3, "warning"), (2, 1.12, "watch"), (30, 0.9, "watch"),
                                           (3, 0.8, "normal"), (None, 1.0, "no_data")])
def test_classify(inc, ratio, lvl):
    assert classify(inc, ratio) == lvl


def test_simulator_smoke():
    from healthpulse.vietnam import data
    if not data.is_built():
        pytest.skip("Viet Nam data not built (hp vn-fetch)")
    from healthpulse.vietnam import sim
    r = sim.run(sim.SimSpec(days=30, trials=20, teams=4))
    assert len(r["provinces"]) == 34 and len(r["dates"]) == 30
    assert r["plan"]["kpi"]["unmet_bed_days"]["p50"] <= r["baseline"]["kpi"]["unmet_bed_days"]["p50"] + 1e-6
    again = sim.run(sim.SimSpec(days=30, trials=20, teams=4))
    assert again["baseline"]["kpi"] == r["baseline"]["kpi"]  # same seed, same result


def _built():
    from healthpulse.vietnam import data
    if not data.is_built():
        pytest.skip("Viet Nam data not built (hp vn-fetch)")


def test_sandbox_actions_and_deterministic_rewind():
    _built()
    from healthpulse.vietnam import advisor, sandbox as sb
    s = sb.create(sb.SandboxSpec(pathogen=sb.PathogenSpec(key="covid"), seeds=[sb.Seed(province_id="VN01", infections=400)], trials=6))
    s.step(20)
    _, applied, errors = s.step(0, [sb.Action(type="lockdown", province_id="VN01", value=0.5),
                                    sb.Action(type="vector_control", province_id="VN01", value=0.2)])
    assert len(applied) == 1 and "vector" in errors[0]["error"]  # vector control refused for a respiratory pathogen
    s.step(15, advisor.rule_actions(s))
    end = s.history[-1]["nat"]
    again = sb.rewind(s, s.day)
    assert again.history[-1]["nat"] == end  # replaying the action log reproduces the run exactly
    assert sb.rewind(again, 10).day == 10


def test_sandbox_lockdown_reduces_spread():
    _built()
    from healthpulse.vietnam import sandbox as sb
    base = dict(pathogen=sb.PathogenSpec(key="pandemic_flu"), seeds=[sb.Seed(province_id="VN79", infections=300)], trials=6)
    a = sb.create(sb.SandboxSpec(**base))
    b = sb.create(sb.SandboxSpec(**base))
    b.step(0, [sb.Action(type="lockdown", province_id="ALL", value=0.5)])
    a.step(60)
    b.step(60)
    assert b.history[-1]["nat"]["cum_inf"][1] < a.history[-1]["nat"]["cum_inf"][1]


def test_advisor_parse_drops_invalid_actions():
    from healthpulse.vietnam import advisor
    text = 'Sure.\n```json\n{"summary": "Act now.", "actions": [{"type": "lockdown", "province_id": "VN79", "value": 0.4, "why": "fast growth"},' \
           '{"type": "nuke", "province_id": "VN79"}, {"type": "team", "province_id": "XX99", "value": 2}, {"type": "autopilot", "value": 1}]}\n```'
    summary, acts, bad = advisor.parse(text)
    assert summary == "Act now." and [a.type for a in acts] == ["lockdown"] and len(bad) == 3


def test_llm_admin_settings_and_fallback(client, tokens, monkeypatch):
    from healthpulse.llm import client as llm
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert client.get("/api/v1/admin/ai/llm", headers=tokens["viewer"]).status_code == 403
    r = client.put("/api/v1/admin/ai/llm", json={"provider": "anthropic", "model": "claude-sonnet-5-5", "effort": "high"},
                   headers=tokens["platform_admin"])
    assert r.status_code == 200 and r.json()["data"]["config"]["model"] == "claude-sonnet-5-5"
    c = llm.config()
    assert c["source"] == "admin" and not c["available"] and c["missing"] == "ANTHROPIC_API_KEY"
    assert llm.test()["ok"] is False  # no key: reported, never raised
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert llm.config()["available"]
    client.put("/api/v1/admin/ai/llm", json={"enabled": False}, headers=tokens["platform_admin"])
    assert not llm.config()["available"]  # admin switch forces the rule-based analyst
    assert "sk-test" not in client.get("/api/v1/admin/ai/llm", headers=tokens["platform_admin"]).text
    assert client.delete("/api/v1/admin/ai/llm", headers=tokens["platform_admin"]).json()["data"]["config"]["source"] == "env"
