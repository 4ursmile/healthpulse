// Injected into the app while recording: a visible cursor, click ripples, highlight rings, smooth scroll helpers.
(() => {
  if (window.__demo) return;
  window.confirm = () => true; window.alert = () => {};
  const css = document.createElement("style");
  css.textContent = `
  #__cur{position:fixed;left:0;top:0;z-index:2147483647;pointer-events:none;will-change:transform;
    transition:transform var(--mv,900ms) cubic-bezier(.45,.05,.2,1);filter:drop-shadow(0 3px 6px rgba(0,0,0,.28))}
  .__rip{position:fixed;z-index:2147483646;pointer-events:none;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:50%;
    border:3px solid rgba(18,105,106,.9);background:rgba(18,105,106,.18);animation:__rip .6s ease-out forwards}
  @keyframes __rip{from{transform:scale(.3);opacity:1}to{transform:scale(1.6);opacity:0}}
  .__ring{position:fixed;z-index:2147483645;pointer-events:none;border-radius:12px;border:3px solid #12696a;
    box-shadow:0 0 0 6px rgba(18,105,106,.16),0 10px 30px rgba(18,105,106,.18);opacity:0;transition:opacity .35s ease, transform .35s ease;transform:scale(1.03)}
  .__ring.on{opacity:1;transform:none}
  .__band{position:fixed;z-index:2147483644;pointer-events:none;background:rgba(18,105,106,.14);border-left:2px dashed #12696a;border-right:2px dashed #c4473a;
    opacity:0;transition:opacity .6s ease}
  .__band.on{opacity:1}
  .__band b{position:absolute;left:50%;top:8px;transform:translateX(-50%);white-space:nowrap;font:600 15px/1 'IBM Plex Sans',system-ui;color:#fff;background:#12696a;padding:7px 11px;border-radius:999px}
  `;
  document.head.appendChild(css);
  const cur = document.createElement("div");
  cur.id = "__cur";
  cur.innerHTML = `<svg width="30" height="30" viewBox="0 0 24 24"><path d="M5 2.5l13.5 9.2-6 1.1 3.6 7-2.8 1.4-3.6-7.1-4.7 4.3z" fill="#141c20" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
  document.body.appendChild(cur);
  let pos = { x: 1200, y: 640 };
  const place = (x, y, ms) => { cur.style.setProperty("--mv", (ms || 0) + "ms"); cur.style.transform = `translate(${x - 5}px,${y - 3}px)`; pos = { x, y }; };
  place(pos.x, pos.y, 0);

  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  function find(q) {
    if (q instanceof Element) return q;
    if (q.sel) {
      const els = [...document.querySelectorAll(q.sel)];
      const t = q.text;
      return t ? (els.find((e) => norm(e.textContent) === t) || els.find((e) => norm(e.textContent).startsWith(t)) || els.find((e) => norm(e.textContent).includes(t))) : els[q.i || 0];
    }
    const t = q.text;
    const pool = [...document.querySelectorAll(q.tags || "button,[role=tab],a,label,h1,h2,h3,p,span,div,td,tr,b,strong,text,select")];
    const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    let c = pool.filter((e) => vis(e) && norm(e.textContent) === t);
    if (!c.length) c = pool.filter((e) => vis(e) && norm(e.textContent).startsWith(t));
    if (!c.length) c = pool.filter((e) => vis(e) && norm(e.textContent).includes(t));
    // smallest matching element = most specific
    c.sort((a, b) => a.getBoundingClientRect().width * a.getBoundingClientRect().height - b.getBoundingClientRect().width * b.getBoundingClientRect().height);
    return c[0];
  }
  const up = (e, n) => { for (let i = 0; i < (n || 0) && e.parentElement; i++) e = e.parentElement; return e; };
  const rectOf = (q) => { let e = find(q); if (!e) return null; e = up(e, q.up); const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height, e }; };

  const rings = {};
  window.__demo = {
    find, rectOf,
    move(q, ms = 900, dx = 0.5, dy = 0.5) {
      const r = q.x != null && q.w == null ? { x: q.x, y: q.y, w: 0, h: 0 } : rectOf(q);
      if (!r) return "NOTFOUND " + JSON.stringify(q);
      place(r.x + r.w * dx, r.y + r.h * dy, ms);
      return "ok";
    },
    click(q) {
      const e = q ? (q.x != null ? document.elementFromPoint(q.x, q.y) : up(find(q), q.up)) : document.elementFromPoint(pos.x, pos.y);
      const rp = document.createElement("div"); rp.className = "__rip"; rp.style.left = pos.x + "px"; rp.style.top = pos.y + "px";
      document.body.appendChild(rp); setTimeout(() => rp.remove(), 700);
      if (!e) return "NOTFOUND";
      if (q && q.x != null) {
        const o = { bubbles: true, cancelable: true, clientX: q.x, clientY: q.y, view: window };
        e.dispatchEvent(new PointerEvent("pointerdown", o)); e.dispatchEvent(new MouseEvent("mousedown", o));
        e.dispatchEvent(new PointerEvent("pointerup", o)); e.dispatchEvent(new MouseEvent("mouseup", o)); e.dispatchEvent(new MouseEvent("click", o));
      } else e.click();
      return "ok:" + norm(e.textContent).slice(0, 30);
    },
    ring(key, q, pad = 8) {
      const r = rectOf(q); if (!r) return "NOTFOUND " + JSON.stringify(q);
      let d = rings[key]; if (!d) { d = rings[key] = document.createElement("div"); d.className = "__ring"; document.body.appendChild(d); }
      Object.assign(d.style, { left: r.x - pad + "px", top: r.y - pad + "px", width: r.w + 2 * pad + "px", height: r.h + 2 * pad + "px" });
      requestAnimationFrame(() => d.classList.add("on"));
      return "ok";
    },
    unring(key) { const d = rings[key]; if (d) d.classList.remove("on"); if (!key) Object.values(rings).forEach((x) => x.classList.remove("on")); return "ok"; },
    band(x1, x2, y, h, label) {
      const d = document.createElement("div"); d.className = "__band";
      Object.assign(d.style, { left: x1 + "px", top: y + "px", width: x2 - x1 + "px", height: h + "px" });
      if (label) d.innerHTML = `<b>${label}</b>`;
      document.body.appendChild(d); requestAnimationFrame(() => d.classList.add("on")); return "ok";
    },
    scroll(dy, ms = 900) {
      const m = document.querySelector("main.sheet") || document.scrollingElement;
      const y0 = m.scrollTop, t0 = performance.now();
      const ease = (t) => (t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);
      const step = (t) => { const p = Math.min(1, (t - t0) / ms); m.scrollTop = y0 + dy * ease(p); if (p < 1) requestAnimationFrame(step); };
      requestAnimationFrame(step); return "ok";
    },
    scrollTo(y, ms = 900) { const m = document.querySelector("main.sheet") || document.scrollingElement; return this.scroll(y - m.scrollTop, ms); },
  };
})();
