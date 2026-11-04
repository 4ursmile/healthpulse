"""Geometry for live response: point → province, zone polygons, km-accurate buffers and population-inside estimates.

Work is done in a local equirectangular projection (km) centred on the shape, which is accurate to well under 1 % at
the scale of a quarantine zone (a few to a few tens of km). Population inside a zone is an ESTIMATE: province
population density (WorldPop total / area) × the zone's area in each province — real density is uneven (towns vs
fields), so the UI always labels it as an estimate.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache

import numpy as np
from shapely import STRtree
from shapely.geometry import MultiPoint, Point, Polygon, mapping, shape
import shapely

KM_LAT = 110.574


def km_per_deg_lon(lat: float) -> float:
    return 111.320 * math.cos(math.radians(lat))


def haversine(lat1, lon1, lat2, lon2):
    """km; accepts scalars or numpy arrays."""
    la1, lo1, la2, lo2 = map(np.radians, (lat1, lon1, lat2, lon2))
    h = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(np.minimum(1.0, h)))


def transform(f, g):
    """Apply a vectorised (x, y) -> (x', y') map to a geometry."""
    return shapely.transform(g, lambda c: np.column_stack(f(c[:, 0], c[:, 1])))


def to_km(lat0: float, lon0: float):
    kx = km_per_deg_lon(lat0)
    return lambda x, y, z=None: ((np.asarray(x) - lon0) * kx, (np.asarray(y) - lat0) * KM_LAT)


def to_deg(lat0: float, lon0: float):
    kx = km_per_deg_lon(lat0)
    return lambda x, y, z=None: (np.asarray(x) / kx + lon0, np.asarray(y) / KM_LAT + lat0)


@lru_cache(maxsize=1)
def provinces() -> dict:
    """Province geometry (simplified display geometry), attributes and a spatial index."""
    from ..vietnam import model
    geo = json.loads(model.geometry_bytes())
    attrs = {p["province_id"]: p for p in model.provinces()}
    ids, geoms = [], []
    for f in geo["features"]:
        ids.append(f["properties"]["id"])
        geoms.append(shape(f["geometry"]).buffer(0))
    return {"ids": ids, "geoms": geoms, "tree": STRtree(geoms), "attrs": attrs}


def reset_cache() -> None:
    provinces.cache_clear()


def province_at(lat: float, lon: float) -> str | None:
    P = provinces()
    pt = Point(lon, lat)
    for i in P["tree"].query(pt):
        if P["geoms"][i].covers(pt):
            return P["ids"][i]
    # just offshore / on a simplified coastline: nearest province within ~15 km
    i = P["tree"].nearest(pt)
    if i is not None and P["geoms"][i].distance(pt) < 0.15:
        return P["ids"][i]
    return None


def centroid(pid: str) -> tuple[float, float]:
    a = provinces()["attrs"][pid]
    return a["lat"], a["lon"]


def circle(lat: float, lon: float, radius_km: float, n: int = 64) -> dict:
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    xs = lon + radius_km * np.cos(t) / km_per_deg_lon(lat)
    ys = lat + radius_km * np.sin(t) / KM_LAT
    ring = [[round(float(x), 6), round(float(y), 6)] for x, y in zip(xs, ys)]
    return {"type": "Polygon", "coordinates": [ring + [ring[0]]]}


def hull_buffer(lats, lons, buffer_km: float) -> dict:
    """Convex hull of points, buffered by `buffer_km` (km-true), as GeoJSON in degrees."""
    lat0, lon0 = float(np.mean(lats)), float(np.mean(lons))
    f, b = to_km(lat0, lon0), to_deg(lat0, lon0)
    x, y = f(np.asarray(lons, float), np.asarray(lats, float))
    g = MultiPoint(list(zip(x, y))).convex_hull.buffer(max(0.05, buffer_km), quad_segs=12)
    return _round(mapping(transform(b, g)))


def buffer_geom(geom: dict, km: float) -> dict:
    g = shape(geom)
    c = g.centroid
    gk = transform(to_km(c.y, c.x), g).buffer(km, quad_segs=12)
    return _round(mapping(transform(to_deg(c.y, c.x), gk)))


def _round(m: dict) -> dict:
    def r(c):
        return [round(float(c[0]), 6), round(float(c[1]), 6)] if isinstance(c[0], (int, float)) else [r(x) for x in c]
    return {"type": m["type"], "coordinates": r(json.loads(json.dumps(m["coordinates"])))}


def valid_polygon(geom: dict) -> dict:
    """Validate a user-supplied zone geometry (Polygon/MultiPolygon in lon/lat, inside a Viet Nam-ish bbox)."""
    if not isinstance(geom, dict) or geom.get("type") not in ("Polygon", "MultiPolygon"):
        raise ValueError("zone geometry must be a GeoJSON Polygon or MultiPolygon")
    g = shape(geom)
    if g.is_empty:
        raise ValueError("zone geometry is empty")
    if not g.is_valid:
        g = g.buffer(0)
    minx, miny, maxx, maxy = g.bounds
    if minx < 100 or maxx > 120 or miny < 5 or maxy > 26:
        raise ValueError("zone lies outside the Viet Nam map")
    return _round(mapping(g))


def area_km2(geom: dict) -> float:
    g = shape(geom)
    c = g.centroid
    return float(transform(to_km(c.y, c.x), g).area)


def zone_population(geom: dict) -> dict:
    """ESTIMATE of residents inside a zone: Σ province density × intersected area. Also returns the provinces touched."""
    P = provinces()
    g = shape(geom)
    c = g.centroid
    f = to_km(c.y, c.x)
    gk = transform(f, g)
    total, parts = 0.0, []
    for i in P["tree"].query(g):
        pid = P["ids"][i]
        inter = P["geoms"][i].intersection(g)
        if inter.is_empty:
            continue
        a = float(transform(f, inter).area)
        attr = P["attrs"].get(pid, {})
        dens = (attr.get("population") or 0) / max(attr.get("area_km2") or 1, 1)
        total += a * dens
        parts.append({"province_id": pid, "name": attr.get("name", pid), "area_km2": round(a, 2), "people_est": round(a * dens)})
    parts.sort(key=lambda r: -r["people_est"])
    return {"area_km2": round(float(gk.area), 2), "people_est": round(total), "provinces": parts}


def contains(geom: dict, lats, lons) -> np.ndarray:
    from shapely import contains_xy
    g = shape(geom)
    return np.asarray(contains_xy(g, np.asarray(lons, float), np.asarray(lats, float)), bool)
