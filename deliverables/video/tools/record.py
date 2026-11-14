"""Record each scene as a screencast (cards from cards.html, app scenes from the private HealthPulse instance).
Usage: record.py [scene_id ...]   -> ../clips/<scene>/ frames + list.txt"""
import asyncio
import json
import os
import pathlib
import sys

os.environ.setdefault("HP_URL", "http://127.0.0.1:18890")
from cdp import Browser  # noqa: E402

TIMING = {s["id"]: s["dur"] for s in json.load(open("../audio/timing.json"))["scenes"]}
EXTRA = 1.3            # recorded past the narration (covers the gap + crossfade)
CARDS = pathlib.Path("cards/cards.html").resolve().as_uri()
OVERLAY = open("overlay.js").read()
INC = open("/tmp/hpvid/inc").read().strip()
J = json.dumps


class Scene:
    def __init__(self, b):
        self.b = b

    async def d(self, method, *args):
        r = await self.b.js(f"window.__demo.{method}({','.join(J(a) for a in args)})")
        if isinstance(r, str) and r.startswith("NOTFOUND"):
            print("   !", method, r)
        return r

    async def inject(self):
        await self.b.js(OVERLAY)


async def run_timeline(sc, steps, total):
    """steps: list of (t, async fn). Waits until each t (relative to recording start), runs it, then waits to `total`."""
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    for t, fn in steps:
        dt = t - (loop.time() - t0)
        if dt > 0:
            await asyncio.sleep(dt)
        await fn()
    rest = total - (loop.time() - t0)
    if rest > 0:
        await asyncio.sleep(rest)


# ---------------------------------------------------------------- scenes
def card(name, extra=""):
    async def prep(b, sc):
        await b.goto("about:blank", 0.3)
    async def go(b, sc, D):
        await b.goto(f"{CARDS}?card={name}{extra}", 0)   # navigation inside the recording so the animation is captured from 0
        return []
    return prep, go


async def story_prep(b, sc, step=None):
    await b.goto("/story?scope=br#as=epi_reviewer", 6)
    await sc.inject()


def s04():
    async def prep(b, sc):
        await story_prep(b, sc)
        await sc.d("move", {"x": 1180, "y": 600}, 0)
    async def go(b, sc, D):
        d = sc.d
        return [
            (0.6, lambda: d("move", {"text": "Dengue early warning, end to end", "tags": "h1,h2"}, 900, 0.3)),
            (1.0, lambda: d("ring", "a", {"text": "Dengue early warning, end to end", "tags": "h1,h2"}, 10)),
            (2.6, lambda: d("unring", "a")),
            (2.7, lambda: d("move", {"text": "1 · Would it have warned us?", "tags": "button"}, 800)),
            (3.1, lambda: d("ring", "a", {"text": "1 · Would it have warned us?", "tags": "button"}, 5)),
            (5.0, lambda: d("unring", "a")),
            (5.2, lambda: d("move", {"text": "Brazil's 2023-24 dengue season was the largest", "tags": "p,div"}, 900, 0.6)),
            (5.6, lambda: d("ring", "a", {"text": "Brazil's 2023-24 dengue season was the largest", "tags": "p,div"}, 10)),
            (10.6, lambda: d("unring", "a")),
            (10.8, lambda: d("move", {"text": "Epidemic states warned early", "up": 1}, 900)),
            (11.2, lambda: d("ring", "a", {"text": "Epidemic states warned early", "up": 1}, 2)),
            (13.4, lambda: d("move", {"text": "Median warning gained", "up": 1}, 800)),
            (13.7, lambda: d("ring", "a", {"text": "Median warning gained", "up": 1}, 2)),
            (16.6, lambda: d("move", {"text": "flat carry-forward rule: 4 weeks"}, 700)),
            (16.9, lambda: d("ring", "b", {"text": "flat carry-forward rule: 4 weeks"}, 6)),
            (19.6, lambda: d("unring", None)),
            (19.8, lambda: d("move", {"text": "HealthPulse shifted climatology", "tags": "td"}, 900)),
        ]
    return prep, go


def s05():
    async def prep(b, sc):
        await story_prep(b, sc)
        await sc.d("move", {"text": "1 · Would it have warned us?", "tags": "button"}, 0)
    async def go(b, sc, D):
        d = sc.d
        geo = {}

        async def measure():
            g = await b.js("""(()=>{const t=[...document.querySelectorAll('svg text')];
              const a=t.find(x=>x.textContent.includes('HealthPulse alert')), s=t.find(x=>x.textContent.includes('status quo'));
              if(!a||!s) return null; const ra=a.getBoundingClientRect(), rs=s.getBoundingClientRect(), sv=a.ownerSVGElement.getBoundingClientRect();
              return {ax:ra.left-4, sx:rs.left-4, top:sv.top+8, h:sv.height-40, ay:ra.top+ra.height/2, sy:rs.top+rs.height/2}})()""")
            geo.update(g or {})
            print("   chart", geo)
        return [
            (0.2, lambda: d("move", {"text": "2 · Replay 2023-24", "tags": "button"}, 700)),
            (0.95, lambda: d("click", {"text": "2 · Replay 2023-24", "tags": "button"})),
            (2.0, measure),
            (2.3, lambda: d("move", {"x": geo.get("ax", 545), "y": geo.get("ay", 315) + 120}, 900)),
            (2.7, lambda: d("ring", "a", {"text": "HealthPulse alert", "tags": "text"}, 8)),
            (5.6, lambda: d("unring", "a")),
            (5.7, lambda: d("move", {"x": geo.get("sx", 697), "y": geo.get("sy", 342) + 120}, 900)),
            (6.1, lambda: d("ring", "a", {"text": "status quo sees it", "tags": "text"}, 8)),
            (8.4, lambda: d("unring", "a")),
            (8.5, lambda: d("band", geo.get("ax", 545), geo.get("sx", 697), geo.get("top", 305), geo.get("h", 240), "≈ 6 weeks to prepare")),
            (8.6, lambda: d("move", {"x": (geo.get("ax", 545) + geo.get("sx", 697)) / 2, "y": geo.get("top", 305) + 150}, 900)),
        ]
    return prep, go


def s06():
    async def prep(b, sc):
        await story_prep(b, sc)
        await b.js("[...document.querySelectorAll('button')].find(x=>x.textContent.includes('2 · Replay')).click()")
        await asyncio.sleep(2)
        await sc.d("move", {"text": "2 · Replay 2023-24", "tags": "button"}, 0)
    async def go(b, sc, D):
        d = sc.d
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        FOLLOW = "(()=>{const m=document.querySelector('main.sheet');const h=[...document.querySelectorAll('h3,b,strong,p,li')].filter(e=>e.getBoundingClientRect().height>0).pop();if(!h)return 0;const r=h.getBoundingClientRect();return Math.max(0,r.bottom-860)})()"

        async def follow():
            dy = await b.js(FOLLOW)
            if dy and dy > 20:
                await d("scroll", dy, 1300)

        async def wait_ready():
            # brief streams from the model; approve is enabled only when it is complete
            while loop.time() - t0 < D - 3.2:
                ok = await b.js("(()=>{const x=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='Approve proposal');return !!x && !x.disabled})()")
                if ok:
                    break
                await follow()
                await asyncio.sleep(0.9)
            print("   brief ready at", round(loop.time() - t0, 1))
            await d("scrollTo", 99999, 1100)
            await asyncio.sleep(1.2)
            await d("ring", "a", {"text": "Human decision", "tags": "b,strong", "up": 1}, 6)
            await d("move", {"text": "Approve proposal", "tags": "button"}, 800)
            await asyncio.sleep(1.1)
            await d("click", {"text": "Approve proposal", "tags": "button"})
            await asyncio.sleep(0.8)
            await d("unring", "a")
        return [
            (0.3, lambda: d("move", {"text": "3 · Next season", "tags": "button"}, 600)),
            (0.95, lambda: d("click", {"text": "3 · Next season", "tags": "button"})),
            (1.9, lambda: d("move", {"x": 1180, "y": 420}, 900)),
            (2.2, lambda: d("ring", "a", {"text": "Forecast made with data to EW25 2026"}, 8)),
            (3.6, lambda: d("unring", "a")),
            (3.7, lambda: d("move", {"text": "4 · Brief & decide", "tags": "button"}, 600)),
            (4.35, lambda: d("click", {"text": "4 · Brief & decide", "tags": "button"})),
            (5.6, lambda: d("move", {"text": "AI ·", "tags": "span,div"}, 800)),
            (5.9, lambda: d("ring", "a", {"text": "AI ·", "tags": "span,div"}, 6)),
            (7.6, lambda: d("unring", "a")),
            (7.7, lambda: d("move", {"x": 760, "y": 640}, 1000)),
            (8.6, follow), (10.4, follow), (12.2, follow),
            (13.5, wait_ready),
        ]
    return prep, go


def s07():
    async def prep(b, sc):
        await b.goto("/vn?scope=vn#as=epi_reviewer", 8)
        await sc.inject()
        await sc.d("move", {"x": 700, "y": 760}, 0)
    async def go(b, sc, D):
        d = sc.d
        return [
            (0.4, lambda: d("move", {"x": 720, "y": 640}, 1200)),
            (2.0, lambda: d("ring", "a", {"text": "Nov 2026", "tags": "h2,h3,div", "up": 2}, 0)),
            (3.0, lambda: d("move", {"text": "Weather to 2026-10"}, 900)),
            (3.3, lambda: d("ring", "b", {"text": "Weather to 2026-10"}, 6)),
            (4.6, lambda: d("unring", None)),
            (4.7, lambda: d("move", {"text": "Cases per 100k", "tags": "button"}, 700)),
            (5.3, lambda: d("click", {"text": "Cases per 100k", "tags": "button"})),
            (7.0, lambda: d("move", {"text": "Provinces on alert / warning", "up": 1}, 900)),
            (7.3, lambda: d("ring", "a", {"text": "Provinces on alert / warning", "up": 1}, 2)),
            (8.8, lambda: d("move", {"text": "Alert level", "tags": "button"}, 800)),
            (9.4, lambda: d("click", {"text": "Alert level", "tags": "button"})),
            (9.6, lambda: d("unring", "a")),
            (9.8, lambda: d("move", {"text": "Needs attention", "tags": "h2,h3,div,span"}, 900)),
            (10.1, lambda: d("ring", "a", {"text": "Needs attention", "tags": "h2,h3,div,span", "up": 1}, 0)),
            (11.6, lambda: d("unring", "a")),
            (11.7, lambda: d("move", {"text": "22.4 est. cases per 100k this month"}, 900, 0.4)),
            (12.0, lambda: d("ring", "a", {"text": "22.4 est. cases per 100k this month", "up": 1}, 2)),
            (13.6, lambda: d("move", {"text": "1.69× its seasonal norm", "tags": "p,div,span"}, 900, 0.85)),
            (15.4, lambda: d("unring", "a")),
            (15.5, lambda: d("move", {"text": "Model estimate", "tags": "span"}, 900)),
            (15.8, lambda: d("ring", "a", {"text": "Model estimate", "tags": "span"}, 5)),
            (17.6, lambda: d("move", {"text": "Observed", "tags": "span"}, 900)),
            (17.9, lambda: d("ring", "b", {"text": "Observed", "tags": "span"}, 5)),
            (20.0, lambda: d("unring", None)),
        ]
    return prep, go


def s08():
    async def prep(b, sc):
        await b.goto("/vn/sandbox?scope=vn#as=epi_reviewer", 7)
        await b.js("[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='Start outbreak').click()")
        await asyncio.sleep(9)
        await b.js("(()=>{const x=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='8 d/s'); x&&x.click()})()")
        await sc.inject()
        await sc.d("move", {"x": 1100, "y": 760}, 0)
    async def go(b, sc, D):
        d = sc.d
        return [
            (0.3, lambda: d("ring", "a", {"text": "Day 0", "tags": "h2,h3,div,span", "up": 2}, 0)),
            (1.2, lambda: d("move", {"text": "Seed", "tags": "button"}, 700)),
            (1.9, lambda: d("click", {"text": "Seed", "tags": "button"})),
            (2.1, lambda: d("unring", "a")),
            (2.2, lambda: d("move", {"x": 1205, "y": 760}, 700)),
            (2.95, lambda: d("click", {"x": 1205, "y": 760})),
            (3.3, lambda: d("scroll", 300, 800)),
            (4.0, lambda: d("move", {"sel": "button.play"}, 700)),
            (4.75, lambda: d("click", {"sel": "button.play"})),
            (5.4, lambda: d("move", {"text": "Distancing", "tags": "button"}, 600)),
            (5.6, lambda: d("ring", "a", {"text": "Distancing", "tags": "button"}, 4)),
            (6.4, lambda: d("move", {"text": "Vaccinate", "tags": "button"}, 500)),
            (6.5, lambda: d("ring", "a", {"text": "Vaccinate", "tags": "button"}, 4)),
            (7.3, lambda: d("move", {"text": "Vector control", "tags": "button"}, 500)),
            (7.4, lambda: d("ring", "a", {"text": "Vector control", "tags": "button"}, 4)),
            (8.2, lambda: d("unring", "a")),
            (8.3, lambda: d("scrollTo", 0, 700)),
            (9.0, lambda: d("move", {"text": "In hospital", "up": 1}, 700)),
            (9.3, lambda: d("ring", "a", {"text": "In hospital", "up": 1}, 2)),
            (10.4, lambda: d("ring", "b", {"text": "16 trials", "tags": "span,p,div"}, 4)),
        ]
    return prep, go


ANS_SCROLLER = """(()=>[...document.querySelectorAll('*')].find(e=>e.getBoundingClientRect().left>1300&&e.scrollHeight>e.clientHeight+30&&/auto|scroll/.test(getComputedStyle(e).overflowY)))()"""


def s09():
    async def prep(b, sc):
        await b.goto(f"/vn/ops?scope=vn&inc={INC}#as=epi_reviewer", 9)
        await b.js("[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='AI analyst').click()")
        await asyncio.sleep(1)
        await b.js("[...document.querySelectorAll('button')].find(x=>x.textContent.trim().startsWith('What is the situation')).click()")
        t0 = asyncio.get_event_loop().time()
        await asyncio.sleep(8)
        while asyncio.get_event_loop().time() - t0 < 150:
            done = await b.js("(()=>{const a=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='Ask');return !!a && !!document.body.innerText.match(/proposals wait for a human decision/)})()")
            if done:
                break
            await asyncio.sleep(2)
        print("   analyst answered in", round(asyncio.get_event_loop().time() - t0), "s")
        await asyncio.sleep(1.5)
        print("   scroller", await b.js("(()=>{const e=" + ANS_SCROLLER + "; if(e) e.scrollTop=0; return !!e})()"))
        await sc.inject()
        await sc.d("move", {"x": 900, "y": 640}, 0)
    async def go(b, sc, D):
        d = sc.d

        async def scroll_answer():
            await b.js("(()=>{const e=" + ANS_SCROLLER + "; if(!e) return; const y0=e.scrollTop, dy=Math.min(520, e.scrollHeight-e.clientHeight), t0=performance.now();"
                       "const st=(t)=>{const p=Math.min(1,(t-t0)/3200); e.scrollTop=y0+dy*(p<.5?2*p*p:1-Math.pow(-2*p+2,2)/2); if(p<1) requestAnimationFrame(st)}; requestAnimationFrame(st)})()")
        e = D - 1.3          # narration end
        return [
            (0.3, lambda: d("move", {"text": "Dengue response drill", "tags": "h1,h2,span"}, 900)),
            (0.6, lambda: d("ring", "a", {"text": "Dengue response drill", "tags": "h1,h2", "up": 0}, 8)),
            (3.0, lambda: d("unring", "a")),
            (3.1, lambda: d("move", {"text": "Confirmed", "up": 1}, 800)),
            (3.4, lambda: d("ring", "a", {"text": "Confirmed", "up": 1}, 2)),
            (4.9, lambda: d("unring", "a")),
            (5.0, lambda: d("move", {"text": "C2 ·", "tags": "div,span"}, 800)),
            (5.3, lambda: d("ring", "a", {"text": "C2 ·", "tags": "div,span"}, 10)),
            (6.1, lambda: d("ring", "b", {"text": "C3 ·", "tags": "div,span"}, 10)),
            (7.0, lambda: d("unring", None)),
            (7.1, lambda: d("move", {"text": "Tool-using analyst", "tags": "span,div"}, 900)),
            (7.4, lambda: d("ring", "a", {"text": "Tool-using analyst", "tags": "span,div", "up": 1}, 2)),
            (7.9, lambda: d("move", {"x": 1640, "y": 700}, 900)),
            (8.4, scroll_answer),
            (e - 4.4, lambda: d("unring", "a")),
            (e - 4.3, lambda: d("move", {"text": "Resources", "tags": "button"}, 600)),
            (e - 3.65, lambda: d("click", {"text": "Resources", "tags": "button"})),
            (e - 3.2, lambda: d("move", {"text": "Run optimiser", "tags": "button"}, 600)),
            (e - 2.55, lambda: d("click", {"text": "Run optimiser", "tags": "button"})),
            (e - 1.7, lambda: d("scroll", 230, 700)),
            (e - 0.9, lambda: d("move", {"text": "Approve for dispatch", "tags": "button"}, 600)),
            (e - 0.6, lambda: d("ring", "a", {"text": "Approve for dispatch", "tags": "button"}, 5)),
            (e + 0.1, lambda: d("click", {"text": "Approve for dispatch", "tags": "button"})),
        ]
    return prep, go


SCENES = {
    "s01_intro": card("intro"), "s02_problem": card("problem"), "s03_how": card("how"),
    "s04_warned": s04(), "s05_replay": s05(), "s06_brief": s06(), "s07_vietnam": s07(), "s08_sandbox": s08(), "s09_ops": s09(),
    "s10_evidence": card("evidence"), "s11_vision": card("vision", "&end=10.9"),
}


async def main(ids):
    b = await Browser(port=9340).start()
    sc = Scene(b)
    try:
        for sid in ids:
            prep, go = SCENES[sid]
            D = TIMING[sid] + EXTRA + (2.6 if sid == "s11_vision" else 0)
            print(f"== {sid}  {D:.2f}s")
            await prep(b, sc)
            await b.record_start(f"../clips/{sid}")
            steps = await go(b, sc, D)
            await run_timeline(sc, steps, D)
            real = await b.record_stop()
            print(f"   frames {len(b.frames)}  span {real:.2f}s  fps {len(b.frames) / max(real, 0.01):.1f}")
    finally:
        await b.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or list(SCENES)))
