"""Polygon layers read from Bronze (the immutable snapshots), never from the simplified map copies.

IBGE municipalities   data/raw/ibge_geometry   properties.id = IBGE 7-digit code (tbrugz/geodata-br mirror of the IBGE mesh)
OCHA COD-AB provinces data/raw/vn_cod_ab       properties.adm1_pcode = VNxx (34 provinces valid from 1 July 2025)
GADM 4.1              data/raw/gadm_boundaries properties.GID_<level>
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..config import get_settings
from ..core.util import sha256_file
from . import crosswalk as cw


def latest_bronze(source_id: str) -> dict | None:
    root = get_settings().raw_dir / source_id
    if not root.exists():
        return None
    snaps = sorted(p for p in root.iterdir() if (p / "manifest.json").exists())
    return json.loads((snaps[-1] / "manifest.json").read_text()) if snaps else None


def _file(man: dict, name: str) -> Path:
    for f in man["files"]:
        if f["name"] == name:
            return get_settings().data_dir / f["path"]
    raise FileNotFoundError(f"{name} not in {man['source_id']} snapshot {man['bronze_dir']}")


def _load(path: Path, id_of):
    fc = json.loads(path.read_text())
    ids, geoms = cw.to_geoms(fc["features"], id_of)
    return ids, geoms, fc["features"], sha256_file(path)


def ibge_municipalities() -> dict:
    man = latest_bronze("ibge_geometry")
    if not man:
        raise FileNotFoundError("ibge_geometry has not been ingested (hp ingest ibge_geometry)")
    ids, geoms, feats, sha = _load(get_settings().data_dir / man["files"][0]["path"], lambda f: str(f["properties"]["id"]).zfill(7))
    return {"ids": ids, "geoms": geoms, "sha256": sha, "bronze": man["bronze_dir"], "layer": "IBGE municipalities (geodata-br mirror of the IBGE mesh)"}


def cod_ab_provinces() -> dict:
    man = latest_bronze("vn_cod_ab")
    if not man:
        raise FileNotFoundError("vn_cod_ab has not been fetched (hp vn-fetch)")
    ids, geoms, feats, sha = _load(_file(man, "vnm_admin1.geojson"), lambda f: f["properties"]["adm1_pcode"])
    return {"ids": ids, "geoms": geoms, "sha256": sha, "bronze": man["bronze_dir"], "layer": "OCHA COD-AB v02 admin-1 (34 provinces)"}


_PREFIX = {"4.1": "gadm41", "4.0": "gadm40", "3.6": "gadm36"}


def gadm_versions(country: str, level: int) -> list[str]:
    man = latest_bronze("gadm_boundaries")
    names = {f["name"] for f in man["files"]} if man else set()
    return [v for v, pre in _PREFIX.items() if f"{pre}_{country}_{level}.json" in names]


def gadm(country: str, level: int, version: str = "4.1") -> dict:
    man = latest_bronze("gadm_boundaries")
    if not man:
        raise FileNotFoundError("gadm_boundaries has not been ingested (hp ingest gadm_boundaries)")
    ids, geoms, feats, sha = _load(_file(man, f"{_PREFIX[version]}_{country}_{level}.json"), lambda f: f["properties"][f"GID_{level}"])
    props = {f["properties"][f"GID_{level}"]: f["properties"] for f in feats}
    return {"ids": ids, "geoms": geoms, "props": props, "sha256": sha, "bronze": man["bronze_dir"],
            "layer": f"GADM {version} {country} level {level}", "level": level, "country": country, "version": version}


def area_km2(geoms: np.ndarray, ref_geoms: np.ndarray | None = None) -> np.ndarray:
    """Equal-area polygon areas (km²) in the same projection the crosswalk uses (centred on `ref_geoms` or the layer itself)."""
    import shapely
    lon0, lat0 = cw._centre(ref_geoms if ref_geoms is not None else geoms)
    return shapely.area(shapely.transform(geoms, cw.laea(lon0, lat0)))
