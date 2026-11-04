"""Polygon crosswalk + Meta mobility profile: synthetic polygons and rows only, no network."""
import numpy as np
import polars as pl
import pytest
from shapely.geometry import box, mapping

from healthpulse.geo import crosswalk as cw


def _layer(boxes: dict[str, tuple]):
    feats = [{"geometry": mapping(box(*b)), "id": k} for k, b in boxes.items()]
    return cw.to_geoms(feats, lambda f: f["id"])


def _xw(src, tgt, **kw):
    si, sg = _layer(src)
    ti, tg = _layer(tgt)
    return cw.build(si, sg, ti, tg, cw.Params(**kw) if kw else None)


TARGETS = {"T1": (0, 0, 2, 2), "T2": (2, 0, 4, 2), "T3": (0, 2, 2, 4), "T4": (2, 2, 4, 4)}


def test_nested_merge_split_and_one_to_one():
    src = {"S_same": (0, 0, 2, 2), "S_a": (2, 0, 3, 2), "S_b": (3, 0, 4, 2), "S_split": (1, 2, 3, 4)}
    df, rep = _xw(src, TARGETS)
    r = {(a, b): (w, rel) for a, b, w, rel in df.select("source_id", "target_id", "weight", "relation").iter_rows()}
    assert r[("S_same", "T1")] == (1.0, "ONE_TO_ONE")
    assert r[("S_a", "T2")][1] == "MANY_TO_ONE" and r[("S_b", "T2")][1] == "MANY_TO_ONE"
    assert r[("S_split", "T3")][1] == "ONE_TO_MANY" and r[("S_split", "T3")][0] == pytest.approx(0.5, abs=1e-6)
    assert r[("S_split", "T4")][0] == pytest.approx(0.5, abs=1e-6)
    # weights of every accepted source sum to 1
    sums = df.filter(pl.col("status") == "ACCEPTED").group_by("source_id").agg(pl.col("weight").sum())["weight"].to_list()
    assert np.allclose(sums, 1.0)
    assert rep["source_status"] == {"ACCEPTED": 4, "AMBIGUOUS": 0, "REFUSED": 0}
    assert rep["area_matched_share"] == pytest.approx(1.0)


def test_sliver_is_dropped_and_reported():
    df, rep = _xw({"S": (0, 0, 2, 2)}, {"T1": (0, 0, 2, 2), "T1b": (1.99, 0, 3, 2)})
    acc = df.filter(pl.col("status") == "ACCEPTED")
    assert acc.height == 1 and acc["target_id"][0] == "T1" and acc["weight"][0] == 1.0
    assert rep["sliver_area_dropped_share"] > 0


def test_ambiguous_is_not_guessed():
    grid = {f"G{i}{j}": (i, j, i + 1, j + 1) for i in range(4) for j in range(4)}
    df, rep = _xw({"BIG": (0, 0, 4, 4)}, grid)
    assert df["status"].to_list() == ["AMBIGUOUS"] and df["weight"].to_list() == [0.0]
    assert rep["ambiguous_source_ids"] == ["BIG"]
    # an ambiguous source contributes nothing to an aggregation
    agg = cw.aggregate(df, pl.DataFrame({"source_id": ["BIG"], "v": [0.7]}), ["v"])
    assert agg.height == 0


def test_refused_when_target_layer_does_not_cover_source():
    df, rep = _xw({"OUT": (10, 10, 12, 12), "EDGE": (3.5, 0, 6.5, 2)}, TARGETS)
    st = dict(df.select("source_id", "status").iter_rows())
    assert st["OUT"] == "REFUSED" and st["EDGE"] == "REFUSED"  # only 17% of EDGE is inside the layer
    assert df.filter(pl.col("source_id") == "OUT")["target_id"][0] is None


def test_deterministic_regardless_of_input_order():
    src = {"S_same": (0, 0, 2, 2), "S_a": (2, 0, 3, 2), "S_b": (3, 0, 4, 2), "S_split": (1, 2, 3, 4)}
    a, _ = _xw(src, TARGETS)
    b, _ = _xw(dict(reversed(list(src.items()))), dict(reversed(list(TARGETS.items()))))
    key = ["source_id", "target_id"]
    assert a.sort(key).drop("inter_km2", "share_of_source", "share_of_target", "iou").equals(
        b.sort(key).drop("inter_km2", "share_of_source", "share_of_target", "iou"))


def test_equal_area_projection_preserves_area_ratios():
    # a 1x1 degree box near the equator and one at 60N must differ in area by cos(lat) once projected
    ids, g = _layer({"eq": (0, 0, 1, 1), "n60": (0, 60, 1, 61)})
    import shapely
    pr = shapely.transform(g, cw.laea(0.5, 30))
    a = shapely.area(pr)
    assert a[1] / a[0] == pytest.approx(np.cos(np.radians(60.5)) / np.cos(np.radians(0.5)), rel=0.01)


def test_aggregate_area_and_mass_weights_and_nulls():
    src = {"S_a": (2, 0, 3, 2), "S_b": (3, 0, 4, 2), "S_same": (0, 0, 2, 2)}
    df, _ = _xw(src, TARGETS)
    vals = pl.DataFrame({"source_id": ["S_a", "S_b", "S_same"], "v": [0.2, 0.6, None]})
    # equal areas -> simple mean in T2; T1's only source has a missing value -> null, never 0
    ag = cw.aggregate(df, vals, ["v"]).to_dict(as_series=False)
    by = dict(zip(ag["target_id"], ag["v"]))
    assert by["T2"] == pytest.approx(0.4, abs=1e-6) and by["T1"] is None
    # population weights: S_b has 3x the people -> 0.5
    ag = cw.aggregate(df, vals, ["v"], source_mass={"S_a": 1000.0, "S_b": 3000.0, "S_same": 10.0})
    assert dict(zip(ag["target_id"], ag["v"]))["T2"] == pytest.approx(0.5, abs=1e-6)
    assert set(ag["weight_basis"]) == {"population-weighted"}


def test_aggregate_nulls_where_target_is_poorly_covered():
    df, _ = _xw({"S": (0, 0, 2, 2)}, {"T": (0, 0, 2, 20)})  # S is contained in T but covers 10% of it
    ag = cw.aggregate(df, pl.DataFrame({"source_id": ["S"], "v": [0.5]}), ["v"])
    assert ag["v"][0] is None and ag["target_cover"][0] < 0.2


def test_audit_reports_agreement_without_changing_weights():
    src = {"S_same": (0, 0, 2, 2), "S_a": (2, 0, 3, 2)}
    df, _ = _xw(src, TARGETS)
    before = df["weight"].to_list()
    a = cw.audit(df, {"S_same": "T1", "S_a": "T4"})
    assert a["n"] == 2 and a["agree"] == 0.5 and a["disagreeing_source_ids"] == ["S_a"]
    assert df["weight"].to_list() == before


# ---------------------------------------------------------------- Meta mobility (synthetic rows only)
def _meta_rows(gid, ds, f0, f1, f2, f3, country="VNM"):
    return [{"gadm_id": gid, "gadm_name": "x", "country": country, "polygon_level": "2", "home_to_ping_distance_category": c,
             "distance_category_ping_fraction": str(v), "ds": ds} for c, v in zip(["0", "(0, 10)", "[10, 100)", "100+"], [f0, f1, f2, f3]) if v is not None]


def test_pivot_bands_and_unknown_band():
    from healthpulse.geo.meta import pivot_bands
    rows = _meta_rows("VNM.1.1_1", "2026-09-17", 0.3, 0.5, 0.15, 0.05) + _meta_rows("VNM.1.2_1", "2026-09-17", 0.3, 0.5, 0.2, None)
    w = pivot_bands(pl.DataFrame(rows)).sort("gadm_id")
    assert w["share_100plus_km"].to_list() == [0.05, None]  # absent band is null, never 0
    assert w["share_10_100km"].to_list() == [0.15, 0.2]
    bad = pl.DataFrame(rows).with_columns(pl.lit("5-50").alias("home_to_ping_distance_category"))
    with pytest.raises(RuntimeError, match="schema drift"):
        pivot_bands(bad)


def test_quality_gate_rejects_bands_not_summing_to_one():
    from healthpulse.sources import quality, silver
    from healthpulse.geo.meta import pivot_bands
    good = pivot_bands(pl.DataFrame(_meta_rows("A", "2026-09-17", 0.3, 0.5, 0.15, 0.05) * 1)).with_columns(
        pl.lit(None).cast(silver.DT).alias("available_at"), pl.lit("meta").alias("source_id"))
    ok = quality.check("mobility_polygon", silver.conform("mobility_polygon", good))
    assert ok["status"] == "passed"
    broken = good.with_columns(pl.col("share_0km") + 0.4)
    rep = quality.check("mobility_polygon", silver.conform("mobility_polygon", broken))
    assert rep["status"] == "quarantined" and any(g["gate"] == "band_fractions_sum_to_one" and not g["passed"] for g in rep["gates"])


def test_mobility_aggregates_through_crosswalk_with_population_weights():
    # two districts merge into one province; the bigger population dominates, a polygon with no data is ignored
    src = {"D1": (2, 0, 3, 2), "D2": (3, 0, 4, 2), "D3": (0, 0, 2, 2)}
    df, _ = _xw(src, TARGETS)
    vals = pl.DataFrame({"source_id": ["D1", "D2", "D3"], "share_100plus_km": [0.02, 0.10, None]})
    out = cw.aggregate(df, vals, ["share_100plus_km"], source_mass={"D1": 100.0, "D2": 300.0, "D3": 50.0})
    by = dict(zip(out["target_id"], out["share_100plus_km"]))
    assert by["T2"] == pytest.approx((0.02 * 100 + 0.10 * 300) / 400, abs=1e-9)
    assert by["T1"] is None  # its only polygon has no data: null, not zero
    assert out["weight_basis"][0] == "population-weighted"


def test_sandbox_reference_brackets_the_default():
    from healthpulse.geo import pipeline as gp
    vn = pl.DataFrame({"unit_id": ["VN01", "VN79"], "share_100plus_km": [0.02, 0.04], "share_10_100km": [0.10, 0.12]})
    r = gp.vn_sandbox_reference(vn)
    assert r["lower_bound"] == pytest.approx(0.03) and r["upper_bound"] == pytest.approx(0.14)
    assert r["sandbox_default"] == 0.04 and r["default_within_range"] and 0 < r["default_position_in_range"] < 0.2


def test_meta_id_version_check_refuses_ids_from_another_gadm_version(monkeypatch):
    """Same id, different place: Meta's BRA.16.70_1 is one municipality, GADM 4.1's BRA.16.70 another. The check must refuse."""
    from healthpulse.geo import pipeline as gp
    monkeypatch.setattr(gp, "_meta_names", lambda c: pl.DataFrame({"gadm_id": ["BRA.16.70_1", "BRA.16.171_1"], "gadm_name": ["Carlópolis", "Jacarezinho"]}))
    props = {"BRA.16.70_1": {"NAME_2": "Cascavel"}, "BRA.16.171_1": {"NAME_2": "Jaguariaíva"}}
    monkeypatch.setattr(gp.layers, "gadm", lambda c, l, v="4.1": {"props": props})
    r = gp.check_meta_ids("BRA", 2, "4.1")
    assert r["id_found_share"] == 1.0 and r["label_agree_share"] == 0.0 and not r["passes"]  # ids exist, labels disagree -> different version
    props["BRA.16.70_1"]["NAME_2"], props["BRA.16.171_1"]["NAME_2"] = "Carlópolis", "Jacarezinho"
    assert gp.check_meta_ids("BRA", 2, "4.1")["passes"]


def test_geo_api_reports_not_built_as_409(fixture_data):
    from fastapi.testclient import TestClient
    from healthpulse.api.main import create_app
    c = TestClient(create_app())
    assert c.get("/api/v1/geo/crosswalks").status_code == 200
    assert c.get("/api/v1/geo/crosswalks/nope").status_code == 404
    assert c.get("/api/v1/geo/mobility").status_code == 409
    assert c.get("/api/v1/geo/mobility/xx/1").status_code == 404


def test_sanitize_nulls_noise_dominated_polygon_days_without_clipping():
    from healthpulse.geo.meta import pivot_bands, sanitize
    rows = (_meta_rows("OK", "2026-09-17", 0.3, 0.5, 0.15, 0.05)
            + _meta_rows("NEG", "2026-09-17", 0.58, 0.40, -0.37, 0.19)      # negative band: privacy noise
            + _meta_rows("SUM", "2026-09-17", 0.5, 0.5, 0.3, 0.2))          # sums to 1.5
    w, st = sanitize(pivot_bands(pl.DataFrame(rows)))
    by = {r["gadm_id"]: r for r in w.to_dicts()}
    assert by["OK"]["share_0km"] == 0.3
    for k in ("NEG", "SUM"):  # all four bands null: nothing clipped, nothing renormalised, nothing zero
        assert all(by[k][b] is None for b in ("share_0km", "share_0_10km", "share_10_100km", "share_100plus_km"))
    assert st["noise_nulled"] == 2 and st["polygon_days"] == 3


def test_quality_gate_quarantines_when_most_days_are_unusable():
    from healthpulse.geo.meta import pivot_bands, sanitize
    from healthpulse.sources import quality, silver
    rows = []
    for i in range(10):
        rows += _meta_rows(f"P{i}", "2026-09-17", 0.3, 0.5, 0.15, 0.05) if i < 6 else _meta_rows(f"P{i}", "2026-09-17", 0.9, 0.9, 0.9, 0.9)
    w, _ = sanitize(pivot_bands(pl.DataFrame(rows)))
    w = w.with_columns(pl.lit(None).cast(silver.DT).alias("available_at"), pl.lit("meta").alias("source_id"))
    rep = quality.check("mobility_polygon", silver.conform("mobility_polygon", w))
    assert rep["status"] == "quarantined"  # 40% of polygon-days unusable -> the format has probably changed
