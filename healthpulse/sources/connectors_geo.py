"""Geography-keyed connectors used by the polygon crosswalk (healthpulse/geo).

  gadm_boundaries  GADM 4.1 polygons (BRA level 2, VNM levels 1-2). The polygons that Meta's datasets are keyed on.
  meta_mobility    Meta "Movement Distribution" on HDX (CC BY 4.0): share of device pings by distance from the home location
                   (0 km, (0,10) km, [10,100) km, 100+ km) per GADM polygon and day.

Honesty notes (also shown in the product):
  * Movement Distribution is a DISTANCE-FROM-HOME indicator. It has no destination, so it is not an origin-destination flow.
  * HDX keeps only ~90 days. Each ingest therefore adds days to a vintaged Silver table; the history that exists is the history
    we have collected, nothing is back-filled.
  * The upstream files are global (every country, ~180 MB per fortnight). Only BRA and VNM rows are kept in Bronze; the filter
    and the SHA-256 of every upstream file are recorded in the manifest.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import polars as pl

from ..config import get_settings
from ..core import lake
from ..core.util import loads, parse_dt, sha256_file, utcnow
from ..geo.meta import META_COLUMNS, pivot_bands, sanitize
from . import silver
from .connectors import Connector, _approved

DT = pl.Datetime("us", "UTC")
UA = "python-httpx (HealthPulse public-health pilot)"  # HDX answers unknown agents with a 202 bot challenge


class GADMBoundaries(Connector):
    source_id, tables, expect = "gadm_boundaries", ("gadm_unit",), "zip"

    def _jobs(self, src, params) -> list[tuple[str, str, str]]:
        """(version, code, url) for every configured layer. Meta's Brazilian ids belong to GADM 3.6, its Vietnamese ids to 4.1."""
        cfg = src["config_json"]
        out = []
        for v, tmpl in cfg["url_templates"].items():
            for code in (params.get("layers") or cfg["layers"]).get(v, []) if isinstance(cfg["layers"], dict) else []:
                out.append((v, code, tmpl.format(code=code)))
        return out

    def probe(self, src):
        v, code, url = self._jobs(src, {})[-1]
        _, meta = lake.fetch(url, approved=_approved(src), expect="zip", max_bytes=30_000_000, timeout=120, user_agent=UA)
        return {"ok": True, "layer": f"GADM {v} {code}", "bytes": meta["bytes"]}

    def collect(self, ctx, src, params):
        pay = []
        for v, code, url in self._jobs(src, params):
            ctx.log(f"downloading GADM {v} {code}")
            body, _ = lake.fetch(url, approved=_approved(src), expect="zip", max_bytes=60_000_000, timeout=240, user_agent=UA)
            with zipfile.ZipFile(io.BytesIO(body)) as zf:
                name = next(n for n in zf.namelist() if n.endswith(".json"))
                pay.append((Path(name).name, zf.read(name)))
        return pay, {"geographic_level": "GADM admin polygons", "gadm_versions": sorted({v for v, _, _ in self._jobs(src, params)})}

    def parse(self, ctx, src, manifest):
        rows = []
        for name, body in self._payloads(manifest):
            level = int(re.search(r"_(\d)\.json$", name).group(1))
            version = {"gadm41": "4.1", "gadm40": "4.0", "gadm36": "3.6"}[name.split("_")[0]]
            for f in loads(body)["features"]:
                pr, g = f["properties"], f["geometry"]
                polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
                xs = [c[0] for p in polys for r in p for c in r]
                ys = [c[1] for p in polys for r in p for c in r]
                rows.append({"gadm_version": version, "gadm_id": pr[f"GID_{level}"], "country": pr["GID_0"], "level": level,
                             "name_in_source": pr.get(f"NAME_{level}"), "parent_gid": pr.get(f"GID_{level - 1}") if level > 1 else None,
                             "code_in_source": next((pr[k] for k in (f"CC_{level}", "ISO_1", f"HASC_{level}") if pr.get(k) not in (None, "NA")), None),
                             "vertices": sum(len(r) for p in polys for r in p), "min_lon": min(xs), "min_lat": min(ys),
                             "max_lon": max(xs), "max_lat": max(ys)})
        df = pl.DataFrame(rows).with_columns(pl.lit(parse_dt(manifest["available_at"])).cast(DT).alias("available_at"),
                                             pl.lit(self.source_id).alias("source_id"))
        ctx.log(f"{df.height:,} GADM polygons in {df['level'].n_unique()} layer(s)")
        return {"gadm_unit": silver.conform("gadm_unit", df)}


class MetaMobility(Connector):
    source_id, tables, expect = "meta_mobility", ("mobility_polygon",), "csv"

    def _resources(self, src, n: int) -> list[dict]:
        body, _ = lake.fetch(src["config_json"]["package_api"], approved=_approved(src), expect="json", max_bytes=5_000_000,
                             user_agent=UA)
        res = [r for r in loads(body)["result"]["resources"] if (r.get("format") or "").upper() == "CSV"]
        # newest first by the platform's own timestamp; the file name formats are not consistent
        res.sort(key=lambda r: (r.get("last_modified") or r.get("created") or "", r.get("size") or 0), reverse=True)
        return res[:n]

    def probe(self, src):
        res = self._resources(src, int(src["config_json"].get("files", 2)))
        return {"ok": bool(res), "latest_resources": [{"name": r["name"], "bytes": r.get("size"), "modified": r.get("last_modified")} for r in res],
                "note": "global files, filtered to BRA and VNM on ingest"}

    def collect(self, ctx, src, params):
        cfg = src["config_json"]
        countries = list(params.get("countries") or cfg["countries"])
        res = self._resources(src, int(params.get("files", cfg.get("files", 2))))
        if not res:
            raise RuntimeError("HDX lists no CSV resources for movement-distribution")
        tmp = get_settings().uploads_dir / "downloads"
        tmp.mkdir(parents=True, exist_ok=True)
        payloads, upstream = [], []
        for i, r in enumerate(res):
            url = r.get("download_url") or r["url"]
            dst = tmp / f"meta_md_{i}.csv"
            ctx.log(f"downloading {r['name']} ({(r.get('size') or 0) / 1e6:.0f} MB) with parallel ranges")
            info = lake.fetch_ranged_to_file(url, dst, approved=_approved(src), expect="csv", workers=int(cfg.get("workers", 4)),
                                             max_bytes=int(cfg.get("max_file_bytes", 600_000_000)), user_agent=UA, timeout=180)
            up_sha = sha256_file(dst)
            lf = pl.scan_csv(dst, infer_schema=False)
            if lf.collect_schema().names() != META_COLUMNS:
                dst.unlink(missing_ok=True)
                raise RuntimeError(f"Movement Distribution schema changed: {lf.collect_schema().names()}")
            kept = lf.filter(pl.col("country").is_in(countries)).collect(engine="streaming")
            dst.unlink(missing_ok=True)
            buf = io.BytesIO()
            kept.write_csv(buf)
            ds = kept["ds"]
            upstream.append({"resource_id": r.get("id"), "name": r["name"], "url": info["final_url"].split("?")[0], "bytes": info["bytes"],
                             "sha256": up_sha, "rows_kept": kept.height, "ds_min": ds.min(), "ds_max": ds.max()})
            ctx.log(f"kept {kept.height:,} rows for {','.join(countries)} ({ds.min()}..{ds.max()}); upstream sha256 {up_sha[:12]}")
            payloads.append((f"movement_distribution_{'_'.join(countries)}_{i}.csv", buf.getvalue()))
        return payloads, {"geographic_level": "GADM polygon-day", "filter": f"country in {countries}", "upstream": upstream}

    def parse(self, ctx, src, manifest):
        frames = [pl.read_csv(io.BytesIO(b), infer_schema=False) for _, b in self._payloads(manifest)]
        wide, noise = sanitize(pivot_bands(pl.concat(frames)))
        ctx.log(f"Meta adds privacy noise: {noise['noise_nulled']:,} of {noise['polygon_days']:,} polygon-days ({noise['noise_nulled_share']:.1%}, "
                f"{noise['polygons_with_noise_nulled']:,} polygons) are noise-dominated ({noise['rule']}) and are set to null, not clipped")
        wide = wide.with_columns(pl.lit(parse_dt(manifest["available_at"])).cast(DT).alias("available_at"), pl.lit(self.source_id).alias("source_id"))
        ctx.log(f"{wide.height:,} polygon-days, {wide['gadm_id'].n_unique():,} polygons, {wide['ds'].min()}..{wide['ds'].max()}")
        return {"mobility_polygon": silver.conform("mobility_polygon", wide.sort("country", "gadm_id", "ds"))}


GEO = (GADMBoundaries(), MetaMobility())
