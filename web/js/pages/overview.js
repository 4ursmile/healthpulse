// Overview — question: "Where is reported disease pressure expected in the coming weeks, and how fresh is the evidence?"
import { html, useMemo } from "../../vendor/preact-htm.js";
import { useApi, setQuery, navigate, linkWith } from "../api.js";
import { Panel, TitleRow, Kpis, Notices, Sem, Status, Seg, Loadable, SkBlock, SkRows, Link, fmt, fdate, ago, weekLabel, Icon, Prov, Empty, Id } from "../ui.js";
import { ChoroplethMap, Legend } from "../map.js";
import { TimeChart } from "../charts.js";
import { scopeOf, stateParam, scopeDisease, useScopes } from "../scope.js";
import GlobalOverview from "./global.js";

// `only`: disease ids the current scope has a series for (others are not offered)
export function DiseaseSelect({ value, onChange, only }) {
  const d = useApi("/diseases");
  return html`<select aria-label="Disease" value=${value} onChange=${(e) => onChange(e.target.value)}>
    ${(d.data || [{ disease_id: "dengue", label: "Dengue" }]).filter((x) => !only || only.includes(x.disease_id)).map((x) => html`<option value=${x.disease_id}>${x.label}${x.data_status === "no_data" ? " (no data)" : x.data_status === "synthetic" ? " (synthetic)" : ""}</option>`)}
  </select>`;
}

export default function OverviewPage(props) {
  return scopeOf(props.q).country ? html`<${Overview} ...${props} />` : html`<${GlobalOverview} ...${props} />`;
}

function Overview({ q }) {
  const tree = useScopes();
  const sc = scopeOf(q);
  const { country: cnode, region: rnode } = tree.find(sc);
  const only = (rnode || cnode)?.diseases;
  const disease = scopeDisease(q, tree);
  const sp = stateParam(q);
  const horizon = +(q.get("horizon") || 2);
  const layer = q.get("layer") || "forecast_incidence";
  const region = q.get("region");
  const ov = useApi(`/overview?disease_id=${disease}&horizon=${horizon}${sp}`);
  const mp = useApi(`/map/regions?layer=${layer}&disease_id=${disease}&horizon=${horizon}${sp}`);
  const d = ov.data;
  const k = d?.kpis;
  const regions = d?.regions || [];
  const sel = region || regions[0]?.municipality_id;
  const ts = useApi(sel ? `/regions/${sel}/timeseries?disease_id=${disease}&weeks=104` : null);
  const top = regions[0];
  const unit = d?.disease?.units || "cases / week";
  const finding = top && k ? html`<b>${top.name}</b> has the highest ${top.forecast_incidence_per_100k != null ? "forecast" : "reported"} rate${", "}    ${fmt.d1(top.forecast_incidence_per_100k ?? top.incidence_per_100k)} per 100k ${top.forecast_incidence_per_100k != null ? weekLabel(top.target_week) : weekLabel(k.latest_week)}.
    Latest released surveillance week: ${fdate(k.latest_week)}${k.weeks_behind > 1 ? ` (${k.weeks_behind} weeks behind)` : ""}.` : null;
  const kp = k ? [
    { label: sc.region ? `Forecast, ${rnode?.name || sc.region}` : "Forecast, all regions with data", sem: "FORECAST", lead: true, value: k.forecast ? fmt.int(k.forecast.p50) : "—", unit: k.forecast ? "cases" : "",
      sub: k.forecast ? `P10–P90 ${fmt.int(k.forecast.p10)}–${fmt.int(k.forecast.p90)} · ${weekLabel(k.forecast.target_week)}` : "No published forecast" },
    { label: "Reported last released week", sem: "OBSERVED", value: fmt.int(k.reported_last_week), unit: "cases",
      sub: html`<span class=${k.change > 0 ? "up-bad" : k.change < 0 ? "down-good" : ""}>${fmt.signed(k.change)}</span> vs the week before (${fmt.int(k.reported_prev_week)})` },
    { label: "Draft alerts awaiting review", value: fmt.int(k.open_alerts), sub: k.open_alerts ? html`<${Link} to=${linkWith("/alerts")} cls="link">Review</${Link}>` : "None open" },
    { label: "Reporting delay", value: fmt.int(k.weeks_behind), unit: k.weeks_behind === 1 ? "week" : "weeks", sub: `behind the last complete week · ${k.regions_with_data} regions with data` },
  ] : [];
  const values = mp.data?.values || {};
  const names = useMemo(() => Object.fromEntries(regions.map((r) => [r.municipality_id, r])), [regions]);
  return html`<div class="page">
    <${TitleRow} title=${sc.region ? `${rnode?.name || sc.region} overview` : "Brazil overview"} sub=${finding || (ov.loading ? "Loading the latest released week…" : null)}
      right=${html`<span class="stamp"><span class=${"dot" + ((k?.weeks_behind || 0) > 2 ? " stale" : "")}></span>Updated ${ago(ov.at)}</span>
        <button class="btn" onClick=${() => { ov.reload(); mp.reload(); }} aria-label="Refresh"><${Icon} n="refresh" s=${14} />Refresh</button>
        <button class="btn pri" disabled=${!ov.meta?.forecast_id} title=${ov.meta?.forecast_id ? "" : "Needs a published forecast"}
          onClick=${() => navigate(linkWith("/simulation", { forecast_id: ov.meta.forecast_id, region: sel }))}><${Icon} n="sim" s=${14} />Run a preparedness scenario</button>`} />
    <div class="filters">
      <${DiseaseSelect} value=${disease} only=${only} onChange=${(v) => setQuery({ disease: v, region: null })} />
      <${Seg} label="Forecast horizon" value=${horizon} onChange=${(v) => setQuery({ horizon: v })} options=${[[1, "+1 wk"], [2, "+2 wk"], [3, "+3 wk"], [4, "+4 wk"]]} />
      <${Seg} label="Map layer" value=${layer} onChange=${(v) => setQuery({ layer: v })} options=${[["forecast_incidence", "Forecast per 100k"], ["incidence", "Reported per 100k"], ["forecast_p50", "Forecast cases"]]} />
      <span class="sep"></span>
      ${ov.meta?.data_status === "synthetic" && html`<${Sem} k="SYNTHETIC" />`}
      ${ov.meta?.forecast_quality && html`<${Status} s=${ov.meta.forecast_quality} title="Backtest evidence level of the published model" />`}
    </div>
    <${Notices} items=${ov.warnings || []} />
    <${Loadable} q=${ov} sk=${html`<${SkBlock} h=${96} />`}>${() => k ? html`<${Kpis} items=${kp} />` : html`<${Empty}>No ${d?.disease?.label} data has been ingested${sc.region ? ` for ${rnode?.name || sc.region}` : ""}.</${Empty}>`}</${Loadable}>
    <div class="grid" style="margin-top:var(--gap)">
      <div class="s8"><${Panel} title=${mp.meta?.label || "Map"} note=${html`${mp.meta?.semantics === "FORECAST" ? html`<${Sem} k="FORECAST" /> ${weekLabel(mp.meta?.target_week)} · ${mp.meta?.unit}` : html`<${Sem} k="OBSERVED" /> ${weekLabel(mp.meta?.week)} · ${mp.meta?.unit || ""}`}`}>
        <${ChoroplethMap} values=${values} breaks=${mp.meta?.breaks || []} selected=${sel} focus=${regions.map((r) => r.municipality_id)} height=${520}
          onSelect=${(id) => setQuery({ region: id })} label="Municipal choropleth"
          tooltip=${(id, v) => html`<b>${names[id]?.name || id}</b><br/>${v == null ? html`<span class="muted">No data</span>` : html`${fmt.d1(v)} <span class="muted">${mp.meta?.unit}</span>`}
            ${mp.meta?.intervals?.[id] ? html`<br/><span class="muted">P10–P90 ${fmt.d1(mp.meta.intervals[id][0])}–${fmt.d1(mp.meta.intervals[id][1])}</span>` : ""}`} />
        <${Legend} breaks=${mp.meta?.breaks || []} unit=${mp.meta?.unit} note=${mp.meta?.semantics === "FORECAST" ? "Forecast median; hover for the interval" : "Reported, may be revised"} />
      </${Panel}></div>
      <div class="s4 stack"><${Panel} title="Regions ranked" note=${`by ${layer === "incidence" ? "reported" : "forecast"} rate per 100k`} flush>
        <${Loadable} q=${ov}>${() => regions.length ? html`<table class="tbl">
          <thead><tr><th>Municipality</th><th class="num">Forecast /100k</th><th class="num">Reported</th></tr></thead>
          <tbody>${regions.slice(0, 8).map((r) => html`<tr class=${"click" + (r.municipality_id === sel ? " sel" : "")} onClick=${() => setQuery({ region: r.municipality_id })}>
            <td><div class="two">${r.name}<div class="s">${r.state_abbr} · ${r.open_alerts ? `${r.open_alerts} draft alert${r.open_alerts > 1 ? "s" : ""}` : r.quality_status === "insufficient_data" ? "insufficient data" : `pop ${fmt.compact(r.population)}`}</div></div></td>
            <td class="num"><div class="two">${fmt.d1(r.forecast_incidence_per_100k)}<div class="s">${r.p10 != null ? `${fmt.int(r.p10)}–${fmt.int(r.p90)} cases` : "—"}</div></div></td>
            <td class="num"><div class="two">${fmt.int(r.observed_cases)}<div class="s">${fmt.signed(r.change_vs_prev_week)}</div></div></td></tr>`)}</tbody></table>`
          : html`<div style="padding:0 20px"><${Empty}>No regions with data.</${Empty}></div>`}</${Loadable}>
      </${Panel}>
        <${Panel} title="Evidence freshness" note="Latest release per source" flush>
          <${Loadable} q=${ov} sk=${html`<${SkRows} n=${5} cols=${2} />`}>${() => { const rows = (d?.freshness || []).filter((s) => s.group !== "fixture")
            .sort((a, b) => (b.last_success_at ? 1 : 0) - (a.last_success_at ? 1 : 0)); const shown = rows.slice(0, 6);
            return html`<ul class="fresh">${shown.map((s) => html`<li title=${`${s.group} · ${s.release_class.replace(/_/g, " ").toLowerCase()}`}>
              <span class=${"dot" + (s.state === "healthy" ? "" : s.state === "stale" ? " stale" : ["failed", "quarantined"].includes(s.state) ? " bad" : " off")}></span>
              <span class="n">${s.display_name}</span>
              <span class="w">${s.coverage_end ? `to ${fdate(s.coverage_end, { day: "numeric", month: "short" })}` : html`<${Status} s=${s.state} />`}</span></li>`)}</ul>
            <div class="pfoot"><${Link} to=${linkWith("/data-lab")} cls="link small">${rows.length > shown.length ? `All ${rows.length} sources` : "Source status"}</${Link}></div>`; }}</${Loadable}>
        </${Panel}>
      </div>
      <div class="s12"><${Panel} title=${html`${names[sel]?.name || "Region"} — ${d?.disease?.metric_label?.toLowerCase() || "reported cases"}`}
        note=${html`<div class="ckey" style="margin-top:4px"><span><i></i>Reported (observed)</span><span><i class="dash"></i>Forecast median</span><span><i class="band"></i>P10–P90 interval</span></div>`}
        right=${sel && html`<${Link} to=${linkWith(`/regions/${sel}`, { region: sel })} cls="btn sm">Open region</${Link}>`}>
        <${Loadable} q=${ts} sk=${html`<${SkBlock} h=${240} />`}>${(t, meta) => t.observed.length ? html`
          <${TimeChart} observed=${t.observed.map((o) => ({ t: o.week_start, y: o.observed_cases }))} forecast=${t.forecast.map((f) => ({ t: f.target_week, ...f }))}
            markers=${t.holidays.map((h) => ({ t: h.date, label: h.holiday_name }))} unit="cases" label=${`${names[sel]?.name} reported cases and forecast`} />
          <${Prov} items=${[meta.metric_label, t.model ? html`Model <span class="idc">${t.model.model_version}</span>` : "no published model",
            t.model && html`Forecast <${Id} id=${t.model.forecast_id} n=${16} />`, t.model && `issued ${ago(t.model.issue_at)}`,
            `Source vintage ${meta.source_vintage_ids?.length || 0}`, "Ticks on the axis mark national holidays"]} />`
          : html`<${Empty}>No series for this municipality.</${Empty}>`}</${Loadable}>
      </${Panel}></div>
    </div>
  </div>`;
}
