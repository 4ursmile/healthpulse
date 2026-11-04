// Mobility-range panel — question: "How far from home do people in this place usually go?"
// Source: Meta Movement Distribution (CC BY 4.0), the share of device pings by distance from the home location. It is a
// distance-from-home indicator, NOT origin-destination flows, and a recent window only (HDX keeps ~90 days), so it is context for
// the reader and never an input to a forecast. Missing is shown as missing, never as zero.
import { html } from "../vendor/preact-htm.js";
import { useApi } from "./api.js";
import { Panel, Sem, Empty, SkBlock, Prov, fmt } from "./ui.js";

const BANDS = [
  ["share_0km", "At home location", 0.18],
  ["share_0_10km", "0–10 km", 0.38],
  ["share_10_100km", "10–100 km", 0.66],
  ["share_100plus_km", "100+ km", 1],
];
const pct = (v) => (v == null ? "—" : fmt.pct(v, v < 0.1 ? 1 : 0));

function Bar({ unit, median }) {
  return html`<div role="img" aria-label=${"Share of device pings by distance from home: " + BANDS.map(([k, l]) => `${l} ${pct(unit[k])}`).join(", ")}>
    <div style="display:flex;height:22px;border-radius:4px;overflow:hidden;background:var(--grid)">
      ${BANDS.map(([k, l, o]) => unit[k] != null && html`<div title=${`${l}: ${pct(unit[k])}`} style=${`width:${(unit[k] * 100).toFixed(2)}%;background:var(--chart-1);opacity:${o}`}></div>`)}
    </div>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:10px">
      ${BANDS.map(([k, l, o]) => html`<div>
        <div style="display:flex;align-items:center;gap:6px;font-size:12px"><i style=${`width:9px;height:9px;border-radius:2px;background:var(--chart-1);opacity:${o}`}></i>${l}</div>
        <div style="font-size:18px;font-weight:600;font-variant-numeric:tabular-nums">${pct(unit[k])}</div>
        ${median && html`<div class="muted" style="font-size:11px">median unit ${pct(median[k])}</div>`}
      </div>`)}
    </div>
  </div>`;
}

// scope: "br" (unitId = IBGE 7-digit code) or "vn" (unitId = province P-code)
export function MobilityPanel({ scope, unitId, name, cls = "" }) {
  const q = useApi(unitId ? `/geo/mobility/${scope}/${unitId}` : null);
  if (q.error?.status === 409 || q.error?.status === 404) return null; // profile not built / unit outside it: say nothing rather than guess
  const d = q.data, u = d?.unit, ctx = d?.context, w = d?.window;
  const missing = u && u.share_100plus_km == null;
  return html`<div class=${cls}><${Panel} title="Mobility range" note=${html`Share of device pings by distance from the home location <${Sem} k="OBSERVED" />`}>
    ${!q.data ? html`<${SkBlock} h=${96} />` : missing
      ? html`<${Empty}>${name || unitId}: no profile. The polygon crosswalk covers too little of it, or Meta holds too few days. Not shown as zero.</${Empty}>`
      : html`<${Bar} unit=${u} median=${ctx?.median} />
        ${ctx && html`<div class="muted" style="margin-top:10px;font-size:12px">${fmt.pct(ctx.percentile_100plus, 0)} of the ${fmt.int(ctx.n_units)} ${scope === "vn" ? "provinces" : "municipalities"} with values have a smaller 100+ km share.</div>`}
        <${Prov} items=${[w?.from && `${w.from} to ${w.to} (${w.days} days)`, `${q.meta?.weight_basis || ""} aggregation`.trim(), q.meta?.crosswalk_version && `polygon crosswalk ${q.meta.crosswalk_version}`,
          "distance from home, not origin-destination flows", "recent window, not used by any forecast"]} />`}
  </${Panel}></div>`;
}

// VN sandbox helper: the data-grounded RANGE for "Contacts outside home province" (the default is never changed silently)
export function useMobilitySuggestion() {
  const q = useApi("/geo/mobility");
  const r = q.data?.vn_sandbox_reference;
  return r ? { lower: r.lower_bound, upper: r.upper_bound, inside: r.default_within_range, window: q.data?.units?.vn ? `${q.data.units.vn.ds_min} to ${q.data.units.vn.ds_max}` : null } : null;
}
