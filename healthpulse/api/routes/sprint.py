"""IMDC 2026 benchmark: the replayed validation-test leaderboard and the season forecast per state (`hp sprint`)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ...core.security import Actor
from ...sprint import run as sr
from ..deps import envelope, require

router = APIRouter()


@router.get("/sprint/report")
def sprint_report(request: Request, a: Actor = Depends(require("read"))):
    rep = sr.load_report()
    if not rep:
        raise HTTPException(404, "no IMDC report yet: run `hp sprint`")
    rep = {k: v for k, v in rep.items() if k != "per_state"}
    return envelope(rep, request, warnings=rep.get("caveats", []))


@router.get("/sprint/forecast/{uf}")
def sprint_forecast(request: Request, uf: str, season: str = "2026-2027", model: str = sr.CHAMPION,
                    a: Actor = Depends(require("read"))):
    """Weekly median and intervals for one state from the written submission CSV, with observed cases where they exist."""
    import polars as pl

    from ...sprint import data as D
    if model not in ("climatology", sr.CHAMPION):
        raise HTTPException(422, "model must be climatology or the champion")
    p = D.root() / "submission" / model / season / f"{uf.upper()}.csv"
    if not p.exists():
        raise HTTPException(404, "no forecast for this state / season")
    fc = pl.read_csv(p, try_parse_dates=True)
    obs = D.uf_weekly().filter(pl.col("uf") == uf.upper()).select("date", pl.col("cases").alias("observed"))
    out = fc.join(obs, on="date", how="left").sort("date")
    return envelope(out.with_columns(pl.col("date").cast(pl.Utf8)).to_dicts(), request,
                    meta={"uf": uf.upper(), "season": season, "model": model, "unit": "probable dengue cases per week",
                          "kind": "forecast"})
