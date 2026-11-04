"""Admin: choose the LLM used for analysis (provider, model, effort, on/off). API keys stay in .env and are never
stored or returned; the UI only learns whether each key is present. When the LLM is off, unconfigured or failing,
every screen falls back to the rule-based analyst, so nothing depends on it."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ...core import db
from ...core.security import Actor
from ...core.util import dumps, iso, utcnow
from ...llm import client as llm
from ..deps import do_audit, envelope, require

router = APIRouter(prefix="/admin/ai", tags=["admin"])


class LLMSettings(BaseModel):
    enabled: bool = True
    provider: Literal["auto", "anthropic", "openai"] = "auto"
    model: str | None = Field(None, max_length=120, pattern=r"^[A-Za-z0-9._:/@-]*$")
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    base_url: str | None = Field(None, max_length=300, pattern=r"^https?://[^\s]+$")


def _view(request: Request) -> dict:
    c = llm.config()
    return envelope({"config": c, "settings": llm.admin_overrides(), "suggested": llm.SUGGESTED, "efforts": llm.EFFORTS}, request)


@router.get("/llm")
def get_llm(request: Request, a: Actor = Depends(require("admin.settings"))):
    return _view(request)


@router.put("/llm")
def put_llm(body: LLMSettings, request: Request, a: Actor = Depends(require("admin.settings"))):
    v = body.model_dump()
    if v["provider"] == "openai" and not v["model"]:
        raise HTTPException(400, "an OpenAI-compatible provider needs a model id")
    db.ex("INSERT INTO settings_kv(key, value_json, updated_at) VALUES('llm',?,?) ON CONFLICT(key) DO UPDATE SET "
          "value_json=excluded.value_json, updated_at=excluded.updated_at", dumps(v), iso(utcnow()))
    do_audit(request, a, "settings.llm.update", "settings", "llm", meta=v)
    return _view(request)


@router.delete("/llm")
def reset_llm(request: Request, a: Actor = Depends(require("admin.settings"))):
    db.ex("DELETE FROM settings_kv WHERE key='llm'")
    do_audit(request, a, "settings.llm.reset", "settings", "llm")
    return _view(request)


@router.get("/llm/models")
def models(request: Request, a: Actor = Depends(require("admin.settings"))):
    return envelope(llm.list_models(), request)


@router.post("/llm/test")
def test(request: Request, a: Actor = Depends(require("admin.settings"))):
    return envelope(llm.test() | {"config": llm.config()}, request)
