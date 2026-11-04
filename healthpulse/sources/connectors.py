"""Source connectors. Each one: probe (bounded reachability + schema check), collect (Bronze bytes), parse (Silver
frames with enforced types). Schema mapping is declarative per connector; no user code runs here."""
from __future__ import annotations

import csv
import io
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import polars as pl

from ..core import lake
from ..core.jobs import JobContext
from ..core.util import epi_week_code, epi_week_start, loads, parse_dt, utcnow
from . import silver

MUNI = pl.Utf8


def _approved(src: dict) -> list[str]:
    return src.get("approved_hostnames") or []


class Connector:
    source_id = ""
    tables: tuple[str, ...] = ()
    expect = "json"

    def probe(self, src: dict) -> dict:
        raise NotImplementedError

    def collect(self, ctx: JobContext, src: dict, params: dict) -> tuple[list[tuple[str, bytes]], dict]:
        raise NotImplementedError

    def parse(self, ctx: JobContext, src: dict, manifest: dict) -> dict[str, pl.DataFrame]:
        raise NotImplementedError

    def _payloads(self, manifest: dict) -> list[tuple[str, bytes]]:
        return [(f["name"], lake.bronze_path(f["path"]).read_bytes()) for f in manifest["files"]]


def scope_munis(params: dict) -> list[str]:
    from ..config import get_settings
    m = params.get("municipalities") or get_settings().pilot
    return [str(x).zfill(7) for x in m]


# ---------------------------------------------------------------- IBGE municipality register
class IBGEMunicipalities(Connector):
    source_id, tables, expect = "ibge_municipalities", ("municipality_dim",), "csv"
    UF = {11: "RO", 12: "AC", 13: "AM", 14: "RR", 15: "PA", 16: "AP", 17: "TO", 21: "MA", 22: "PI", 23: "CE", 24: "RN",
          25: "PB", 26: "PE", 27: "AL", 28: "SE", 29: "BA", 31: "MG", 32: "ES", 33: "RJ", 35: "SP", 41: "PR", 42: "SC",
          43: "RS", 50: "MS", 51: "MT", 52: "GO", 53: "DF"}

    def probe(self, src):
        body, meta = lake.fetch(src["config_json"]["url"], approved=_approved(src), expect="csv", max_bytes=5_000_000)
        head = body.decode("utf-8", "replace").splitlines()[:3]
        return {"ok": head[0].startswith("codigo_ibge"), "columns": head[0].split(","), "bytes": meta["bytes"]}

    def collect(self, ctx, src, params):
        body, meta = lake.fetch(src["config_json"]["url"], approved=_approved(src), expect="csv", max_bytes=20_000_000)
        return [("municipios.csv", body)], {"content_type": meta["content_type"], "geographic_level": "municipality"}

    def parse(self, ctx, src, manifest):
        (_, body), = self._payloads(manifest)
        df = pl.read_csv(io.BytesIO(body), schema_overrides={"codigo_ibge": pl.Utf8})
        df = df.select(
            pl.col("codigo_ibge").str.zfill(7).alias("municipality_id"),
            pl.col("nome").alias("name"),
            pl.col("codigo_uf").cast(pl.Int32).alias("state_code"),
            pl.col("codigo_uf").cast(pl.Int32).replace_strict(self.UF, default=None, return_dtype=pl.Utf8).alias("state_abbr"),
            pl.col("latitude").cast(pl.Float64).alias("centroid_lat"),
            pl.col("longitude").cast(pl.Float64).alias("centroid_lon"),
            (pl.col("capital") == 1).alias("is_capital"),
            pl.col("fuso_horario").alias("timezone"),
        )
        return {"municipality_dim": df}


# ---------------------------------------------------------------- IBGE geometry (polygons keyed by IBGE code)
def _simplify(ring: list, tol: float) -> list:
    """Douglas–Peucker on one ring (iterative), keeping the closing point."""
    pts = np.asarray(ring, dtype=float)
    if len(pts) <= 8:
        return [[round(x, 4), round(y, 4)] for x, y in pts]
    keep = np.zeros(len(pts), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        seg = pts[b] - pts[a]
        rel = pts[a + 1:b] - pts[a]
        n = math.hypot(*seg)
        d = np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / n if n > 0 else np.hypot(rel[:, 0], rel[:, 1])
        i = int(np.argmax(d))
        if d[i] > tol:
            keep[a + 1 + i] = True
            stack += [(a, a + 1 + i), (a + 1 + i, b)]
    out = pts[keep]
    if len(out) < 4:
        out = pts[np.linspace(0, len(pts) - 1, 4).astype(int)]
    return [[round(x, 4), round(y, 4)] for x, y in out]


class IBGEGeometry(Connector):
    source_id, tables, expect = "ibge_geometry", ("geometry",), "geojson"

    def probe(self, src):
        body, meta = lake.fetch(src["config_json"]["url"].replace("geojs-100-mun", "geojs-33-mun"),
                                approved=_approved(src), expect="geojson", max_bytes=20_000_000)
        fc = loads(body)
        return {"ok": fc.get("type") == "FeatureCollection", "features_in_sample_state": len(fc["features"]),
                "id_field": "properties.id (IBGE 7-digit)", "bytes": meta["bytes"]}

    def collect(self, ctx, src, params):
        ctx.log("downloading national municipal polygons (~22 MB)")
        body, meta = lake.fetch(src["config_json"]["url"], approved=_approved(src), expect="geojson", max_bytes=80_000_000,
                                timeout=180)
        return [("geojs-100-mun.json", body)], {"content_type": meta["content_type"], "geographic_level": "municipality"}

    def parse(self, ctx, src, manifest):
        (_, body), = self._payloads(manifest)
        fc = loads(body)
        tol = float(src["config_json"].get("simplify_tolerance_deg", 0.004))
        feats, rows = [], []
        for f in fc["features"]:
            mid = str(f["properties"]["id"]).zfill(7)
            g = f["geometry"]
            polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
            simp = [[_simplify(r, tol) for r in poly] for poly in polys]
            nv = sum(len(r) for p in simp for r in p)
            xs = [c[0] for p in simp for c in p[0]]
            ys = [c[1] for p in simp for c in p[0]]
            feats.append({"type": "Feature", "id": int(mid), "properties": {"id": mid},
                          "geometry": {"type": "MultiPolygon", "coordinates": simp}})
            rows.append({"municipality_id": mid, "name_in_source": f["properties"].get("name"), "vertices": nv,
                         "min_lon": min(xs), "min_lat": min(ys), "max_lon": max(xs), "max_lat": max(ys)})
        ctx.log(f"simplified {len(feats)} polygons at tolerance {tol}°")
        df = pl.DataFrame(rows)
        return {"geometry": df, "_geojson": {"type": "FeatureCollection", "features": feats}}  # type: ignore[dict-item]


# ---------------------------------------------------------------- InfoDengue
class InfoDengue(Connector):
    source_id, tables = "infodengue", ("disease_weekly", "population_annual")
    optional_tables = ("population_annual",)

    def probe(self, src):
        cfg = src["config_json"]
        y = utcnow().year
        body, meta = lake.fetch(cfg["base_url"], approved=_approved(src), expect="json", max_bytes=2_000_000, params={
            "geocode": "3304557", "disease": "dengue", "format": "json", "ew_start": 1, "ew_end": 2, "ey_start": y, "ey_end": y})
        rows = loads(body)
        return {"ok": isinstance(rows, list), "rows": len(rows), "columns": sorted(rows[0].keys()) if rows else [],
                "bytes": meta["bytes"]}

    def collect(self, ctx, src, params):
        cfg = src["config_json"]
        munis = scope_munis(params)
        if not params.get("municipalities") and cfg.get("include_arranjo_neighbours", True):
            from .connectors_extra import arranjo_neighbours
            nb = arranjo_neighbours(munis, int(cfg.get("max_neighbours", 80)))
            if nb:
                ctx.log(f"+{len(nb)} commuting-arranjo neighbours of the pilot (for the commuter-pressure feature)")
                munis = munis + nb
        diseases = params.get("diseases") or cfg.get("diseases", ["dengue"])
        y0 = int(params.get("start_year", cfg.get("start_year", 2018)))
        y1 = int(params.get("end_year", utcnow().year))
        out = []
        total = len(munis) * len(diseases)
        for i, m in enumerate(munis):
            for d in diseases:
                ctx.check_cancel()
                body, _ = lake.fetch(cfg["base_url"], approved=_approved(src), expect="json", min_interval_s=cfg.get("rate_limit_s", 0.4),
                                     params={"geocode": m, "disease": d, "format": "json", "ew_start": 1, "ew_end": 53,
                                             "ey_start": y0, "ey_end": y1})
                out.append((f"{d}_{m}.json", body))
            ctx.progress(0.05 + 0.5 * (i + 1) / max(1, len(munis)), f"downloaded {len(out)}/{total} series")
        return out, {"period_start": f"{y0}-01-01", "period_end": utcnow().date().isoformat(),
                     "geographic_level": "municipality", "municipalities": munis, "diseases": diseases}

    def parse(self, ctx, src, manifest):
        lag = int(src.get("publication_lag_days") or 3)
        avail = parse_dt(manifest["available_at"])
        rows = []
        for name, body in self._payloads(manifest):
            disease = name.split("_", 1)[0]
            for r in loads(body) or []:
                ws = datetime.fromtimestamp(r["data_iniSE"] / 1000, tz=timezone.utc).date()
                rows.append({
                    "municipality_id": str(name.rsplit("_", 1)[1].split(".")[0]).zfill(7),
                    "week_start": ws, "epi_week": int(r["SE"]), "disease_id": disease,
                    "observed_cases": None if r.get("casos") is None else int(r["casos"]),
                    "estimated_cases": None if r.get("casos_est") is None else float(r["casos_est"]),
                    "estimated_min": None if r.get("casos_est_min") is None else float(r["casos_est_min"]),
                    "estimated_max": None if r.get("casos_est_max") is None else float(r["casos_est_max"]),
                    "population": int(float(r["pop"])) if r.get("pop") not in (None, "") else None,
                    "infodengue_rt": None if r.get("Rt") is None else float(r["Rt"]),
                    "infodengue_alert_level": r.get("nivel"),
                    "row_model_version": r.get("versao_modelo"),
                })
        if not rows:
            return {"disease_weekly": pl.DataFrame(schema=silver.SCHEMAS["disease_weekly"]),
                    "population_annual": pl.DataFrame(schema=silver.SCHEMAS["population_annual"])}
        df = pl.DataFrame(rows, infer_schema_length=None).with_columns(
            pl.lit("notification").alias("signal_type"),
            pl.lit(avail).cast(pl.Datetime("us", "UTC")).alias("available_at"),
            # earliest plausible first-release time; used only by the PIT approximation, marked PIT_UNVERIFIED
            (pl.col("week_start").cast(pl.Datetime("us", "UTC")) + pl.duration(days=7 + lag)).alias("release_approx_at"),
            pl.lit("infodengue").alias("source_id"),
        )
        df = df.sort(["municipality_id", "disease_id", "week_start"]).unique(
            ["municipality_id", "week_start", "disease_id"], keep="last", maintain_order=True)
        pop = (df.filter(pl.col("population").is_not_null())
               .with_columns((pl.col("epi_week") // 100).cast(pl.Int32).alias("reference_year"))
               .group_by(["municipality_id", "reference_year"]).agg(pl.col("population").max()))
        ibge = silver.latest("population_annual")
        if ibge.height:
            # official IBGE (SIDRA) estimates take precedence; InfoDengue's copy only fills years IBGE has not published
            ibge = ibge.filter(pl.col("source_id") == "ibge_population").select("municipality_id", "reference_year")
            pop = pop.join(ibge, on=["municipality_id", "reference_year"], how="anti")
        pop = (pop
               .with_columns(pl.lit("infodengue (IBGE estimate as published by InfoDengue)").alias("source_note"),
                             pl.lit(avail).cast(pl.Datetime("us", "UTC")).alias("available_at"),
                             pl.lit("infodengue").alias("source_id")))
        return {"disease_weekly": silver.conform("disease_weekly", df), "population_annual": silver.conform("population_annual", pop)}


# ---------------------------------------------------------------- Open-Meteo (reanalysis)
class OpenMeteo(Connector):
    source_id, tables = "open_meteo", ("weather_weekly",)
    DAILY = "rain_sum,temperature_2m_mean,temperature_2m_max,relative_humidity_2m_mean"

    def probe(self, src):
        cfg = src["config_json"]
        d = (utcnow().date() - timedelta(days=14)).isoformat()
        body, meta = lake.fetch(cfg["base_url"], approved=_approved(src), expect="json", max_bytes=1_000_000, params={
            "latitude": -22.9, "longitude": -43.2, "start_date": d, "end_date": d, "daily": self.DAILY, "timezone": "America/Sao_Paulo"})
        j = loads(body)
        return {"ok": "daily" in j, "variables": list(j.get("daily_units", {}).keys()), "bytes": meta["bytes"]}

    def collect(self, ctx, src, params):
        cfg = src["config_json"]
        dim = silver.latest("municipality_dim")
        munis = scope_munis(params)
        sel = dim.filter(pl.col("municipality_id").is_in(munis)).sort("municipality_id")
        missing = set(munis) - set(sel["municipality_id"].to_list())
        if missing:
            ctx.log(f"no centroid for {sorted(missing)} — skipped (ingest ibge_municipalities first)", level="warning")
        start = params.get("start_date", cfg.get("start_date", "2017-12-01"))
        end = params.get("end_date", (utcnow().date() - timedelta(days=6)).isoformat())
        bs = int(cfg.get("batch_size", 10))
        out = []
        recs = sel.to_dicts()
        for i in range(0, len(recs), bs):
            ctx.check_cancel()
            chunk = recs[i:i + bs]
            body, _ = lake.fetch(cfg["base_url"], approved=_approved(src), expect="json", min_interval_s=0.5, timeout=120, params={
                "latitude": ",".join(f"{r['centroid_lat']:.4f}" for r in chunk),
                "longitude": ",".join(f"{r['centroid_lon']:.4f}" for r in chunk),
                "start_date": start, "end_date": end, "daily": self.DAILY, "timezone": "America/Sao_Paulo"})
            out.append((f"batch_{i // bs:04d}__{'-'.join(r['municipality_id'] for r in chunk)}.json", body))
            ctx.progress(0.05 + 0.5 * min(1, (i + bs) / max(1, len(recs))), f"weather batch {i // bs + 1}")
        return out, {"period_start": start, "period_end": end, "geographic_level": "municipality centroid",
                     "source_kind": "observed_reanalysis"}

    def parse(self, ctx, src, manifest):
        lag = int(src.get("publication_lag_days") or 6)
        min_cov = float(src["config_json"].get("min_weekly_coverage", 0.85))
        avail = parse_dt(manifest["available_at"])
        frames = []
        for name, body in self._payloads(manifest):
            ids = name.split("__", 1)[1].rsplit(".", 1)[0].split("-")
            j = loads(body)
            items = j if isinstance(j, list) else [j]
            for mid, it in zip(ids, items):
                d = it["daily"]
                frames.append(pl.DataFrame({
                    "municipality_id": [mid] * len(d["time"]), "date": d["time"], "rain_mm": d["rain_sum"],
                    "t2m_mean": d["temperature_2m_mean"], "t2m_max": d["temperature_2m_max"], "rh_mean": d["relative_humidity_2m_mean"],
                }, schema_overrides={"rain_mm": pl.Float64, "t2m_mean": pl.Float64, "t2m_max": pl.Float64, "rh_mean": pl.Float64}))
        daily = pl.concat(frames).with_columns(pl.col("date").str.to_date())
        wk = (daily.with_columns((pl.col("date") - pl.duration(days=(pl.col("date").dt.weekday() % 7))).alias("week_start"))
              .group_by(["municipality_id", "week_start"]).agg(
                  pl.col("rain_mm").sum().alias("rain_mm_sum"), pl.col("t2m_mean").mean().alias("t2m_mean"),
                  pl.col("t2m_max").max().alias("t2m_max"), pl.col("rh_mean").mean().alias("rh_mean"),
                  pl.col("rain_mm").is_not_null().sum().alias("days_observed"))
              .with_columns((pl.col("days_observed") / 7.0).alias("coverage"))
              .with_columns(
                  pl.when(pl.col("coverage") < min_cov).then(None).otherwise(pl.col("rain_mm_sum")).alias("rain_mm_sum"),
                  pl.lit("observed_reanalysis").alias("source_kind"),
                  pl.lit(avail).cast(pl.Datetime("us", "UTC")).alias("available_at"),
                  (pl.col("week_start").cast(pl.Datetime("us", "UTC")) + pl.duration(days=7 + lag)).alias("release_approx_at"),
                  pl.lit("open_meteo").alias("source_id")))
        return {"weather_weekly": silver.conform("weather_weekly", wk)}


# ---------------------------------------------------------------- Nager.Date holidays
class NagerHolidays(Connector):
    source_id, tables = "nager_holidays", ("holiday_daily",)

    def probe(self, src):
        cfg = src["config_json"]
        body, meta = lake.fetch(f"{cfg['base_url']}/{utcnow().year}/{cfg['country']}", approved=_approved(src), expect="json")
        rows = loads(body)
        return {"ok": isinstance(rows, list), "holidays_this_year": len(rows), "bytes": meta["bytes"]}

    def collect(self, ctx, src, params):
        cfg = src["config_json"]
        y0 = int(params.get("start_year", cfg.get("start_year", 2017)))
        y1 = utcnow().year + int(cfg.get("years_ahead", 1))
        out = []
        for y in range(y0, y1 + 1):
            body, _ = lake.fetch(f"{cfg['base_url']}/{y}/{cfg['country']}", approved=_approved(src), expect="json", min_interval_s=0.2)
            out.append((f"{y}.json", body))
        return out, {"period_start": f"{y0}-01-01", "period_end": f"{y1}-12-31", "geographic_level": "country"}

    def parse(self, ctx, src, manifest):
        avail = parse_dt(manifest["available_at"])
        rows = []
        for name, body in self._payloads(manifest):
            for h in loads(body):
                subs = h.get("counties") or [None]
                for sub in subs:
                    rows.append({"date": date.fromisoformat(h["date"]), "country_code": h["countryCode"],
                                 "subdivision": sub, "holiday_type": ",".join(h.get("types") or []),
                                 "holiday_name": h["name"], "local_name": h.get("localName"), "is_national": bool(h.get("global"))})
        df = pl.DataFrame(rows).with_columns(
            # scheduled public holidays are known ahead; we record the calendar as known at the start of its year
            pl.col("date").dt.truncate("1y").cast(pl.Datetime("us", "UTC")).alias("known_at"),
            pl.lit(avail).cast(pl.Datetime("us", "UTC")).alias("available_at"), pl.lit("nager_holidays").alias("source_id"))
        return {"holiday_daily": silver.conform("holiday_daily", df)}


# ---------------------------------------------------------------- CNES facilities + beds
CNES_TYPES = {1: "Posto de saúde", 2: "Centro de saúde / UBS", 5: "Hospital geral", 7: "Hospital especializado",
              15: "Unidade mista", 73: "Pronto atendimento"}


class CNES(Connector):
    source_id, tables = "cnes_facilities_beds", ("facility", "facility_beds")

    def probe(self, src):
        cfg = src["config_json"]
        body, meta = lake.fetch(cfg["facilities_url"], approved=_approved(src), expect="json",
                                params={"codigo_municipio": "330455", "limit": 2})
        e = loads(body).get("estabelecimentos", [])
        beds = self._beds_url(src)
        return {"ok": bool(e), "facility_columns": sorted(e[0].keys()) if e else [], "beds_file": beds,
                "beds_join": "CNES establishment code -> facility register (IBGE code); names are never used"}

    def _beds_url(self, src) -> str | None:
        from .connectors_extra import _discover
        page = src["config_json"].get("beds_dataset_page")
        if not page:
            return None
        urls = _discover(src, page, r"https://s3\.sa-east-1\.amazonaws\.com/ckan\.saude\.gov\.br/Leitos_SUS/Leitos_(?:csv_)?\d{4}\.(?:csv|zip)")
        key = lambda u: int(__import__("re").search(r"(\d{4})\.(?:csv|zip)$", u).group(1))
        return max(urls, key=key) if urls else None

    def collect(self, ctx, src, params):
        cfg = src["config_json"]
        munis = scope_munis(params)
        out = []
        for i, m in enumerate(munis):
            for t in params.get("unit_types", list(CNES_TYPES)):
                page = 0
                while page < int(cfg.get("max_pages_per_municipality", 400)):
                    ctx.check_cancel()
                    body, _ = lake.fetch(cfg["facilities_url"], approved=_approved(src), expect="json",
                                         min_interval_s=cfg.get("rate_limit_s", 0.15),
                                         params={"codigo_municipio": m[:6], "codigo_tipo_unidade": t,
                                                 "limit": cfg.get("page_size", 20), "offset": page * cfg.get("page_size", 20)})
                    rows = loads(body).get("estabelecimentos", [])
                    if not rows:
                        break
                    out.append((f"fac_{m}_{t}_{page:04d}.json", body))
                    if len(rows) < cfg.get("page_size", 20):
                        break
                    page += 1
            ctx.progress(0.05 + 0.4 * (i + 1) / len(munis), f"facilities for {i + 1}/{len(munis)} municipalities")
        beds = self._beds_url(src)
        if beds:
            ctx.log(f"registered beds file {beds}")
            body, _ = lake.fetch(beds, approved=_approved(src), max_bytes=200_000_000, timeout=300,
                                 expect="zip" if beds.endswith(".zip") else "csv")
            out.append(("beds_" + beds.rsplit("/", 1)[1], body))
        else:
            ctx.log("no Leitos file found on the dataset page; beds skipped", level="warning")
        return out, {"geographic_level": "establishment", "municipalities": munis, "beds_file": beds}

    def parse(self, ctx, src, manifest):
        avail = parse_dt(manifest["available_at"])
        dim = silver.latest("municipality_dim").select("municipality_id")
        six_to_seven = {m[:6]: m for m in dim["municipality_id"].to_list()}
        facs, beds = [], []
        beds_raw = None
        for name, body in self._payloads(manifest):
            if name.startswith("beds_"):
                beds_raw = _read_leitos(name, body)
                continue
            j = loads(body)
            if name.startswith("fac_"):
                for e in j.get("estabelecimentos", []):
                    m6 = str(e.get("codigo_municipio") or "").zfill(6)
                    facs.append({
                        "facility_id": str(e["codigo_cnes"]).zfill(7), "municipality_id": six_to_seven.get(m6),
                        "municipality_code6": m6, "name": e.get("nome_fantasia") or e.get("nome_razao_social"),
                        "facility_type_code": int(e["codigo_tipo_unidade"]), "facility_type": CNES_TYPES.get(int(e["codigo_tipo_unidade"]), "Other"),
                        "lat": e.get("latitude_estabelecimento_decimo_grau"), "lon": e.get("longitude_estabelecimento_decimo_grau"),
                        "has_inpatient": bool(e.get("estabelecimento_possui_atendimento_hospitalar")),
                        "sus_ambulatory": e.get("estabelecimento_faz_atendimento_ambulatorial_sus") == "SIM",
                        "admin_sphere": e.get("descricao_esfera_administrativa"), "updated_in_source": e.get("data_atualizacao"),
                        "disabled_reason_code": e.get("codigo_motivo_desabilitacao_estabelecimento"),
                    })
        fdf = pl.DataFrame(facs, infer_schema_length=None) if facs else pl.DataFrame(schema=silver.SCHEMAS["facility"])
        if facs:
            fdf = fdf.unique("facility_id", keep="last").with_columns(
                pl.lit(utcnow().strftime("%Y-%m")).alias("snapshot_month"),
                pl.lit(avail).cast(pl.Datetime("us", "UTC")).alias("available_at"), pl.lit("cnes_facilities_beds").alias("source_id"))
        bdf = pl.DataFrame(schema=silver.SCHEMAS["facility_beds"])
        if beds_raw is not None and beds_raw.height and fdf.height:
            comp = beds_raw["COMP"].max()
            latest = beds_raw.filter(pl.col("COMP") == comp)
            # join on the CNES establishment code; the facility register supplies the IBGE municipality code
            reg = fdf.select("facility_id", "municipality_id")
            bdf = latest.join(reg, left_on="CNES", right_on="facility_id", how="inner").select(
                pl.col("CNES").alias("facility_id"), pl.col("COMP").alias("competence"),
                pl.col("NOME_ESTABELECIMENTO").alias("hospital_name"), pl.col("UF").alias("state_abbr"), "municipality_id",
                pl.col("LEITOS_EXISTENTES").cast(pl.Float64).alias("registered_beds"),
                pl.col("LEITOS_SUS").cast(pl.Float64).alias("registered_beds_sus"),
                pl.col("UTI_TOTAL_EXIST").cast(pl.Float64).alias("registered_icu_beds"),
                pl.col("UTI_TOTAL_SUS").cast(pl.Float64).alias("registered_icu_beds_sus"),
            ).unique("facility_id", keep="last").with_columns(
                pl.lit(avail).cast(pl.Datetime("us", "UTC")).alias("available_at"), pl.lit("cnes_facilities_beds").alias("source_id"))
            ctx.log(f"beds competence {comp}: {bdf.height:,} hospitals matched by CNES code to in-scope facilities "
                    f"({latest.height - bdf.height:,} out-of-scope establishments not loaded)")
        return {"facility": silver.conform("facility", fdf), "facility_beds": silver.conform("facility_beds", bdf)}


def _read_leitos(name: str, body: bytes) -> pl.DataFrame:
    """OpenDataSUS 'Hospitais e Leitos' CSV (latin-1, comma separated) or the zipped CSV, typed and CNES-coded."""
    import zipfile
    if body[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(body))
        inner = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not inner or z.getinfo(inner[0]).file_size > 1_000_000_000:
            raise ValueError("Leitos archive has no CSV or is too large")
        body = z.read(inner[0])
    text = body.decode("latin-1")
    sep = ";" if text[:2000].count(";") > text[:2000].count(",") else ","
    df = pl.read_csv(io.StringIO(text), separator=sep, infer_schema=False)
    need = {"COMP", "CNES", "UF", "LEITOS_EXISTENTES", "LEITOS_SUS", "UTI_TOTAL_EXIST", "UTI_TOTAL_SUS"}
    if not need <= set(df.columns):
        raise ValueError(f"Leitos file lacks {sorted(need - set(df.columns))}")
    if "NOME_ESTABELECIMENTO" not in df.columns:
        df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias("NOME_ESTABELECIMENTO"))
    return df.with_columns(pl.col("CNES").str.strip_chars().str.zfill(7), pl.col("COMP").str.strip_chars())


# ---------------------------------------------------------------- SIVEP-Gripe (file import, aggregated immediately)
SIVEP_CLASS = {"1": "influenza_sari", "2": "other_sari", "3": "other_sari", "4": "other_sari", "5": "covid19_sari"}


class SIVEP(Connector):
    """Case-level SRAG export -> municipality-week SARI counts. Streams with Polars so multi-million-row files fit.
    Personal columns never leave this function; Silver holds counts only."""
    source_id, tables, expect = "sivep_gripe", ("sari_weekly",), "csv"

    def probe(self, src):
        return {"ok": True, "mode": "file_upload", "required_columns": ["DT_SIN_PRI", "CO_MUN_RES", "CLASSI_FIN"],
                "note": "Upload the OpenDataSUS SRAG CSV (semicolon separated). Row-level data is aggregated on import."}

    def collect(self, ctx, src, params):
        raise RuntimeError("SIVEP-Gripe is file-import only: POST /admin/sources/sivep_gripe/upload")

    def parse(self, ctx, src, manifest):
        f = manifest["files"][0]
        path = lake.bronze_path(f["path"])
        avail = parse_dt(manifest["available_at"])
        delim = src["config_json"].get("delimiter", ";")
        dim = silver.latest("municipality_dim").select("municipality_id")
        six = pl.DataFrame({"m6": [m[:6] for m in dim["municipality_id"].to_list()], "municipality_id": dim["municipality_id"].to_list()})
        lf = pl.scan_csv(path, separator=delim, infer_schema=False, encoding="utf8-lossy", ignore_errors=True)
        cols = lf.collect_schema().names()
        need = {"DT_SIN_PRI", "CO_MUN_RES", "CLASSI_FIN"}
        if not need.issubset(cols):
            raise ValueError(f"SIVEP file is missing columns {sorted(need - set(cols))}")
        agg = (lf.select("DT_SIN_PRI", "CO_MUN_RES", "CLASSI_FIN")
               .with_columns(pl.coalesce(pl.col("DT_SIN_PRI").str.to_date("%d/%m/%Y", strict=False),
                                         pl.col("DT_SIN_PRI").str.to_date("%Y-%m-%d", strict=False)).alias("onset"),
                             pl.col("CO_MUN_RES").str.strip_chars().str.slice(0, 6).alias("m6"),
                             pl.col("CLASSI_FIN").str.strip_chars().replace_strict(SIVEP_CLASS, default="unclassified_sari").alias("disease_id"))
               .filter(pl.col("onset").is_not_null())
               .with_columns((pl.col("onset") - pl.duration(days=(pl.col("onset").dt.weekday() % 7))).alias("week_start"))
               .group_by(["m6", "week_start", "disease_id"]).agg(pl.len().alias("observed_cases"))
               .collect(engine="streaming"))
        ctx.log(f"aggregated {int(agg['observed_cases'].sum()):,} case rows into {agg.height:,} municipality-week cells")
        out = (agg.join(six, on="m6", how="left")
               .with_columns(pl.col("observed_cases").cast(pl.Int64), pl.lit("sari_admission").alias("signal_type"),
                             pl.lit(avail).cast(pl.Datetime("us", "UTC")).alias("available_at"),
                             (pl.col("week_start").cast(pl.Datetime("us", "UTC")) + pl.duration(days=14)).alias("release_approx_at"),
                             pl.lit("sivep_gripe").alias("source_id"),
                             pl.lit(bool(manifest.get("is_synthetic"))).alias("is_synthetic"))
               .drop("m6"))
        return {"sari_weekly": silver.conform("sari_weekly", out)}


# ---------------------------------------------------------------- CEMPRE (file import)
class CEMPRE(Connector):
    source_id, tables, expect = "ibge_cempre", ("workforce_sector",), "csv"

    def probe(self, src):
        return {"ok": True, "mode": "file_upload", "required_columns": src["config_json"]["columns"]}

    def collect(self, ctx, src, params):
        raise RuntimeError("CEMPRE is file-import only: POST /admin/sources/ibge_cempre/upload")

    def parse(self, ctx, src, manifest):
        path = lake.bronze_path(manifest["files"][0]["path"])
        df = pl.read_csv(path, schema_overrides={"municipality_id": pl.Utf8, "cnae_section": pl.Utf8})
        df = df.with_columns(pl.col("municipality_id").str.zfill(7),
                             pl.lit(parse_dt(manifest["available_at"])).cast(pl.Datetime("us", "UTC")).alias("available_at"),
                             pl.lit("ibge_cempre").alias("source_id"))
        return {"workforce_sector": silver.conform("workforce_sector", df)}


class GeoBoundaries(Connector):
    source_id, tables = "geoboundaries", ()

    def probe(self, src):
        body, meta = lake.fetch(src["config_json"]["meta_url"], approved=_approved(src), expect="json")
        j = loads(body)
        return {"ok": True, "boundary_id": j.get("boundaryID"), "units": j.get("admUnitCount"), "license": j.get("boundaryLicense"),
                "warning": src["config_json"]["note"]}

    def collect(self, ctx, src, params):
        raise RuntimeError("geoBoundaries is probe-only: its polygons carry no IBGE code and name-based crosswalks are refused")


class Unavailable(Connector):
    def __init__(self, sid: str):
        self.source_id = sid

    def probe(self, src):
        return {"ok": False, "status": src["status"],
                "message": "This connector needs an authorization or a manual file import before it can run."}

    def collect(self, ctx, src, params):
        raise RuntimeError(f"{self.source_id} is {src['status']}; no data was fetched or substituted")


CONNECTORS: dict[str, Connector] = {c.source_id: c for c in (IBGEMunicipalities(), IBGEGeometry(), InfoDengue(), OpenMeteo(),
                                                              NagerHolidays(), CNES(), SIVEP(), CEMPRE(), GeoBoundaries())}


def _register_extra() -> None:
    from .connectors_extra import EXTRA
    from .connectors_geo import GEO
    for c in (*EXTRA, *GEO):
        CONNECTORS[c.source_id] = c


_register_extra()


def write_synthetic_sivep(path, rows: int, munis: list[str], start: date, weeks: int, seed: int = 7) -> int:
    """SYNTHETIC case-level SRAG-shaped file for load testing (millions of rows). Never presented as real data.
    Week-of-onset follows a seasonal wave per municipality so the aggregated signal has a plausible shape."""
    rng = np.random.default_rng(seed)
    chunk = 1_000_000
    written = 0
    m6 = np.array([m[:6] for m in munis])
    wave = np.exp(-0.5 * ((np.arange(weeks) - weeks * 0.55) / max(2.0, weeks / 7)) ** 2) + 0.15
    p_week = wave / wave.sum()
    with open(path, "wb") as f:
        first = True
        while written < rows:
            n = min(chunk, rows - written)
            wk = rng.choice(weeks, n, p=p_week)
            day = rng.integers(0, 7, n)
            onset = np.datetime64(start) + (wk * 7 + day).astype("timedelta64[D]")
            df = pl.DataFrame({
                "DT_SIN_PRI": onset.astype("datetime64[D]"),
                "CO_MUN_RES": m6[rng.integers(0, len(m6), n)],
                "CLASSI_FIN": rng.choice(np.array(["1", "4", "5", "2"]), n, p=[0.2, 0.35, 0.4, 0.05]),
                "CS_SEXO": rng.choice(np.array(["M", "F"]), n),
                "NU_IDADE_N": rng.integers(0, 95, n),
            }).with_columns(pl.col("DT_SIN_PRI").dt.strftime("%d/%m/%Y"))
            df.write_csv(f, separator=";", include_header=first)
            first = False
            written += n
    return written
