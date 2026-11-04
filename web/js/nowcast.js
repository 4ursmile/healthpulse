// Nowcast panel for Brazil SARI diseases — question: "How many cases will the most recent weeks eventually have?"
// Bars are the count reported as of the nowcast date. Weeks still being filled in are hatched. The dashed line and band are the
// nowcast median and its 80% interval. A week the data cannot speak about yet is hatched with no estimate, never drawn as zero.
import { html, useState, useMemo } from "../vendor/preact-htm.js";
import { useApi } from "./api.js";
import { Panel, Sem, Empty, SkBlock, Notices, Prov, fmt, fdate } from "./ui.js";
import { useWidth, ticks } from "./charts.js";

export const NOWCAST_DISEASES = ["covid19_sari", "influenza_sari"];
const tms = (s) => new Date(s + "T00:00:00Z").getTime();
let uid = 0;

function NowcastChart({ rows, height = 240, label }) {
  const [ref, W] = useWidth();
  const [hover, setHover] = useState(null);
  const id = useMemo(() => `hatch${++uid}`, []);
  const pad = { l: 44, r: 12, t: 10, b: 24 };
  const n = rows.length;
  const ymax = Math.max(1, ...rows.map((r) => Math.max(r.observed ?? 0, r.p90 ?? 0)));
  const yt = ticks(0, ymax, 4);
  const iw = Math.max(10, W - pad.l - pad.r), ih = height - pad.t - pad.b;
  const step = iw / Math.max(1, n), bw = Math.max(2, step * 0.7);
  const X = (i) => pad.l + step * (i + 0.5);
  const Y = (v) => pad.t + ih - (v / yt[yt.length - 1]) * ih;
  const done = (r) => r.status === "complete" || r.status === "unmodelled";
  const est = rows.map((r, i) => ({ r, i })).filter((d) => d.r.status === "nowcast");
  const band = est.length ? [...est.map((d) => `${X(d.i).toFixed(1)},${Y(d.r.p90).toFixed(1)}`), ...est.slice().reverse().map((d) => `${X(d.i).toFixed(1)},${Y(d.r.p10).toFixed(1)}`)].join(" ") : "";
  const line = est.map((d, k) => `${k ? "L" : "M"}${X(d.i).toFixed(1)},${Y(d.r.nowcast_p50).toFixed(1)}`).join("");
  const labelEvery = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 70))));
  const onMove = (e) => {
    const b = e.currentTarget.getBoundingClientRect();
    const i = Math.floor((e.clientX - b.left - pad.l) / step);
    setHover(i >= 0 && i < n ? i : null);
  };
  const h = hover != null ? rows[hover] : null;
  return html`<div ref=${ref} style="position:relative">
    ${W > 0 && html`<svg class="chart" width=${W} height=${height} role="img" aria-label=${label} onMouseMove=${onMove} onMouseLeave=${() => setHover(null)}>
      <defs><pattern id=${id} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="5" stroke="var(--text)" stroke-opacity=".55" stroke-width="1.4" /></pattern></defs>
      ${yt.map((v) => html`<line x1=${pad.l} x2=${W - pad.r} y1=${Y(v)} y2=${Y(v)} stroke="var(--grid)" /><text x=${pad.l - 8} y=${Y(v) + 4} text-anchor="end">${fmt.compact(v)}</text>`)}
      ${rows.map((r, i) => i % labelEvery === 0 && html`<text x=${X(i)} y=${height - 6} text-anchor="middle">${fdate(r.week_start, { day: "numeric", month: "short" })}</text>`)}
      ${rows.map((r, i) => {
        const y = Y(r.observed ?? 0), base = Y(0);
        return done(r)
          ? html`<rect x=${X(i) - bw / 2} y=${y} width=${bw} height=${Math.max(0, base - y)} fill="var(--text)" fill-opacity=".5" />`
          : html`<rect x=${X(i) - bw / 2} y=${y} width=${bw} height=${Math.max(1, base - y)} fill=${`url(#${id})`} stroke="var(--text)" stroke-opacity=".55" stroke-width="1" />`;
      })}
      ${band && html`<polygon class="fadein" points=${band} fill="var(--chart-1)" fill-opacity="0.2" />`}
      ${est.length > 1 && html`<path class="fadein" d=${line} fill="none" stroke="var(--chart-1)" stroke-width="2" stroke-dasharray="5 4" />`}
      ${est.length === 1 && html`<line x1=${X(est[0].i) - bw / 2} x2=${X(est[0].i) + bw / 2} y1=${Y(est[0].r.nowcast_p50)} y2=${Y(est[0].r.nowcast_p50)} stroke="var(--chart-1)" stroke-width="2" />`}
      ${h && html`<line x1=${X(hover)} x2=${X(hover)} y1=${pad.t} y2=${pad.t + ih} stroke="var(--control)" />`}
      <line x1=${pad.l} x2=${W - pad.r} y1=${Y(0)} y2=${Y(0)} stroke="var(--control)" />
    </svg>`}
    ${h && html`<div class="ctip" style=${`left:${Math.min(W - 230, Math.max(0, X(hover) + 10))}px;top:8px`}>
      <b>${fdate(h.week_start, { day: "numeric", month: "short", year: "numeric" })}</b>
      <div>Reported so far: <b>${fmt.int(h.observed)}</b></div>
      ${h.status === "nowcast" ? html`<div>Nowcast: <b>${fmt.int(h.nowcast_p50)}</b> (80%: ${fmt.int(h.p10)}–${fmt.int(h.p90)}; 95%: ${fmt.int(h.p025)}–${fmt.int(h.p975)})</div>
        <div class="muted small">about ${fmt.pct(h.completeness, 0)} of eventual records are in</div>`
        : h.status === "insufficient_data" ? html`<div class="muted">Too early: only ~${fmt.pct(h.completeness, 0)} of eventual records are in, so no estimate.</div>`
        : html`<div class="muted small">Older than the delay model: shown as reported.</div>`}
    </div>`}
  </div>`;
}

export function NowcastPanel({ disease, municipalityId, state, weeks = 40, title }) {
  if (!NOWCAST_DISEASES.includes(disease)) return null;
  const qs = `disease_id=${disease}&weeks=${weeks}${municipalityId ? `&municipality_id=${municipalityId}` : ""}${state ? `&state=${state}` : ""}`;
  const q = useApi(`/nowcasts/latest/series?${qs}`);
  const d = q.data, m = q.meta || {};
  const rows = d?.rows || [];
  const place = d ? (d.unit_level === "national" ? "Brazil" : d.unit_level === "state" ? `state ${d.unit_id}` : `municipality ${d.unit_id}`) : "";
  const bt = m.backtest || {};
  const none = q.error?.status === 404;
  return html`<${Panel} title=${title || "Nowcast: what the recent weeks will eventually count"}
    note=${html`<div class="ckey" style="margin-top:4px"><span><i class="bar"></i>Reported as of ${m.asof_cutoff ? fdate(m.asof_cutoff) : "—"} (complete)</span>
      <span><i style="height:10px;border:1px solid var(--muted);background:repeating-linear-gradient(45deg,var(--muted) 0 1.5px,transparent 1.5px 5px)"></i>Still being filled in</span>
      <span><i class="dash"></i>Nowcast median</span><span><i class="band"></i>80% interval</span></div>`}
    right=${html`<${Sem} k="NOWCAST" title="Correction of an observed count for records not yet entered. Not a forecast." />`}>
    ${none ? html`<${Empty}>No nowcast has been published for this disease yet. Run <code>hp nowcast</code> after ingesting the SIVEP-Gripe files.</${Empty}>`
      : q.error && !q.data ? html`<${Empty}>Nowcast unavailable.</${Empty}>`
      : !d ? html`<${SkBlock} h=${240} />`
      : rows.length ? html`
        <${Notices} items=${d.fallback ? [`No separate series for this place: showing ${place}.`] : []} />
        <${NowcastChart} rows=${rows} label=${`Nowcast of ${disease} for ${place}`} />
        <${Prov} items=${[`${place} · SARI cases per onset week`, m.nowcast_id, `as of ${m.asof_cutoff ? fdate(m.asof_cutoff) : "—"}`,
          m.is_synthetic && html`<${Sem} k="SYNTHETIC" />`]} />
        <div class="small muted" style="margin-top:6px">
          ${bt.holdout_cov80 != null ? html`Backtest on held-out weeks: the 80% interval contained the final count ${fmt.pct(bt.holdout_cov80, 0)} of the time, and the nowcast was off by ${fmt.pct(bt.holdout_wape, 0)} on average against ${fmt.pct(bt.holdout_wape_naive, 0)} for the raw count. ` : ""}
          The delay is reconstructed from record entry and closure dates in a single snapshot (${m.label}); edits and reclassifications made later are not visible, so treat the bands as optimistic at the margin. The newest weeks are left blank, not zero, when the data say too little.
        </div>`
      : html`<${Empty}>No series for this place.</${Empty}>`}
  </${Panel}>`;
}
