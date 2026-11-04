"""Viet Nam calibration: the report (what was calibrated, with what, what is NOT identified) and the back-test series the
sandbox draws. Read-only; the calibration itself is produced by `hp vn-calibrate`."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ...vietnam import calibration, sandbox as sb
from ..deps import envelope
from .vietnam import _need

router = APIRouter(prefix="/vn/calibration", tags=["vietnam"])

_SUMMARY_DROP = {"series", "profiles", "backtests"}


def _art() -> dict:
    _need()
    c = calibration.load()
    if not c:
        raise HTTPException(404, "NO_CALIBRATION: run `hp vn-calibrate` (needs the Viet Nam data and the WHO COVID-19 download)")
    return c


@router.get("")
def report(request: Request, full: bool = False):
    c = _art()
    stale = calibration.overlay()
    out = {k: v for k, v in c.items() if k not in ("backtests", "covid", "dengue")}
    out["dengue"] = c.get("dengue")
    out["covid"] = {k: ({kk: vv for kk, vv in v.items() if kk != "series"} if not full else v) for k, v in (c.get("covid") or {}).items()}
    out["stale"] = any(v.get("stale") for v in stale.values())
    return envelope(out, request, meta={"semantics": "CALIBRATION", "created_at": c.get("created_at"), "version": c.get("version")},
                    warnings=(["calibrated with a previous version of the sandbox engine: re-run `hp vn-calibrate`"] if out["stale"] else []))


@router.get("/overlay")
def overlay(request: Request):
    _need()
    return envelope({"overlay": sb.CALIBRATION, "literature": {k: {kk: v[kk] for kk in sb.CALIB_KEYS if kk in v} for k, v in sb.LITERATURE.items()}},
                    request, meta={"semantics": "CALIBRATION"})


@router.get("/backtest/{name}")
def backtest(name: str, request: Request):
    _need()
    b = calibration.backtest(name)
    if b is None:
        raise HTTPException(404, f"no back-test named {name}: {', '.join((calibration.backtest() or {}).keys()) or 'none (run hp vn-calibrate)'}")
    return envelope(b, request, meta={"semantics": "CALIBRATION", "note": "in-sample replay of the fitted path unless marked HOLDOUT"})
