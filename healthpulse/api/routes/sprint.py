"""IMDC 2026 benchmark: the replayed validation-test leaderboard and the season forecast per state (`hp sprint`)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ...core.security import Actor
from ...sprint import run as sr
from ..deps import do_audit, envelope, require

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


@router.get("/sprint/retro")
def sprint_retro(request: Request, a: Actor = Depends(require("read"))):
    import json

    from ...sprint import data as D
    p = D.root() / "retro_2023_24.json"
    if not p.exists():
        raise HTTPException(404, "no retrospective yet: run `hp retro`")
    rep = json.loads(p.read_text())
    return envelope(rep, request, warnings=rep.get("caveats", []))


class BriefReq(BaseModel):
    uf: str = Field(min_length=2, max_length=2)
    lang: Literal["en", "pt"] = "en"
    question: str | None = Field(None, max_length=600)


_brief_calls: list[float] = []


@router.post("/sprint/brief")
def sprint_brief(request: Request, req: BriefReq, a: Actor = Depends(require("read"))):
    """Server-sent events: `meta` (provider, the CONTEXT the model sees), text chunks, `done`. Rule-based without an LLM."""
    import json
    import time

    from fastapi.responses import StreamingResponse

    from ...llm import client as llm
    from ...sprint import brief
    ctx = brief.context(req.uf, req.lang)
    if not ctx["replay_2023_24"] and not ctx["outlook_2026_27"]:
        raise HTTPException(404, "no data for this state: run `hp sprint` and `hp retro`")
    cfg = llm.config()
    use = cfg["available"]
    now = time.time()
    if use:
        if len([x for x in _brief_calls if now - x < 60]) >= 20:
            raise HTTPException(429, "too many briefs in the last minute")
        _brief_calls.append(now)

    def gen():
        meta = {"provider": cfg["provider"] if use else "rule_based", "model": cfg["model"] if use else None, "context": ctx,
                "why_rules": None if use else ("disabled by admin" if not cfg["enabled"] else "not configured" if not cfg["configured"]
                                                else "cooling down after an error")}
        yield f"event: meta\ndata: {json.dumps(meta, default=str)}\n\n"
        if not use:
            yield f"data: {json.dumps({'t': brief.rule_based(ctx)})}\n\n"
        else:
            try:
                for chunk in llm.stream(brief.SYSTEM, brief.user_prompt(ctx, req.question), max_tokens=4000):
                    yield f"data: {json.dumps({'t': chunk})}\n\n"
            except llm.LLMError as e:
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                yield f"data: {json.dumps({'t': brief.rule_based(ctx)})}\n\n"
        yield "event: done\ndata: {}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class DecisionReq(BaseModel):
    uf: str = Field(min_length=2, max_length=2)
    decision: Literal["approve", "reject"]
    note: str | None = Field(None, max_length=1000)
    provider: str | None = Field(None, max_length=40)


@router.post("/sprint/decision")
def sprint_decision(request: Request, req: DecisionReq, a: Actor = Depends(require("ops.command"))):
    """The human step: the AI (or rule) only proposes; an epi reviewer / admin approves or rejects, and it is audited."""
    do_audit(request, a, f"story.proposal.{req.decision}", "state", req.uf.upper(), reason=req.note,
             meta={"provider": req.provider, "proposal": "weekly alert rule + pre-agreed response package from EW41"})
    return envelope({"uf": req.uf.upper(), "decision": req.decision, "by": a.user_id, "role": a.role}, request)
