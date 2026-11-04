"""Connectors that close the data gaps listed in docs/LIMITATIONS.md with real, downloadable public sources.

Every source here is keyed by the IBGE municipality code (or by the CNES establishment code, joined to an IBGE code
through the CNES establishment register). Nothing is joined on names, nothing is substituted, and every release is
kept as a Bronze snapshot with its checksum, like the other connectors.

  ibge_population           IBGE SIDRA table 6579  official annual population estimates, all municipalities
  ibge_cempre               IBGE SIDRA table 6450  CEMPRE salaried workers by CNAE 2.0 section
  ibge_commuting            IBGE "Arranjos Populacionais" 2nd ed. (Census 2010 commuting integration), tab 1.1
  sivep_gripe               OpenDataSUS SRAG yearly parquet exports, aggregated to municipality-week on import
  cnes_beds (in connectors) OpenDataSUS "Hospitais e Leitos" CSV, joined by CNES code (see CNES connector)
  brazil_dengue_multimodal  Zenodo record 22029053 (CC BY 4.0) remote-sensing / ENSO / sanitation variables
  worldpop                  WorldPop R2025A 1 km constrained population raster, summed over IBGE polygons
"""
from __future__ import annotations

import io
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import polars as pl

from ..config import get_settings
from ..core import lake
from ..core.util import loads, parse_dt, utcnow
from ..core.xlsx import read_rows
from . import silver
from .connectors import Connector, _approved, scope_munis

DT = pl.Datetime("us", "UTC")


def _tmp(suffix: str) -> Path:
    d = get_settings().uploads_dir / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkstemp(dir=d, suffix=suffix)[1])


def _download(ctx, src: dict, url: str, name: str, expect: str, max_bytes: int, lo: float, hi: float) -> tuple[str, Path]:
    dest = _tmp("." + name.rsplit(".", 1)[-1])
    ctx.log(f"downloading {url}")
    last = [0.0]

    def prog(f):
        if f - last[0] >= 0.05:
            last[0] = f
            ctx.progress(lo + (hi - lo) * f, f"{name}: {f:.0%}")
    meta = lake.fetch_to_file(url, dest, approved=_approved(src), max_bytes=max_bytes, expect=expect, progress=prog)
    ctx.log(f"{name}: {meta['bytes'] / 1e6:.1f} MB")
    return name, dest


def _md5(path: Path) -> str:
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _cache_hit(md5: str | None, size: int) -> Path | None:
    """A completed download left behind by a failed ingest (same size and publisher md5) is reused, not re-fetched."""
    if not md5:
        return None
    for p in (get_settings().uploads_dir / "downloads").glob("*"):
        if p.suffix != ".part" and p.is_file() and p.stat().st_size == size and _md5(p) == md5:
            return p
    return None


def _discover(src: dict, page_url: str, pattern: str) -> list[str]:
    """Resolve current file URLs from the publisher's dataset page (file names carry a release date that changes)."""
    body, _ = lake.fetch(page_url, approved=_approved(src), max_bytes=10_000_000, timeout=60)
    return sorted(set(re.findall(pattern, body.decode("utf-8", "replace"))))


# ---------------------------------------------------------------- SIDRA helpers
def _sidra(src: dict, path: str) -> list[dict]:
    body, _ = lake.fetch(f"{src['config_json']['base_url']}/{path}", approved=_approved(src), expect="json",
                         max_bytes=200_000_000, timeout=180, min_interval_s=0.3)
    rows = loads(body)
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"SIDRA returned no rows for {path}")
    return rows[1:]  # the first row is the column legend


def _sidra_value(v: str | None) -> int | None:
    """SIDRA symbols: '-' is a true zero, 'X' is suppressed for confidentiality, '..'/'...' not available -> null."""
    if v is None:
        return None
    v = v.strip()
    if v == "-":
        return 0
    try:
        return int(float(v))
    except ValueError:
        return None


class IBGEPopulation(Connector):
    """Official IBGE municipal population estimates (and census counts) from SIDRA table 6579."""
    source_id, tables = "ibge_population", ("population_annual",)

    def probe(self, src):
        rows = _sidra(src, "t/6579/n6/3304557/v/9324/p/last")
        return {"ok": bool(rows), "sample": {"municipality": rows[0]["D1N"], "year": rows[0]["D3N"], "population": rows[0]["V"]}}

    def collect(self, ctx, src, params):
        cfg = src["config_json"]
        y0 = int(params.get("start_year", cfg.get("start_year", 2015)))
        y1 = int(params.get("end_year", utcnow().year))
        out = []
        for i, y in enumerate(range(y0, y1 + 1)):
            ctx.check_cancel()
            try:
                body, _ = lake.fetch(f"{cfg['base_url']}/t/6579/n6/all/v/9324/p/{y}", approved=_approved(src), expect="json",
                                     max_bytes=100_000_000, timeout=180, min_interval_s=0.3)
            except lake.FetchError as e:
                # census years (e.g. 2022) have no estimate in 6579; that year is simply absent, never interpolated
                ctx.log(f"{y}: no estimate published ({str(e)[-80:]})", level="warning")
                continue
            rows = loads(body) if body.strip().startswith(b"[") else []
            if len(rows) > 1:
                out.append((f"pop_{y}.json", body))
            else:
                ctx.log(f"{y}: no estimate published (census year or not released yet)", level="warning")
            ctx.progress(0.05 + 0.5 * (i + 1) / (y1 - y0 + 1), f"population {y}")
        if not out:
            raise RuntimeError("SIDRA returned no population years")
        return out, {"geographic_level": "municipality", "period_start": f"{y0}-07-01", "period_end": f"{y1}-07-01",
                     "table": "SIDRA 6579 (Estimativas da População)"}

    def parse(self, ctx, src, manifest):
        avail = parse_dt(manifest["available_at"])
        rows = []
        for name, body in self._payloads(manifest):
            for r in loads(body)[1:]:
                v = _sidra_value(r.get("V"))
                if v is None:
                    continue
                rows.append({"municipality_id": str(r["D1C"]).zfill(7), "reference_year": int(r["D3C"]), "population": v})
        df = pl.DataFrame(rows).unique(["municipality_id", "reference_year"], keep="last").with_columns(
            pl.lit("IBGE official estimate (SIDRA table 6579)").alias("source_note"),
            pl.lit(avail).cast(DT).alias("available_at"), pl.lit(self.source_id).alias("source_id"))
        ctx.log(f"{df['municipality_id'].n_unique():,} municipalities × {df['reference_year'].n_unique()} years")
        return {"population_annual": silver.conform("population_annual", df)}


class IBGECempre(Connector):
    """CEMPRE salaried workers (pessoal ocupado assalariado, variable 708) by CNAE 2.0 section, SIDRA table 6450."""
    source_id, tables = "ibge_cempre", ("workforce_sector",)
    SECTION = re.compile(r"^([A-U]) (.+)$")

    def probe(self, src):
        rows = _sidra(src, "t/6450/n6/3304557/v/708/p/last/c12762/117897")
        return {"ok": bool(rows), "sample": {"municipality": rows[0]["D1N"], "year": rows[0]["D3N"], "salaried_workers": rows[0]["V"]}}

    def collect(self, ctx, src, params):
        cfg = src["config_json"]
        munis = scope_munis(params)
        years = int(params.get("years", cfg.get("years", 3)))
        out = []
        for i in range(0, len(munis), 5):
            ctx.check_cancel()
            chunk = munis[i:i + 5]
            body, _ = lake.fetch(f"{cfg['base_url']}/t/6450/n6/{','.join(chunk)}/v/708/p/last%20{years}/c12762/all",
                                 approved=_approved(src), expect="json", max_bytes=100_000_000, timeout=180, min_interval_s=0.3)
            out.append((f"cempre_{i // 5:04d}.json", body))
            ctx.progress(0.05 + 0.5 * min(1, (i + 5) / len(munis)), f"CEMPRE {min(i + 5, len(munis))}/{len(munis)}")
        return out, {"geographic_level": "municipality", "municipalities": munis, "table": "SIDRA 6450 (CEMPRE, CNAE 2.0)"}

    def parse(self, ctx, src, manifest):
        avail = parse_dt(manifest["available_at"])
        rows = []
        for _, body in self._payloads(manifest):
            for r in loads(body)[1:]:
                m = self.SECTION.match(r.get("D4N") or "")
                if not m:
                    continue  # only the 21 top-level sections; divisions/groups would double count
                rows.append({"municipality_id": str(r["D1C"]).zfill(7), "year": int(r["D3C"]), "cnae_section": m.group(1),
                             "cnae_label": m.group(2).strip(), "formal_workers": _sidra_value(r.get("V"))})
        df = pl.DataFrame(rows, infer_schema_length=None).with_columns(
            pl.lit(avail).cast(DT).alias("available_at"), pl.lit(self.source_id).alias("source_id"))
        sup = df["formal_workers"].null_count()
        ctx.log(f"{df.height:,} section rows; {sup} suppressed by IBGE ('X') kept as null, not zero")
        return {"workforce_sector": silver.conform("workforce_sector", df)}


# ---------------------------------------------------------------- IBGE commuting (Census 2010 arranjos populacionais)
class IBGECommuting(Connector):
    source_id, tables = "ibge_commuting", ("commuting_arranjo",)

    def probe(self, src):
        body, meta = lake.fetch(src["config_json"]["url"], approved=_approved(src), expect="xlsx", max_bytes=10_000_000)
        rows = read_rows(body)
        return {"ok": len(rows) > 100, "rows": len(rows), "header": [c for c in rows[1] if c], "bytes": meta["bytes"]}

    def collect(self, ctx, src, params):
        body, meta = lake.fetch(src["config_json"]["url"], approved=_approved(src), expect="xlsx", max_bytes=10_000_000)
        return [("tab01.xlsx", body)], {"geographic_level": "municipality", "reference_year": 2010,
                                        "content_type": meta["content_type"]}

    def parse(self, ctx, src, manifest):
        (_, body), = self._payloads(manifest)
        avail = parse_dt(manifest["available_at"])
        rows, arranjo = [], None
        for r in read_rows(body)[2:]:
            name, code = r[0], r[1]
            if isinstance(name, str) and code is None and r[2] is None:
                if name.startswith("Fonte"):
                    break
                arranjo = name.strip()
                continue
            if not isinstance(code, int) or arranjo is None:
                continue
            rows.append({"municipality_id": str(code).zfill(7), "arranjo_name": arranjo, "is_core": r[3] == "Sim",
                         "population_2010": int(r[2]) if isinstance(r[2], (int, float)) else None,
                         "commuters_in_arranjo": int(r[4]) if isinstance(r[4], (int, float)) else None,
                         "integration_index": float(r[5]) if isinstance(r[5], (int, float)) else None})
        df = pl.DataFrame(rows)
        # one municipality belongs to at most one arranjo; keep the first listing if IBGE repeats a row
        df = df.unique("municipality_id", keep="first", maintain_order=True)
        aid = {a: i for i, a in enumerate(sorted(df["arranjo_name"].unique().to_list()))}
        df = df.with_columns(pl.col("arranjo_name").replace_strict(aid, return_dtype=pl.Int64).alias("arranjo_id"),
                             pl.lit(2010).alias("reference_year"), pl.lit(avail).cast(DT).alias("available_at"),
                             pl.lit(self.source_id).alias("source_id"))
        ctx.log(f"{df.height:,} municipalities in {len(aid)} arranjos populacionais (Census 2010 commuting)")
        return {"commuting_arranjo": silver.conform("commuting_arranjo", df)}


def arranjo_neighbours(munis: list[str], cap: int = 80) -> list[str]:
    """Other members of the pilot municipalities' arranjos (needed for the commuter-pressure feature)."""
    ca = silver.latest("commuting_arranjo")
    if not ca.height:
        return []
    ids = ca.filter(pl.col("municipality_id").is_in(munis))["arranjo_id"].unique().to_list()
    nb = ca.filter(pl.col("arranjo_id").is_in(ids) & ~pl.col("municipality_id").is_in(munis)).sort(
        "population_2010", descending=True, nulls_last=True)
    return nb["municipality_id"].head(cap).to_list()


# ---------------------------------------------------------------- SIVEP-Gripe / SRAG (OpenDataSUS)
SIVEP_CLASS = {"1": "influenza_sari", "2": "other_sari", "3": "other_sari", "4": "other_sari", "5": "covid19_sari"}


def aggregate_srag(paths: list[Path], delimiter: str = ";") -> pl.DataFrame:
    """Case rows -> (6-digit residence code, epi week of onset, disease) counts. Only three columns are ever read."""
    frames = []
    for p in paths:
        if p.suffix == ".parquet":
            lf = pl.scan_parquet(p)
            cols = lf.collect_schema().names()
            if not {"DT_SIN_PRI", "CO_MUN_RES", "CLASSI_FIN"} <= set(cols):
                raise ValueError(f"SRAG parquet {p.name} lacks DT_SIN_PRI/CO_MUN_RES/CLASSI_FIN")
            lf = lf.select(pl.col("DT_SIN_PRI").cast(pl.Date, strict=False).alias("onset"),
                           pl.col("CO_MUN_RES").cast(pl.Utf8).str.strip_chars().str.slice(0, 6).alias("m6"),
                           pl.col("CLASSI_FIN").cast(pl.Int64, strict=False).cast(pl.Utf8).alias("cls"))
        else:
            lf = pl.scan_csv(p, separator=delimiter, infer_schema=False, encoding="utf8-lossy", ignore_errors=True)
            cols = lf.collect_schema().names()
            need = {"DT_SIN_PRI", "CO_MUN_RES", "CLASSI_FIN"}
            if not need <= set(cols):
                raise ValueError(f"SIVEP file is missing columns {sorted(need - set(cols))}")
            lf = lf.select(pl.coalesce(pl.col("DT_SIN_PRI").str.to_date("%d/%m/%Y", strict=False),
                                       pl.col("DT_SIN_PRI").str.to_date("%Y-%m-%d", strict=False)).alias("onset"),
                           pl.col("CO_MUN_RES").str.strip_chars().str.slice(0, 6).alias("m6"),
                           pl.col("CLASSI_FIN").str.strip_chars().alias("cls"))
        frames.append(lf)
    lf = pl.concat(frames, how="vertical_relaxed")
    return (lf.with_columns(pl.col("cls").replace_strict(SIVEP_CLASS, default="unclassified_sari").alias("disease_id"))
            .filter(pl.col("onset").is_not_null())
            .with_columns((pl.col("onset") - pl.duration(days=(pl.col("onset").dt.weekday() % 7))).alias("week_start"))
            .group_by(["m6", "week_start", "disease_id"]).agg(pl.len().alias("observed_cases"))
            .collect(engine="streaming"))


class SRAGDownload(Connector):
    """SIVEP-Gripe SRAG case-level exports from OpenDataSUS (yearly parquet), downloaded and aggregated immediately.
    Personal columns are never read; Silver holds municipality-week counts only. Manual CSV uploads still work."""
    source_id, tables = "sivep_gripe", ("sari_weekly",)

    def _urls(self, src, years: list[int]) -> list[str]:
        cfg = src["config_json"]
        found = _discover(src, cfg["dataset_page"], r"https://s3\.sa-east-1\.amazonaws\.com/ckan\.saude\.gov\.br/SRAG/\d{4}/INFLUD\d{2}-[\d-]+\.parquet")
        by_year: dict[int, str] = {}
        for u in found:
            y = int(re.search(r"/SRAG/(\d{4})/", u).group(1))
            if y in years:
                by_year[y] = max(by_year.get(y, ""), u)
        return [by_year[y] for y in sorted(by_year)]

    def probe(self, src):
        urls = self._urls(src, list(range(2019, utcnow().year + 1)))
        return {"ok": bool(urls), "files_found": len(urls), "latest": urls[-1] if urls else None, "mode": "download + file_upload",
                "note": "Yearly OpenDataSUS parquet exports; aggregated to municipality-week on import."}

    def collect(self, ctx, src, params):
        cfg = src["config_json"]
        y1 = utcnow().year
        years = params.get("years") or list(range(y1 - int(cfg.get("years_back", 2)), y1 + 1))
        urls = self._urls(src, [int(y) for y in years])
        if not urls:
            raise RuntimeError("no SRAG parquet files found on the OpenDataSUS dataset page")
        out = []
        for i, u in enumerate(urls):
            ctx.check_cancel()
            out.append(_download(ctx, src, u, u.rsplit("/", 1)[1], "parquet", int(cfg.get("max_file_bytes", 900_000_000)),
                                 0.05 + 0.5 * i / len(urls), 0.05 + 0.5 * (i + 1) / len(urls)))
        return out, {"geographic_level": "case row -> municipality-week", "files": [u for u in urls],
                     "pii_note": "row-level file kept read-only in Bronze; only counts reach Silver"}

    def parse(self, ctx, src, manifest):
        avail = parse_dt(manifest["available_at"])
        paths = [lake.bronze_path(f["path"]) for f in manifest["files"]]
        agg = aggregate_srag(paths, src["config_json"].get("delimiter", ";"))
        ctx.log(f"aggregated {int(agg['observed_cases'].sum()):,} case rows into {agg.height:,} municipality-week cells")
        dim = silver.latest("municipality_dim").select("municipality_id")
        six = pl.DataFrame({"m6": [m[:6] for m in dim["municipality_id"].to_list()], "municipality_id": dim["municipality_id"].to_list()})
        out = (agg.join(six, on="m6", how="left")
               .with_columns(pl.col("observed_cases").cast(pl.Int64), pl.lit("sari_admission").alias("signal_type"),
                             pl.lit(avail).cast(DT).alias("available_at"),
                             (pl.col("week_start").cast(DT) + pl.duration(days=14)).alias("release_approx_at"),
                             pl.lit(self.source_id).alias("source_id"),
                             pl.lit(bool(manifest.get("is_synthetic"))).alias("is_synthetic"))
               .drop("m6"))
        unmapped = out.filter(pl.col("municipality_id").is_null())
        if unmapped.height:
            ctx.log(f"{int(unmapped['observed_cases'].sum()):,} cases with a missing/foreign residence code dropped "
                    f"({unmapped.height} cells)", level="warning")
        out = out.filter(pl.col("municipality_id").is_not_null())
        return {"sari_weekly": silver.conform("sari_weekly", out)}


# ---------------------------------------------------------------- Zenodo Brazil Dengue Multimodal
class ZenodoMultimodal(Connector):
    source_id, tables = "brazil_dengue_multimodal", ("environment_weekly", "municipality_profile")
    WEEKLY = {"NDVI_mean": "ndvi_mean", "EVI_mean": "evi_mean", "LST_Day_mean": "lst_day_c", "LST_Night_mean": "lst_night_c",
              "nino34": "nino34", "soi": "soi"}
    STATIC = {"area_km2": "area_km2", "population_density": "population_density", "water_supply_pct": "water_supply_pct",
              "sewage_pct": "sewage_pct", "garbage_collection_pct": "garbage_collection_pct", "pct_builtup": "pct_builtup",
              "pct_tree_cover": "pct_tree_cover", "elev_mean": "elev_mean", "dominant_landcover": "dominant_landcover"}

    def _files(self, src) -> tuple[dict[str, dict], dict]:
        body, _ = lake.fetch(src["config_json"]["record_api"], approved=_approved(src), expect="json")
        rec = loads(body)
        return {f["key"]: {"url": f["links"]["self"], "size": f["size"], "checksum": f.get("checksum")} for f in rec["files"]}, rec

    def probe(self, src):
        files, rec = self._files(src)
        return {"ok": bool(files), "title": rec["metadata"]["title"], "license": (rec["metadata"].get("license") or {}).get("id"),
                "files": {k: v["size"] for k, v in files.items()}}

    def collect(self, ctx, src, params):
        cfg = src["config_json"]
        files, rec = self._files(src)
        key = params.get("file", cfg.get("file", "dataset_multimodal_v8.parquet"))
        if key not in files:
            raise RuntimeError(f"{key} not in Zenodo record (have {sorted(files)})")
        f = files[key]
        md5 = f["checksum"][4:] if (f.get("checksum") or "").startswith("md5:") else None
        cached = _cache_hit(md5, f["size"])
        if cached:
            ctx.log(f"reusing a verified earlier download of {key} (md5 {md5[:10]}…)")
            name, path = key, cached
        else:
            name, path = _download(ctx, src, f["url"], key, "parquet", int(cfg.get("max_file_bytes", 900_000_000)), 0.05, 0.55)
            if md5:
                if _md5(path) != md5:
                    path.unlink(missing_ok=True)
                    raise RuntimeError("Zenodo file checksum mismatch (download corrupted)")
                ctx.log("Zenodo md5 checksum verified")
        return [(name, path)], {"geographic_level": "municipality-week", "zenodo_record": rec["id"],
                                "dataset_license": (rec["metadata"].get("license") or {}).get("id"), "doi": rec.get("doi")}

    def parse(self, ctx, src, manifest):
        avail = parse_dt(manifest["available_at"])
        path = lake.bronze_path(manifest["files"][0]["path"])
        lf = pl.scan_parquet(path)
        cols = set(lf.collect_schema().names())
        wk_cols = [c for c in self.WEEKLY if c in cols]
        st_cols = [c for c in self.STATIC if c in cols]
        weekly = (lf.select(pl.col("codigo_ibge").cast(pl.Utf8).str.zfill(7).alias("municipality_id"),
                            pl.col("data_iniSE").cast(pl.Date).alias("week_start"), *[pl.col(c).cast(pl.Float64).alias(self.WEEKLY[c]) for c in wk_cols])
                  .unique(["municipality_id", "week_start"], keep="last").collect(engine="streaming"))
        # remote-sensing composites (MODIS 16-day) are released after the period; document a conservative lag
        weekly = weekly.with_columns(pl.lit(avail).cast(DT).alias("available_at"),
                                     (pl.col("week_start").cast(DT) + pl.duration(days=7 + int(src["config_json"].get("release_lag_days", 21)))).alias("release_approx_at"),
                                     pl.lit(self.source_id).alias("source_id"))
        static = (lf.select(pl.col("codigo_ibge").cast(pl.Utf8).str.zfill(7).alias("municipality_id"), pl.col("data_iniSE"),
                            *[pl.col(c) for c in st_cols])
                  .sort("data_iniSE").group_by("municipality_id").agg([pl.col(c).drop_nulls().last().alias(self.STATIC[c]) for c in st_cols])
                  .collect(engine="streaming"))
        static = static.with_columns(pl.lit(avail).cast(DT).alias("available_at"), pl.lit(self.source_id).alias("source_id"))
        ctx.log(f"environment_weekly {weekly.height:,} rows, municipality_profile {static.height:,} rows")
        return {"environment_weekly": silver.conform("environment_weekly", weekly),
                "municipality_profile": silver.conform("municipality_profile", static)}


# ---------------------------------------------------------------- WorldPop raster -> IBGE polygons
def _scanline_spans(polys: list, x0: float, y0: float, res: float, nrows: int, ncols: int):
    """Even-odd scanline fill of a (multi)polygon on the raster grid; yields (row, col_start, col_end) spans of cells whose
    centre lies inside. Exact for the cell-centre rule, O(edges × rows crossed)."""
    rows_l, xs_l = [], []
    for poly in polys:
        for ring in poly:
            a = np.asarray(ring, dtype=np.float64)
            if len(a) < 3:
                continue
            p, q = a[:-1], a[1:]
            ymin, ymax = np.minimum(p[:, 1], q[:, 1]), np.maximum(p[:, 1], q[:, 1])
            # row r has centre y = y0 - (r + 0.5) * res
            r0 = np.ceil((y0 - ymax) / res - 0.5).astype(int)
            r1 = np.floor((y0 - ymin) / res - 0.5).astype(int)
            for e in np.nonzero(r1 >= r0)[0]:
                rr = np.arange(max(r0[e], 0), min(r1[e], nrows - 1) + 1)
                if not len(rr):
                    continue
                yc = y0 - (rr + 0.5) * res
                (xa, ya), (xb, yb) = p[e], q[e]
                if ya == yb:
                    continue
                keep = (yc >= min(ya, yb)) & (yc < max(ya, yb))  # half-open: vertices counted once
                rr, yc = rr[keep], yc[keep]
                rows_l.append(rr)
                xs_l.append(xa + (yc - ya) * (xb - xa) / (yb - ya))
    if not rows_l:
        return np.empty(0, int), np.empty(0, int), np.empty(0, int)
    rows, xs = np.concatenate(rows_l), np.concatenate(xs_l)
    o = np.lexsort((xs, rows))
    rows, xs = rows[o], xs[o]
    # pair consecutive crossings per row (even-odd)
    out_r, out_a, out_b = [], [], []
    starts = np.r_[0, np.nonzero(np.diff(rows))[0] + 1, len(rows)]
    for s, e in zip(starts[:-1], starts[1:]):
        n = (e - s) // 2 * 2
        if n == 0:
            continue
        xa, xb = xs[s:s + n:2], xs[s + 1:s + n:2]
        ca = np.clip(np.ceil((xa - x0) / res - 0.5).astype(int), 0, ncols)
        cb = np.clip(np.floor((xb - x0) / res - 0.5).astype(int) + 1, 0, ncols)
        ok = cb > ca
        out_r.append(np.full(ok.sum(), rows[s]))
        out_a.append(ca[ok])
        out_b.append(cb[ok])
    if not out_r:
        return np.empty(0, int), np.empty(0, int), np.empty(0, int)
    return np.concatenate(out_r), np.concatenate(out_a), np.concatenate(out_b)


def zonal_sums(raster: np.ndarray, x0: float, y0: float, res: float, features: list[dict]) -> list[dict]:
    nrows, ncols = raster.shape
    pos = np.where(raster > 0, raster, 0).astype(np.float64)
    cs = np.zeros((nrows, ncols + 1))
    np.cumsum(pos, axis=1, out=cs[:, 1:])
    cnt = np.zeros((nrows, ncols + 1))
    np.cumsum(pos > 0, axis=1, out=cnt[:, 1:])
    out = []
    for f in features:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        r, a, b = _scanline_spans(polys, x0, y0, res, nrows, ncols)
        tot = float((cs[r, b] - cs[r, a]).sum()) if len(r) else 0.0
        cells = float((cnt[r, b] - cnt[r, a]).sum()) if len(r) else 0.0
        out.append({"municipality_id": str(f["properties"]["id"]).zfill(7), "worldpop_population": tot, "populated_cells": int(cells)})
    return out


class WorldPop(Connector):
    source_id, tables = "worldpop", ("population_grid_annual",)

    def _url(self, src, year: int) -> str:
        return src["config_json"]["url_template"].format(year=year)

    def probe(self, src):
        y = min(utcnow().year, int(src["config_json"].get("max_year", 2030)))
        body, m = lake.fetch(self._url(src, y - 1), approved=_approved(src), max_bytes=40_000_000, expect="tiff", timeout=120)
        return {"ok": True, "year": y - 1, "bytes": m["bytes"], "resolution": "30 arc-seconds (~1 km), constrained"}

    def collect(self, ctx, src, params):
        y = int(params.get("year", src["config_json"].get("year", utcnow().year - 1)))
        name, path = _download(ctx, src, self._url(src, y), f"bra_pop_{y}_1km.tif", "tiff", 200_000_000, 0.05, 0.5)
        return [(name, path)], {"geographic_level": "raster 1 km -> municipality", "reference_year": y,
                                "release": src["config_json"].get("release", "R2025A")}

    def parse(self, ctx, src, manifest):
        import tifffile
        avail = parse_dt(manifest["available_at"])
        year = int(manifest.get("reference_year") or utcnow().year - 1)
        path = lake.bronze_path(manifest["files"][0]["path"])
        with tifffile.TiffFile(path) as t:
            pg = t.pages[0]
            sx, sy, _ = pg.tags["ModelPixelScaleTag"].value
            tie = pg.tags["ModelTiepointTag"].value
            raster = pg.asarray()
        if abs(sx - sy) > 1e-9:
            raise ValueError("non-square pixels are not supported")
        x0, y0 = tie[3] - tie[0] * sx, tie[4] + tie[1] * sy
        geo = get_settings().gold_dir / "map" / "municipalities.geojson"
        if not geo.exists():
            raise RuntimeError("IBGE geometry not published yet — ingest ibge_geometry first")
        feats = loads(geo.read_bytes())["features"]
        ctx.log(f"raster {raster.shape[1]}×{raster.shape[0]} at {sx:.6f}°, {len(feats):,} IBGE polygons")
        rows = zonal_sums(raster, x0, y0, sx, feats)
        df = pl.DataFrame(rows).with_columns(
            pl.lit(year).cast(pl.Int32).alias("reference_year"),
            (pl.col("populated_cells") * (sx * 111.32) ** 2).alias("populated_area_km2_approx"),
            pl.lit(avail).cast(DT).alias("available_at"), pl.lit(self.source_id).alias("source_id"))
        tot = float(np.where(raster > 0, raster, 0).sum())
        ctx.log(f"assigned {df['worldpop_population'].sum() / tot:.2%} of the raster population ({tot / 1e6:.1f} M) to IBGE polygons")
        return {"population_grid_annual": silver.conform("population_grid_annual", df)}


EXTRA = (IBGEPopulation(), IBGECempre(), IBGECommuting(), SRAGDownload(), ZenodoMultimodal(), WorldPop())
