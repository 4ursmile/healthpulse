"""Live situation analysis for an incident: clusters, growth and R, estimated affected area, suggested zones, and a
short-range projection under three scenarios.

Methods (all transparent, all labelled in the UI):
- Clusters: DBSCAN on great-circle distance (eps = incident `cluster_km`), weighted by report counts. Points that join
  no cluster are "sporadic" and are grouped by province.
- Growth: quasi-Poisson log-linear fit of daily cases over the last 14 complete days. The last `report_delay_days`
  are left out because they are still filling in (reporting delay), so a fresh dip is not read as a decline.
  R = (1 + r·L)(1 + r·D) (SEIR with latent L and infectious D days; Wallinga & Lipsitch 2007). Too few cases → the
  pathogen prior R0·(1 − immune), flagged `basis: prior`.
- Affected area: convex hull of the cluster's points buffered by a quarter of its radius. Suggested quarantine adds
  the distance the cluster spread in one latent period + reporting delay (spread speed is measured from how far new
  cases land from the centre compared with older ones). Safe = provinces with no reports and far from every cluster.
- Projection (SCENARIO, 28 days): per cluster, incidence grows at r(t) from R(t) = R_obs × zone effects × tracing
  × testing × S(t)/S(0). Effects already in the observed data (real zones active for ≥ 7 days) are not counted again.
  Scenarios: baseline = nothing new; current = real zones as set now; plan = current + simulated zones + an
  allocation plan. Uncertainty band = the growth-rate 95 % interval.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import numpy as np

from ..core.util import parse_dt, utcnow
from . import geo, store

TRANSMISSION = ("case", "suspected")
FIT_DAYS = 14
BAKE_DAYS = 7          # a real zone active this long is already reflected in the observed growth
HORIZON = 28
TRACE_PER_TEAM = 15    # new cases a response team can trace and isolate per day
TRACE_EFFECT = 0.35    # max R reduction from full contact tracing
TEST_PER_CASE = 8      # test kits used per new case per day (contacts + suspects)
TEST_EFFECT = 0.2      # max R reduction from finding and isolating cases earlier
VAX_LAG = 10           # days from dose to protection
AMBULANCE_TRANSFERS = 4  # patients per day an ambulance moves to a province with free beds


def gen_params(ps: dict) -> tuple[float, float]:
    L, D = float(ps["latent"]), float(ps["infectious"])
    if ps.get("route") == "vector":
        L += 8.0   # extrinsic incubation in the mosquito lengthens the generation
    return L, D


def R_from_r(r, L, D):
    return np.maximum(0.0, (1 + np.asarray(r) * L) * (1 + np.asarray(r) * D))


def r_from_R(R, L, D):
    R = np.maximum(np.asarray(R, float), 1e-6)
    return (-(L + D) + np.sqrt((L + D) ** 2 - 4 * L * D * (1 - R))) / (2 * L * D)


def poisson_trend(y: np.ndarray) -> dict | None:
    """Quasi-Poisson log-linear fit; returns daily growth r with a 95 % interval and the fitted level at the last day."""
    n = len(y)
    if n < 5 or y.sum() < 8:
        return None
    t = np.arange(n, dtype=float) - (n - 1)
    X = np.column_stack([np.ones(n), t])
    b = np.array([math.log(max(y.mean(), 0.5)), 0.0])
    for _ in range(50):
        mu = np.exp(np.clip(X @ b, -30, 30))
        W = mu
        z = X @ b + (y - mu) / np.maximum(mu, 1e-9)
        XtW = X.T * W
        try:
            nb = np.linalg.solve(XtW @ X, XtW @ z)
        except np.linalg.LinAlgError:
            return None
        if np.max(np.abs(nb - b)) < 1e-8:
            b = nb
            break
        b = nb
    mu = np.exp(np.clip(X @ b, -30, 30))
    phi = max(1.0, float(np.sum((y - mu) ** 2 / np.maximum(mu, 1e-9)) / max(1, n - 2)))
    try:
        cov = np.linalg.inv((X.T * mu) @ X) * phi
    except np.linalg.LinAlgError:
        return None
    se = float(math.sqrt(max(cov[1, 1], 0)))
    r = float(np.clip(b[1], -0.5, 0.7))
    return {"r": r, "lo": float(np.clip(r - 1.96 * se, -0.6, 0.8)), "hi": float(np.clip(r + 1.96 * se, -0.6, 0.8)),
            "level": float(math.exp(min(30, b[0]))), "n_days": n, "dispersion": round(phi, 2)}


def _dates(reps: list[dict], today: date) -> list[date]:
    ds = [date.fromisoformat(r["event_date"]) for r in reps if r["kind"] in TRANSMISSION]
    d0 = min(ds) if ds else today - timedelta(days=13)
    d0 = min(d0, today - timedelta(days=13))
    d1 = max([today] + [date.fromisoformat(r["event_date"]) for r in reps])
    return [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]


def _daily(idx: np.ndarray, cnt: np.ndarray, n: int) -> np.ndarray:
    out = np.zeros(n)
    np.add.at(out, idx, cnt)
    return out


def analyze(iid: str, today: date | None = None, *, with_projection: bool = True) -> dict:
    inc = store.get_incident(iid)
    if not inc:
        raise KeyError("incident not found")
    S, ps = inc["settings"], inc["pathogen_spec"]
    L, D = gen_params(ps)
    reps = store.reports(iid)
    zs = store.zones(iid)
    P = geo.provinces()
    attrs = P["attrs"]
    today = today or date.today()
    days = _dates(reps, today)
    n = len(days)
    d_index = {d.isoformat(): i for i, d in enumerate(days)}

    approx = 0
    rows = []
    for r in reps:
        lat, lon = r["lat"], r["lon"]
        if lat is None:
            lat, lon = geo.centroid(r["province_id"])
            approx += 1
        rows.append((r["kind"], r["count"], lat, lon, r["province_id"], d_index.get(r["event_date"], n - 1), r["lat"] is None))
    K = np.array([x[0] for x in rows], object) if rows else np.array([], object)
    C = np.array([x[1] for x in rows], float)
    LAT = np.array([x[2] for x in rows], float)
    LON = np.array([x[3] for x in rows], float)
    PID = np.array([x[4] for x in rows], object) if rows else np.array([], object)
    DI = np.array([x[5] for x in rows], int)
    APX = np.array([x[6] for x in rows], bool)

    def tot(kind, mask=None):
        m = K == kind if len(K) else np.zeros(0, bool)
        if mask is not None:
            m = m & mask
        return float(C[m].sum()) if len(C) else 0.0

    tm = np.isin(K, TRANSMISSION) if len(K) else np.zeros(0, bool)
    weight = np.where(K == "suspected", 0.5, 1.0) * C if len(C) else C
    delay = int(math.ceil(S["report_delay_days"]))

    def fit_series(series: np.ndarray) -> dict | None:
        end = max(1, n - delay)
        y = series[max(0, end - FIT_DAYS):end]
        return poisson_trend(y)

    prior_R = float(ps["r0"]) * (1 - float(ps.get("immune") or 0))

    def growth(series: np.ndarray) -> dict:
        f = fit_series(series)
        if S.get("r_override"):
            R = float(S["r_override"])
            r = float(r_from_R(R, L, D))
            return {"r": r, "lo": r, "hi": r, "R": R, "R_lo": R, "R_hi": R, "basis": "override", "level": float(series[-7:].mean()) if n else 0}
        if f is None:
            r = float(r_from_R(prior_R, L, D))
            return {"r": r, "lo": float(r_from_R(prior_R * 0.7, L, D)), "hi": float(r_from_R(prior_R * 1.3, L, D)), "R": prior_R,
                    "R_lo": prior_R * 0.7, "R_hi": prior_R * 1.3, "basis": "prior", "level": float(series[-7:].mean()) if n else 0}
        return {**f, "R": float(R_from_r(f["r"], L, D)), "R_lo": float(R_from_r(f["lo"], L, D)), "R_hi": float(R_from_r(f["hi"], L, D)), "basis": "fitted"}

    # ---------------------------------------------------------------- national
    nat_cases = _daily(DI[tm], weight[tm], n) if len(C) else np.zeros(n)
    nat_conf = _daily(DI[K == "case"], C[K == "case"], n) if len(C) else np.zeros(n)
    g_nat = growth(nat_cases)
    last7 = float(nat_conf[-7:].sum())
    prev7 = float(nat_conf[-14:-7].sum())

    # ---------------------------------------------------------------- clusters
    labels = np.full(len(C), -1)
    if tm.sum() > 0:
        from sklearn.cluster import DBSCAN
        idx = np.where(tm)[0]
        X = np.radians(np.column_stack([LAT[idx], LON[idx]]))
        lab = DBSCAN(eps=S["cluster_km"] / 6371.0, min_samples=3, metric="haversine", algorithm="ball_tree").fit(X, sample_weight=weight[idx]).labels_
        labels[idx] = lab
    clusters = []
    for lab in sorted(set(labels[labels >= 0].tolist())):
        m = labels == lab
        w = weight[m]
        la, lo = LAT[m], LON[m]
        clat, clon = float(np.average(la, weights=w)), float(np.average(lo, weights=w))
        dist = geo.haversine(clat, clon, la, lo)
        order = np.argsort(dist)
        cw = np.cumsum(w[order]) / w.sum()
        radius = float(max(0.3, dist[order][np.searchsorted(cw, 0.9)] if len(dist) else 0.3))
        series = _daily(DI[m], w, n)
        g = growth(series)
        # spread speed: how much further out the last week's cases land than the earlier ones
        recent = DI[m] >= n - 7
        if recent.any() and (~recent).any():
            r_new = float(np.average(dist[recent], weights=w[recent]))
            r_old = float(np.average(dist[~recent], weights=w[~recent]))
            span = max(1.0, float(np.average(DI[m][recent], weights=w[recent]) - np.average(DI[m][~recent], weights=w[~recent])))
            speed = float(np.clip((r_new - r_old) / span, 0.0, 5.0))
        else:
            speed = 0.15
        pids = {}
        for p_, c_ in zip(PID[m], w):
            pids[p_] = pids.get(p_, 0) + c_
        main = max(pids, key=pids.get)
        hull = geo.hull_buffer(la, lo, max(0.3, radius * 0.25))
        margin = float(np.clip(speed * (L + S["report_delay_days"]) + 1.0, 1.0, 15.0))
        quarantine = geo.buffer_geom(hull, margin)
        l7 = float(series[-7:].sum())
        p7 = float(series[-14:-7].sum())
        trend = "growing" if g["lo"] > 0.01 else "declining" if g["hi"] < -0.01 else "stable"
        clusters.append({
            "lab": int(lab), "lat": round(clat, 5), "lon": round(clon, 5), "radius_km": round(radius, 2), "cases": float(w.sum()),
            "confirmed": tot("case", m), "suspected": tot("suspected", m), "last7": l7, "prev7": p7,
            "first": days[int(DI[m].min())].isoformat(), "last": days[int(DI[m].max())].isoformat(),
            "growth": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in g.items()},
            "doubling_days": round(math.log(2) / g["r"], 1) if g["r"] > 0.005 else None,
            "halving_days": round(math.log(2) / -g["r"], 1) if g["r"] < -0.005 else None,
            "trend": trend, "spread_km_day": round(speed, 2), "province_id": main, "province": attrs.get(main, {}).get("name", main),
            "provinces": sorted(pids, key=lambda k: -pids[k]), "hull": hull, "quarantine_margin_km": round(margin, 1),
            "suggest_quarantine": quarantine, "series": series.tolist(), "n_reports": int(m.sum()),
            "approx_share": round(float(APX[m].mean()), 2),
        })
    clusters.sort(key=lambda c: -c["last7"] - 0.1 * c["cases"])
    for i, c in enumerate(clusters):
        c["id"] = f"C{i + 1}"
        c["label"] = f"C{i + 1} · {c['province']}"
        aff = geo.zone_population(c["hull"])
        qua = geo.zone_population(c["suggest_quarantine"])
        c["affected_km2"], c["affected_people_est"] = aff["area_km2"], aff["people_est"]
        c["quarantine_km2"], c["quarantine_people_est"] = qua["area_km2"], qua["people_est"]

    # sporadic: transmission points that joined no cluster, grouped by province
    spor = []
    sm = tm & (labels < 0)
    for pid in sorted(set(PID[sm].tolist())):
        m = sm & (PID == pid)
        series = _daily(DI[m], weight[m], n)
        spor.append({"province_id": pid, "province": attrs.get(pid, {}).get("name", pid), "cases": float(weight[m].sum()),
                     "last7": float(series[-7:].sum()), "series": series.tolist(), "lat": float(np.mean(LAT[m])), "lon": float(np.mean(LON[m]))})
    spor.sort(key=lambda s: -s["last7"])

    # ---------------------------------------------------------------- provinces
    h_per_case = min(1.0, float(ps["p_hosp"]) / max(float(ps["report"]), 1e-3))
    prov = []
    for pid in P["ids"]:
        a = attrs.get(pid, {})
        mk = PID == pid if len(PID) else np.zeros(0, bool)
        if not mk.any():
            continue
        conf_series = _daily(DI[mk & (K == "case")], C[mk & (K == "case")], n)
        susp = tot("suspected", mk)
        los = int(round(float(ps["los"])))
        occ = float((_daily(DI[mk & tm], weight[mk & tm], n)[-los:]).sum() * h_per_case)
        cap = float((a.get("beds_est") or 0) * S["free_bed_share"])
        vac = tot("vaccinated", mk)
        prov.append({"province_id": pid, "name": a.get("name", pid), "population": a.get("population"), "confirmed": float(conf_series.sum()),
                     "suspected": susp, "last7": float(conf_series[-7:].sum()), "deaths": tot("death", mk), "recovered": tot("recovered", mk),
                     "vaccinated": vac, "coverage": round(vac / max(a.get("population") or 1, 1), 4), "tested": tot("tested", mk),
                     "per100k_7d": round(float(conf_series[-7:].sum()) / max(a.get("population") or 1, 1) * 1e5, 2),
                     "hosp_est": round(occ), "beds_free_est": round(cap), "bed_load": round(occ / cap, 2) if cap else None,
                     "lat": a.get("lat"), "lon": a.get("lon")})
    prov.sort(key=lambda p: -p["last7"] - 0.01 * p["confirmed"])

    # ---------------------------------------------------------------- zones: what is inside
    zone_out = []
    people_q = 0
    for z in zs:
        st = {"cases_inside": 0.0, "last7_inside": 0.0, "vaccinated_inside": 0.0, "clusters": []}
        if len(C):
            inside = geo.contains(z["geometry"], LAT, LON)
            st["cases_inside"] = float(weight[inside & tm].sum())
            st["last7_inside"] = float(weight[inside & tm & (DI >= n - 7)].sum())
            st["vaccinated_inside"] = tot("vaccinated", inside)
        cin = geo.contains(z["geometry"], [c["lat"] for c in clusters], [c["lon"] for c in clusters]) if clusters else []
        st["clusters"] = [c["id"] for c, ok in zip(clusters, cin) if ok]
        for c, ok in zip(clusters, cin):
            if ok:
                c.setdefault("zones", []).append(z["zone_id"])
        if z["status"] == "active" and z["basis"] == "real" and z["kind"] == "quarantine":
            people_q += z["params"].get("people_est") or 0
        zone_out.append({**z, "stats": st})

    # suggested zones (not stored until a controller accepts them)
    suggestions = []
    covered = lambda c, kind: any(  # noqa: E731
        zz["kind"] == kind and zz["status"] != "lifted" and c["id"] in zz["stats"]["clusters"] for zz in zone_out)
    for c in clusters:
        if c["cases"] < 3:
            continue
        if not covered(c, "affected"):
            suggestions.append({"kind": "affected", "cluster": c["id"], "geometry": c["hull"], "area_km2": c["affected_km2"], "people_est": c["affected_people_est"],
                                "why": f"{c['label']}: hull of {c['n_reports']} reports ({c['cases']:.0f} cases), buffered {max(0.3, c['radius_km'] * 0.25):.1f} km"})
        if c["trend"] != "declining" and not covered(c, "quarantine"):
            suggestions.append({"kind": "quarantine", "cluster": c["id"], "geometry": c["suggest_quarantine"], "area_km2": c["quarantine_km2"],
                                "people_est": c["quarantine_people_est"],
                                "why": f"{c['label']}: affected area + {c['quarantine_margin_km']} km (spread {c['spread_km_day']} km/day × {L + S['report_delay_days']:.0f} days latent + reporting delay); R ≈ {c['growth']['R']:.2f}"})
        if (ps.get("vax_inf") or 0) > 0 and c["trend"] == "growing" and not covered(c, "vaccination"):
            ring = geo.buffer_geom(c["suggest_quarantine"], 2.0)
            pop = geo.zone_population(ring)
            suggestions.append({"kind": "vaccination", "cluster": c["id"], "geometry": ring, "area_km2": pop["area_km2"], "people_est": pop["people_est"],
                                "why": f"{c['label']} is growing (doubling {c['doubling_days'] or '—'} d): ring vaccination 2 km beyond the quarantine line"})

    # safe provinces: no reports at all and ≥ max(60 km, 4 × the widest quarantine) from every cluster
    reach = max([60.0] + [4 * (c["radius_km"] + c["quarantine_margin_km"]) for c in clusters])
    with_reports = set(PID[tm].tolist()) if len(PID) else set()
    safe = []
    for pid in P["ids"]:
        if pid in with_reports:
            continue
        a = attrs[pid]
        dmin = min([float(geo.haversine(a["lat"], a["lon"], c["lat"], c["lon"])) for c in clusters] + [1e9])
        if dmin >= reach:
            safe.append({"province_id": pid, "name": a["name"], "nearest_cluster_km": None if dmin >= 1e9 else round(dmin)})

    warnings = []
    if approx:
        warnings.append(f"{approx} report(s) have no coordinates and are placed at the province centre — cluster areas are less precise.")
    if g_nat["basis"] == "prior":
        warnings.append("Too few cases for a growth fit: R uses the pathogen prior until ≥ 8 cases over ≥ 5 days are keyed in.")
    if delay:
        warnings.append(f"The last {delay} day(s) are excluded from the growth fit (reporting delay {S['report_delay_days']:g} d).")
    if inc["mode"] == "drill":
        warnings.append("DRILL incident: reports are an exercise (SYNTHETIC), not real cases.")

    out = {
        "incident": inc, "as_of": today.isoformat(), "days": [d.isoformat() for d in days],
        "kpis": {"confirmed": tot("case"), "suspected": tot("suspected"), "deaths": tot("death"), "recovered": tot("recovered"),
                 "vaccinated": tot("vaccinated"), "tested": tot("tested"),
                 "positivity": round(tot("case") / tot("tested"), 3) if tot("tested") else None,
                 "today": float(nat_conf[d_index.get(today.isoformat(), n - 1)]), "last7": last7, "prev7": prev7,
                 "change_7d": round((last7 - prev7) / prev7, 3) if prev7 else None,
                 "R": round(g_nat["R"], 2), "R_lo": round(g_nat["R_lo"], 2), "R_hi": round(g_nat["R_hi"], 2), "r_basis": g_nat["basis"],
                 "doubling_days": round(math.log(2) / g_nat["r"], 1) if g_nat["r"] > 0.005 else None,
                 "clusters": len(clusters), "growing_clusters": sum(c["trend"] == "growing" for c in clusters),
                 "provinces_affected": len(with_reports), "people_in_real_quarantine": people_q,
                 "zones_active": sum(z["status"] == "active" for z in zs), "reports": len(reps)},
        "series": {"confirmed": nat_conf.tolist(), "transmission": nat_cases.tolist(),
                   "deaths": (_daily(DI[K == "death"], C[K == "death"], n) if len(C) else np.zeros(n)).tolist(),
                   "vaccinated": (_daily(DI[K == "vaccinated"], C[K == "vaccinated"], n) if len(C) else np.zeros(n)).tolist()},
        "clusters": clusters, "sporadic": spor, "provinces": prov, "zones": zone_out, "suggestions": suggestions, "safe_provinces": safe,
        "warnings": warnings,
        "method": {"growth": "quasi-Poisson log-linear on the last 14 complete days", "R": "(1+rL)(1+rD), L=%.1f D=%.1f" % (L, D),
                   "clusters": f"DBSCAN, {S['cluster_km']} km", "population": "province density × zone area (ESTIMATE)"},
    }
    if with_projection:
        from . import project
        out["projection"] = project.scenarios(out)
    return out


def zone_active_weight(z: dict, now: datetime | None = None) -> float:
    """Share of a zone's effect NOT yet visible in observed growth (1 = all of it is new)."""
    if z["basis"] == "simulated":
        return 1.0
    at = parse_dt(z["params"].get("activated_at"))
    if not at:
        return 1.0
    d = ((now or utcnow()) - at).total_seconds() / 86400
    return float(np.clip(1 - d / BAKE_DAYS, 0, 1))
