"""Scope tree for the one scope picker: Global → country → state/province, listing only places that have data.

Every country and province returned here has at least one ingested series; nothing is listed "for completeness".
Each node carries a small headline (latest reported week for Brazil, this month's model level for Viet Nam) so the
picker and the Global overview can show where attention is needed without opening each page.
"""
from __future__ import annotations

from datetime import timedelta

import polars as pl
from fastapi import APIRouter, Request

from ...core import db
from ...sources import catalog, silver
from ...vietnam import data as vdata, model as vmodel
from ..deps import envelope
from .public import _cases, _pop

router = APIRouter()

UF = {"AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia", "CE": "Ceará", "DF": "Distrito Federal",
      "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul", "MG": "Minas Gerais",
      "PA": "Pará", "PB": "Paraíba", "PR": "Paraná", "PE": "Pernambuco", "PI": "Piauí", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
      "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo", "SE": "Sergipe", "TO": "Tocantins"}


def _brazil() -> dict | None:
    dim = silver.latest("municipality_dim")
    if not dim.height:
        return None
    frames = [df.select("municipality_id", "week_start", "observed_cases", "disease_id") for k in catalog.DISEASES if (df := _cases(k)).height]
    if not frames:
        return None
    cases = pl.concat(frames)
    diseases = sorted(set(cases["disease_id"].unique().to_list()))
    dengue = cases.filter(pl.col("disease_id") == "dengue") if "dengue" in diseases else cases.filter(pl.col("disease_id") == diseases[0])
    last = dengue["week_start"].max()
    prev = last - timedelta(weeks=1)
    st = dim.select("municipality_id", "state_abbr")
    wk = (dengue.filter(pl.col("week_start").is_in([last, prev])).join(st, on="municipality_id")
          .group_by("state_abbr").agg(pl.col("observed_cases").filter(pl.col("week_start") == last).sum().alias("last"),
                                      pl.col("observed_cases").filter(pl.col("week_start") == prev).sum().alias("prev")))
    wk = {r["state_abbr"]: r for r in wk.iter_rows(named=True)}
    n = (cases.select("municipality_id").unique().join(st, on="municipality_id").join(_pop(), on="municipality_id", how="left")
         .group_by("state_abbr").agg(pl.len().alias("n"), pl.col("population").sum().alias("population")))
    dis = {r["state_abbr"]: sorted(r["d"]) for r in cases.select("municipality_id", "disease_id").unique().join(st, on="municipality_id")
           .group_by("state_abbr").agg(pl.col("disease_id").unique().alias("d")).iter_rows(named=True)}
    alerts = {}
    names = dict(zip(st["municipality_id"].to_list(), st["state_abbr"].to_list()))
    for r in db.q("SELECT municipality_id FROM alert WHERE state IN ('draft','held')"):
        uf = names.get(r["municipality_id"])
        alerts[uf] = alerts.get(uf, 0) + 1
    regions = []
    # states with the headline (dengue) series first, then by population
    n = n.with_columns(pl.col("state_abbr").is_in(list(wk)).alias("_h"))
    for r in n.sort(["_h", "population"], descending=True, nulls_last=True).iter_rows(named=True):
        w = wk.get(r["state_abbr"], {})
        regions.append({"id": r["state_abbr"], "name": UF.get(r["state_abbr"], r["state_abbr"]), "units": r["n"], "population": r["population"],
                        "diseases": dis.get(r["state_abbr"], []), "reported_last_week": w.get("last"), "reported_prev_week": w.get("prev"), "open_alerts": alerts.get(r["state_abbr"], 0)})
    return {"id": "br", "name": "Brazil", "level_name": "State", "unit_name": "municipalities", "diseases": diseases,
            "latest": str(last), "latest_kind": "week", "semantics": "OBSERVED", "regions": regions,
            "reported_last_week": sum(x["reported_last_week"] or 0 for x in regions),
            "reported_prev_week": sum(x["reported_prev_week"] or 0 for x in regions),
            "open_alerts": sum(alerts.values()), "units": sum(x["units"] for x in regions)}


def _vietnam() -> dict | None:
    if not vdata.is_built():
        return None
    try:
        st = vmodel.state()
    except Exception:  # noqa: BLE001 - not fitted yet: list nothing rather than half a country
        return None
    rows = [p for p in st["provinces"] if p.get("estimate") is not None]
    if not rows:
        return None
    rows.sort(key=lambda p: (-{"alert": 3, "warning": 2, "watch": 1, "normal": 0}.get(p["level"], -1), -(p.get("incidence_per_100k") or 0)))
    return {"id": "vn", "name": "Viet Nam", "level_name": "Province", "unit_name": "provinces", "diseases": ["dengue"],
            "latest": st["month"], "latest_kind": "month", "semantics": st.get("semantics", "MODEL_ESTIMATE"),
            "national_estimate": st.get("national_estimate"), "national_normal": st.get("national_normal"), "counts": st.get("counts"),
            "units": len(rows),
            "regions": [{"id": p["province_id"], "name": p["name"], "level": p["level"], "population": p.get("population"),
                         "incidence_per_100k": p.get("incidence_per_100k"), "ratio": p.get("ratio"), "reason": p.get("reason")} for p in rows]}


@router.get("/scopes")
def scopes(request: Request):
    countries = [c for c in (_brazil(), _vietnam()) if c]
    return envelope({"countries": countries}, request,
                    meta={"note": "Only countries and provinces with ingested data are listed.",
                          "semantics": {c["id"]: c["semantics"] for c in countries}})
