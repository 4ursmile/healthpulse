"""Streaming text generation for the grounded analyst: Claude (official SDK) or OpenAI-compatible (HTTP)."""
from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator

import httpx

DEFAULT_CLAUDE = "claude-opus-5-5"


class LLMError(RuntimeError):
    pass


SUGGESTED = {
    "anthropic": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"],
    "openai": [],
}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
_health = {"ok_at": None, "error": None, "error_at": None}
COOLDOWN_S = 60  # after a failure, use the rule-based fallback straight away for this long


def admin_overrides() -> dict:
    """Admin choices stored in settings_kv['llm'] (provider, model, effort, base_url, enabled). Never holds keys."""
    try:
        from ..core import db
        from ..core.util import loads
        r = db.q1("SELECT value_json FROM settings_kv WHERE key='llm'")
        return loads(r["value_json"]) if r else {}
    except Exception:  # noqa: BLE001 - no database yet (tests, CLI): env only
        return {}


def config() -> dict:
    ov = admin_overrides()
    prov = (ov.get("provider") or os.environ.get("HP_LLM_PROVIDER", "auto")).strip().lower()
    a_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    o_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if prov == "auto":
        prov = "anthropic" if a_key else "openai" if o_key else "none"
    model = (ov.get("model") or os.environ.get("HP_LLM_MODEL", "")).strip()
    if prov == "anthropic":
        model = model or DEFAULT_CLAUDE
        ok = bool(a_key)
    elif prov == "openai":
        ok = bool(o_key) and bool(model)
    else:
        ok = False
    enabled = ov.get("enabled", True)
    missing = None if ok else ("HP_LLM_MODEL" if prov == "openai" and o_key and not model else
                               "ANTHROPIC_API_KEY" if prov == "anthropic" else
                               "OPENAI_API_KEY" if prov == "openai" else "ANTHROPIC_API_KEY or OPENAI_API_KEY in .env")
    cooling = bool(_health["error_at"] and time.time() - _health["error_at"] < COOLDOWN_S)
    return {"provider": prov, "model": model or None, "configured": ok and enabled, "enabled": enabled,
            "effort": (ov.get("effort") or os.environ.get("HP_LLM_EFFORT", "medium")).strip().lower(),
            "base_url": (ov.get("base_url") or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/"),
            "keys": {"anthropic": bool(a_key), "openai": bool(o_key)}, "source": "admin" if ov else "env",
            "missing": None if ok else missing, "available": ok and enabled and not cooling,
            "health": dict(_health)}


def _mark(ok: bool, err: str | None = None):
    if ok:
        _health.update(ok_at=time.time(), error=None, error_at=None)
    else:
        _health.update(error=err, error_at=time.time())


def stream(system: str, user: str, max_tokens: int = 8000) -> Iterator[str]:
    c = config()
    if not c["configured"]:
        raise LLMError("LLM disabled by an admin" if not c["enabled"] else f"LLM not configured: set {c['missing']}")
    try:
        gen = _claude(system, user, c, max_tokens) if c["provider"] == "anthropic" else _openai(system, user, c, max_tokens)
        yield from gen
        _mark(True)
    except LLMError as e:
        _mark(False, str(e))
        raise


def complete(system: str, user: str, max_tokens: int = 8000) -> str:
    return "".join(stream(system, user, max_tokens))


def list_models() -> list[str]:
    """Models the configured key can use (live from the provider), falling back to the suggested list."""
    c = config()
    try:
        if c["provider"] == "anthropic" and c["keys"]["anthropic"]:
            import anthropic
            return [m.id for m in anthropic.Anthropic(max_retries=0, timeout=10.0).models.list(limit=100)]
        if c["provider"] == "openai" and c["keys"]["openai"]:
            r = httpx.get(f"{c['base_url']}/models", headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY'].strip()}"}, timeout=10)
            r.raise_for_status()
            return sorted(m["id"] for m in r.json().get("data", []))
    except Exception:  # noqa: BLE001 - listing is a convenience; the suggested list still works
        pass
    return SUGGESTED.get(c["provider"], [])


def test() -> dict:
    t = time.time()
    try:
        out = complete("Reply with the single word: ready", "ping", max_tokens=2000)
        return {"ok": True, "reply": out.strip()[:80], "ms": round((time.time() - t) * 1000)}
    except LLMError as e:
        return {"ok": False, "error": str(e), "ms": round((time.time() - t) * 1000)}


def _claude(system: str, user: str, c: dict, max_tokens: int) -> Iterator[str]:
    import anthropic
    client = anthropic.Anthropic(max_retries=2, timeout=180.0)
    kw = dict(model=c["model"], max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}])
    if c["effort"] in ("low", "medium", "high", "xhigh", "max"):
        kw["output_config"] = {"effort": c["effort"]}
    use_fallback = os.environ.get("HP_LLM_FALLBACK", "1") == "1" and c["model"].startswith(("claude-opus-5", "claude-fable-5", "claude-sonnet-5-5"))
    try:
        if use_fallback:
            # server-side refusal fallback: a declined request is re-run on a fallback model inside the same call
            ctx = client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw)
        else:
            ctx = client.messages.stream(**kw)
        with ctx as s:
            for text in s.text_stream:
                yield text
            final = s.get_final_message()
        if final.stop_reason == "refusal":
            yield "\n\n_The model declined this request._"
        elif final.stop_reason == "max_tokens":
            yield "\n\n_(answer truncated at the token limit)_"
    except anthropic.AuthenticationError:
        raise LLMError("Claude rejected the API key (check ANTHROPIC_API_KEY)")
    except anthropic.PermissionDeniedError as e:
        raise LLMError(f"Claude: permission denied ({e.message})")
    except anthropic.NotFoundError:
        raise LLMError(f"Claude: model {c['model']} not found (check HP_LLM_MODEL)")
    except anthropic.RateLimitError:
        raise LLMError("Claude: rate limited, try again shortly")
    except anthropic.BadRequestError as e:
        raise LLMError(f"Claude: bad request ({e.message})")
    except anthropic.APIStatusError as e:
        raise LLMError(f"Claude API error {e.status_code}")
    except anthropic.APIConnectionError:
        raise LLMError("Claude: network error (is api.anthropic.com reachable?)")


def _openai(system: str, user: str, c: dict, max_tokens: int) -> Iterator[str]:
    base = c["base_url"]
    body = {"model": c["model"], "stream": True, "max_completion_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    headers = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY'].strip()}"}
    try:
        with httpx.stream("POST", f"{base}/chat/completions", json=body, headers=headers, timeout=httpx.Timeout(180, connect=15)) as r:
            if r.status_code >= 400:
                r.read()
                raise LLMError(f"OpenAI-compatible API error {r.status_code}: {r.text[:200]}")
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"].get("content")
                except (KeyError, IndexError, json.JSONDecodeError):
                    continue
                if delta:
                    yield delta
    except httpx.HTTPError as e:
        raise LLMError(f"network error: {e}")
