// Explore — question: "How does this indicator vary across municipalities and weeks?"
import { html, useMemo } from "../../vendor/preact-htm.js";
import { useApi, setQuery, linkWith } from "../api.js";
import { Panel, TitleRow, Notices, Sem, Seg, Loadable, Link, Empty, fmt, fdate, weekLabel, SkRows } from "../ui.js";
import { ChoroplethMap, Legend } from "../map.js";
import { BarList } from "../charts.js";
import { DiseaseSelect } from "./_product_shared.js";
import { stateParam, scopeDisease, useScopes, scopeOf } from "../scope.js";

const LAYERS = [["incidence", "Reported per 100k"], ["observed_cases", "Reported cases"], ["forecast_incidence", "Forecast per 100k"],
  ["forecast_p50", "Forecast cases"], ["facilities", "Registered facilities"]];

export default function Explore({ q }) {
  const tree = useScopes();
  const sn = tree.find(scopeOf(q));
  const disease = scopeDisease(q, tree);
  const sp = stateParam(q);
  const layer = q.get("layer") || "incidence";
  const horizon = +(q.get("horizon") || 1);
  const week = q.get("week");
  const region = q.get("region");
  const observed = layer === "incidence" || layer === "observed_cases";
  const forecast = layer.startsWith("forecast");
  const mp = useApi(`/map/regions?layer=${layer}&disease_id=${disease}&horizon=${horizon}${observed && week ? `&week=${week}` : ""}${sp}`);
  const regs = useApi(`/metadata/regions?scope=pilot&limit=${sp ? 1000 : 200}${sp}`);
  const names = useMemo(() => Object.fromEntries((regs.data || []).map((r) => [r.municipality_id, r])), [regs.data]);
  const meta = mp.meta || {};
  const weeks = meta.weeks_available || [];
  const wi = Math.max(0, weeks.indexOf(meta.week));
  const values = mp.data?.values || {};
  const ranked = Object.entries(values).filter(([, v]) => v != null).sort((a, b) => b[1] - a[1]);
  const unitFmt = layer === "observed_cases" || layer === "forecast_p50" || layer === "facilities" ? fmt.int : fmt.d1;
  const sem = layer === "facilities" ? "OBSERVED" : meta.semantics;
  const sub = forecast ? html`Forecast median ${weekLabel(meta.target_week)} (+${horizon} wk) · hover for the P10–P90 interval`
    : observed ? html`Reported ${weekLabel(meta.week)} · values may be revised by the source` : "Registered CNES establishments per municipality (not operational capacity)";
  return html`<div class="page wide">
    <${TitleRow} title="Explore map" sub=${sub}
      right=${region && html`<${Link} to=${linkWith(`/regions/${region}`, { region })} cls="btn pri">Open ${names[region]?.name || "region"}</${Link}>`} />
    <div class="filters">
      <${DiseaseSelect} value=${disease} only=${(sn.region || sn.country)?.diseases} onChange=${(v) => setQuery({ disease: v, week: null })} />
      <select aria-label="Layer" value=${layer} onChange=${(e) => setQuery({ layer: e.target.value })}>${LAYERS.map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select>
      ${forecast && html`<${Seg} label="Horizon" value=${horizon} onChange=${(v) => setQuery({ horizon: v })} options=${[[1, "+1 wk"], [2, "+2 wk"], [3, "+3 wk"], [4, "+4 wk"]]} />`}
      <span class="sep"></span>
      ${sem && html`<${Sem} k=${sem} />`}${meta.data_status === "synthetic" && html`<${Sem} k="SYNTHETIC" />`}
    </div>
    ${observed && weeks.length > 1 && html`<div class="panel" style="padding:12px 20px;margin-bottom:var(--gap)">
      <div class="row"><label for="wk" style="font-weight:500;white-space:nowrap">Week</label>
        <input id="wk" type="range" min="0" max=${weeks.length - 1} value=${wi} aria-valuetext=${fdate(meta.week)}
          onChange=${(e) => setQuery({ week: weeks[+e.target.value] })} />
        <span class="num" style="white-space:nowrap;min-width:110px;text-align:right">${fdate(meta.week)}</span></div>
      <div class="row small muted" style="justify-content:space-between;margin-top:4px"><span>${fdate(weeks[0])}</span><span>${weeks.length} released weeks</span><span>${fdate(weeks[weeks.length - 1])}</span></div>
    </div>`}
    <${Notices} items=${mp.warnings || []} />
    <div class="grid">
      <div class="s8"><${Panel} title=${LAYERS.find((l) => l[0] === layer)?.[1]} note=${meta.unit}>
        <${ChoroplethMap} values=${values} breaks=${meta.breaks || []} selected=${region} focus=${Object.keys(values).length < 60 ? Object.keys(values) : []}
          height=${560} onSelect=${(id) => setQuery({ region: id })} label="Municipal explorer map"
          tooltip=${(id, v) => html`<b>${names[id]?.name || id}</b> <span class="muted">${names[id]?.state_abbr || ""}</span><br/>
            ${v == null ? html`<span class="muted">No data for this layer</span>` : html`${unitFmt(v)} <span class="muted">${meta.unit}</span>`}
            ${meta.intervals?.[id] ? html`<br/><span class="muted">P10–P90 ${fmt.d1(meta.intervals[id][0])}–${fmt.d1(meta.intervals[id][1])}</span>` : ""}`} />
        <${Legend} breaks=${meta.breaks || []} unit=${meta.unit} format=${unitFmt} note=${forecast ? `Quality: ${meta.quality || "—"}` : null} />
      </${Panel}></div>
      <div class="s4 stack"><${Panel} title="Ranking" note=${`${ranked.length} municipalities with a value`}>
        <${Loadable} q=${mp} sk=${html`<${SkRows} n=${5} cols=${2} />`}>${() => ranked.length ? html`
          <${BarList} format=${unitFmt} rows=${ranked.slice(0, 12).map(([id, v]) => ({ label: html`<button class="link" onClick=${() => setQuery({ region: id })}>${names[id]?.name || id}</button>`, value: v }))} />
          ${ranked.length > 12 && html`<div class="small muted" style="margin-top:8px">Showing 12 of ${ranked.length}</div>`}`
          : html`<${Empty}>No values on this layer.</${Empty}>`}</${Loadable}>
        ${region && html`<div class="well" style="margin-top:16px"><div class="row"><b style="font-weight:500">${names[region]?.name || region}</b><span class="spacer"></span><span class="idc muted">${region}</span></div>
          <div class="small muted" style="margin-top:4px">${values[region] == null ? "No data on this layer" : `${unitFmt(values[region])} ${meta.unit || ""}`}</div>
          <div class="row" style="margin-top:8px"><${Link} to=${linkWith(`/regions/${region}`, { region })} cls="link">Region detail</${Link}><${Link} to=${linkWith("/facilities", { region })} cls="link">Facilities</${Link}></div></div>`}
      </${Panel}>
      <${Panel} title="About this layer">
        <dl class="kv"><dt>Meaning</dt><dd>${forecast ? "Model estimate of reported cases; not all infections" : layer === "facilities" ? "Registered establishments; not operational capacity" : "Cases reported to surveillance; revised after publication"}</dd>
          <dt>Unit</dt><dd>${meta.unit || "—"}</dd><dt>Denominator</dt><dd>${layer.includes("incidence") ? "Resident population, prior year (IBGE estimate via InfoDengue)" : "None — counts"}</dd>
          <dt>Source</dt><dd>${forecast ? html`forecast <span class="mono">${(meta.forecast_id || "—").slice(0, 24)}</span>` : layer === "facilities" ? "CNES dados abertos" : "InfoDengue (SINAN notifications)"}</dd>
          <dt>No data</dt><dd>Hatched; never drawn as zero</dd></dl>
      </${Panel}></div>
    </div>
  </div>`;
}
