"""IMDC benchmark: WIS against hand-computed values, epiweek season dates, no look-ahead past EW25. Offline."""
from datetime import date

import numpy as np
import polars as pl

from healthpulse.sprint import data as D
from healthpulse.sprint import models as M
from healthpulse.sprint import score as S


def test_wis_point_forecast_is_abs_error():
    # all quantiles equal to the median -> intervals have zero width, WIS = (0.5|y-m| + sum a/2 * 2/a |y-m|)/(K+0.5) = |y-m|
    q = np.full((1, len(D.QUANTILES)), 10.0)
    assert np.isclose(S.wis(q, np.array([14.0]))[0], 4.0)


def test_wis_inside_all_intervals_is_width_term():
    q = np.array([[0, 1, 2, 3, 5, 7, 8, 9, 10]], float)  # QUANTILES order
    y = np.array([5.0])
    widths = {0.5: 4, 0.2: 6, 0.1: 8, 0.05: 10}
    expect = sum(a / 2 * w for a, w in widths.items()) / 4.5
    assert np.isclose(S.wis(q, y)[0], expect)


def test_season_dates_mmwr():
    s = D.season_dates(2025)  # 2025 has an EW53
    assert s[0] == date(2025, 10, 5) and s[-1] == date(2026, 10, 4) and len(s) == 53
    assert len(D.season_dates(2023)) == 52 and D.season_dates(2023)[0] == date(2023, 10, 8)


def _synthetic(seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for uf in ("AA", "BB", "CC"):
        for s in range(2010, 2024):
            amp = rng.lognormal(6, 0.7)
            for k, dt in enumerate(D.season_dates(s)):
                y, w = (dt.year, int(dt.strftime("%U")))
                lam = 5 + amp * np.exp(-((k - 22) / 6) ** 2)
                rows.append({"date": dt, "uf": uf, "cases": float(rng.poisson(lam)), "season": s, "k": k})
    d = pl.DataFrame(rows)
    # epiweek from season position: EW41 + k, wrapping at the year's week count
    ew = []
    for s, k in d.select("season", "k").iter_rows():
        n1 = len(D.season_dates(s)) - 40  # weeks of year s from EW41 on
        ew.append(s * 100 + 41 + k if k < n1 else (s + 1) * 100 + (k - n1 + 1))
    d = d.with_columns(pl.Series("epiweek", ew)).with_columns(year=pl.col("epiweek") // 100, week=pl.col("epiweek") % 100)
    return d


def test_no_lookahead(monkeypatch):
    monkeypatch.setattr(M, "enso_weekly", lambda: pl.DataFrame({"epiweek": [201001], "enso": [0.0], "iod": [0.0], "pdo": [0.0]}))
    d = _synthetic()
    f1 = M.SeasonForecaster(d)
    a = f1.shifted(2021)
    # changing every value after the 2021 cut-off (EW25 2021) must not change the forecast
    d2 = d.with_columns(pl.when(pl.col("epiweek") > 202125).then(pl.col("cases") * 50).otherwise(pl.col("cases")).alias("cases"))
    b = M.SeasonForecaster(d2).shifted(2021)
    for x, y in zip(a, b):
        assert np.allclose(x.q, y.q)
    q = a[0].q
    assert (np.diff(q, axis=1) >= -1e-9).all()  # quantiles are ordered


def test_retro_replay_rules():
    from healthpulse.sprint import retro
    pop = 1e6  # threshold = 3,000 cumulative cases
    rising = np.array([10, 20, 40, 80, 160, 320, 640, 1280, 2560, 2560, 1000, 500] + [50] * 40, float)
    r = retro.replay_season(rising, pop, delay=2, horizon=4)
    assert r["c"] == 8  # cumsum first exceeds 3,000 in week 8
    assert r["a"] is not None and r["a"] < r["c"] + 2  # warned before the status quo sees it
    flat = retro.replay_season(rising, pop, delay=2, horizon=4, growth=False)
    assert flat["a"] >= r["a"]  # growth projection never later than carry-forward on a rising curve
    quiet = np.full(52, 20.0)  # 1,040 cases: never epidemic, never alerted
    assert retro.replay_season(quiet, pop, 2, 4) == {"c": None, "a": None}


def _ctx():
    return {"screen": "story", "lang": "en", "state": "MG",
            "replay_2023_24": {"label": "REPLAY", "incidence_per_100k_observed": 8350.0, "crossed_epidemic_level_week": "2024-01-07",
                               "status_quo_would_see_it": "2024-01-21", "healthpulse_alert_week": "2023-12-10", "weeks_gained": 6,
                               "weeks_gained_flat_rule": 4, "peak_week_observed": "2024-03-17"},
            "alert_rule_track_record_2015_2022": {"label": "REPLAY", "epidemics": 85, "missed": 0, "median_weeks_gained": 7.0,
                                                  "false_alarm_rate": 0.073},
            "outlook_2026_27": None, "benchmark": {}, "definitions": {}}


def test_rule_based_brief_uses_only_context_numbers():
    from healthpulse.sprint import brief
    t = brief.rule_based(_ctx())
    assert "6 weeks earlier" in t and "2023-12-10" in t and "every 14 non-epidemic seasons" in t
    assert "Rule-based brief" in t


def _stream_events(text):
    import json
    out = []
    for blk in text.split("\n\n"):
        ev = next((l[7:] for l in blk.splitlines() if l.startswith("event: ")), "message")
        data = next((l[6:] for l in blk.splitlines() if l.startswith("data: ")), None)
        if data:
            out.append((ev, json.loads(data)))
    return out


def test_brief_route_llm_and_fallback(client, tokens, monkeypatch):
    from healthpulse.llm import client as llm
    from healthpulse.sprint import brief
    monkeypatch.setattr(brief, "context", lambda uf, lang="en": _ctx())
    cfg = {"provider": "anthropic", "model": "claude-opus-5-5", "available": True, "enabled": True, "configured": True}
    monkeypatch.setattr(llm, "config", lambda: cfg)
    seen = {}

    def fake_stream(system, user, max_tokens=8000):
        seen["user"] = user
        yield "**Situation** — "
        yield "MG."
    monkeypatch.setattr(llm, "stream", fake_stream)
    ev = _stream_events(client.post("/api/v1/sprint/brief", json={"uf": "MG"}, headers=tokens["viewer"]).text)
    assert ev[0][0] == "meta" and ev[0][1]["provider"] == "anthropic"
    assert "".join(d.get("t", "") for e, d in ev if e == "message") == "**Situation** — MG."
    assert '"weeks_gained": 6' in seen["user"]  # the model is given the grounded CONTEXT

    def broken(*a, **k):
        raise llm.LLMError("boom")
        yield  # pragma: no cover
    monkeypatch.setattr(llm, "stream", broken)
    ev = _stream_events(client.post("/api/v1/sprint/brief", json={"uf": "MG"}, headers=tokens["viewer"]).text)
    assert any(e == "error" for e, _ in ev)
    assert "Rule-based brief" in "".join(d.get("t", "") for e, d in ev if e == "message")


def test_decision_requires_reviewer(client, tokens):
    assert client.post("/api/v1/sprint/decision", json={"uf": "MG", "decision": "approve"}, headers=tokens["viewer"]).status_code == 403
    r = client.post("/api/v1/sprint/decision", json={"uf": "MG", "decision": "reject", "note": "t"}, headers=tokens["epi_reviewer"])
    assert r.status_code == 200 and r.json()["data"]["decision"] == "reject"
