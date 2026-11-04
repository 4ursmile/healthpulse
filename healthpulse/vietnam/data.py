"""Viet Nam data: download real public sources into Bronze (append-only, SHA-256 manifests) and build the Gold tables
the dashboard and simulator read.

Sources (all public, all downloaded by `hp vn-fetch`):
  vn_cod_ab      OCHA COD-AB v02, 34 provinces valid from 1 July 2025 (polygons + label points), P-code VNxx
  vn_heigit      HeiGIT/HDX ADM1 risk-assessment inputs keyed by the same P-code: WorldPop population, age groups,
                 rural share, OSM hospital / primary-care counts, travel-time access
  vn_opendengue  OpenDengue V1.3 spatial extract: province-month dengue 1994-2010 (Project Tycho) and national
                 weekly/monthly series 2011-2025 (WHO WPRO)
  vn_who_gho     WHO GHO via HDX: national medical doctors, nurses/midwives, hospital beds per 10,000
  vn_nasa_power  NASA POWER (MERRA-2) monthly 1994-last year and daily for the current year, at each province point

Joins are by code only: P-code (COD-AB = HeiGIT), GAUL -> P-code crosswalk (OpenDengue), point id (POWER).
"""
from __future__ import annotations

import io
import json
import logging
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import polars as pl

from ..config import get_settings
from ..core import lake
from ..core.util import sha256_file, write_json_atomic
from .crosswalk import GAUL_TO_NEW, MERGER, REGION

log = logging.getLogger("healthpulse.vietnam")

# HDX answers unknown User-Agents with a 202 bot challenge; the library's own agent string is accepted
UA = "python-httpx (HealthPulse public-health pilot)"
VN_HOSTS = ("data.humdata.org", "s3.us-east-1.amazonaws.com", "hot.storage.heigit.org", "raw.githubusercontent.com",
            "power.larc.nasa.gov", "srhdpeuwpubsa.blob.core.windows.net")

URLS = {
    "cod_ab": "https://data.humdata.org/dataset/3cb544a9-9d04-4f54-94e2-93230efd8ceb/resource/048a6012-a8c2-4fa9-be87-cb6c01e9afbf/download/vnm_admin_boundaries.geojson.zip",
    "heigit": "https://hot.storage.heigit.org/heigit-hdx-public/risk_assessment_inputs/vnm/VNM_ADM1_{}.csv",
    "opendengue": "https://raw.githubusercontent.com/OpenDengue/master-repo/main/data/releases/V1.3/Spatial_extract_V1_3.zip",
    "who_workforce": "https://data.humdata.org/dataset/1a52c32e-2907-4c6d-a8fd-5f2afac7e62f/resource/7329e59f-c13c-407b-bac8-7d896467d9a4/download/health_workforce_indicators_vnm.csv",
    "who_systems": "https://data.humdata.org/dataset/1a52c32e-2907-4c6d-a8fd-5f2afac7e62f/resource/35f5eb90-6bbd-4f68-a78c-bb05c0501339/download/health_systems_indicators_vnm.csv",
    # the old covid19.who.int URL now redirects to an HTML dashboard; WHO serves the CSV from this blob store
    "who_covid": "https://srhdpeuwpubsa.blob.core.windows.net/whdh/COVID/WHO-COVID-19-global-data.csv",
    "power_monthly": "https://power.larc.nasa.gov/api/temporal/monthly/point",
    "power_daily": "https://power.larc.nasa.gov/api/temporal/daily/point",
}
HEIGIT_TABLES = ("vulnerability", "facilities", "access")


def gold_dir() -> Path:
    d = get_settings().gold_dir / "vn"
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_built() -> bool:
    return (gold_dir() / "manifest.json").exists()


def _latest_bronze(source_id: str) -> dict | None:
    root = get_settings().raw_dir / source_id
    if not root.exists():
        return None
    snaps = sorted(p for p in root.iterdir() if (p / "manifest.json").exists())
    return json.loads((snaps[-1] / "manifest.json").read_text()) if snaps else None


def _file(man: dict, name: str) -> Path:
    for f in man["files"]:
        if f["name"] == name:
            return get_settings().data_dir / f["path"]
    raise FileNotFoundError(f"{name} not in {man['source_id']} snapshot")


# ---------------------------------------------------------------- fetch (Bronze)
def fetch_all(force: bool = False, say=print) -> dict:
    tmp = get_settings().data_dir / "tmp_vn"
    tmp.mkdir(parents=True, exist_ok=True)
    out = {}

    def need(sid):
        return force or _latest_bronze(sid) is None

    if need("vn_cod_ab"):
        say("  vn_cod_ab      OCHA COD-AB v02 (34 provinces)…")
        z = tmp / "cod_ab.zip"
        lake.fetch_to_file(URLS["cod_ab"], z, approved=VN_HOSTS, expect="zip", user_agent=UA)
        with zipfile.ZipFile(z) as zf:
            parts = [(n, zf.read(n)) for n in zf.namelist() if n.endswith(("admin1.geojson", "adminpoints.geojson"))]
        z.unlink()
        out["vn_cod_ab"] = lake.bronze_write("vn_cod_ab", [(Path(n).name, b) for n, b in parts],
                                             lake.manifest_base("vn_cod_ab", URLS["cod_ab"], license="CC BY-IGO (OCHA COD-AB)"))
    if need("vn_heigit"):
        say("  vn_heigit      HeiGIT ADM1 population / facilities / access…")
        pay = [(f"{t}.csv", lake.fetch(URLS["heigit"].format(t), approved=VN_HOSTS, expect="csv", user_agent=UA)[0]) for t in HEIGIT_TABLES]
        out["vn_heigit"] = lake.bronze_write("vn_heigit", pay, lake.manifest_base("vn_heigit", URLS["heigit"].format("*"), license="CC BY 4.0"))
    if need("vn_opendengue"):
        say("  vn_opendengue  OpenDengue V1.3 spatial extract (55 MB)…")
        z = tmp / "od.zip"
        lake.fetch_to_file(URLS["opendengue"], z, approved=VN_HOSTS, expect="zip", user_agent=UA)
        # keep only Viet Nam rows (the global CSV is ~480 MB unzipped); the raw zip hash is kept in the manifest
        zsha = sha256_file(z)
        with zipfile.ZipFile(z) as zf:
            name = next(n for n in zf.namelist() if n.endswith(".csv"))
            with zf.open(name) as fh:
                df = pl.read_csv(fh, infer_schema_length=0)
        z.unlink()
        vn = df.filter(pl.col("adm_0_name") == "VIET NAM")
        buf = io.BytesIO()
        vn.write_csv(buf)
        out["vn_opendengue"] = lake.bronze_write("vn_opendengue", [("opendengue_vnm.csv", buf.getvalue())],
                                                 lake.manifest_base("vn_opendengue", URLS["opendengue"], license="CC BY 4.0",
                                                                    upstream_zip_sha256=zsha, filter="adm_0_name == 'VIET NAM'"))
    if need("vn_who_gho"):
        say("  vn_who_gho     WHO workforce + health-system indicators…")
        pay = [("workforce.csv", lake.fetch(URLS["who_workforce"], approved=VN_HOSTS, expect="csv", user_agent=UA)[0]),
               ("systems.csv", lake.fetch(URLS["who_systems"], approved=VN_HOSTS, expect="csv", user_agent=UA)[0])]
        out["vn_who_gho"] = lake.bronze_write("vn_who_gho", pay, lake.manifest_base("vn_who_gho", URLS["who_workforce"], license="CC BY-NC-SA 3.0 IGO"))
    if need("vn_who_covid"):
        try:
            out["vn_who_covid"] = fetch_covid(say=say)
        except Exception as e:  # noqa: BLE001 — optional source: the calibration reports NO_CALIBRATION without it
            say(f"  vn_who_covid   skipped ({type(e).__name__}: {e})")
    # weather is refreshed on every fetch (the current year grows daily)
    say("  vn_nasa_power  NASA POWER monthly + daily at 34 province points…")
    pts = _points()
    today = datetime.now(timezone.utc).date()
    last_full_year = today.year - 1
    pay = []
    for pid, (lat, lon) in pts.items():
        common = {"parameters": "T2M,PRECTOTCORR,RH2M", "community": "AG", "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "format": "JSON"}
        mb, _ = lake.fetch(URLS["power_monthly"], params={**common, "start": "1994", "end": str(last_full_year)}, approved=VN_HOSTS,
                           expect="json", timeout=120, min_interval_s=0.2)
        db, _ = lake.fetch(URLS["power_daily"], params={**common, "start": f"{today.year}0101", "end": today.strftime("%Y%m%d")},
                           approved=VN_HOSTS, expect="json", timeout=120, min_interval_s=0.2)
        pay += [(f"monthly_{pid}.json", mb), (f"daily_{pid}.json", db)]
    out["vn_nasa_power"] = lake.bronze_write("vn_nasa_power", pay, lake.manifest_base("vn_nasa_power", URLS["power_monthly"],
                                             license="NASA POWER (public)", points="COD-AB v02 admin-1 label points"))
    return out


def _points() -> dict[str, tuple[float, float]]:
    man = _latest_bronze("vn_cod_ab")
    if not man:
        raise RuntimeError("vn_cod_ab not ingested")
    pts = json.loads(_file(man, "vnm_adminpoints.geojson").read_text())
    res = {}
    for f in pts["features"]:
        p = f["properties"]
        if p.get("admin_level") == 1 and p.get("adm1_pcode") in MERGER:
            res[p["adm1_pcode"]] = (f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0])
    return res


# ---------------------------------------------------------------- geometry simplification (no GEOS dependency)
def _dp(pts: np.ndarray, tol: float) -> np.ndarray:
    """Iterative Douglas-Peucker on one ring."""
    n = len(pts)
    if n < 5:
        return pts
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        seg = pts[b] - pts[a]
        L = np.hypot(*seg)
        d = pts[a + 1:b] - pts[a]
        dist = np.abs(seg[0] * d[:, 1] - seg[1] * d[:, 0]) / L if L > 0 else np.hypot(d[:, 0], d[:, 1])
        i = int(np.argmax(dist))
        if dist[i] > tol:
            m = a + 1 + i
            keep[m] = True
            stack += [(a, m), (m, b)]
    return pts[keep]


def _ring_area(r: np.ndarray) -> float:
    x, y = r[:, 0], r[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def simplify_geometry(geom: dict, tol: float = 0.008, min_area: float = 2e-5) -> dict:
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    out = []
    for poly in polys:
        outer = np.asarray(poly[0], float)
        a = _ring_area(outer)
        if a < min_area:
            # small islands (e.g. Hoàng Sa, Trường Sa): keep them, coarsely
            if a < 1e-7:
                continue
            r = _dp(outer, tol / 8)
        else:
            r = _dp(outer, tol)
        if len(r) < 4:
            continue
        rings = [np.round(r, 4).tolist()]
        for hole in poly[1:]:
            h = np.asarray(hole, float)
            if _ring_area(h) > min_area * 5:
                hh = _dp(h, tol)
                if len(hh) >= 4:
                    rings.append(np.round(hh, 4).tolist())
        out.append(rings)
    return {"type": "MultiPolygon", "coordinates": out}


# ---------------------------------------------------------------- build (Gold)
def _power_series(raw: bytes, freq: str) -> pl.DataFrame:
    d = json.loads(raw)["properties"]["parameter"]
    keys = sorted(d["T2M"].keys())
    rows = []
    for k in keys:
        if freq == "monthly" and k.endswith("13"):  # annual mean row
            continue
        vals = [d[p].get(k) for p in ("T2M", "PRECTOTCORR", "RH2M")]
        vals = [None if v is None or v <= -998 else float(v) for v in vals]
        dt = date(int(k[:4]), int(k[4:6]), 1 if freq == "monthly" else int(k[6:8]))
        rows.append((dt, *vals))
    return pl.DataFrame(rows, schema=["date", "t2m", "rain_mm_day", "rh"], orient="row")


def _who_latest(raw: bytes) -> dict:
    df = pl.read_csv(io.BytesIO(raw), infer_schema_length=0, skip_rows_after_header=1)  # row 2 is an HXL tag row
    df = df.filter(pl.col("DIMENSION (NAME)").is_null() | (pl.col("DIMENSION (NAME)") == "Both sexes"))
    df = df.with_columns(pl.col("Numeric").cast(pl.Float64, strict=False), pl.col("YEAR (DISPLAY)").cast(pl.Int32, strict=False))
    res = {}
    for code, g in df.drop_nulls("Numeric").group_by("GHO (CODE)"):
        r = g.sort("YEAR (DISPLAY)").row(-1, named=True)
        res[code[0]] = {"label": r["GHO (DISPLAY)"], "year": r["YEAR (DISPLAY)"], "value": r["Numeric"]}
    return res


def fetch_covid(say=print) -> dict:
    """WHO COVID-19 weekly cases and deaths for Viet Nam into Bronze (only the VN rows are kept; the upstream file hash is
    in the manifest). Used by the sandbox calibration; not by the dashboard."""
    say("  vn_who_covid   WHO COVID-19 weekly cases / deaths (Viet Nam rows)…")
    raw, _ = lake.fetch(URLS["who_covid"], approved=VN_HOSTS, expect="csv", user_agent=UA, timeout=180)
    import hashlib
    up = hashlib.sha256(raw).hexdigest()
    df = pl.read_csv(io.BytesIO(raw), infer_schema_length=0)
    if "Country_code" not in df.columns or "New_cases" not in df.columns:
        raise RuntimeError("unexpected WHO COVID-19 file layout")
    vn = df.filter(pl.col("Country_code") == "VN")
    if vn.height < 50:
        raise RuntimeError(f"only {vn.height} Viet Nam rows in the WHO COVID-19 file")
    buf = io.BytesIO()
    vn.write_csv(buf)
    return lake.bronze_write("vn_who_covid", [("who_covid_vnm.csv", buf.getvalue())],
                             lake.manifest_base("vn_who_covid", URLS["who_covid"], license="WHO COVID-19 dashboard data (CC BY 4.0)",
                                                upstream_sha256=up, filter="Country_code == 'VN'"))


def covid_weekly() -> pl.DataFrame | None:
    """Weekly WHO series (week_end, cases, deaths) from the latest Bronze snapshot, or None if never fetched. Missing is
    null, never zero (WHO leaves weeks unreported empty)."""
    man = _latest_bronze("vn_who_covid")
    if not man:
        return None
    df = pl.read_csv(_file(man, "who_covid_vnm.csv"), infer_schema_length=0)
    return (df.select(pl.col("Date_reported").str.to_date().alias("week_end"), pl.col("New_cases").cast(pl.Float64, strict=False).alias("cases"),
                      pl.col("New_deaths").cast(pl.Float64, strict=False).alias("deaths")).sort("week_end"))


def build(say=print) -> dict:
    mans = {sid: _latest_bronze(sid) for sid in ("vn_cod_ab", "vn_heigit", "vn_opendengue", "vn_who_gho", "vn_nasa_power")}
    missing = [k for k, v in mans.items() if not v]
    if missing:
        raise RuntimeError(f"not ingested: {', '.join(missing)} (run `hp vn-fetch`)")
    g = gold_dir()

    # --- geometry
    say("  build geometry (simplify 1.3 M vertices)…")
    adm1 = json.loads(_file(mans["vn_cod_ab"], "vnm_admin1.geojson").read_text())
    feats = []
    for f in adm1["features"]:
        p = f["properties"]
        pid = p["adm1_pcode"]
        if pid not in MERGER:
            continue
        feats.append({"type": "Feature", "id": pid, "properties": {"id": pid, "name": p.get("adm1_name1") or p["adm1_name"],
                      "name_en": p["adm1_name"], "area_km2": p.get("area_sqkm")}, "geometry": simplify_geometry(f["geometry"])})
    assert len(feats) == 34, f"expected 34 provinces, got {len(feats)}"
    (g / "provinces.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False, separators=(",", ":")))

    # --- province attributes
    pts = _points()
    heig = {t: pl.read_csv(_file(mans["vn_heigit"], f"{t}.csv")) for t in HEIGIT_TABLES}
    vul = {r["ADM1_PCODE"]: r for r in heig["vulnerability"].iter_rows(named=True)}
    fac = {r["ADM1_PCODE"]: r for r in heig["facilities"].iter_rows(named=True)}
    acc = {r["ADM1_PCODE"]: r for r in heig["access"].iter_rows(named=True)}
    who = {}
    who.update(_who_latest(_file(mans["vn_who_gho"], "workforce.csv").read_bytes()))
    who.update(_who_latest(_file(mans["vn_who_gho"], "systems.csv").read_bytes()))
    doc10k = who.get("HWF_0001", {}).get("value")
    nur10k = who.get("HWF_0006", {}).get("value")
    beds10k = next((v["value"] for k, v in who.items() if "bed" in v["label"].lower()), None)
    provs = []
    for f in feats:
        pid = f["id"]
        v, fc, ac = vul.get(pid, {}), fac.get(pid, {}), acc.get(pid, {})
        pop = v.get("total_pop")
        provs.append({
            "province_id": pid, "name": f["properties"]["name"], "name_en": f["properties"]["name_en"], "region": REGION[pid],
            "lat": pts[pid][0], "lon": pts[pid][1], "area_km2": f["properties"]["area_km2"],
            "former_provinces": [n for _, _, n in MERGER[pid][1]],
            "population": pop, "children_u5": v.get("children_u5"), "elderly": v.get("elderly"), "rural_pct": v.get("rural_pop_perc"),
            "hospitals_osm": fc.get("hospitals_count"), "primary_care_osm": fc.get("primary_healthcare_count"),
            "pop_hospital_1h": ac.get("access_pop_hospitals_1h"),
            "access_hospital_1h_share": (ac.get("access_pop_hospitals_1h") / pop) if pop and ac.get("access_pop_hospitals_1h") is not None else None,
            # national WHO densities applied to provincial population: ESTIMATE, labelled as such everywhere
            "doctors_est": round(pop * doc10k / 1e4) if pop and doc10k else None,
            "nurses_est": round(pop * nur10k / 1e4) if pop and nur10k else None,
            "beds_est": round(pop * beds10k / 1e4) if pop and beds10k else None,
        })
    write_json_atomic(g / "provinces.json", provs)
    write_json_atomic(g / "workforce_national.json", {"indicators": who, "doctors_per_10k": doc10k, "nurses_per_10k": nur10k,
                                                      "beds_per_10k": beds10k, "source": "WHO GHO via HDX"})

    # --- dengue
    say("  build dengue history (GAUL -> P-code crosswalk)…")
    od = pl.read_csv(_file(mans["vn_opendengue"], "opendengue_vnm.csv"), infer_schema_length=0)
    od = od.with_columns(pl.col("dengue_total").cast(pl.Float64, strict=False),
                         pl.col("calendar_start_date").str.to_date(strict=False).alias("start"),
                         pl.col("calendar_end_date").str.to_date(strict=False).alias("end"))
    a1 = od.filter((pl.col("S_res") == "Admin1") & (pl.col("T_res") == "Month"))
    unmapped = set(a1["FAO_GAUL_code"].unique()) - set(GAUL_TO_NEW)
    if unmapped:
        raise RuntimeError(f"unmapped GAUL codes: {unmapped}")
    a1 = a1.with_columns(pl.col("FAO_GAUL_code").replace_strict(GAUL_TO_NEW).alias("province_id"),
                         pl.col("start").dt.truncate("1mo").alias("month"))
    monthly = (a1.group_by("province_id", "month").agg(pl.col("dengue_total").sum().alias("cases"),
                                                       pl.col("dengue_total").is_not_null().sum().alias("units_reporting"))
               .sort("province_id", "month"))
    # full grid: months with no report are null, never zero
    months = pl.date_range(monthly["month"].min(), monthly["month"].max(), "1mo", eager=True).alias("month")
    grid = pl.DataFrame({"province_id": list(MERGER)}).join(pl.DataFrame({"month": months}), how="cross")
    monthly = grid.join(monthly, on=["province_id", "month"], how="left").with_columns(pl.col("units_reporting").fill_null(0))
    monthly.write_parquet(g / "dengue_province_monthly.parquet")
    nat = (od.filter(pl.col("S_res") == "Admin0").select("start", "end", "T_res", pl.col("dengue_total").alias("cases"), "UUID")
           .drop_nulls(["start", "cases"]).unique(["start", "T_res"], keep="last").sort("T_res", "start"))
    nat.write_parquet(g / "dengue_national.parquet")

    # --- weather
    say("  build weather (NASA POWER)…")
    wm, wd = [], []
    for pid in MERGER:
        m = _power_series(_file(mans["vn_nasa_power"], f"monthly_{pid}.json").read_bytes(), "monthly").with_columns(pl.lit(pid).alias("province_id"))
        d = _power_series(_file(mans["vn_nasa_power"], f"daily_{pid}.json").read_bytes(), "daily").with_columns(pl.lit(pid).alias("province_id"))
        wd.append(d)
        dm = (d.drop_nulls("t2m").with_columns(pl.col("date").dt.truncate("1mo").alias("m"))
              .group_by("m").agg(pl.col("t2m").mean(), pl.col("rain_mm_day").mean(), pl.col("rh").mean(), pl.len().alias("days"))
              .rename({"m": "date"}))
        wm.append(pl.concat([m.with_columns(pl.lit(None, pl.UInt32).alias("days")), dm.select(m.columns[:-1] + ["days"]).with_columns(pl.lit(pid).alias("province_id"))], how="diagonal"))
    pl.concat(wm, how="diagonal").sort("province_id", "date").rename({"date": "month"}).write_parquet(g / "weather_monthly.parquet")
    pl.concat(wd).sort("province_id", "date").write_parquet(g / "weather_daily.parquet")

    man = {"built_at": datetime.now(timezone.utc).isoformat(), "sources": {k: {"sha256": v["sha256"], "retrieved_at": v["retrieved_at"],
           "bronze_dir": v["bronze_dir"], "source_url": v["source_url"]} for k, v in mans.items()},
           "provinces": 34, "admin_basis": "Resolution 202/2025/QH15, effective 2025-07-01 (OCHA COD-AB v02)"}
    write_json_atomic(g / "manifest.json", man)
    from . import model
    model.reset_cache()
    say("  fit climate model…")
    model.fit_and_store()
    return man
