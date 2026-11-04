"""API plumbing: response envelope, auth/RBAC dependencies, errors, idempotency and cached read models."""
from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

import polars as pl
from fastapi import Depends, Header, HTTPException, Request

from ..core import db, lake
from ..core.security import Actor, PERMISSIONS, audit, resolve_token
from ..core.util import dumps, iso, loads, utcnow


def envelope(data: Any, request: Request | None = None, *, meta: dict | None = None, warnings: list[str] | None = None) -> dict:
    return {"data": data, "meta": meta or {}, "warnings": warnings or [],
            "request_id": getattr(request.state, "request_id", None) if request else None}


def actor(request: Request, authorization: str | None = Header(None)) -> Actor:
    tok = None
    if authorization and authorization.lower().startswith("bearer "):
        tok = authorization[7:].strip()
    a = resolve_token(tok)
    request.state.actor = a
    return a


def require(action: str):
    if action not in PERMISSIONS:
        raise RuntimeError(f"unknown permission {action}")

    def dep(request: Request, a: Actor = Depends(actor)) -> Actor:
        if a.user_id == "anonymous" and action != "read":
            raise HTTPException(401, "sign in required")
        if not a.can(action):
            raise HTTPException(403, f"role {a.role} may not {action}")
        return a
    return dep


def idem_key(idempotency_key: str | None = Header(None, alias="Idempotency-Key")) -> str | None:
    if idempotency_key and len(idempotency_key) > 128:
        raise HTTPException(400, "Idempotency-Key too long")
    return idempotency_key


def remember(key: str | None, a: Actor, route: str, response: dict) -> None:
    if not key:
        return
    try:
        db.insert("idempotency", {"key": f"{a.user_id}:{route}:{key}", "actor_id": a.user_id, "route": route,
                                  "response_json": dumps(response), "created_at": iso(utcnow())})
    except Exception:  # noqa: BLE001 — duplicate key is fine
        pass


def replay(key: str | None, a: Actor, route: str) -> dict | None:
    if not key:
        return None
    r = db.q1("SELECT response_json FROM idempotency WHERE key=?", f"{a.user_id}:{route}:{key}")
    return loads(r["response_json"]) if r else None


def do_audit(request: Request, a: Actor, action: str, target_type: str, target_id: str, **kw) -> None:
    audit(a, action, target_type, target_id, request_id=getattr(request.state, "request_id", None), **kw)


@lru_cache(maxsize=64)
def forecast_frame(fid: str) -> pl.DataFrame:
    """Published forecasts are immutable, so a process-level cache is always correct."""
    f = db.q1("SELECT parquet_uri FROM forecast WHERE forecast_id=?", fid)
    if not f:
        raise HTTPException(404, "unknown forecast")
    return pl.read_parquet(lake.abs_artifact(f["parquet_uri"]))


def latest_public_forecast(disease_id: str) -> dict | None:
    return db.jcol(db.q1("SELECT * FROM forecast WHERE disease_id=? AND is_public=1 ORDER BY created_at DESC LIMIT 1", disease_id),
                   "horizons_json", "provenance_json")


def page(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, 500)), max(0, offset)


class Timer:
    def __init__(self):
        self.t = time.perf_counter()

    def ms(self) -> float:
        return round((time.perf_counter() - self.t) * 1000, 1)
