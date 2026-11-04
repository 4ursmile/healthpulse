"""Generic polygon crosswalk between two administrative layers (source -> target).

Why: some public datasets (Meta Movement Distribution) are keyed by GADM polygon ids while this product keys everything by
IBGE / OCHA P-codes. Name matching is refused, so the join is made from geometry alone.

Method (deterministic, no tuning to any reference):
  1. Both layers are repaired (`make_valid`) and projected to a Lambert azimuthal EQUAL-AREA plane centred on the union of
     the two layers (spherical, authalic radius). Areas are therefore exact up to the vertex spacing; ratios of areas are
     what is used, so the sphere-vs-ellipsoid scale error cancels.
  2. Exact pairwise intersection areas through an STRtree.
  3. Each SOURCE polygon is decided on its own, with these thresholds (`Params`, stored with the result):
       * intersections smaller than `sliver` of the source are dropped (and reported);
       * `covered` < `min_cover`                      -> REFUSED   (the target layer does not cover this source);
       * one target holds >= `contain` of the covered part -> ACCEPTED, weight 1 to that target;
       * otherwise, >= 2 targets that each hold >= `split_min` and together >= `contain` -> ACCEPTED as a split,
         weights = their share of the covered part (sum to 1);
       * otherwise                                    -> AMBIGUOUS (many small fragments); weight 0, never guessed.
  4. Relation labels (for review): ONE_TO_ONE (mutual best match, IoU >= `iou_one_to_one`), CONTAINED (one source inside one
     target, IoU lower), MANY_TO_ONE (merge), ONE_TO_MANY (split).
  5. `aggregate()` moves values from sources to targets with area (or caller-supplied mass) weights and returns null,
     never zero, where the targets are not covered well enough.

Names are never used. `audit()` can compare the result with an independent code-based reference, which is reported as an
audit statistic only and never changes a weight.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

import numpy as np
import polars as pl
import shapely
from shapely import STRtree
from shapely.geometry import shape

METHOD_VERSION = "polygon-crosswalk/1.0"
R_KM = 6371.0072  # authalic radius of the Earth, km

ACCEPTED, AMBIGUOUS, REFUSED = "ACCEPTED", "AMBIGUOUS", "REFUSED"


@dataclass(frozen=True)
class Params:
    min_cover: float = 0.50       # share of the source polygon that must lie inside the target layer
    contain: float = 0.90         # share of the covered part inside ONE target (or jointly, for a split)
    split_min: float = 0.10       # a target takes part in a split only with at least this share
    sliver: float = 0.02          # intersections below this share of the source are slivers (dropped, reported)
    iou_one_to_one: float = 0.80  # IoU needed to call a mutual-best pair ONE_TO_ONE
    min_target_cover: float = 0.50  # aggregate(): a target needs this much of its area covered by accepted sources


SCHEMA = {
    "source_id": pl.Utf8, "target_id": pl.Utf8, "inter_km2": pl.Float64, "share_of_source": pl.Float64,
    "share_of_target": pl.Float64, "iou": pl.Float64, "weight": pl.Float64, "relation": pl.Utf8, "status": pl.Utf8,
    "reason": pl.Utf8,
}


# ---------------------------------------------------------------- geometry helpers
def _centre(geoms: np.ndarray) -> tuple[float, float]:
    xmin, ymin, xmax, ymax = shapely.total_bounds(geoms)
    return (xmin + xmax) / 2.0, (ymin + ymax) / 2.0


def laea(lon0: float, lat0: float):
    """Spherical Lambert azimuthal equal-area projection (km). Exact for areas on the sphere."""
    l0, p0 = np.radians(lon0), np.radians(lat0)

    def f(c: np.ndarray) -> np.ndarray:
        lam, phi = np.radians(c[:, 0]), np.radians(c[:, 1])
        cosc = np.sin(p0) * np.sin(phi) + np.cos(p0) * np.cos(phi) * np.cos(lam - l0)
        k = np.sqrt(2.0 / np.maximum(1.0 + cosc, 1e-12))
        x = R_KM * k * np.cos(phi) * np.sin(lam - l0)
        y = R_KM * k * (np.cos(p0) * np.sin(phi) - np.sin(p0) * np.cos(phi) * np.cos(lam - l0))
        return np.column_stack([x, y])

    return f


def to_geoms(features: Iterable[Mapping[str, Any]], id_of) -> tuple[list[str], np.ndarray]:
    """GeoJSON features -> (ids, repaired shapely geometries). `id_of(feature)` must return a code, never a name."""
    ids, geoms = [], []
    for f in features:
        g = shape(f["geometry"])
        if not g.is_valid:
            g = shapely.make_valid(g)
        ids.append(str(id_of(f)))
        geoms.append(g)
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate polygon ids in a layer")
    return ids, np.array(geoms, dtype=object)


def layer_hash(ids: list[str], geoms: np.ndarray) -> str:
    h = hashlib.sha256()
    for i in np.argsort(np.array(ids)):
        h.update(ids[i].encode())
        h.update(shapely.to_wkb(shapely.set_precision(geoms[i], 1e-6), output_dimension=2))
    return h.hexdigest()


# ---------------------------------------------------------------- the crosswalk
def build(src_ids: list[str], src_geoms: np.ndarray, tgt_ids: list[str], tgt_geoms: np.ndarray,
          params: Params | None = None) -> tuple[pl.DataFrame, dict]:
    """Returns (pairs, report). `pairs` has one row per (source, accepted-or-candidate target) plus one row per source
    that has no candidate; see SCHEMA. `report` is the JSON-able quality report."""
    p = params or Params()
    allg = np.concatenate([src_geoms, tgt_geoms])
    lon0, lat0 = _centre(allg)
    proj = laea(lon0, lat0)
    sg = shapely.transform(src_geoms, proj)
    tg = shapely.transform(tgt_geoms, proj)
    sg = np.array([g if g.is_valid else shapely.make_valid(g) for g in sg], dtype=object)
    tg = np.array([g if g.is_valid else shapely.make_valid(g) for g in tg], dtype=object)
    sarea, tarea = shapely.area(sg), shapely.area(tg)

    tree = STRtree(tg)
    si, ti = tree.query(sg, predicate="intersects")
    try:
        inter = shapely.area(shapely.intersection(sg[si], tg[ti]))
    except shapely.errors.GEOSException:  # rare topology failure: fall back to a per-pair repair
        inter = np.array([shapely.area(shapely.intersection(sg[a].buffer(0), tg[b].buffer(0))) for a, b in zip(si, ti)])
    keep = inter > 0
    si, ti, inter = si[keep], ti[keep], inter[keep]

    by_src: dict[int, list[tuple[int, float]]] = {}
    for a, b, x in zip(si.tolist(), ti.tolist(), inter.tolist()):
        by_src.setdefault(a, []).append((b, x))

    rows: list[dict] = []
    dropped_sliver = 0.0
    decided: dict[int, list[tuple[int, float, float, float]]] = {}  # s -> [(t, weight, inter, share_of_source)]
    status: dict[int, tuple[str, str]] = {}
    for s in range(len(src_ids)):
        cand = sorted(by_src.get(s, []), key=lambda z: (-z[1], tgt_ids[z[0]]))  # deterministic ties
        a_s = sarea[s]
        kept = [(t, x) for t, x in cand if x / a_s >= p.sliver]
        dropped_sliver += sum(x for t, x in cand if x / a_s < p.sliver) / a_s
        covered_area = sum(x for _, x in kept)
        covered = min(1.0, covered_area / a_s) if a_s > 0 else 0.0
        if not kept or covered < p.min_cover:
            status[s] = (REFUSED, f"only {covered:.0%} of the polygon lies inside the target layer (< {p.min_cover:.0%})")
            continue
        norm = [(t, x, x / covered_area) for t, x in kept]
        t0, x0, n0 = norm[0]
        if n0 >= p.contain:
            decided[s] = [(t0, 1.0, x0, x0 / a_s)]
            status[s] = (ACCEPTED, "contained" + ("" if covered >= 0.9 else f"; only {covered:.0%} of it is covered by the target layer"))
            continue
        parts = [z for z in norm if z[2] >= p.split_min]
        if len(parts) >= 2 and sum(z[2] for z in parts) >= p.contain:
            tot = sum(z[2] for z in parts)
            decided[s] = [(t, n / tot, x, x / a_s) for t, x, n in parts]
            status[s] = (ACCEPTED, f"split across {len(parts)} targets")
            continue
        status[s] = (AMBIGUOUS, f"no target holds {p.contain:.0%} and no clean split (largest share {n0:.0%}, {len(norm)} fragments)")

    # target-side view of the accepted pairs
    srcs_of: dict[int, list[tuple[int, float]]] = {}
    for s, lst in decided.items():
        for t, w, x, _ in lst:
            srcs_of.setdefault(t, []).append((s, x / tarea[t]))
    best_src_of = {t: min(((s, x) for s, x in lst), key=lambda z: (-z[1], src_ids[z[0]]))[0] for t, lst in srcs_of.items()}

    def relation(s: int, t: int, n_targets: int, inter_km2: float) -> str:
        if n_targets >= 2:
            return "ONE_TO_MANY"
        big = [z for z in srcs_of.get(t, []) if z[1] >= p.split_min]
        if len(big) >= 2:
            return "MANY_TO_ONE"
        iou = inter_km2 / (sarea[s] + tarea[t] - inter_km2)
        return "ONE_TO_ONE" if best_src_of.get(t) == s and iou >= p.iou_one_to_one else "CONTAINED"

    for s in range(len(src_ids)):
        st, why = status[s]
        if s in decided:
            for t, w, x, sh in decided[s]:
                rows.append({"source_id": src_ids[s], "target_id": tgt_ids[t], "inter_km2": x, "share_of_source": sh,
                             "share_of_target": x / tarea[t], "iou": x / (sarea[s] + tarea[t] - x), "weight": w,
                             "relation": relation(s, t, len(decided[s]), x), "status": st, "reason": why})
        else:
            best = sorted(by_src.get(s, []), key=lambda z: (-z[1], tgt_ids[z[0]]))[:1]
            if best:
                t, x = best[0]
                rows.append({"source_id": src_ids[s], "target_id": tgt_ids[t], "inter_km2": x, "share_of_source": x / sarea[s],
                             "share_of_target": x / tarea[t], "iou": x / (sarea[s] + tarea[t] - x), "weight": 0.0,
                             "relation": "NONE", "status": st, "reason": why + " (largest candidate shown, weight 0)"})
            else:
                rows.append({"source_id": src_ids[s], "target_id": None, "inter_km2": 0.0, "share_of_source": 0.0,
                             "share_of_target": None, "iou": None, "weight": 0.0, "relation": "NONE", "status": st, "reason": why})
    df = pl.DataFrame(rows, schema=SCHEMA)

    # ---- quality report
    acc = df.filter(pl.col("status") == ACCEPTED)
    n_src = len(src_ids)
    src_status = df.group_by("source_id").agg(pl.col("status").first())
    counts = {k: int(v) for k, v in src_status.group_by("status").len().iter_rows()}
    rel_counts = {k: int(v) for k, v in acc.group_by("relation").len().iter_rows()}
    one = acc.filter(pl.col("relation") == "ONE_TO_ONE")
    tcov = np.zeros(len(tgt_ids))
    for t, lst in srcs_of.items():
        tcov[t] = min(1.0, sum(x for _, x in lst))
    # a target is "unmatched" when no accepted source reaches it
    unmatched_t = [tgt_ids[t] for t in range(len(tgt_ids)) if tcov[t] <= 0]
    report = {
        "method": METHOD_VERSION, "params": asdict(p), "crs": f"spherical Lambert azimuthal equal-area, centre ({lon0:.3f}, {lat0:.3f}), R={R_KM} km",
        "n_source": n_src, "n_target": len(tgt_ids),
        "source_status": {k: counts.get(k, 0) for k in (ACCEPTED, AMBIGUOUS, REFUSED)},
        "source_accept_rate": counts.get(ACCEPTED, 0) / n_src if n_src else None,
        "relations": rel_counts,
        "area_matched_share": float((sum(sarea[s] for s in decided)) / sarea.sum()) if n_src else None,
        "target_with_accepted_source_share": float(np.mean(tcov > 0)) if len(tgt_ids) else None,
        "target_cover_median": float(np.median(tcov)) if len(tgt_ids) else None,
        "target_cover_p05": float(np.quantile(tcov, 0.05)) if len(tgt_ids) else None,
        "unmatched_targets": len(unmatched_t), "unmatched_target_ids": unmatched_t[:50],
        "one_to_one_iou": {"n": one.height, "median": float(one["iou"].median()) if one.height else None,
                           "p05": float(one["iou"].quantile(0.05)) if one.height else None},
        "sliver_area_dropped_share": dropped_sliver / n_src if n_src else None,
        "ambiguous_source_ids": df.filter(pl.col("status") == AMBIGUOUS)["source_id"].unique().sort().to_list()[:50],
        "refused_source_ids": df.filter(pl.col("status") == REFUSED)["source_id"].unique().sort().to_list()[:50],
    }
    return df, report


def version_id(report: dict, source_hash: str, target_hash: str) -> str:
    key = json.dumps({"m": report["method"], "p": report["params"], "s": source_hash, "t": target_hash}, sort_keys=True)
    return hashlib.sha256(key.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- using it
def aggregate(xw: pl.DataFrame, values: pl.DataFrame, value_cols: list[str], *, source_mass: Mapping[str, float] | None = None,
              min_target_cover: float = 0.50, min_data_share: float = 0.50) -> pl.DataFrame:
    """Move intensive values (shares, rates) from source polygons to target polygons.

    weight of source s in target t = mass(s) x w(s->t)   (mass = population if given, else the area of s inside t: area-weighted)
    A value column is null when the target is not covered well enough (`target_cover` = share of the target's area reached by
    accepted sources) or when too little of the weight has data (`<col>__data_share`). Missing source values are skipped and
    the remaining weights renormalised, never treated as zero. Splits assume uniform density inside the source polygon."""
    acc = xw.filter((pl.col("status") == ACCEPTED) & (pl.col("weight") > 0)).select("source_id", "target_id", "weight", "inter_km2",
                                                                                       "share_of_target")
    if source_mass is not None:
        acc = acc.with_columns((pl.col("source_id").replace_strict(dict(source_mass), default=None, return_dtype=pl.Float64)
                                * pl.col("weight")).alias("wm"))
        basis = "population-weighted"
    else:
        acc = acc.with_columns(pl.col("inter_km2").alias("wm"))
        basis = "area-weighted"
    j = acc.join(values.select("source_id", *value_cols), on="source_id", how="left")
    cover = acc.group_by("target_id").agg(pl.col("share_of_target").sum().clip(0, 1).alias("target_cover"),
                                          pl.col("source_id").n_unique().alias("n_sources"))
    res = cover
    for c in value_cols:
        ok = pl.col(c).is_not_null() & pl.col("wm").is_not_null()
        g = j.group_by("target_id").agg(pl.col("wm").filter(ok).sum().alias("w_data"),
                                        pl.col("wm").filter(pl.col("wm").is_not_null()).sum().alias("w_all"),
                                        (pl.col("wm") * pl.col(c)).filter(ok).sum().alias("num"))
        g = g.with_columns((pl.col("w_data") / pl.col("w_all")).alias(f"{c}__data_share"),
                           pl.when(pl.col("w_data") > 0).then(pl.col("num") / pl.col("w_data")).otherwise(None).alias(c))
        g = g.select("target_id", c, f"{c}__data_share")
        res = res.join(g, on="target_id", how="left")
    res = res.with_columns([pl.when((pl.col("target_cover") >= min_target_cover) & (pl.col(f"{c}__data_share") >= min_data_share))
                            .then(pl.col(c)).otherwise(None).alias(c) for c in value_cols])
    return res.with_columns(pl.lit(basis).alias("weight_basis")).sort("target_id")


def audit(xw: pl.DataFrame, reference: Mapping[str, str]) -> dict:
    """Agreement with an independent CODE reference (source_id -> expected target_id). AUDIT ONLY: nothing here feeds weights.
    Reported for accepted sources that have a reference, by relation."""
    a = xw.filter((pl.col("status") == ACCEPTED) & (pl.col("weight") > 0))
    best = a.sort("weight", descending=True).group_by("source_id", maintain_order=True).first()
    ref = pl.DataFrame({"source_id": list(reference), "ref": list(reference.values())})
    j = best.join(ref, on="source_id", how="inner")
    if not j.height:
        return {"n": 0}
    j = j.with_columns((pl.col("target_id") == pl.col("ref")).alias("agree"))
    by_rel = {r: {"n": int(n), "agree": float(m)} for r, n, m in j.group_by("relation").agg(pl.len(), pl.col("agree").mean()).iter_rows()}
    return {"n": j.height, "agree": float(j["agree"].mean()), "by_relation": by_rel, "reference_covers_sources": j.height / max(1, xw["source_id"].n_unique()),
            "disagreeing_source_ids": j.filter(~pl.col("agree"))["source_id"].sort().to_list()[:50]}
