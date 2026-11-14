"""Download a finished qwen-tts job's files via the same MCP server Claude Code is configured with (read_file_chunk tool).
Usage: TTS_TOKEN=... fetch_tts.py JOB OUTDIR MCP_URL (token from the environment; never printed)."""
import base64
import json
import sys
from pathlib import Path

import httpx

job, out = sys.argv[1], Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
import os
tok = os.environ["TTS_TOKEN"]
srv = {"url": sys.argv[3]}
H = {"Authorization": tok if tok.lower().startswith("bearer ") else "Bearer " + tok, "Accept": "application/json, text/event-stream", "Content-Type": "application/json",
     "ngrok-skip-browser-warning": "1"}
c = httpx.Client(timeout=120)
mid = 0


def rpc(method, params=None, notify=False):
    global mid
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if not notify:
        mid += 1
        body["id"] = mid
    r = c.post(srv["url"], headers=H, json=body)
    r.raise_for_status()
    if "mcp-session-id" in r.headers:
        H["Mcp-Session-Id"] = r.headers["mcp-session-id"]
    if notify:
        return None
    txt = r.text
    if r.headers.get("content-type", "").startswith("text/event-stream"):
        data = [l[5:].strip() for l in txt.splitlines() if l.startswith("data:")]
        msgs = [json.loads(d) for d in data if d]
        msg = next(m for m in msgs if m.get("id") == mid)
    else:
        msg = r.json()
    if "error" in msg:
        raise RuntimeError(msg["error"])
    return msg["result"]


def tool(name, **args):
    res = rpc("tools/call", {"name": name, "arguments": args})
    if res.get("structuredContent"):
        sc = res["structuredContent"]
        return sc.get("result", sc)
    return json.loads(res["content"][0]["text"])


rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "tts-fetch", "version": "1"}})
rpc("notifications/initialized", notify=True)
info = tool("get_download_info", job_id=job)
for f in info["files"]:
    fn = f["filename"]
    buf, off = bytearray(), 0
    while True:
        ch = tool("read_file_chunk", job_id=job, filename=fn, offset=off)
        buf += base64.b64decode(ch.get("base64") or ch.get("data") or ch.get("chunk") or "")
        if ch.get("eof") or ch.get("next_offset") in (None, off):
            break
        off = ch["next_offset"]
    (out / fn).write_bytes(buf)
    print(fn, len(buf), "of", f["bytes"])
