// Shared pieces for the Viet Nam screens: multi-series chart with a cursor, playback bar, level pill, streaming analyst.
import { html, useState, useEffect, useRef } from "../../../vendor/preact-htm.js";
import { getToken } from "../../api.js";
import { useWidth, ticks } from "../../charts.js";
import { fmt, fdate, Icon, toast } from "../../ui.js";
import { LEVEL } from "../../vnmap.js";

export const LevelPill = ({ l, small }) => { const L = LEVEL[l] || LEVEL.no_data;
  return html`<span class=${"lvpill" + (small ? " sm" : "")} style=${`--lv:${L.c};--lv-ink:${L.ink}`}>${L.label}</span>`; };

export const monthLabel = (s) => (s ? new Date(s + "T00:00:00Z").toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" }) : "—");

// series: [{name, color, dash, values:[y|null], band:[[lo,hi]]?, width}] over a shared x array (ISO dates or numbers)
export function MultiLine({ x, series, height = 220, yfmt = fmt.compact, cursor, onPick, label = "chart", unit = "", markers = [], ymax, xfmt, minW = 280 }) {
  const [ref, W] = useWidth(minW);
  const [hv, setHv] = useState(null);
  const pad = { l: 46, r: 14, t: 10, b: 24 };
  const n = x.length;
  const all = series.flatMap((s) => [...s.values, ...(s.band || []).map((b) => b?.[1])]).filter((v) => v != null && isFinite(v));
  const yt = ticks(0, ymax ?? Math.max(1, ...all), 4);
  const iw = Math.max(10, W - pad.l - pad.r), ih = height - pad.t - pad.b;
  const X = (i) => pad.l + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw);
  const Y = (v) => pad.t + ih - (Math.min(v, yt[yt.length - 1]) / yt[yt.length - 1]) * ih;
  const isDate = typeof x[0] === "string";
  const xl = [];
  const maxT = Math.max(2, Math.floor(iw / 80));
  if (isDate && x.some((v) => !v.endsWith("-01"))) {
    // daily axis: ticks on the first of each month, thinned to fit
    const firsts = x.map((v, i) => (v.endsWith("-01") ? i : -1)).filter((i) => i >= 0);
    const k = Math.max(1, Math.ceil(firsts.length / maxT));
    firsts.forEach((i, j) => { if (j % k === 0) xl.push(i); });
  } else {
    const step = Math.max(1, Math.ceil(n / maxT));
    for (let i = 0; i < n; i += step) xl.push(i);
  }
  const fx = xfmt || ((v) => (isDate ? (n > 400 && v.endsWith("-01") && x.every((u) => u.endsWith("-01")) ? v.slice(0, 4) : new Date(v + "T00:00:00Z").toLocaleDateString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" })) : v));
  const path = (vals) => { let d = "", pen = false; vals.forEach((v, i) => { if (v == null || !isFinite(v)) { pen = false; return; } d += `${pen ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`; pen = true; }); return d; };
  const bandPts = (b) => { const ok = b.map((v, i) => [v, i]).filter(([v]) => v && v[0] != null && v[1] != null); if (!ok.length) return "";
    return [...ok.map(([v, i]) => `${X(i).toFixed(1)},${Y(v[1]).toFixed(1)}`), ...ok.reverse().map(([v, i]) => `${X(i).toFixed(1)},${Y(v[0]).toFixed(1)}`)].join(" "); };
  const idxAt = (e) => { const r = e.currentTarget.getBoundingClientRect(); return Math.max(0, Math.min(n - 1, Math.round(((e.clientX - r.left - pad.l) / iw) * (n - 1)))); };
  return html`<div ref=${ref} style="position:relative">
    ${W > 0 && html`<svg class="chart" width=${W} height=${height} role="img" aria-label=${label} style=${onPick ? "cursor:crosshair" : ""}
      onMouseMove=${(e) => setHv(idxAt(e))} onMouseLeave=${() => setHv(null)} onClick=${(e) => onPick?.(idxAt(e))}>
      ${yt.map((v) => html`<line x1=${pad.l} x2=${W - pad.r} y1=${Y(v)} y2=${Y(v)} stroke="var(--grid)" /><text x=${pad.l - 8} y=${Y(v) + 4} text-anchor="end">${yfmt(v)}</text>`)}
      ${xl.map((i) => html`<text x=${Math.min(W - pad.r - 18, Math.max(pad.l + 14, X(i)))} y=${height - 6} text-anchor="middle">${fx(x[i])}</text>`)}
      ${markers.map((m) => html`<rect x=${X(m.from)} width=${Math.max(0, X(m.to) - X(m.from))} y=${pad.t} height=${ih} fill=${m.fill || "var(--raised)"} />
        ${m.label && html`<text x=${X(m.from) + 4} y=${pad.t + 12} class="lbl" style="font-size:11px">${m.label}</text>`}`)}
      ${series.map((s) => s.band && html`<polygon points=${bandPts(s.band)} fill=${s.color} fill-opacity="0.14" />`)}
      ${series.map((s) => html`<path d=${path(s.values)} fill="none" stroke=${s.color} stroke-width=${s.width || 2} stroke-dasharray=${s.dash ? "5 4" : null} stroke-linejoin="round" />`)}
      ${cursor != null && html`<line x1=${X(cursor)} x2=${X(cursor)} y1=${pad.t} y2=${pad.t + ih} stroke="var(--text)" stroke-width="1.2" />
        ${series.map((s) => s.values[cursor] != null && html`<circle cx=${X(cursor)} cy=${Y(s.values[cursor])} r="3.5" fill=${s.color} stroke="var(--surface)" stroke-width="1.5" />`)}`}
      ${hv != null && html`<line x1=${X(hv)} x2=${X(hv)} y1=${pad.t} y2=${pad.t + ih} stroke="var(--control)" />`}
      <line x1=${pad.l} x2=${W - pad.r} y1=${Y(0)} y2=${Y(0)} stroke="var(--control)" />
    </svg>`}
    ${hv != null && html`<div class="ctip" style=${`left:${Math.min(W - 220, Math.max(0, X(hv) + 10))}px;top:6px`}>
      <b>${isDate ? fdate(x[hv]) : x[hv]}</b>${series.map((s) => html`<div class="row" style="gap:6px"><i class="sw" style=${`background:${s.color}`}></i>${s.name}
        <span class="num" style="margin-left:auto">${s.values[hv] == null ? "—" : yfmt(s.values[hv])}${s.band?.[hv] ? html` <span class="muted">(${yfmt(s.band[hv][0])}–${yfmt(s.band[hv][1])})</span>` : ""}</span></div>`)}${unit && html`<div class="muted small">${unit}</div>`}</div>`}
  </div>`;
}

// play / pause / scrub / speed. `value` is an index into `n` steps; calls onChange(next) on a timer while playing.
export function Playback({ n, value, onChange, labelFor, playing, setPlaying, speeds = [1, 2, 4, 8], speed, setSpeed, fps = 6, kind, right, step = 1 }) {
  const timer = useRef(null);
  const cur = useRef(value);
  cur.current = value;
  useEffect(() => {
    if (!playing) return;
    timer.current = setInterval(() => {
      // sub-day steps: fixed frame rate, speed scales the step; whole steps: speed scales the frame rate
      const nx = cur.current + (step < 1 ? step * speed : step);
      if (nx > n - 1) { setPlaying(false); onChange(n - 1); return; }
      onChange(nx);
    }, step < 1 ? 1000 / fps : 1000 / (fps * speed));
    return () => clearInterval(timer.current);
  }, [playing, speed, n, fps]);
  useEffect(() => {
    const k = (e) => {
      if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
      if (e.key === " ") { e.preventDefault(); setPlaying((p) => !p); }
      if (e.key === "ArrowRight") onChange(Math.min(n - 1, Math.floor(cur.current) + 1));
      if (e.key === "ArrowLeft") onChange(Math.max(0, Math.ceil(cur.current) - 1));
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [n]);
  return html`<div class="playbar" role="group" aria-label="Timeline playback">
    <button class="btn sm pri play" onClick=${() => { if (value >= n - 1) onChange(0); setPlaying(!playing); }} aria-label=${playing ? "Pause" : "Play"} title="Space">
      <${Icon} n=${playing ? "stop" : "play"} s=${13} /></button>
    <div class="pb-label"><b class="num">${labelFor(Math.floor(value))}</b>${kind}</div>
    <input type="range" min="0" max=${n - 1} step=${step} value=${value} onInput=${(e) => { setPlaying(false); onChange(+e.target.value); }} aria-label="Timeline position" />
    <div class="seg sm" role="radiogroup" aria-label="Speed">${speeds.map((s) => html`<button class=${s === speed ? "on" : ""} onClick=${() => setSpeed(s)}>${s}×</button>`)}</div>
    ${right}
  </div>`;
}

// tiny, safe markdown: escape, then **bold**, _italic_, headings, bullets, line breaks
export function md(src) {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[\s(])_(.+?)_(?=[\s).,;:!?]|$)/g, "$1<i>$2</i>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const out = [];
  let list = null;
  for (const raw of src.split("\n")) {
    const l = raw.trimEnd();
    const b = l.match(/^\s*[-*•]\s+(.*)/) || l.match(/^\s*\d+[.)]\s+(.*)/);
    if (b) { if (!list) { list = []; } list.push(`<li>${inline(b[1])}</li>`); continue; }
    if (list) { out.push(`<ul>${list.join("")}</ul>`); list = null; }
    const h = l.match(/^#{1,4}\s+(.*)/);
    if (h) out.push(`<h4>${inline(h[1])}</h4>`);
    else if (l.trim()) out.push(`<p>${inline(l)}</p>`);
  }
  if (list) out.push(`<ul>${list.join("")}</ul>`);
  return out.join("");
}

// POST + SSE reader for /vn/analyze
export function AnalystStream({ body, suggestions = [], status, title = "AI analyst" }) {
  const [text, setText] = useState("");
  const [meta, setMeta] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [q, setQ] = useState("");
  const [lang, setLang] = useState(() => { try { return localStorage.getItem("hp_vn_lang") || "en"; } catch (e) { return "en"; } });
  const ctl = useRef(null);
  useEffect(() => { try { localStorage.setItem("hp_vn_lang", lang); } catch (e) {} }, [lang]);
  useEffect(() => () => ctl.current?.abort(), []);
  const run = async (question) => {
    if (!body) return;
    ctl.current?.abort();
    const c = new AbortController(); ctl.current = c;
    setBusy(true); setErr(null); setText(""); setMeta(null);
    try {
      const h = { "Content-Type": "application/json" };
      if (getToken()) h.Authorization = `Bearer ${getToken()}`;
      const r = await fetch("/api/v1/vn/analyze", { method: "POST", headers: h, body: JSON.stringify({ ...body, question: question || null, lang }), signal: c.signal });
      if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j?.error?.detail || r.statusText); }
      const rd = r.body.getReader(), dec = new TextDecoder();
      let buf = "", acc = "";
      for (;;) {
        const { value, done } = await rd.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let k;
        while ((k = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, k); buf = buf.slice(k + 2);
          const ev = (block.match(/^event: (.*)$/m) || [])[1] || "message";
          const data = (block.match(/^data: (.*)$/m) || [])[1];
          if (!data) continue;
          const j = JSON.parse(data);
          if (ev === "meta") setMeta(j);
          else if (ev === "error") setErr(j.error);
          else if (j.t) { acc += j.t; setText(acc); }
        }
      }
    } catch (e) { if (e.name !== "AbortError") { setErr(e.message); toast(e.message, "bad"); } }
    setBusy(false);
  };
  const llm = status?.llm;
  return html`<div class="stack analyst" style="gap:12px">
    <div class="row wrap" style="gap:8px">
      <button class="btn pri" disabled=${busy || !body} onClick=${() => run(null)}><${Icon} n="model" s=${14} />${busy ? "Analysing…" : "Generate brief"}</button>
      ${suggestions.map((s) => html`<button class="btn sm" disabled=${busy || !body} onClick=${() => { setQ(s); run(s); }}>${s}</button>`)}
      <span class="spacer"></span>
      <div class="seg sm" role="radiogroup" aria-label="Language"><button class=${lang === "en" ? "on" : ""} onClick=${() => setLang("en")}>EN</button><button class=${lang === "vi" ? "on" : ""} onClick=${() => setLang("vi")}>VI</button></div>
    </div>
    <form class="row" onSubmit=${(e) => { e.preventDefault(); if (q.trim()) run(q); }}>
      <input type="text" style="flex:1" placeholder="Ask about this situation, a province, or the team plan…" value=${q} onInput=${(e) => setQ(e.target.value)} maxlength="2000" aria-label="Question" />
      <button class="btn" disabled=${busy || !q.trim() || !body}>Ask</button>
      ${busy && html`<button type="button" class="btn ghost" onClick=${() => { ctl.current?.abort(); setBusy(false); }}>Stop</button>`}
    </form>
    ${err && html`<div class="notice warn small"><${Icon} n="warn" s=${14} /><div>${err}</div></div>`}
    ${(text || busy) && html`<div class=${"well md" + (busy ? " streaming" : "")} dangerouslySetInnerHTML=${{ __html: md(text) + (busy ? '<span class="caret"></span>' : "") }}></div>`}
    <div class="small faint row wrap" style="gap:6px">
      ${meta ? (meta.provider === "rule_based" ? "Rule-based brief (no LLM key)" : `${meta.provider} · ${meta.model}`) : llm?.configured ? `${llm.provider} · ${llm.model}` : "No LLM key in .env — the brief will be rule-based. Add ANTHROPIC_API_KEY (or OPENAI_API_KEY + HP_LLM_MODEL) and restart."}
      <span>· grounded on this screen's numbers only; options for human review</span>
      ${meta?.context && html`<details><summary class="link">context sent</summary><pre class="mono small ctxpre">${JSON.stringify(meta.context, null, 1)}</pre></details>`}
    </div>
  </div>`;
}
