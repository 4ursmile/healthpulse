"""Viet Nam outbreak sandbox: create a session, step it with interventions, rewind, and ask the advisor (LLM with a
rule-based fallback). Sessions live in memory; everything returned is a SCENARIO."""
from __future__ import annotations

import time
from collections import deque

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ...llm import client as llm
from ...vietnam import advisor, sandbox as sb
from ..deps import envelope
from .vietnam import _need

router = APIRouter(prefix="/vn/sandbox", tags=["vietnam"])
_calls: deque[float] = deque(maxlen=60)


def _get(sid: str) -> sb.Session:
    s = sb.get(sid)
    if not s:
        raise HTTPException(404, "sandbox session expired — start a new one")
    return s


@router.get("/catalog")
def catalog(request: Request):
    _need()
    return envelope(sb.catalog(), request)


@router.post("")
def create(spec: sb.SandboxSpec, request: Request):
    _need()
    try:
        s = sb.create(spec)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"could not start the sandbox: {e}")
    return envelope(s.public(), request, meta={"semantics": "SCENARIO"})


class StepReq(BaseModel):
    days: int = Field(1, ge=0, le=60)
    actions: list[sb.Action] = []


@router.post("/{sid}/step")
def step(sid: str, req: StepReq, request: Request):
    s = _get(sid)
    t = time.perf_counter()
    frames, applied, errors = s.step(req.days, req.actions)
    out = s.public(frames=frames)
    out["applied"], out["errors"] = applied, errors
    return envelope(out, request, meta={"ms": round((time.perf_counter() - t) * 1000)},
                    warnings=[e["error"] for e in errors])


class RewindReq(BaseModel):
    day: int = Field(..., ge=0)


@router.post("/{sid}/rewind")
def rewind(sid: str, req: RewindReq, request: Request):
    s = sb.rewind(_get(sid), req.day)
    return envelope(s.public(), request)


@router.get("/{sid}")
def get(sid: str, request: Request):
    return envelope(_get(sid).public(), request)


class AdviseReq(BaseModel):
    lang: str = Field("en", pattern="^(en|vi)$")
    question: str | None = Field(None, max_length=1000)
    use_llm: bool = True


@router.post("/{sid}/advise")
def advise(sid: str, req: AdviseReq, request: Request):
    """Proposals only — nothing is applied until the user presses Apply (or turns on autopilot)."""
    s = _get(sid)
    cfg = llm.config()
    fallback_reason = None
    if req.use_llm and cfg["available"]:
        now = time.time()
        if len([x for x in _calls if now - x < 60]) >= 20:
            fallback_reason = "LLM rate limit for this server (20/min) — rule-based advice instead"
        else:
            _calls.append(now)
            ctx = advisor.context(s, req.lang)
            import json
            q = req.question or "What should we do now? Propose the next actions."
            user = f"CONTEXT:\n```json\n{json.dumps(ctx, ensure_ascii=False, default=str)}\n```\n\nQUESTION: {q}"
            try:
                text = llm.complete(advisor.system_prompt(), user, max_tokens=6000)
                summary, acts, bad = advisor.parse(text)
                return envelope({"source": "llm", "provider": cfg["provider"], "model": cfg["model"], "summary": summary,
                                 "actions": [a.model_dump(exclude_none=True) for a in acts], "rejected": bad, "day": s.day}, request)
            except (llm.LLMError, ValueError) as e:
                fallback_reason = f"AI unavailable ({e}) — rule-based advice instead"
    elif req.use_llm:
        fallback_reason = ("AI turned off by an admin" if not cfg["enabled"] else
                           f"AI not configured ({cfg['missing']})" if not cfg["configured"] else
                           "AI cooling down after an error") + " — rule-based advice"
    acts = advisor.rule_actions(s)
    return envelope({"source": "rules", "summary": advisor.summary_rules(s, acts, req.lang == "vi"),
                     "actions": [a.model_dump(exclude_none=True) for a in acts], "rejected": [], "day": s.day,
                     "fallback_reason": fallback_reason}, request)
