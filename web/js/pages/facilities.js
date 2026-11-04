// Facilities — question: "What registered care sites exist in each municipality, and what capacity is actually confirmed?"
import { html, useMemo, useState } from "../../vendor/preact-htm.js";
import { useApi, setQuery, linkWith } from "../api.js";
import { Panel, TitleRow, Notices, Loadable, SkRows, SkBlock, Empty, Link, Sem, Kpis, fmt, fdate } from "../ui.js";
import { ChoroplethMap, Legend } from "../map.js";
import { RegionSelect } from "./_product_shared.js";
import { stateParam } from "../scope.js";

export default function Facilities({ q }) {
  const region = q.get("region") || "";
  const type = q.get("type") || "";
  const [limit, setLimit] = useState(12);
  const sp = stateParam(q);
  const fac = useApi(`/facilities?limit=5000${region ? `&region_id=${region}` : sp}${type ? `&facility_type=${encodeURIComponent(type)}` : ""}`);
  const mp = useApi(`/map/regions?layer=facilities${sp}`);
  const all = useApi("/facilities?limit=1");
  const d = fac.data;
  const types = (all.data?.by_type || []).map((t) => t.facility_type);
  const pts = useMemo(() => (d?.facilities || []).map((f) => ({ lat: f.lat, lon: f.lon, name: f.name })), [d]);
  const inpatient = (d?.by_type || []).reduce((a, t) => a + t.inpatient, 0);
  const withCoords = (d?.facilities || []).filter((f) => f.lat != null).length;
  return html`<div class="page">
    <${TitleRow} title="Facilities" sub="Registered CNES establishments. Registered means listed in the national register — operational beds, staff and teams need confirmed planner inputs."
      right=${html`<${RegionSelect} value=${region} onChange=${(v) => setQuery({ region: v || null })} />
        <select aria-label="Facility type" value=${type} onChange=${(e) => setQuery({ type: e.target.value || null })}><option value="">All types</option>${types.map((t) => html`<option value=${t}>${t}</option>`)}</select>`} />
    <${Notices} items=${fac.warnings || []} />
    <${Loadable} q=${fac} sk=${html`<${SkBlock} h=${96} />`}>${() => html`<${Kpis} items=${[
      { label: "Registered facilities", sem: "OBSERVED", lead: true, value: fmt.int(d.total), sub: region ? "in this municipality" : sp ? "in this state" : "across ingested municipalities" },
      { label: "With inpatient care", value: fmt.int(inpatient), sub: "registered hospital care flag" },
      { label: "Registered beds", sem: "OBSERVED", value: d.beds ? fmt.int(d.beds.registered_beds) : "—",
        sub: d.beds ? `${fmt.int(d.beds.registered_icu_beds)} ICU · ${fmt.int(d.beds.registered_beds_sus)} SUS · CNES ${d.beds.competence || ""}` : "beds file not ingested" },
      { label: "Confirmed operational capacity", sem: "ASSUMPTION", value: fmt.int(d.overrides.length), unit: d.overrides.length === 1 ? "entry" : "entries", sub: "planner-entered overrides" },
      { label: "Located on the map", value: fmt.pct(d.total ? withCoords / Math.max(1, d.facilities.length) : null), sub: "have coordinates in CNES" }]} />`}</${Loadable}>
    <div class="grid" style="margin-top:var(--gap)">
      <div class="s8"><${Panel} title="Registered facilities" note=${html`<${Sem} k="OBSERVED" /> points are CNES coordinates; shading is facilities per municipality`}>
        <${ChoroplethMap} values=${mp.data?.values || {}} breaks=${mp.meta?.breaks || []} points=${pts} selected=${region || null}
          focus=${region ? [region] : Object.keys(mp.data?.values || {})} height=${460} onSelect=${(id) => setQuery({ region: id })} label="Facility map"
          tooltip=${(id, v) => html`<b>${id}</b><br/>${v == null ? html`<span class="muted">Not ingested</span>` : `${fmt.int(v)} registered facilities`}`} />
        <${Legend} breaks=${mp.meta?.breaks || []} unit="facilities" format=${fmt.int} note="Dots: individual establishments" />
      </${Panel}></div>
      <div class="s4"><${Panel} title="By type" flush>
        <${Loadable} q=${fac} sk=${html`<${SkRows} n=${5} cols=${2} />`}>${() => d.by_type.length ? html`<table class="tbl">
          <thead><tr><th>Type</th><th class="num">Registered</th><th class="num">Inpatient</th><th class="num">Beds</th></tr></thead>
          <tbody>${d.by_type.map((t) => html`<tr class=${"click" + (t.facility_type === type ? " sel" : "")} onClick=${() => setQuery({ type: t.facility_type === type ? null : t.facility_type })}>
            <td>${t.facility_type}</td><td class="num">${fmt.int(t.n)}</td><td class="num">${fmt.int(t.inpatient)}</td><td class="num">${t.beds ? fmt.int(t.beds) : "—"}</td></tr>`)}</tbody></table>`
          : html`<div style="padding:0 20px"><${Empty} action=${html`<${Link} to="/admin/sources/cnes_facilities_beds" cls="btn sm">Ingest CNES</${Link}>`}>No facilities ingested.</${Empty}></div>`}</${Loadable}>
      </${Panel}></div>
      <div class="s8"><${Panel} title="Establishments" note="Registered, availability unverified" flush
        right=${d && html`<span class="small muted">${fmt.int(Math.min(limit, d.facilities.length))} of ${fmt.int(d.total)}</span>`}>
        <${Loadable} q=${fac} sk=${html`<${SkRows} n=${6} />`}>${() => d.facilities.length ? html`<table class="tbl cards">
          <thead><tr><th>Name</th><th>Type</th><th>Sphere</th><th class="num">Beds</th><th class="num">CNES</th></tr></thead>
          <tbody>${d.facilities.slice(0, limit).map((f) => html`<tr><td><div class="two">${f.name}<div class="s">${f.has_inpatient ? "inpatient care registered" : "outpatient"}${f.sus_ambulatory ? " · SUS ambulatory" : ""}</div></div></td>
            <td class="sub">${f.facility_type}</td><td class="hide-sm muted">${(f.admin_sphere || "—").toLowerCase()}</td>
            <td class="num">${f.registered_beds != null ? html`${fmt.int(f.registered_beds)}${f.registered_icu_beds ? html`<span class="muted small"> · ${fmt.int(f.registered_icu_beds)} ICU</span>` : ""}` : "—"}</td><td class="num idc">${f.facility_id}</td></tr>`)}</tbody></table>
          ${d.facilities.length > limit && html`<div style="padding:8px 20px"><button class="btn sm" onClick=${() => setLimit(limit + 50)}>Show 50 more</button></div>`}`
          : html`<div style="padding:0 20px"><${Empty}>No establishments match.</${Empty}></div>`}</${Loadable}>
      </${Panel}></div>
      <div class="s4"><${Panel} title="Confirmed capacity" note=${html`<${Sem} k="ASSUMPTION" /> planner-entered operational inputs`} flush>
        <${Loadable} q=${fac} sk=${html`<${SkRows} n=${2} cols=${2} />`}>${() => d.overrides.length ? html`<table class="tbl dense">
          <thead><tr><th>Municipality</th><th class="num">Beds</th><th class="num">Visits/wk</th><th class="num">Expires</th></tr></thead>
          <tbody>${d.overrides.map((o) => html`<tr><td class="idc">${o.municipality_id}</td><td class="num">${fmt.int(o.verified_available_beds)}</td><td class="num">${fmt.int(o.visits_per_week)}</td><td class="num">${fdate(o.expires_at)}</td></tr>`)}</tbody></table>`
          : html`<div style="padding:0 20px 8px"><${Empty}>No confirmed operational capacity. Scenarios use labelled planner assumptions instead.</${Empty}>
            <div style="margin-top:12px"><${Link} to=${linkWith("/simulation", { region: region || null })} cls="link">Set capacity in a scenario</${Link}></div></div>`}</${Loadable}>
      </${Panel}></div>
    </div>
  </div>`;
}
