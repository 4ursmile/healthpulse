"""Build and read the polygon crosswalks and the Meta mobility-range profile.

Layout (Gold, immutable per version):
  data/gold/geo/crosswalk/{pair}/{version}/pairs.parquet   one row per (source, target) candidate, see crosswalk.SCHEMA
  data/gold/geo/crosswalk/{pair}/{version}/report.json     method, thresholds, input hashes, quality report, audit
  data/gold/geo/crosswalk/{pair}/latest.json               pointer to the newest version
  data/gold/geo/mobility_range_{br,vn}.parquet + mobility_range.json

The version id is a hash of (method, thresholds, input layer hashes): same inputs, same version, byte-identical result.

THE MOBILITY PROFILE IS NOT A PIT TRAINING FEATURE. HDX keeps ~90 days, so the series starts at our first ingest. A feature
that does not exist before that date is null for every training week, and filling it backwards would leak. It is therefore
shown on screens and used to ground simulator defaults only, and it is not registered in any feature set.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl

from ..config import get_settings
from ..core import lake
from ..core.util import utcnow, write_json_atomic
from ..sources import silver
from . import crosswalk as cw
from . import layers

log = logging.getLogger("healthpulse.geo")
BANDS = ["share_0km", "share_0_10km", "share_10_100km", "share_100plus_km"]
WORLDPOP_VNM = "https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/2025/VNM/v1/1km_ua/constrained/vnm_pop_2025_CN_1km_R2025A_UA_v1.tif"
PAIRS = {
    "gadm_br_ibge": {"label": "GADM 4.1 Brazil level 2 -> IBGE municipalities", "country": "BRA", "level": 2, "version": "4.1", "target": "ibge"},
    "gadm36_br_ibge": {"label": "GADM 3.6 Brazil level 2 -> IBGE municipalities (the version Meta's Brazilian ids belong to)", "country": "BRA",
                       "level": 2, "version": "3.6", "target": "ibge"},
    "gadm_vn2_cod": {"label": "GADM 4.1 Viet Nam level 2 (districts) -> OCHA COD-AB 34 provinces", "country": "VNM", "level": 2, "version": "4.1",
                     "target": "cod"},
    "gadm_vn1_cod": {"label": "GADM 4.1 Viet Nam level 1 (63 provinces) -> OCHA COD-AB 34 provinces (validation pair)", "country": "VNM", "level": 1,
                     "version": "4.1", "target": "cod"},
}
# Meta id check: a Meta polygon id may only be resolved through a GADM layer that is demonstrably the same version
MIN_ID_FOUND, MIN_NAME_AGREE = 0.99, 0.95


class GeoError(RuntimeError):
    pass


def geo_dir() -> Path:
    d = get_settings().gold_dir / "geo"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- audit references (CODES only, never names)
def _norm(name: str) -> str:
    import re
    import unicodedata
    t = unicodedata.normalize("NFKD", name.replace("đ", "d").replace("Đ", "D"))
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in t if not unicodedata.combining(c)).lower())


def _vn_refs() -> tuple[dict[str, str], dict[str, str]]:
    """(old ISO 3166-2 code -> new P-code, normalised old-province name -> new P-code) from the existing merger table."""
    from ..vietnam.crosswalk import MERGER
    iso = {i: new for new, (_, olds) in MERGER.items() for _, i, _ in olds if i}
    names = {_norm(n.split("(")[0]): new for new, (_, olds) in MERGER.items() for _, _, n in olds}
    return iso, names


def _vn_province_ref(p: dict, iso: dict, names: dict) -> str | None:
    if p.get("ISO_1") in iso:
        return iso[p["ISO_1"]]
    return names.get(_norm(p.get("NAME_1") or ""))


def _audit_reference(pair: str, src: dict) -> tuple[dict[str, str], str]:
    props = src["props"]
    if pair == "gadm_br_ibge":
        ref = {gid: str(p["CC_2"]).zfill(7) for gid, p in props.items() if p.get("CC_2") not in (None, "NA", "")}
        return ref, "GADM's own CC_2 attribute (the IBGE code the polygon vendor attaches)"
    iso, names = _vn_refs()
    note = ("ISO 3166-2 code of the old province where GADM carries one (only ~4 of 63), otherwise a normalised-name match against the "
            "old-province names in the merger table. NAMES ARE USED HERE AS AN AUDIT STATISTIC ONLY, never as a join key or a weight")
    if pair == "gadm_vn1_cod":
        return {gid: r for gid, p in props.items() if (r := _vn_province_ref(p, iso, names))}, note
    if pair == "gadm_vn2_cod":
        p1 = layers.gadm("VNM", 1)["props"]
        return ({gid: r for gid, p in props.items() if p.get("GID_1") in p1 and (r := _vn_province_ref(p1[p["GID_1"]], iso, names))},
                note + "; a district takes the reference of its parent province (GID_1)")
    return {}, ""


# ---------------------------------------------------------------- crosswalk
def _layers_for(pair: str):
    spec = PAIRS[pair]
    src = layers.gadm(spec["country"], spec["level"], spec["version"])
    tgt = layers.ibge_municipalities() if spec["target"] == "ibge" else layers.cod_ab_provinces()
    return src, tgt


def build_crosswalk(pair: str, params: cw.Params | None = None, say=print) -> dict:
    if pair not in PAIRS:
        raise GeoError(f"unknown crosswalk pair {pair}; known: {', '.join(PAIRS)}")
    import time
    t0 = time.perf_counter()
    src, tgt = _layers_for(pair)
    say(f"  {pair}: {len(src['ids']):,} source polygons x {len(tgt['ids']):,} target polygons")
    df, rep = cw.build(src["ids"], src["geoms"], tgt["ids"], tgt["geoms"], params)
    rep["seconds"] = round(time.perf_counter() - t0, 1)
    ref, ref_note = _audit_reference(pair, src)
    rep["audit"] = {**cw.audit(df, ref), "reference": ref_note,
                    "note": "AUDIT ONLY: an independent code-based reference; it never changes a weight."}
    vid = cw.version_id(rep, cw.layer_hash(src["ids"], src["geoms"]), cw.layer_hash(tgt["ids"], tgt["geoms"]))
    rep.update({"pair": pair, "label": PAIRS[pair]["label"], "version": vid, "built_at": utcnow().isoformat(),
                "inputs": {"source": {"layer": src["layer"], "bronze": src["bronze"], "sha256": src["sha256"], "n": len(src["ids"])},
                           "target": {"layer": tgt["layer"], "bronze": tgt["bronze"], "sha256": tgt["sha256"], "n": len(tgt["ids"])}},
                "accuracy_note": "IBGE polygons are the geodata-br mirror of the IBGE mesh; GADM 4.1 uses its own, older and generalised "
                                 "boundaries, so IoU below 1 for a ONE_TO_ONE pair is expected."})
    d = geo_dir() / "crosswalk" / pair / vid
    d.mkdir(parents=True, exist_ok=True)
    lake.write_parquet_atomic(df, d / "pairs.parquet")
    write_json_atomic(d / "report.json", rep)
    write_json_atomic(d.parent / "latest.json", {"version": vid, "built_at": rep["built_at"]})
    say(f"    version {vid}: {rep['source_status']} accept {rep['source_accept_rate']:.1%}, area matched {rep['area_matched_share']:.1%}, "
        f"audit agree {rep['audit'].get('agree')}, {rep['seconds']} s")
    return rep


def buildable_pairs() -> list[str]:
    return [p for p, sp in PAIRS.items() if sp["version"] in layers.gadm_versions(sp["country"], sp["level"])]


def list_crosswalks() -> list[dict]:
    out = []
    for pair, spec in PAIRS.items():
        p = geo_dir() / "crosswalk" / pair / "latest.json"
        if not p.exists():
            out.append({"pair": pair, "label": spec["label"], "built": False})
            continue
        rep = json.loads((p.parent / json.loads(p.read_text())["version"] / "report.json").read_text())
        out.append({"pair": pair, "label": spec["label"], "built": True, "version": rep["version"], "built_at": rep["built_at"],
                    "source_status": rep["source_status"], "source_accept_rate": rep["source_accept_rate"],
                    "area_matched_share": rep["area_matched_share"], "audit_agree": rep["audit"].get("agree"),
                    "audit_n": rep["audit"].get("n")})
    return out


def load_crosswalk(pair: str, version: str | None = None) -> tuple[pl.DataFrame, dict]:
    base = geo_dir() / "crosswalk" / pair
    if version is None:
        if not (base / "latest.json").exists():
            raise GeoError(f"crosswalk {pair} has not been built (hp crosswalk)")
        version = json.loads((base / "latest.json").read_text())["version"]
    d = base / version
    if not d.exists():
        raise GeoError(f"crosswalk {pair} version {version} does not exist")
    return pl.read_parquet(d / "pairs.parquet"), json.loads((d / "report.json").read_text())


# ---------------------------------------------------------------- WorldPop population on GADM polygons (population weights)
def vn_population_by_gadm(level: int, say=print) -> dict[str, float] | None:
    """WorldPop R2025A 1 km constrained raster summed over GADM polygons (cell-centre rule). None when it cannot be fetched.
    WorldPop is a MODELLED grid; it is used only as a weight, never shown as a population count."""
    from ..sources import connectors  # noqa: F401  (imports the connector registry first: connectors_extra imports back from it)
    from ..sources.connectors_extra import zonal_sums
    import tifffile
    from shapely.geometry import mapping
    man = layers.latest_bronze("vn_worldpop")
    if man is None:
        try:
            body, _ = lake.fetch(WORLDPOP_VNM, approved=("data.worldpop.org",), expect="tiff", max_bytes=60_000_000, timeout=180)
        except lake.FetchError as e:
            say(f"    WorldPop VNM raster unavailable ({e}); using area weights")
            return None
        man = lake.bronze_write("vn_worldpop", [("vnm_pop_2025_1km.tif", body)],
                                lake.manifest_base("vn_worldpop", WORLDPOP_VNM, license="CC BY 4.0 (WorldPop R2025A, constrained 1 km)"))
    path = get_settings().data_dir / man["files"][0]["path"]
    with tifffile.TiffFile(path) as t:
        pg = t.pages[0]
        sx, sy, _ = pg.tags["ModelPixelScaleTag"].value
        tie = pg.tags["ModelTiepointTag"].value
        raster = pg.asarray()
    x0, y0 = tie[3] - tie[0] * sx, tie[4] + tie[1] * sy
    src = layers.gadm("VNM", level)
    feats = [{"geometry": mapping(g), "properties": {"id": str(i)}} for i, g in enumerate(src["geoms"])]
    rows = zonal_sums(raster, x0, y0, sx, feats)
    return {src["ids"][int(r["municipality_id"])]: float(r["worldpop_population"]) for r in rows}


# ---------------------------------------------------------------- mobility profile
def _meta_names(country: str) -> pl.DataFrame:
    """Meta's own polygon labels from the newest Bronze snapshot. Used ONLY by the version check below, never as a key."""
    man = layers.latest_bronze("meta_mobility")
    if not man:
        return pl.DataFrame(schema={"gadm_id": pl.Utf8, "gadm_name": pl.Utf8})
    fr = [pl.read_csv(get_settings().data_dir / f["path"], infer_schema=False).filter(pl.col("country") == country).select("gadm_id", "gadm_name").unique()
          for f in man["files"]]
    return pl.concat(fr).unique("gadm_id") if fr else pl.DataFrame(schema={"gadm_id": pl.Utf8, "gadm_name": pl.Utf8})


def check_meta_ids(country: str, level: int, version: str) -> dict:
    """Do Meta's polygon ids belong to this GADM version? GADM renumbers between versions, so an id that exists in two versions can
    name two different places (Brazil: Meta's BRA.16.70_1 is Carlopolis, GADM 4.1's BRA.16.70 is Cascavel). The check is the share of
    Meta ids found in the layer, and, as a VERSION CHECK ONLY, the share of those whose normalised labels agree. Names never join anything."""
    meta = _meta_names(country)
    g = layers.gadm(country, level, version)["props"]
    found = meta.filter(pl.col("gadm_id").is_in(list(g)))
    agree = sum(1 for i, n in zip(found["gadm_id"], found["gadm_name"]) if _norm(n or "") == _norm(g[i].get(f"NAME_{level}") or ""))
    out = {"version": version, "meta_polygons": meta.height, "id_found_share": found.height / max(1, meta.height),
           "label_agree_share": agree / max(1, found.height)}
    out["passes"] = out["id_found_share"] >= MIN_ID_FOUND and out["label_agree_share"] >= MIN_NAME_AGREE
    return out


def _resolve_gadm_pair(country: str, level: int, target: str) -> tuple[str | None, list[dict]]:
    checks = []
    for v in layers.gadm_versions(country, level):
        c = check_meta_ids(country, level, v)
        checks.append(c)
        if c["passes"]:
            return next(p for p, sp in PAIRS.items() if (sp["country"], sp["level"], sp["version"], sp["target"]) == (country, level, v, target)), checks
    return None, checks


def _polygon_means(country: str, min_days: int) -> tuple[pl.DataFrame, dict]:
    m = silver.latest("mobility_polygon").filter(pl.col("country") == country)
    if not m.height:
        raise GeoError(f"no Meta mobility rows for {country}; run: hp ingest meta_mobility")
    g = m.group_by("gadm_id").agg(*[pl.col(b).mean().alias(b) for b in BANDS], pl.col("ds").filter(pl.col("share_100plus_km").is_not_null()).n_unique().alias("n_days"),
                                  pl.col("ds").min().alias("ds_min"), pl.col("ds").max().alias("ds_max"))
    g = g.filter(pl.col("n_days") >= min_days).rename({"gadm_id": "source_id"})
    return g, {"polygon_days": m.height, "polygons": m["gadm_id"].n_unique(), "ds_min": str(m["ds"].min()), "ds_max": str(m["ds"].max()),
               "days": m["ds"].n_unique(), "usable_polygon_days": m.filter(pl.col("share_100plus_km").is_not_null()).height,
               "polygons_with_min_days": g.height, "min_days": min_days}


def _level_of(gid: str) -> int:
    return gid.split("_")[0].count(".")


def build_mobility_profile(min_days: int = 7, say=print) -> dict:
    """Aggregate polygon means (mean of daily shares over the days we hold) to IBGE municipalities and to the 34 P-codes."""
    out: dict = {"built_at": utcnow().isoformat(), "profile_of": "share of device pings by distance from the home location",
                 "not": "origin-destination flows (Movement Distribution has no destination)",
                 "aggregation": "unweighted mean over the days held; days with fewer than min_days are excluded; polygon -> unit through the "
                                "polygon crosswalk",
                 "pit": "not a point-in-time training feature: HDX keeps ~90 days, nothing exists before the first ingest", "units": {}}
    # ---- Brazil
    try:
        mean, info = _polygon_means("BRA", min_days)
        pair, checks = _resolve_gadm_pair("BRA", 2, "ibge")
        if pair is None:
            raise GeoError("Meta's Brazilian polygon ids do not belong to any GADM version we hold, so they are not joined: "
                           + "; ".join(f"GADM {c['version']}: {c['id_found_share']:.0%} ids found, {c['label_agree_share']:.0%} labels agree" for c in checks)
                           + ". Ingest GADM 3.6 (hp ingest gadm_boundaries) and run hp crosswalk")
        xw, rep = load_crosswalk(pair)
        agg = cw.aggregate(xw, mean, BANDS)
        meta = mean.select(pl.col("ds_min").min().alias("ds_min"), pl.col("ds_max").max().alias("ds_max"), pl.col("n_days").median().alias("median_days"))
        agg = agg.rename({"target_id": "unit_id"}).with_columns(pl.lit(rep["version"]).alias("crosswalk_version"))
        lake.write_parquet_atomic(agg, geo_dir() / "mobility_range_br.parquet")
        have = agg.filter(pl.col("share_100plus_km").is_not_null())
        out["units"]["br"] = {"unit": "IBGE municipality", "n_units": agg.height, "n_with_values": have.height, "crosswalk_version": rep["version"],
                              "weight_basis": "area-weighted", "id_checks": checks, "crosswalk_pair": pair, **info, "median_days_per_polygon": float(meta["median_days"][0])}
        say(f"  BR: {have.height:,} municipalities with values of {agg.height:,} reached ({info['days']} days, {info['ds_min']}..{info['ds_max']})")
    except (GeoError, FileNotFoundError) as e:
        out["units"]["br"] = {"refused": str(e)}
        (geo_dir() / "mobility_range_br.parquet").unlink(missing_ok=True)  # never leave an older profile standing in for a refused one
        say(f"  BR REFUSED: {e}")
    # ---- Viet Nam
    try:
        mean, info = _polygon_means("VNM", min_days)
        level = _level_of(mean["source_id"][0])
        pair, checks = _resolve_gadm_pair("VNM", level, "cod")
        if pair is None:
            raise GeoError("Meta's Vietnamese polygon ids do not belong to any GADM version we hold: "
                           + "; ".join(f"GADM {c['version']}: {c['id_found_share']:.0%} ids found, {c['label_agree_share']:.0%} labels agree" for c in checks))
        xw, rep = load_crosswalk(pair)
        pop = vn_population_by_gadm(level, say)
        agg = _with_all_units(cw.aggregate(xw, mean, BANDS, source_mass=pop), layers.cod_ab_provinces()["ids"],
                              "population-weighted" if pop else "area-weighted")
        agg = agg.rename({"target_id": "unit_id"}).with_columns(pl.lit(rep["version"]).alias("crosswalk_version"))
        lake.write_parquet_atomic(agg, geo_dir() / "mobility_range_vn.parquet")
        have = agg.filter(pl.col("share_100plus_km").is_not_null())
        out["units"]["vn"] = {"unit": "OCHA COD-AB province (34)", "n_units": agg.height, "n_with_values": have.height, "crosswalk_version": rep["version"],
                              "gadm_level": level, "id_checks": checks, "crosswalk_pair": pair, "weight_basis": agg["weight_basis"][0] if agg.height else None, **info}
        out["vn_sandbox_reference"] = vn_sandbox_reference(have)
        say(f"  VN: {have.height} of 34 provinces with values (GADM level {level}, {agg['weight_basis'][0] if agg.height else '-'})")
    except (GeoError, FileNotFoundError) as e:
        out["units"]["vn"] = {"refused": str(e)}
        (geo_dir() / "mobility_range_vn.parquet").unlink(missing_ok=True)
        say(f"  VN REFUSED: {e}")
    write_json_atomic(geo_dir() / "mobility_range.json", out)
    return out


def _with_all_units(agg: pl.DataFrame, all_ids: list[str], basis: str) -> pl.DataFrame:
    """Every target unit appears; units the crosswalk or Meta does not reach carry nulls (shown as missing, never as zero)."""
    full = pl.DataFrame({"target_id": sorted(all_ids)})
    return full.join(agg, on="target_id", how="left").with_columns(pl.col("weight_basis").fill_null(basis))


def vn_sandbox_reference(vn: pl.DataFrame) -> dict | None:
    """Data-grounded reference for the sandbox `MobilitySpec.share` ("share of contacts made outside the home province").

    Pings >= 100 km from home are almost always outside the home province (most provinces are < 200 km across), so
    share_100plus is a LOWER bound for time spent outside; pings >= 10 km from home include trips inside the province, so
    share_10_100 + share_100plus is an UPPER bound. The sandbox default (0.04) is compared with both bounds and is NOT changed.
    Population-weighted over the provinces that have values."""
    if not vn.height:
        return None
    try:
        pop = {p["province_id"]: p["population"] for p in json.loads((get_settings().gold_dir / "vn" / "provinces.json").read_text())}
    except OSError:
        pop = {}
    w = np.array([pop.get(u, np.nan) for u in vn["unit_id"]], dtype=float)
    if not np.isfinite(w).any():
        w = np.ones(vn.height)
    w = np.where(np.isfinite(w), w, np.nanmean(w))
    lo = vn["share_100plus_km"].to_numpy()
    up = lo + vn["share_10_100km"].to_numpy()
    wm = lambda x: float(np.average(x, weights=w))  # noqa: E731
    lo_v, up_v, default = wm(lo), wm(up), 0.04
    return {"lower_bound": lo_v, "lower_bound_basis": "population-weighted share of pings >= 100 km from home",
            "upper_bound": up_v, "upper_bound_basis": "population-weighted share of pings >= 10 km from home",
            "sandbox_default": default, "default_within_range": lo_v <= default <= up_v,
            "default_position_in_range": (default - lo_v) / (up_v - lo_v) if up_v > lo_v else None,
            "interpretation": "A distance-from-home share, not a count of contacts made in other provinces. The share of contacts outside "
                              "the home province lies somewhere between the bounds; the data cannot say where. The sandbox default is not changed."}


def mobility_meta() -> dict:
    p = geo_dir() / "mobility_range.json"
    if not p.exists():
        raise GeoError("the mobility profile has not been built (hp ingest meta_mobility, then hp crosswalk)")
    return json.loads(p.read_text())


def mobility_table(scope: str) -> pl.DataFrame:
    p = geo_dir() / f"mobility_range_{scope}.parquet"
    if not p.exists():
        raise GeoError(f"no mobility profile for {scope}; run hp crosswalk and hp ingest meta_mobility")
    return pl.read_parquet(p)
