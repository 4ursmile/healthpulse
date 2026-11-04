// SVG charts drawn to the app rules: 2px straight lines, no markers, 4–5 faint horizontal gridlines, round ticks above the
// maximum, no y-axis line, a forecast is dashed with its P10–P90 band, one hue on the main series, bare tooltips.
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.js";
import { fmt, fdate } from "./ui.js";

export function useWidth(min = 280) {
  const ref = useRef(null);
  const [w, set] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((e) => set(Math.max(min, Math.floor(e[0].contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

export function ticks(min, max, count = 4) {
  if (!(isFinite(min) && isFinite(max))) return [0, 1];
  if (max === min) max = min + 1;
  const raw = (max - min) / count, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || 10 * mag;
  const lo = Math.floor(min / step) * step;
  let hi = Math.ceil(max / step) * step;
  if (hi <= max) hi += step;
  const out = [];
  for (let v = lo; v <= hi + step / 2; v += step) out.push(+v.toFixed(10));
  return out;
}

const tms = (s) => new Date(s.length <= 10 ? s + "T00:00:00Z" : s).getTime();

// Observed history (ink) + forecast median dashed with its band (teal), optional markers (holidays) and a reference line.
export function TimeChart({ observed = [], forecast = [], markers = [], height = 240, yfmt = fmt.compact, unit = "", reference, label = "time series", hueOnObserved = false }) {
  const [ref, W] = useWidth();
  const [hover, setHover] = useState(null);
  const pad = { l: 44, r: 16, t: 10, b: 24 };
  const pts = observed.filter((d) => d.y !== null && d.y !== undefined);
  const xs = [...observed.map((d) => tms(d.t)), ...forecast.map((d) => tms(d.t))];
  const ymax = Math.max(1, ...pts.map((d) => d.y), ...forecast.map((d) => d.p90 ?? d.p50 ?? 0), reference?.value ?? 0);
  const yt = ticks(0, ymax, 4);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const iw = Math.max(10, W - pad.l - pad.r), ih = height - pad.t - pad.b;
  const X = (t) => pad.l + ((t - x0) / Math.max(1, x1 - x0)) * iw;
  const Y = (v) => pad.t + ih - (v / yt[yt.length - 1]) * ih;
  const path = (arr, f) => arr.map((d, i) => `${i ? "L" : "M"}${X(tms(d.t)).toFixed(1)},${Y(f(d)).toFixed(1)}`).join("");
  // observed line breaks at missing weeks (never drawn through a gap)
  const segs = [];
  let cur = [];
  for (const d of observed) { if (d.y === null || d.y === undefined) { if (cur.length) segs.push(cur); cur = []; } else cur.push(d); }
  if (cur.length) segs.push(cur);
  const fc = forecast.filter((d) => d.p50 !== null && d.p50 !== undefined);
  const lastObs = pts[pts.length - 1];
  const fcLine = lastObs && fc.length ? [{ t: lastObs.t, p50: lastObs.y }, ...fc] : fc;
  const band = fc.length ? [...fc.map((d) => `${X(tms(d.t)).toFixed(1)},${Y(d.p90).toFixed(1)}`), ...fc.slice().reverse().map((d) => `${X(tms(d.t)).toFixed(1)},${Y(d.p10).toFixed(1)}`)].join(" ") : "";
  // x labels: one step all the way, by month or year
  const span = (x1 - x0) / 86400000;
  const xl = [];
  if (isFinite(x0)) {
    const d = new Date(x0);
    const stepM = span > 900 ? 12 : span > 400 ? 6 : span > 160 ? 3 : 1;
    let m = new Date(Date.UTC(d.getUTCFullYear(), Math.ceil((d.getUTCMonth() + 1) / stepM) * stepM, 1));
    while (m.getTime() <= x1) { xl.push(m.getTime()); m = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + stepM, 1)); }
  }
  const all = [...observed.map((d) => ({ ...d, kind: "obs" })), ...forecast.map((d) => ({ ...d, kind: "fc" }))];
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const t = x0 + ((e.clientX - r.left - pad.l) / iw) * (x1 - x0);
    let best = null, bd = Infinity;
    for (const d of all) { const dd = Math.abs(tms(d.t) - t); if (dd < bd) { bd = dd; best = d; } }
    setHover(best);
  };
  const obsColor = hueOnObserved ? "var(--chart-1)" : "var(--text)";
  return html`<div ref=${ref} style="position:relative">
    ${W > 0 && html`<svg class="chart" width=${W} height=${height} role="img" aria-label=${label} onMouseMove=${onMove} onMouseLeave=${() => setHover(null)}>
      ${yt.map((v) => html`<line x1=${pad.l} x2=${W - pad.r} y1=${Y(v)} y2=${Y(v)} stroke="var(--grid)" />
        <text x=${pad.l - 8} y=${Y(v) + 4} text-anchor="end">${yfmt(v)}</text>`)}
      ${xl.map((t) => html`<text x=${Math.min(W - pad.r - 20, Math.max(pad.l + 16, X(t)))} y=${height - 6} text-anchor="middle">${new Date(t).toLocaleDateString("en-US", { month: span > 400 ? undefined : "short", year: span > 400 ? "numeric" : "2-digit", timeZone: "UTC" })}</text>`)}
      ${markers.map((m) => { const x = X(tms(m.t)); if (x < pad.l || x > W - pad.r) return null;
        // m.line: a labelled full-height event line (m.color, m.dash); otherwise a small tick on the axis
        return m.line ? html`<line x1=${x} x2=${x} y1=${pad.t} y2=${pad.t + ih} stroke=${m.color || "var(--muted)"} stroke-width="1.5" stroke-dasharray=${m.dash || "none"}><title>${m.label}</title></line>`
          : html`<line x1=${x} x2=${x} y1=${pad.t + ih - 6} y2=${pad.t + ih} stroke="var(--muted)"><title>${m.label}</title></line>`; })}
      ${markers.map((m, i) => { const x = X(tms(m.t)); return m.line && x >= pad.l && x <= W - pad.r
        ? html`<text x=${x + 4} y=${pad.t + 12 + 14 * (m.row ?? i)} class="lbl" fill=${m.color || "var(--muted)"} style="paint-order:stroke;stroke:var(--surface);stroke-width:4px;stroke-linejoin:round">${m.short || m.label}</text>` : null; })}
      ${reference && html`<line x1=${pad.l} x2=${W - pad.r} y1=${Y(reference.value)} y2=${Y(reference.value)} stroke="var(--muted)" stroke-dasharray="4 3" />
        <text x=${W - pad.r} y=${Y(reference.value) - 4} text-anchor="end" class="lbl">${reference.label}</text>`}
      ${band && html`<polygon class="fadein" points=${band} fill="var(--chart-1)" fill-opacity="0.16" />`}
      ${segs.map((s) => html`<path class="draw" pathLength="1" d=${path(s, (d) => d.y)} fill="none" stroke=${obsColor} stroke-width="1.75" stroke-linejoin="round" />`)}
      ${fcLine.length > 1 && html`<path class="fadein" d=${path(fcLine, (d) => d.p50)} fill="none" stroke="var(--chart-1)" stroke-width="2" stroke-dasharray="5 4" />`}
      ${fc.length && lastObs && html`<line x1=${X(tms(lastObs.t))} x2=${X(tms(lastObs.t))} y1=${pad.t} y2=${pad.t + ih} stroke="var(--control)" stroke-dasharray="2 3" />`}
      ${hover && html`<line x1=${X(tms(hover.t))} x2=${X(tms(hover.t))} y1=${pad.t} y2=${pad.t + ih} stroke="var(--control)" />
        <circle cx=${X(tms(hover.t))} cy=${Y(hover.kind === "obs" ? (hover.y ?? 0) : hover.p50 ?? 0)} r="4" fill=${hover.kind === "obs" ? obsColor : "var(--chart-1)"} />`}
      ${yt.length && html`<line x1=${pad.l} x2=${W - pad.r} y1=${Y(0)} y2=${Y(0)} stroke="var(--control)" />`}
    </svg>`}
    ${hover && html`<div class="ctip" style=${`left:${Math.min(W - 190, Math.max(0, X(tms(hover.t)) + 10))}px;top:8px`}>
      <b>${fdate(hover.t)}</b> · ${hover.kind === "obs" ? html`${hover.y == null ? "no report" : fmt.int(hover.y)} ${unit} <span class="muted">observed</span>`
        : html`${fmt.int(hover.p50)} ${unit} <span class="muted">(P10–P90 ${fmt.int(hover.p10)}–${fmt.int(hover.p90)}) forecast</span>`}</div>`}
  </div>`;
}

// Columns with square corners (e.g. weekly rainfall). One muted series; optional highlight index in the hue.
export function Bars({ data, height = 120, yfmt = fmt.int, unit = "", label = "bars", color = "var(--chart-muted)" }) {
  const [ref, W] = useWidth();
  const [hv, setHv] = useState(null);
  const pad = { l: 44, r: 16, t: 8, b: 20 };
  const vals = data.map((d) => d.y ?? 0);
  const yt = ticks(0, Math.max(1, ...vals), 3);
  const iw = Math.max(10, W - pad.l - pad.r), ih = height - pad.t - pad.b;
  const bw = Math.max(1, iw / Math.max(1, data.length) - 1);
  const Y = (v) => pad.t + ih - (v / yt[yt.length - 1]) * ih;
  return html`<div ref=${ref} style="position:relative">${W > 0 && html`<svg class="chart" width=${W} height=${height} role="img" aria-label=${label} onMouseLeave=${() => setHv(null)}>
    ${yt.map((v) => html`<line x1=${pad.l} x2=${W - pad.r} y1=${Y(v)} y2=${Y(v)} stroke="var(--grid)" /><text x=${pad.l - 8} y=${Y(v) + 4} text-anchor="end">${yfmt(v)}</text>`)}
    ${data.map((d, i) => d.y == null ? null : html`<rect x=${pad.l + i * (iw / data.length)} y=${Y(d.y)} width=${bw} height=${Math.max(0, Y(0) - Y(d.y))} fill=${hv === i ? "var(--text)" : color} onMouseEnter=${() => setHv(i)} />`)}
    <line x1=${pad.l} x2=${W - pad.r} y1=${Y(0)} y2=${Y(0)} stroke="var(--control)" />
  </svg>`}${hv !== null && data[hv] && html`<div class="ctip" style=${`left:${Math.min(W - 160, pad.l + hv * (iw / data.length))}px;top:0`}>${fdate(data[hv].t)} · ${yfmt(data[hv].y)} ${unit}</div>`}</div>`;
}

// Categorical multi-series line (≤3 series), e.g. MAE by horizon for model vs baselines.
export function CatLines({ cats, series, height = 200, yfmt = fmt.int, label = "comparison" }) {
  const [ref, W] = useWidth();
  const pad = { l: 48, r: 16, t: 10, b: 24 };
  const all = series.flatMap((s) => s.values.filter((v) => v != null));
  const yt = ticks(0, Math.max(1e-9, ...all), 4);
  const iw = Math.max(10, W - pad.l - pad.r), ih = height - pad.t - pad.b;
  const X = (i) => pad.l + (cats.length === 1 ? iw / 2 : (i / (cats.length - 1)) * iw);
  const Y = (v) => pad.t + ih - (v / yt[yt.length - 1]) * ih;
  return html`<div ref=${ref}>${W > 0 && html`<svg class="chart" width=${W} height=${height} role="img" aria-label=${label}>
    ${yt.map((v) => html`<line x1=${pad.l} x2=${W - pad.r} y1=${Y(v)} y2=${Y(v)} stroke="var(--grid)" /><text x=${pad.l - 8} y=${Y(v) + 4} text-anchor="end">${yfmt(v)}</text>`)}
    ${cats.map((c, i) => html`<text x=${X(i)} y=${height - 6} text-anchor="middle">${c}</text>`)}
    ${series.map((s) => html`<path class=${s.dash ? "fadein" : "draw"} pathLength=${s.dash ? null : 1} d=${s.values.map((v, i) => v == null ? "" : `${i && s.values[i - 1] != null ? "L" : "M"}${X(i)},${Y(v)}`).join("")} fill="none" stroke=${s.color} stroke-width="2" stroke-dasharray=${s.dash ? "5 4" : null} />
      ${s.values.map((v, i) => v == null ? null : html`<circle cx=${X(i)} cy=${Y(v)} r="2.5" fill=${s.color}><title>${s.name} ${cats[i]}: ${yfmt(v)}</title></circle>`)}`)}
  </svg>`}</div>`;
}

// Reliability: nominal vs empirical coverage with the identity diagonal.
export function Calibration({ points, height = 200 }) {
  const [ref, W] = useWidth(200);
  const pad = { l: 44, r: 16, t: 10, b: 28 };
  const size = Math.min(W - pad.l - pad.r, height - pad.t - pad.b);
  const X = (v) => pad.l + v * size, Y = (v) => pad.t + size - v * size;
  const tk = [0, 0.25, 0.5, 0.75, 1];
  return html`<div ref=${ref}>${W > 0 && html`<svg class="chart" width=${W} height=${size + pad.t + pad.b} role="img" aria-label="Interval calibration">
    ${tk.map((v) => html`<line x1=${pad.l} x2=${pad.l + size} y1=${Y(v)} y2=${Y(v)} stroke="var(--grid)" /><text x=${pad.l - 8} y=${Y(v) + 4} text-anchor="end">${fmt.pct(v)}</text>
      <text x=${X(v)} y=${pad.t + size + 16} text-anchor="middle">${fmt.pct(v)}</text>`)}
    <line x1=${X(0)} y1=${Y(0)} x2=${X(1)} y2=${Y(1)} stroke="var(--muted)" stroke-dasharray="4 3" />
    ${points.map((s) => html`<path class="draw" pathLength="1" d=${s.pts.filter((p) => p.empirical != null).map((p, i) => `${i ? "L" : "M"}${X(p.nominal)},${Y(p.empirical)}`).join("")} fill="none" stroke=${s.color} stroke-width="2" />
      ${s.pts.filter((p) => p.empirical != null).map((p) => html`<circle cx=${X(p.nominal)} cy=${Y(p.empirical)} r="3" fill=${s.color}><title>${s.name}: nominal ${fmt.pct(p.nominal)} → empirical ${fmt.pct(p.empirical, 1)}</title></circle>`)}`)}
    <text x=${pad.l + size} y=${pad.t + size + 26} text-anchor="end" class="lbl" style="font-size:11px">nominal coverage →</text>
  </svg>`}</div>`;
}

// Ranked list where a thin bar under each label carries the magnitude; top item in ink.
export const BarList = ({ rows, format = fmt.int, max }) => {
  const m = max ?? Math.max(1e-9, ...rows.map((r) => r.value ?? 0));
  return html`<div class="barlist">${rows.map((r, i) => html`<div class=${"it" + (i === 0 ? " top" : "")}>
    <span>${r.label}</span><span class="num">${format(r.value)}${r.sub && html`<span class="muted small"> ${r.sub}</span>`}</span>
    <div class="tr"><i style=${`width:${Math.max(0, ((r.value ?? 0) / m) * 100)}%`}></i></div></div>`)}</div>`;
};
