// Global overview — question: "Across every country with data, which places need attention now?"
// Built only from /scopes, so it lists exactly the countries and provinces that have ingested data.
import { html } from "../../vendor/preact-htm.js";
import { Panel, TitleRow, Kpis, Loadable, SkBlock, Empty, Sem, Icon, fmt } from "../ui.js";
import { useScopes, setScope, scopeKey } from "../scope.js";
import { LEVEL } from "../vnmap.js";
import { LevelPill } from "./vn/_vn.js";

const fweek = (d) => new Date(d + "T00:00:00Z").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
const fmonth = (d) => new Date(d + "T00:00:00Z").toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
const ORDER = { alert: 3, warning: 2, watch: 1, normal: 0 };

// one attention list across countries: VN provinces at warning/alert, BR states with draft alerts or a weekly rise
function attention(countries) {
  const out = [];
  for (const c of countries) for (const x of c.regions) {
    if (c.id === "vn" && ORDER[x.level] >= 2)
      out.push({ c, x, rank: ORDER[x.level] * 10 + (x.ratio || 0), why: x.reason, tag: html`<${LevelPill} l=${x.level} small />` });
    if (c.id === "br" && x.reported_last_week != null) {
      const ch = x.reported_prev_week ? (x.reported_last_week - x.reported_prev_week) / x.reported_prev_week : null;
      if (x.open_alerts || (ch != null && ch >= 0.25 && x.reported_last_week >= 20))
        out.push({ c, x, rank: (x.open_alerts ? 25 : 15) + (ch || 0),
          why: `${fmt.int(x.reported_last_week)} dengue cases in the week of ${fweek(c.latest)}${ch != null ? ` (${fmt.signed(Math.round(ch * 100))}% vs the week before)` : ""}${x.open_alerts ? ` · ${x.open_alerts} draft alert${x.open_alerts > 1 ? "s" : ""}` : ""}`,
          tag: x.open_alerts ? html`<span class="pill warn">Draft alerts</span>` : html`<span class="pill">Rising</span>` });
    }
  }
  return out.sort((a, b) => b.rank - a.rank);
}

function CountryPanel({ c }) {
  const vn = c.id === "vn";
  const kp = vn ? [
    { label: "Estimated cases this month", sem: "ESTIMATE", lead: true, value: fmt.int(c.national_estimate),
      sub: `seasonal norm ${fmt.int(c.national_normal)} · ${fmonth(c.latest)}` },
    { label: "Provinces at alert / warning", value: `${c.counts?.alert || 0} / ${c.counts?.warning || 0}`, sub: `${c.counts?.watch || 0} on watch · ${c.units} provinces` },
  ] : [
    { label: "Reported dengue, last week", sem: "OBSERVED", lead: true, value: fmt.int(c.reported_last_week), unit: "cases",
      sub: html`<span class=${c.reported_last_week > c.reported_prev_week ? "up-bad" : "down-good"}>${fmt.signed(c.reported_last_week - c.reported_prev_week)}</span> vs week before · ${fweek(c.latest)}` },
    { label: "Draft alerts", value: fmt.int(c.open_alerts), sub: `${c.regions.length} states · ${fmt.int(c.units)} municipalities with a series` },
  ];
  const top = vn ? c.regions.slice(0, 6) : c.regions.filter((x) => x.reported_last_week != null).concat(c.regions.filter((x) => x.reported_last_week == null)).slice(0, 6);
  return html`<${Panel} title=${c.name} note=${html`<${Sem} k=${vn ? "ESTIMATE" : "OBSERVED"} /> ${vn ? `Monthly model estimate, ${fmonth(c.latest)}` : `Weekly surveillance, week of ${fweek(c.latest)}`}`}
    right=${html`<button class="btn sm pri" onClick=${() => setScope(c.id)}>Open ${c.name}<${Icon} n="chevron" s=${12} /></button>`} flush>
    <div style="padding:0 20px 12px"><${Kpis} items=${kp} /></div>
    <table class="tbl"><thead><tr><th>${c.level_name}</th><th class="num">${vn ? "Per 100k" : "Cases last wk"}</th><th>${vn ? "Level" : "Series"}</th></tr></thead>
      <tbody>${top.map((x) => html`<tr class="click" onClick=${() => setScope(scopeKey(c.id, x.id))}>
        <td>${x.name}</td>
        <td class="num">${vn ? fmt.d1(x.incidence_per_100k) : x.reported_last_week == null ? "—" : fmt.int(x.reported_last_week)}</td>
        <td>${vn ? html`<${LevelPill} l=${x.level} small />` : x.reported_last_week == null ? html`<span class="small muted">SARI only</span>` : x.open_alerts ? html`<span class="pill warn">${x.open_alerts} alerts</span>` : html`<span class="small muted">Dengue + SARI</span>`}</td></tr>`)}</tbody></table>
    ${c.regions.length > top.length && html`<div class="pfoot"><button class="link small" onClick=${() => setScope(c.id)}>All ${c.regions.length} ${c.level_name.toLowerCase()}s</button></div>`}
  </${Panel}>`;
}

export default function GlobalOverview() {
  const tree = useScopes();
  const cs = tree.countries;
  const att = attention(cs);
  const sub = cs.length ? html`${cs.length} countries with data. <b>${att.length}</b> ${att.length === 1 ? "place needs" : "places need"} attention now. Pick a row to scope every screen to it.` : null;
  return html`<div class="page">
    <${TitleRow} title="Global overview" sub=${sub || (tree.loading ? "Loading places with data…" : null)} />
    <${Loadable} q=${tree} sk=${html`<${SkBlock} h=${320} />`}>${() => !cs.length ? html`<${Empty} icon="data">No country has ingested data yet. Run <code>python server.py</code> once, or <code>hp vn-fetch</code> for Viet Nam.</${Empty}>` : html`
      <div class="grid">
        <div class="s12"><${Panel} title="Needs attention" note="Viet Nam provinces at warning or alert; Brazilian states with draft alerts or a ≥25% weekly rise" flush>
          ${att.length ? html`<table class="tbl"><tbody>${att.slice(0, 10).map(({ c, x, why, tag }) => html`
            <tr class="click" onClick=${() => setScope(scopeKey(c.id, x.id))}>
              <td style="width:34%"><div class="two">${x.name}<div class="s">${c.level_name} · ${c.name}</div></div></td>
              <td>${tag}</td><td class="small muted">${why}</td>
              <td class="num"><${Icon} n="chevron" s=${12} /></td></tr>`)}</tbody></table>`
            : html`<div style="padding:0 20px"><${Empty}>Nothing above the attention thresholds.</${Empty}></div>`}
        </${Panel}></div>
        ${cs.map((c) => html`<div class=${cs.length > 1 ? "s6" : "s12"}><${CountryPanel} c=${c} /></div>`)}
      </div>`}</${Loadable}>
    <p class="small faint" style="margin-top:16px">Brazil numbers are reported surveillance; Viet Nam numbers are a climate-driven model estimate. They are not comparable one to one.</p>
  </div>`;
}
