// Region detail — question: "How many reported cases are expected here in the next four weeks, and on what evidence?"
import { html } from "../../vendor/preact-htm.js";
import { useApi, setQuery, navigate, linkWith } from "../api.js";
import { Panel, TitleRow, Kpis, Notices, Sem, Status, Seg, Loadable, SkBlock, SkRows, Empty, Link, Prov, Icon, fmt, fdate, ago, weekLabel } from "../ui.js";
import { TimeChart, Bars } from "../charts.js";
import { DiseaseSelect, Chain, chainFromLineage, Analyst } from "./_product_shared.js";
import { NowcastPanel } from "../nowcast.js";
import { MobilityPanel } from "../geo_mobility.js";

export default function Region({ params, q }) {
  const id = params.id;
  const disease = q.get("disease") || "dengue";
  const span = +(q.get("span") || 52);
  const reg = useApi(`/regions/${id}`);
  const ctxq = useApi(`/regions/${id}/context`);
  const ts = useApi(`/regions/${id}/timeseries?disease_id=${disease}&weeks=${span}`);
  const t = ts.data;
  const fid = t?.model?.forecast_id;
  const lin = useApi(fid ? `/admin/lineage?forecast_id=${fid}` : null);
  const r = reg.data;
  const fc = t?.forecast || [];
  const last = t?.observed?.length ? t.observed[t.observed.length - 1] : null;
  const h1 = fc.find((f) => f.horizon === 1), h4 = fc.find((f) => f.horizon === 4) || fc[fc.length - 1];
  const sub = h1 && h1.p50 != null ? html`Model estimate <b>${fmt.int(h1.p50)}</b> reported cases ${weekLabel(h1.target_week)} (P10–P90 ${fmt.int(h1.p10)}–${fmt.int(h1.p90)});
    last released week ${fdate(last?.week_start)} had ${fmt.int(last?.observed_cases)}.` : t && !t.observed.length ? "No series for this disease here." : null;
  const recent = (t?.climate || []).slice(-26);
  const kpis = r ? [
    { label: "Reported, last released week", sem: "OBSERVED", value: fmt.int(last?.observed_cases), unit: "cases", sub: last ? weekLabel(last.week_start) : "—" },
    { label: "Forecast +1 week", sem: "FORECAST", lead: true, value: h1?.p50 != null ? fmt.int(h1.p50) : "—", unit: h1?.p50 != null ? "cases" : "",
      sub: h1 ? (h1.quality_status === "ok" ? `P10–P90 ${fmt.int(h1.p10)}–${fmt.int(h1.p90)}` : "Insufficient data") : "No published forecast" },
    { label: "Forecast +4 weeks", sem: "FORECAST", value: h4?.p50 != null ? fmt.int(h4.p50) : "—", unit: h4?.p50 != null ? "cases" : "", sub: h4 ? weekLabel(h4.target_week) : "—" },
    { label: "Residents", value: fmt.compact(r.population), sub: `${fmt.int(r.facilities)} registered CNES facilities` },
  ] : [];
  return html`<div class="page">
    <${TitleRow} crumb=${html`<${Link} to=${linkWith("/overview")} cls="link">Overview</${Link}> / ${r?.state_abbr || ""}`} title=${r?.name || id} sub=${sub}
      right=${html`<${DiseaseSelect} value=${disease} onChange=${(v) => setQuery({ disease: v })} />
        <button class="btn pri" disabled=${!fid} title=${fid ? "" : "Needs a published forecast"} onClick=${() => navigate(linkWith("/simulation", { forecast_id: fid, region: id }))}><${Icon} n="sim" s=${14} />Run a preparedness scenario</button>`} />
    <${Notices} items=${[...(reg.error?.status === 404 ? ["Unknown municipality code."] : []), ...(ts.warnings || [])]} />
    <${Loadable} q=${reg} sk=${html`<${SkBlock} h=${96} />`}>${() => html`<${Kpis} items=${kpis} />`}</${Loadable}>
    <div class="grid" style="margin-top:var(--gap)">
      <div class="s12"><${Panel} title=${t ? `${ts.meta?.metric_label || "Reported cases"} and forecast` : "Reported cases and forecast"}
        note=${html`<div class="ckey" style="margin-top:4px"><span><i></i>Reported (observed)</span><span><i class="dash"></i>Forecast median</span><span><i class="band"></i>P10–P90</span><span>Axis ticks: national holidays</span></div>`}
        right=${html`<${Seg} label="Window" value=${span} onChange=${(v) => setQuery({ span: v })} options=${[[26, "6 mo"], [52, "1 yr"], [156, "3 yr"]]} />${t?.model && html`<${Status} s=${t.model.quality} />`}`}>
        <${Loadable} q=${ts} sk=${html`<${SkBlock} h=${280} />`}>${(d, meta) => d.observed.length ? html`
          <${TimeChart} height=${280} observed=${d.observed.map((o) => ({ t: o.week_start, y: o.observed_cases }))} forecast=${fc.map((f) => ({ t: f.target_week, ...f }))}
            markers=${d.holidays.map((h) => ({ t: h.date, label: h.holiday_name }))} unit="cases" label=${`${r?.name} reported cases and forecast`} />
          <${Prov} items=${[meta.units, `signal: ${meta.signal_type}`, `${meta.source_vintage_ids?.length || 0} source vintage(s)`,
            d.model ? `forecast issued ${ago(d.model.issue_at)}, anchored to ${fdate(d.model.anchor_week)}` : "no published model", meta.data_status === "synthetic" && html`<${Sem} k="SYNTHETIC" />`]} />`
          : html`<${Empty}>No ${disease} series for this municipality.</${Empty}>`}</${Loadable}>
      </${Panel}></div>
      ${disease.endsWith("_sari") && html`<div class="s12"><${NowcastPanel} disease=${disease} municipalityId=${id} state=${r?.state_abbr} /></div>`}
      <div class="s7"><${Panel} title="Forecast by horizon" note="Model output; intervals from out-of-fold residuals" flush>
        <${Loadable} q=${ts} sk=${html`<${SkRows} n=${4} />`}>${() => fc.length ? html`<table class="tbl">
          <thead><tr><th>Target week</th><th class="num">Horizon</th><th class="num">Median</th><th class="num">P10</th><th class="num">P90</th><th>Quality</th></tr></thead>
          <tbody>${fc.map((f) => html`<tr><td>${fdate(f.target_week)}</td><td class="num">+${f.horizon} wk</td><td class="num">${fmt.int(f.p50)}</td>
            <td class="num">${fmt.int(f.p10)}</td><td class="num">${fmt.int(f.p90)}</td><td><${Status} s=${f.quality_status} /></td></tr>`)}</tbody></table>`
          : html`<div style="padding:0 20px"><${Empty}>No published forecast for this disease.</${Empty}></div>`}</${Loadable}>
      </${Panel}></div>
      <div class="s5"><${Panel} title="Ask the analyst" note="Reads verified outputs through typed tools; never invents numbers">
        ${fid ? html`<${Analyst} context=${{ forecast_id: fid, municipality_id: id, disease_id: disease }}
          suggestions=${["Why is it elevated next week?", "How many lives will this save?"]} />` : html`<${Empty}>Needs a published forecast.</${Empty}>`}
      </${Panel}></div>
      <${MobilityPanel} cls="s12" scope="br" unitId=${id} name=${r?.name} />
      <div class="s6"><${Panel} title="Weekly rainfall" note=${html`<${Sem} k="OBSERVED" /> ERA5 reanalysis, last 26 weeks, mm`}>
        <${Loadable} q=${ts} sk=${html`<${SkBlock} h=${120} />`}>${() => recent.length ? html`<${Bars} data=${recent.map((c) => ({ t: c.week_start, y: c.rain_mm_sum }))} unit="mm" label="Weekly rainfall" />
          <div class="small muted" style="margin-top:6px">Missing bars are weeks below the coverage threshold (not zero rain).</div>`
          : html`<${Empty}>No weather series.</${Empty}>`}</${Loadable}>
      </${Panel}></div>
      <div class="s6"><${Panel} title="Mean temperature" note=${html`<${Sem} k="OBSERVED" /> weekly mean 2 m temperature, °C`}>
        <${Loadable} q=${ts} sk=${html`<${SkBlock} h=${120} />`}>${() => recent.length ? html`<${TimeChart} height=${120} hueOnObserved=${false} yfmt=${fmt.d1}
          observed=${recent.map((c) => ({ t: c.week_start, y: c.t2m_mean }))} unit="°C" label="Weekly mean temperature" />`
          : html`<${Empty}>No weather series.</${Empty}>`}</${Loadable}>
      </${Panel}></div>
      <div class="s12"><${Panel} title="Place context" note=${html`<${Sem} k="OBSERVED" /> census, register and remote-sensing context · WorldPop is <${Sem} k="PROXY" title="Modelled population grid" />`}>
        <${Loadable} q=${ctxq} sk=${html`<${SkBlock} h=${120} />`}>${(c, meta) => html`<${PlaceContext} c=${c} meta=${meta} />`}</${Loadable}>
      </${Panel}></div>
      <div class="s8"><${Panel} title="Provenance" note="Where this forecast came from: every link is an immutable, hashed artifact">
        ${fid ? html`<${Loadable} q=${lin} sk=${html`<${SkBlock} h=${40} />`}>${(l) => html`<${Chain} links=${chainFromLineage(l)} />`}</${Loadable}>`
          : html`<${Empty}>No published forecast to trace.</${Empty}>`}
        <div class="row wrap" style="margin-top:12px"><${Link} to=${linkWith("/workforce", { region: id })} cls="link">Workforce context</${Link}>
          <${Link} to=${linkWith("/facilities", { region: id })} cls="link">Facilities</${Link}><${Link} to=${linkWith("/model-lab")} cls="link">Model evidence</${Link}></div>
      </${Panel}></div>
      <div class="s4"><${Panel} title="Upcoming national holidays" note="Scheduled, known in advance (Nager.Date)" flush>
        <${Loadable} q=${ts} sk=${html`<${SkRows} n=${3} cols=${2} />`}>${(d) => { const up = d.holidays.filter((h) => h.date >= (last?.week_start || "")).slice(0, 5);
          return up.length ? html`<table class="tbl dense"><tbody>${up.map((h) => html`<tr><td>${h.holiday_name}</td><td class="num">${fdate(h.date)}</td></tr>`)}</tbody></table>`
            : html`<div style="padding:0 20px"><${Empty}>No holidays in the calendar after the last released week.</${Empty}></div>`; }}</${Loadable}>
      </${Panel}></div>
    </div>
  </div>`;
}

function PlaceContext({ c, meta }) {
  const ps = c.population_series || [];
  const lastPop = ps.length ? ps[ps.length - 1] : null;
  const firstPop = ps.length ? ps[0] : null;
  const growth = lastPop && firstPop && firstPop.population ? lastPop.population / firstPop.population - 1 : null;
  const p = c.profile, e = c.environment_latest, a = c.arranjo, w = c.worldpop, b = c.beds;
  const cell = (label, value, sub) => html`<div class="ctx-cell"><div class="ctx-l">${label}</div><div class="ctx-v num">${value}</div>${sub && html`<div class="ctx-s">${sub}</div>`}</div>`;
  return html`<div class="ctx">
    <div class="ctx-grid">
      ${cell("Residents (IBGE)", fmt.compact(lastPop?.population), lastPop ? `${lastPop.reference_year}${growth != null ? ` · ${growth >= 0 ? "+" : ""}${fmt.pct(growth, 1)} since ${firstPop.reference_year}` : ""}` : "not ingested")}
      ${cell("WorldPop grid", fmt.compact(w?.worldpop_population), w ? `${w.reference_year} · ~${fmt.int(w.populated_area_km2_approx)} km² populated` : "not ingested")}
      ${cell("Registered beds", fmt.int(b?.registered_beds), b ? `${fmt.int(b.registered_icu_beds)} ICU · ${fmt.int(b.registered_beds_sus)} SUS · ${b.hospitals} hospitals` : "not ingested")}
      ${cell("Sewage coverage", p?.sewage_pct != null ? fmt.pct(p.sewage_pct / 100, 0) : "—", p ? `water ${p.water_supply_pct != null ? fmt.pct(p.water_supply_pct / 100, 0) : "—"} · waste ${p.garbage_collection_pct != null ? fmt.pct(p.garbage_collection_pct / 100, 0) : "—"}` : "Zenodo profile not ingested")}
      ${cell("Vegetation (NDVI)", e?.ndvi_mean != null ? e.ndvi_mean.toFixed(2) : "—", e ? `week of ${fdate(e.week_start)} · LST ${fmt.d1(e.lst_day_c)} °C` : "not ingested")}
      ${cell("Commuting arranjo", a ? fmt.int(c.arranjo_size) : "—", a ? `municipalities in ${a.arranjo_name} · integration ${fmt.d1(a.integration_index * 100)}%` : "isolated municipality or not ingested")}
    </div>
    ${c.arranjo_members.length > 0 && html`<div class="ctx-arr"><div class="small muted" style="margin-bottom:6px">Most connected neighbours (Census 2010 commuting, people who work or study elsewhere in the arranjo)</div>
      <${BarListLite} rows=${c.arranjo_members.slice(0, 8).map((m) => ({ label: m.name || m.municipality_id, value: m.commuters_in_arranjo, sub: `${fmt.d1(m.integration_index * 100)}%` }))} /></div>`}
    <${Prov} items=${Object.values(meta.sources || {}).slice(0, 4)} />
  </div>`;
}

function BarListLite({ rows }) {
  const m = Math.max(1, ...rows.map((r) => r.value || 0));
  return html`<div class="barlist">${rows.map((r, i) => html`<div class=${"it" + (i === 0 ? " top" : "")}>
    <span>${r.label}</span><span class="num">${fmt.int(r.value)}<span class="muted small"> ${r.sub}</span></span>
    <div class="tr"><i style=${`width:${((r.value || 0) / m) * 100}%`}></i></div></div>`)}</div>`;
}
