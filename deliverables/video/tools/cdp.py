"""Minimal Chrome DevTools driver: launch headless Chrome, navigate, run JS, screenshot, record screencast frames."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
BASE = os.environ.get("HP_URL", "http://127.0.0.1:18880")


class Browser:
    def __init__(self, w=1920, h=1080, port=9333):
        self.w, self.h, self.port = w, h, port
        self.prof = tempfile.mkdtemp(prefix="hpvid-")
        self.proc = subprocess.Popen([
            CHROME, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={self.prof}",
            f"--window-size={w},{h}", "--enable-gpu", "--use-angle=metal", "--hide-scrollbars",
            "--force-device-scale-factor=1", "--no-first-run", "--no-default-browser-check", "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ws = None
        self.mid = 0
        self.pending: dict[int, asyncio.Future] = {}
        self.handlers = []

    async def start(self):
        for _ in range(100):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json"))
                page = next(t for t in tabs if t["type"] == "page")
                break
            except Exception:
                await asyncio.sleep(0.1)
        self.ws = await websockets.connect(page["webSocketDebuggerUrl"], max_size=2**28)
        asyncio.create_task(self._reader())
        await self.send("Page.enable")
        await self.send("Runtime.enable")
        await self.send("Emulation.setDeviceMetricsOverride", width=self.w, height=self.h, deviceScaleFactor=1, mobile=False)
        return self

    async def _reader(self):
        async for raw in self.ws:
            m = json.loads(raw)
            if "id" in m and m["id"] in self.pending:
                self.pending.pop(m["id"]).set_result(m)
            elif "method" in m:
                for h in list(self.handlers):
                    h(m)

    async def send(self, method, **params):
        self.mid += 1
        fut = asyncio.get_event_loop().create_future()
        self.pending[self.mid] = fut
        await self.ws.send(json.dumps({"id": self.mid, "method": method, "params": params}))
        m = await asyncio.wait_for(fut, 60)
        if "error" in m:
            raise RuntimeError(f"{method}: {m['error']}")
        return m.get("result", {})

    async def goto(self, path, wait=2.5):
        url = path if path.startswith(("http", "file:", "about:")) else BASE + path
        await self.send("Page.navigate", url=url)
        await asyncio.sleep(wait)

    async def js(self, expr, await_promise=True):
        r = await self.send("Runtime.evaluate", expression=expr, awaitPromise=await_promise, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", r["exceptionDetails"]))
        return r.get("result", {}).get("value")

    async def shot(self, path):
        r = await self.send("Page.captureScreenshot", format="png")
        Path(path).write_bytes(base64.b64decode(r["data"]))

    async def record_start(self, outdir):
        out = Path(outdir)
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        self.frames = []
        self._rec_dir = out

        def on(m):
            if m["method"] == "Page.screencastFrame":
                p = m["params"]
                i = len(self.frames)
                fn = out / f"f{i:05d}.jpg"
                fn.write_bytes(base64.b64decode(p["data"]))
                self.frames.append((time.time(), fn.name))
                asyncio.create_task(self.send("Page.screencastFrameAck", sessionId=p["sessionId"]))

        self._on = on
        self.handlers.append(on)
        await self.send("Page.startScreencast", format="jpeg", quality=92, maxWidth=self.w, maxHeight=self.h, everyNthFrame=1)
        self.t0 = time.time()

    async def record_stop(self):
        await self.send("Page.stopScreencast")
        t1 = time.time()
        self.handlers.remove(self._on)
        # concat list with real durations so playback matches wall-clock
        lines = []
        fr = self.frames
        for i, (t, fn) in enumerate(fr):
            nxt = fr[i + 1][0] if i + 1 < len(fr) else t1
            start = self.t0 if i == 0 else t   # first frame covers the gap from record start
            lines.append(f"file '{fn}'\nduration {max(nxt - start, 0.001):.4f}")
        if fr:
            lines.append(f"file '{fr[-1][1]}'")
        (self._rec_dir / "list.txt").write_text("\n".join(lines))
        return t1 - self.t0

    async def close(self):
        try:
            await self.ws.close()
        except Exception:
            pass
        self.proc.terminate()
        shutil.rmtree(self.prof, ignore_errors=True)
