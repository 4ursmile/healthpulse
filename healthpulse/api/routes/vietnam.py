"""Viet Nam API: province geometry, current risk state and alerts, monthly timeline, simulator, LLM analysis (SSE)."""
from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...core.util import new_id, write_json_atomic
from ...config import get_settings
from ...llm import analyst_vn, client as llm
from ...vietnam import data as vdata, model, sim
from ..deps import envelope, require

router = APIRouter(prefix="/vn", tags=["vietnam"])
_runs: OrderedDict[str, dict] = OrderedDict()
_runs_lock = threading.Lock()
_llm_calls: deque[float] = deque(maxlen=60)


def _need():
    if not vdata.is_built():
        raise HTTPException(409, "Viet Nam data not built yet: run `hp vn-fetch` (or restart `python server.py`)")
    try:
        model.model_info()
    except FileNotFoundError:
        raise HTTPException(409, "Viet Nam model not fitted: run `hp vn-fetch --build-only`")


@router.get("/status")
def status(request: Request):
    built = vdata.is_built()
    out = {"built": built, "llm": {k: v for k, v in llm.config().items() if k != "missing"} | {"missing": llm.config()["missing"]}}
    if built:
        i = model.model_info()
        out |= {"manifest": model.manifest(), "model": {k: v for k, v in i.items() if k != "national_calibration"}}
    return envelope(out, request)


@router.get("/geometry", include_in_schema=False)
def geometry(request: Request):
    _need()
    p = vdata.gold_dir() / "provinces.geojson"
    etag = f'"{int(p.stat().st_mtime)}-{p.stat().st_size}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(model.geometry_bytes(), media_type="application/geo+json",
                    headers={"Cache-Control": "public, max-age=3600", "ETag": etag})


@router.get("/provinces")
def provinces(request: Request):
    _need()
    return envelope(model.provinces(), request, meta={"basis": {
        "population": "WorldPop via HeiGIT/HDX (OBSERVED estimate)", "hospitals_osm": "OpenStreetMap counts via HeiGIT",
        "doctors_est/nurses_est/beds_est": "WHO national density × provincial population (ESTIMATE)"}})


@router.get("/state")
def state(request: Request, month: str | None = None):
    _need()
    try:
        st = model.state(month)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"no estimate for {month}: {e}")
    i = model.model_info()
    warn = []
    if st["month"] > i["latest_weather_month"]:
        warn.append(f"Estimate for {st['month'][:7]} uses climate up to {i['latest_weather_month'][:7]} (1-month lead).")
    return envelope(st, request, meta={"semantics": "MODEL_ESTIMATE", "kappa_basis": i["kappa_basis"],
                                       "method": i["method"], "levels": {
                                           "alert": "≥1.5× seasonal norm and ≥10 per 100k", "warning": "≥1.25× norm and ≥5 per 100k",
                                           "watch": "≥1.1× norm or ≥25 per 100k", "normal": "otherwise"}}, warnings=warn)


@router.get("/timeline")
def timeline(request: Request):
    _need()
    return envelope(model.timeline(), request, meta={"observed_until": "2010-12", "note":
        "1997-2010 OBSERVED with leave-one-year-out endemic-channel alerts; 2011+ MODEL ESTIMATE (climate model × national calibration)"})


@router.get("/provinces/{pid}/series")
def province_series(pid: str, request: Request):
    _need()
    if pid not in {p["province_id"] for p in model.provinces()}:
        raise HTTPException(404, "unknown province")
    return envelope(model.province_series(pid), request)


@router.get("/national")
def national(request: Request):
    _need()
    i = model.model_info()
    return envelope(model.national_series() | {"calibration": i["national_calibration"]}, request,
                    meta={"source": "OpenDengue V1.3 (WHO WPRO / literature), OBSERVED"})


@router.post("/simulate")
def simulate(spec: sim.SimSpec, request: Request):
    _need()
    t = time.perf_counter()
    res = sim.run(spec)
    sid = new_id("vnsim")
    res["sim_id"] = sid
    with _runs_lock:
        _runs[sid] = res
        while len(_runs) > 30:
            _runs.popitem(last=False)
    d = get_settings().artifacts_dir / "vn_sim"
    d.mkdir(parents=True, exist_ok=True)
    write_json_atomic(d / f"{sid}.json", {"spec": res["spec"], "kpi_baseline": res["baseline"]["kpi"], "kpi_plan": res["plan"]["kpi"],
                                          "avoided": res["avoided_unmet_bed_days"], "start_month": res["start_month"]})
    return envelope(res, request, meta={"semantics": "SCENARIO", "ms": round((time.perf_counter() - t) * 1000)})


class AnalyzeReq(BaseModel):
    kind: str = Field("dashboard", pattern="^(dashboard|simulation|sandbox)$")
    sim_id: str | None = None
    sandbox_id: str | None = None
    month: str | None = None
    question: str | None = Field(None, max_length=2000)
    lang: str = Field("en", pattern="^(en|vi)$")


@router.post("/analyze")
def analyze(req: AnalyzeReq, request: Request):
    _need()
    rule_text = None
    if req.kind == "sandbox":
        from ...vietnam import advisor, sandbox as sb
        s = sb.get(req.sandbox_id or "")
        if not s:
            raise HTTPException(404, "sandbox session expired")
        ctx = advisor.context(s, req.lang)
        rule_text = advisor.summary_rules(s, advisor.rule_actions(s), req.lang == "vi") + (
            "\n\n_Tóm tắt theo quy tắc — AI chưa sẵn sàng._" if req.lang == "vi" else "\n\n_Rule-based brief — AI not available._")
    elif req.kind == "simulation":
        with _runs_lock:
            res = _runs.get(req.sim_id or "")
        if not res:
            raise HTTPException(404, "simulation not found (run it again)")
        ctx = analyst_vn.sim_context(res, req.lang)
    else:
        nat = model.national_series()
        recent = (nat["weekly"] or nat["monthly"])[-8:]
        ctx = analyst_vn.dashboard_context(model.state(req.month), model.model_info(), recent, req.lang)
    cfg = llm.config()
    use = cfg["available"]
    rb = (lambda: rule_text) if rule_text else (lambda: analyst_vn.rule_based(ctx))
    now = time.time()
    if use:
        recent_calls = [x for x in _llm_calls if now - x < 60]
        if len(recent_calls) >= 20:
            raise HTTPException(429, "too many analyses in the last minute")
        _llm_calls.append(now)

    def gen():
        meta = {"provider": cfg["provider"] if use else "rule_based", "model": cfg["model"] if use else None, "context": ctx,
                "why_rules": None if use else ("disabled by admin" if not cfg["enabled"] else "not configured" if not cfg["configured"]
                                                else "cooling down after an error")}
        yield f"event: meta\ndata: {json.dumps(meta, ensure_ascii=False, default=str)}\n\n"
        if not use:
            yield f"data: {json.dumps({'t': rb()}, ensure_ascii=False)}\n\n"
        else:
            try:
                for chunk in llm.stream(analyst_vn.SYSTEM, analyst_vn.user_prompt(ctx, req.question)):
                    yield f"data: {json.dumps({'t': chunk}, ensure_ascii=False)}\n\n"
            except llm.LLMError as e:
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                yield f"data: {json.dumps({'t': rb()}, ensure_ascii=False)}\n\n"
        yield "event: done\ndata: {}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


_refresh = {"running": False, "last": None, "error": None}


@router.post("/refresh")
def refresh(request: Request, a=Depends(require("source.ingest"))):
    """Re-download weather (and any missing source), rebuild Gold and refit. Runs in the background."""
    if _refresh["running"]:
        raise HTTPException(409, "refresh already running")

    def job():
        _refresh.update(running=True, error=None)
        try:
            vdata.fetch_all(say=lambda *_: None)
            vdata.build(say=lambda *_: None)
            _refresh["last"] = time.time()
        except Exception as e:  # noqa: BLE001
            _refresh["error"] = str(e)
        finally:
            _refresh["running"] = False
    threading.Thread(target=job, daemon=True).start()
    return envelope({"started": True}, request)


@router.get("/refresh")
def refresh_status(request: Request):
    return envelope(_refresh, request)
