"""FastAPI application: versioned /api/v1 routers, request ids, timing, gzip, typed error mapping, static web app."""
from __future__ import annotations

import logging
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
import orjson
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..bootstrap import init
from ..config import ROOT
from ..core.util import new_id
from ..features.pit import CardinalityError, LeakageError
from .routes import admin_data, admin_ml, ai, geo, nowcast, ops, public, scenarios, scope, sprint, vietnam, vn_calibration, vn_sandbox

log = logging.getLogger("healthpulse.api")


class FastJSON(JSONResponse):
    """orjson rendering with numpy and datetime support (NaN becomes null, never 0)."""

    def render(self, content) -> bytes:
        return orjson.dumps(content, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY, default=str)
WEB = ROOT / "web"


def create_app() -> FastAPI:
    init()
    app = FastAPI(title="HealthPulse API", version="3.1.0", default_response_class=FastJSON,
                  description="Climate-informed disease forecasting, operational simulation and Admin ML Studio. "
                              "Every epidemiological value carries its semantics (OBSERVED / FORECAST / SCENARIO / SYNTHETIC).")
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or new_id("req")
        request.state.request_id = rid
        t = time.perf_counter()
        resp = await call_next(request)
        ms = (time.perf_counter() - t) * 1000
        resp.headers["X-Request-ID"] = rid
        resp.headers["Server-Timing"] = f"app;dur={ms:.1f}"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            log.info("%s %s %s %.1fms %s", request.method, request.url.path, resp.status_code, ms, rid)
        return resp

    def err(request: Request, status: int, detail):
        return FastJSON({"error": {"status": status, "detail": detail}, "request_id": getattr(request.state, "request_id", None)},
                            status_code=status)

    @app.exception_handler(HTTPException)
    async def http_exc(request: Request, exc: HTTPException):
        return err(request, exc.status_code, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def val_exc(request: Request, exc: RequestValidationError):
        return err(request, 422, [{"loc": ".".join(map(str, e["loc"])), "msg": e["msg"]} for e in exc.errors()])

    @app.exception_handler(LeakageError)
    async def leak_exc(request: Request, exc: LeakageError):
        return err(request, 422, f"point-in-time violation: {exc}")

    @app.exception_handler(CardinalityError)
    async def card_exc(request: Request, exc: CardinalityError):
        return err(request, 422, f"cardinality violation: {exc}")

    for r in (public.router, scope.router, scenarios.router, admin_data.router, admin_ml.router, ai.router, vn_sandbox.router, vn_calibration.router, vietnam.router, nowcast.router, geo.router, ops.router, sprint.router):
        app.include_router(r, prefix="/api/v1")

    if WEB.exists():
        app.mount("/static", StaticFiles(directory=WEB), name="static")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            f = (WEB / path).resolve()
            if path and f.is_file() and WEB in f.parents:
                return FileResponse(f)
            return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})
    return app


app = create_app()
