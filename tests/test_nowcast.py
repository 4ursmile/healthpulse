"""Nowcasting: delay recovery, calibration, point-in-time safety, nulls, the job, the artefact and the API. Offline, synthetic.

The synthetic row-level file has a KNOWN onset-to-availability delay distribution, so the estimator can be held to the truth.
"""
from datetime import date, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from healthpulse.ml import nowcast as nc

ORIGIN = date(2022, 1, 2)                                  # a Sunday
TRUE_PMF = np.array([0.05, 0.22, 0.20, 0.15, 0.10, 0.07, 0.05, 0.04, 0.03, 0.025, 0.02, 0.015, 0.01, 0.01, 0.005])
TRUE_PMF = TRUE_PMF / TRUE_PMF.sum()                       # delays 0..14 weeks


def synth_srag(path, weeks: int = 150, lam: float = 240.0, seed: int = 3, ufs=("35", "33"), cut_at_week: int | None = None):
    """One parquet shaped like the OpenDataSUS file (only the columns the nowcast reads). `cut_at_week` drops every record that
    became available after that week, i.e. the snapshot as it stood then."""
    rng = np.random.default_rng(seed)
    rows = []
    for w in range(weeks):
        for uf in ufs:
            n = rng.poisson(lam * (1.4 + np.sin(w / 14)) / len(ufs))
            k = rng.choice(len(TRUE_PMF), size=n, p=TRUE_PMF)
            for kk in k:
                onset = ORIGIN + timedelta(weeks=w, days=int(rng.integers(0, 7)))
                avail = ORIGIN + timedelta(weeks=w + int(kk), days=int(rng.integers(0, 7)))
                code = f"{uf}{('5030', '0001', '0002')[int(rng.integers(0, 3))]}"      # drawn before any skip so the RNG stream is identical
                if cut_at_week is not None and (avail - ORIGIN).days // 7 > cut_at_week:
                    continue
                rows.append((datetime.combine(onset, datetime.min.time()), datetime.combine(avail, datetime.min.time()), None, 5, code))
    df = pl.DataFrame(rows, schema={"DT_SIN_PRI": pl.Datetime, "DT_DIGITA": pl.Datetime, "DT_ENCERRA": pl.Datetime, "CLASSI_FIN": pl.Int64,
                                    "CO_MUN_RES": pl.Utf8}, orient="row")
    df.write_parquet(path)
    return path


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    p = tmp_path_factory.mktemp("nc") / "INFLUD_SYN.parquet"
    synth_srag(p)
    long, meta = nc.build_triangle([p], {"355030": "3550308"})
    return p, long, meta, nc.Triangle(long)


def test_triangle_holds_counts_only(synth):
    _, long, meta, tri = synth
    assert long.columns == ["level", "unit_id", "disease_id", "onset_week", "delay_w", "n"]   # no row-level or personal column survives
    assert set(long["level"].unique()) == {"national", "state", "municipality"} and set(long["unit_id"].unique()) >= {"BR", "SP", "RJ", "3550308"}
    assert meta["label"] == nc.LABEL and meta["columns_read"] == ["DT_SIN_PRI", "DT_DIGITA", "DT_ENCERRA", "CLASSI_FIN", "CO_MUN_RES"]
    assert tri.get("national", "BR", "covid19_sari") is not None and tri.get("national", "BR", "influenza_sari") is None


def test_estimated_delay_pmf_recovers_truth(synth):
    _, _, _, tri = synth
    cfg = nc.Config(max_delay=14, window=100, ess_cap=None)
    T = tri.n_weeks - 1
    c, ess, nw = nc.delay_counts(nc.asof(tri.get("national", "BR", "covid19_sari"), T), T, cfg, 0)
    est = c / c.sum()
    assert nw == 100 and ess > 10_000
    assert np.abs(est - TRUE_PMF).max() < 0.008


def test_nowcast_interval_coverage_near_nominal(synth):
    _, _, _, tri = synth
    cfg = nc.Config(max_delay=14, window=60, ess_cap=None, draws=300, horizon=8, min_cdf=0.2)
    bt = nc.backtest(tri, "covid19_sari", cfg, nc.unit_layout(tri, "covid19_sari"), t_first=80, t_last=tri.n_weeks - 1 - 14, levels=("national",))
    e = bt.filter(pl.col("status") == "nowcast")
    assert e.height > 300
    cov80 = float(((e["truth"] >= e["p10"]) & (e["truth"] <= e["p90"])).mean())
    cov95 = float(((e["truth"] >= e["p025"]) & (e["truth"] <= e["p975"])).mean())
    assert 0.72 <= cov80 <= 0.88 and 0.90 <= cov95 <= 0.99, (cov80, cov95)
    wape = float((e["est"] - e["truth"]).abs().sum() / e["truth"].sum())
    wape_raw = float((e["observed"] - e["truth"]).abs().sum() / e["truth"].sum())
    assert wape < 0.5 * wape_raw                           # a real gain over reporting the raw count
    assert abs(float((e["est"] - e["truth"]).sum() / e["truth"].sum())) < 0.03   # essentially unbiased


def test_data_entered_after_asof_changes_nothing(synth):
    """PIT: perturbing every cell with s + k > T (what was entered after the as-of week) must not move any output."""
    _, _, _, tri = synth
    cfg = nc.Config(max_delay=14, window=60, ess_cap=None, draws=200, horizon=8)
    T = 100
    arr = tri.get("national", "BR", "covid19_sari")
    lay = nc.unit_layout(tri, "covid19_sari")
    base = nc.run_asof(tri, "covid19_sari", T, cfg, lay, display_weeks=30)
    s = np.arange(arr.shape[0])[:, None]
    k = np.arange(arr.shape[1])[None, :]
    future = (s + k) > T
    assert future.any()
    tri.arr[("national", "BR", "covid19_sari")] = np.where(future, arr * 7 + 1000, arr)
    for key in [x for x in tri.arr if x[0] == "state"]:
        tri.arr[key] = np.where(future, tri.arr[key] * 5 + 500, tri.arr[key])
    try:
        again = nc.run_asof(tri, "covid19_sari", T, cfg, lay, display_weeks=30)
    finally:
        tri.arr[("national", "BR", "covid19_sari")] = arr
    assert base.equals(again)
    masked = nc.asof(np.where(future, 99.0, arr), T)
    assert nc.pit_ok(masked, T) and masked[future].sum() == 0


def test_unusable_weeks_are_null_not_zero(synth):
    _, _, _, tri = synth
    cfg = nc.Config(max_delay=14, window=60, ess_cap=None, draws=100, horizon=6, min_cdf=0.2)
    df = nc.run_asof(tri, "covid19_sari", 120, cfg, nc.unit_layout(tri, "covid19_sari"), display_weeks=20)
    nat = df.filter((pl.col("level") == "national")).sort("week_start")
    newest = nat.filter(pl.col("age_w") == 0).row(0, named=True)
    assert newest["status"] == "insufficient_data" and newest["nowcast_p50"] is None and newest["p10"] is None and newest["observed"] >= 0
    mid = nat.filter(pl.col("age_w") == 5).row(0, named=True)
    assert mid["status"] == "nowcast" and mid["p10"] <= mid["nowcast_p50"] <= mid["p90"] and mid["nowcast_p50"] >= mid["observed"]
    old = nat.filter(pl.col("age_w") == 19).row(0, named=True)
    assert old["status"] in ("complete", "unmodelled")      # weeks older than the model horizon are shown as reported, with no band


def test_too_little_history_gives_no_nowcast(synth):
    _, _, _, tri = synth
    cfg = nc.Config(max_delay=14, window=60)
    assert nc.fit_posteriors(tri, "covid19_sari", 18, cfg, 0, nc.unit_layout(tri, "covid19_sari")) == {}


def test_snapshot_cut_matches_triangle_asof(tmp_path):
    """The as-of view of the full triangle equals the triangle of a file that was cut at that week (the reconstruction is exact)."""
    full, cut = tmp_path / "full.parquet", tmp_path / "cut.parquet"
    synth_srag(full, weeks=60, lam=120, seed=9, ufs=("35",))
    synth_srag(cut, weeks=60, lam=120, seed=9, ufs=("35",), cut_at_week=45)
    tf, tc = nc.Triangle(nc.build_triangle([full])[0]), nc.Triangle(nc.build_triangle([cut])[0])
    a = nc.asof(tf.get("national", "BR", "covid19_sari"), 45)
    b = nc.asof(tc.get("national", "BR", "covid19_sari"), 45)
    n = min(len(a), len(b))
    assert np.array_equal(a[:n], b[:n])


def test_job_artifact_registry_and_api(tmp_path, client, tokens, fixture_data):
    from healthpulse.core import db, jobs
    p = tmp_path / "INFLUD_SYN.parquet"
    synth_srag(p, weeks=140, lam=200, seed=5)
    cfg = {"max_delay": 14, "window": 60, "ess_cap": None, "draws": 150, "horizon": 8, "min_window": 8}
    j = jobs.enqueue("nowcast", {"paths": [str(p)], "config": cfg, "diseases": ["covid19_sari"], "municipality_ids": ["3550308"]}, actor_id="u_scientist")
    r = jobs.run_job_inline(j["job_id"])
    assert r["status"] in ("succeeded", "succeeded_with_warnings"), r
    res = r["result"]
    row = db.q1("SELECT * FROM nowcast_run WHERE nowcast_id=?", res["nowcast_id"])
    assert row["status"] == res["status"] and row["label"] == nc.LABEL
    from healthpulse.core import lake
    d = lake.abs_artifact(row["artifact_uri"])
    names = {f.name for f in d.iterdir()}
    assert {"nowcast.parquet", "backtest.parquet", "triangle.parquet", "metrics.json", "provenance.json"} <= names
    assert all(not (f.stat().st_mode & 0o222) for f in d.iterdir())            # immutable: read-only files
    prov = __import__("json").loads((d / "provenance.json").read_text())
    assert prov["input_files"][0]["sha256"] and prov["outputs"]["nowcast.parquet"] and "viet_nam" in prov and prov["gates"]["pit_audit"] is True
    if res["status"] != "published":
        pytest.skip(f"synthetic gate failed ({res['gates']}); API served only for published runs")
    # API: envelope, caveat, label, municipality series, fallback, typed errors, RBAC
    s = client.get(f"/api/v1/nowcasts/{res['nowcast_id']}/series?disease_id=covid19_sari&municipality_id=3550308&weeks=20")
    assert s.status_code == 200
    j = s.json()
    assert set(j) >= {"data", "meta", "warnings", "request_id"} and j["meta"]["label"] == nc.LABEL and j["meta"]["type"] == "NOWCAST"
    assert j["data"]["unit_level"] == "municipality" and not j["data"]["fallback"] and len(j["data"]["rows"]) == 20
    assert any("first-entry" in w for w in j["warnings"])
    fb = client.get(f"/api/v1/nowcasts/{res['nowcast_id']}/series?disease_id=covid19_sari&municipality_id=3304557").json()
    assert fb["data"]["fallback"] is True and fb["data"]["unit_level"] in ("state", "national")
    assert client.get(f"/api/v1/nowcasts/{res['nowcast_id']}/series?disease_id=dengue").status_code == 422
    assert client.get("/api/v1/nowcasts/nc_nope").status_code == 404
    assert client.get("/api/v1/nowcasts/latest/series?disease_id=covid19_sari").status_code == 200
    assert client.get(f"/api/v1/nowcasts/{res['nowcast_id']}/backtest").json()["data"]["gates"]["pit_audit"] is True
    assert client.post("/api/v1/nowcasts", json={}).status_code == 401
    assert client.post("/api/v1/nowcasts", json={}, headers=tokens["viewer"]).status_code == 403
    assert client.post("/api/v1/nowcasts", json={"diseases": ["dengue"]}, headers=tokens["data_scientist"]).status_code == 422
    ok = client.post("/api/v1/nowcasts", json={"diseases": ["covid19_sari"], "asof": "2026-01-03"}, headers=tokens["data_scientist"])
    assert ok.status_code == 202 and ok.json()["data"]["job_id"]


def test_synthetic_snapshot_is_refused(fixture_data):
    """The fixture SIVEP data has no entry/closure dates and is SYNTHETIC: the job must say so, not invent delays."""
    from healthpulse.core import jobs
    j = jobs.enqueue("nowcast", {}, actor_id="u_scientist")
    r = jobs.run_job_inline(j["job_id"])
    assert r["status"] == "failed" and ("SYNTHETIC" in (r.get("error") or "") or "snapshot" in (r.get("error") or ""))
