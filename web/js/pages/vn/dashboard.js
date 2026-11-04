// Viet Nam dashboard — question: "Which provinces need attention this month, why, and how did we get here?"
import { html, useMemo, useState, useEffect } from "../../../vendor/preact-htm.js";
import { useApi, setQuery, navigate, linkWith } from "../../api.js";
import { Panel, TitleRow, Kpis, Notices, Loadable, SkBlock, SkRows, Empty, Icon, Seg, Prov, fmt, fdate, ago } from "../../ui.js";
import { VnMap, LEVEL, SEQ, HEAT, classBreaks, colorFor } from "../../vnmap.js";
import { LevelPill, MultiLine, Playback, AnalystStream, monthLabel } from "./_vn.js";
import { scopeOf, setScope } from "../../scope.js";
import { MobilityPanel } from "../../geo_mobility.js";

// the selected province IS the scope (Viet Nam · <province>), so every screen follows the same pick
const pickProvince = (id) => setScope(id ? `vn:${id}` : "vn");

const ORDER = { alert: 3, warning: 2, watch: 1, normal: 0, no_data: -1 };

function Setup({ status }) {
  return html`<div class="page"><${TitleRow} title="Viet Nam" sub="Dengue situation for the 34 provinces in force since 1 July 2025." />
    <${Panel} title="Data not built yet"><${Empty} icon="data">
      ${status?.error ? String(status.error.message) : "Run the one-time download:"} <code>.venv/bin/hp vn-fetch</code> (≈5–15 min: OCHA boundaries, HeiGIT population and facilities,
      OpenDengue, WHO workforce, NASA POWER weather). Then reload this page.</${Empty}></${Panel}></div>`;
}

function nearest(p, all, k = 3) {
  return all.filter((q) => q.province_id !== p.province_id).map((q) => ({ q, d: (q.lat - p.lat) ** 2 + ((q.lon - p.lon) * Math.cos(p.lat * Math.PI / 180)) ** 2 }))
    .sort((a, b) => a.d - b.d).slice(0, k).map((x) => x.q);
}

export default function VnDashboard({ q }) {
  const status = useApi("/vn/status");
  const st = useApi(status.data?.built ? "/vn/state" : null);
  const tl = useApi(status.data?.built ? "/vn/timeline" : null);
  const nat = useApi(status.data?.built ? "/vn/national" : null);
  const provs = useApi(status.data?.built ? "/vn/provinces" : null);
  const layer = q.get("layer") || "level";
  const sel = scopeOf(q).region;
  const [fx, setFx] = useState({ pulse: true, flow: true, labels: true });
  const frames = tl.data?.frames || [];
  const [ix, setIx] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(2);
  useEffect(() => { if (frames.length && ix == null) setIx(frames.length - 1); }, [frames.length]);
  const series = useApi(sel ? `/vn/provinces/${sel}/series` : null);

  const pv = provs.data || [];
  const pById = useMemo(() => Object.fromEntries(pv.map((p) => [p.province_id, p])), [pv]);
  const now = st.data;
  const frame = ix != null ? frames[ix] : null;
  const isNow = frame && now && frame.month === now.month;
  const pop = tl.data?.population || {};
  // what the map shows for this frame
  const view = useMemo(() => {
    if (!frame) return { colors: {}, val: {}, lv: {} };
    const val = {}, lv = frame.lv;
    for (const [id, v] of Object.entries(frame.v)) val[id] = layer === "inc" ? (pop[id] ? (v / pop[id]) * 1e5 : null) : v;
    let colors = {};
    if (layer === "level") for (const id of Object.keys(pById)) colors[id] = LEVEL[lv[id] || "no_data"].c;
    else { const br = classBreaks(Object.values(val)); for (const [id, v] of Object.entries(val)) colors[id] = colorFor(v, br, layer === "inc" ? HEAT : SEQ); colors.__br = br; }
    return { colors, val, lv, br: colors.__br };
  }, [frame, layer, pById]);
  const flagged = useMemo(() => pv.filter((p) => ["alert", "warning"].includes(view.lv[p.province_id])), [view.lv, pv]);
  const pulses = fx.pulse ? flagged.map((p) => ({ lon: p.lon, lat: p.lat, level: view.lv[p.province_id] })) : [];
  const flows = fx.flow ? flagged.flatMap((p) => nearest(p, pv, 3).filter((n) => ORDER[view.lv[n.province_id]] < ORDER[view.lv[p.province_id]])
    .map((n) => ({ from: [p.lon, p.lat], to: [n.lon, n.lat], w: view.lv[p.province_id] === "alert" ? 1 : 0.6 }))) : [];
  const labels = fx.labels ? [...pv].sort((a, b) => (ORDER[view.lv[b.province_id]] ?? -1) - (ORDER[view.lv[a.province_id]] ?? -1) || (view.val[b.province_id] ?? 0) - (view.val[a.province_id] ?? 0))
    .filter((p, i) => ORDER[view.lv[p.province_id]] >= 2 || p.province_id === sel || (i < 8 && ORDER[view.lv[p.province_id]] >= 1))
    .map((p) => ({ id: p.province_id, lon: p.lon, lat: p.lat, level: view.lv[p.province_id], text: p.name })) : [];

  if (status.error || (status.data && !status.data.built)) return html`<${Setup} status=${status} />`;
  const k = now?.counts || {};
  const top = now?.provinces?.[0];
  const info = status.data?.model;
  const sub = now ? html`<b>${k.alert || 0}</b> province${k.alert === 1 ? "" : "s"} on alert and <b>${k.warning || 0}</b> on warning for ${monthLabel(now.month)}${top ? html` — highest pressure in <b>${top.name}</b> (${fmt.d2(top.ratio)}× its seasonal norm)` : ""}.
    Estimates from a climate model calibrated to WHO national totals; not reported cases.` : "Loading…";
  const rows = now?.provinces || [];
  const selRow = rows.find((r) => r.province_id === sel);
  const P = pById[sel];
  const lastWho = nat.data?.weekly?.at(-1)?.t || nat.data?.monthly?.at(-1)?.t;

  return html`<div class="page vn">
    <${TitleRow} title=${P ? `${P.name} — dengue situation` : "Viet Nam — dengue situation"} sub=${P && selRow ? html`${selRow.reason}. Model estimate, not reported cases.` : sub}
      right=${html`<span class="stamp"><span class="dot"></span>Weather to ${info?.latest_weather_month?.slice(0, 7) || "—"}</span>
        <button class="btn pri" onClick=${() => navigate(linkWith("/vn/simulate", { origin: sel || top?.province_id }))}><${Icon} n="sim" s=${14} />Simulate response</button>`} />
    <${Notices} items=${st.warnings || []} />
    <${Loadable} q=${st} sk=${html`<${SkBlock} h=${96} />`}>${() => html`<${Kpis} items=${P && selRow ? [
      { label: `${P.name} this month`, lead: true, value: html`<${LevelPill} l=${selRow.level} />`, sub: `rank ${rows.indexOf(selRow) + 1} of ${rows.length} provinces` },
      { label: "Estimated cases", sem: "ESTIMATE", value: fmt.int(selRow.estimate), sub: html`<span class=${selRow.ratio > 1 ? "up-bad" : "down-good"}>${fmt.d2(selRow.ratio)}×</span> seasonal norm ${fmt.int(selRow.normal)}` },
      { label: "Per 100k", value: fmt.d1(selRow.incidence_per_100k), sub: selRow.trend != null ? `${selRow.trend > 0 ? "▲" : "▼"} ${fmt.pct(Math.abs(selRow.trend))} vs last month` : "" },
      { label: "Hospitals within 1 h", value: fmt.pct(P.access_hospital_1h_share), sub: `${fmt.int(P.hospitals_osm)} hospitals (OSM) · ${fmt.int(P.beds_est)} beds est.` },
      { label: "Country", value: html`<span style="color:${LEVEL.alert.c}">${k.alert || 0}</span> / <span style="color:${LEVEL.warning.c}">${k.warning || 0}</span>`, sub: html`provinces on alert / warning · <button class="link small" onClick=${() => pickProvince(null)}>all provinces</button>` },
    ] : [
      { label: "Provinces on alert / warning", lead: true, value: html`<span style="color:${LEVEL.alert.c}">${k.alert || 0}</span> / <span style="color:${LEVEL.warning.c}">${k.warning || 0}</span>`, sub: `${k.watch || 0} on watch · ${k.normal || 0} normal` },
      { label: "Estimated cases this month", sem: "ESTIMATE", value: fmt.compact(now.national_estimate), sub: html`<span class=${now.national_estimate > now.national_normal ? "up-bad" : "down-good"}>${fmt.signed(((now.national_estimate / now.national_normal) - 1) * 100, fmt.d1)}%</span> vs seasonal norm ${fmt.compact(now.national_normal)}` },
      { label: "Highest ratio to norm", value: top ? top.name : "—", sub: top ? `${fmt.d2(top.ratio)}× · ${fmt.d1(top.incidence_per_100k)} per 100k` : "" },
      { label: "Model skill", value: info ? `r = ${fmt.d2(info.national_shape_corr_2011_on)}` : "—", sub: info ? `national seasonal shape vs WHO, ${info.national_months_compared} months` : "" },
      { label: "Latest national report", sem: "OBSERVED", value: lastWho ? fdate(lastWho, { month: "short", year: "numeric" }) : "—", sub: "WHO WPRO via OpenDengue" },
    ]} />`}</${Loadable}>
    <div class="filters" style="margin-top:var(--gap)">
      <${Seg} label="Map layer" value=${layer} onChange=${(v) => setQuery({ layer: v })} options=${[["level", "Alert level"], ["inc", "Cases per 100k"], ["cases", "Cases"]]} />
      <span class="sep"></span>
      ${[["pulse", "Pulsing alerts"], ["flow", "Spread pressure"], ["labels", "Labels"]].map(([k2, l]) => html`<label class="pill toggle-pill"><input type="checkbox" checked=${fx[k2]} onChange=${(e) => setFx({ ...fx, [k2]: e.target.checked })} />${l}</label>`)}
    </div>
    <div class="grid">
      ${sel && P && html`<div class="s12"><${Panel} title=${html`${P.name} ${selRow && html`<${LevelPill} l=${selRow.level} />`}`}
          note=${P.former_provinces.length > 1 ? `Merged 1 July 2025 from ${P.former_provinces.join(", ")}` : "Boundary unchanged in 2025"}
          right=${html`<button class="btn sm" onClick=${() => navigate(linkWith("/vn/simulate", { origin: sel }))}>Simulate outbreak here</button><button class="btn sm ghost" onClick=${() => pickProvince(null)} aria-label="Back to all provinces"><${Icon} n="close" s=${14} /></button>`}>
        <div class="grid" style="gap:16px">
          <div class="s8"><${Loadable} q=${series} sk=${html`<${SkBlock} h=${220} />`}>${(s) => { const x = [...s.observed.filter((o) => o.t >= "1997").map((o) => o.t), ...s.estimate.map((e) => e.t)];
            const obsN = s.observed.filter((o) => o.t >= "1997").length;
            return html`<${MultiLine} x=${x} height=${230} label=${`${P.name} dengue`} unit="cases per month"
              markers=${[{ from: obsN, to: x.length - 1, label: "model estimate" }]}
              series=${[{ name: "Reported", color: "var(--text)", values: [...s.observed.filter((o) => o.t >= "1997").map((o) => o.y), ...s.estimate.map(() => null)] },
                { name: "Estimate", color: "var(--chart-1)", values: [...Array(obsN).fill(null), ...s.estimate.map((e) => e.p50)] },
                { name: "Seasonal norm", color: "var(--chart-muted)", dash: true, width: 1.5, values: [...Array(obsN).fill(null), ...s.estimate.map((e) => e.normal)] }]} />`; }}</${Loadable}></div>
          <div class="s4"><dl class="kv small">
            <dt>Population</dt><dd>${fmt.int(P.population)} <span class="muted">WorldPop</span></dd>
            <dt>Under 5 / 65+</dt><dd>${fmt.int(P.children_u5)} / ${fmt.int(P.elderly)}</dd>
            <dt>Rural</dt><dd>${fmt.d1(P.rural_pct)}%</dd>
            <dt>Hospitals (OSM)</dt><dd>${fmt.int(P.hospitals_osm)} · primary care ${fmt.int(P.primary_care_osm)}</dd>
            <dt>Hospital ≤ 1 h</dt><dd>${fmt.pct(P.access_hospital_1h_share)} of residents</dd>
            <dt>Doctors / nurses</dt><dd>${fmt.int(P.doctors_est)} / ${fmt.int(P.nurses_est)} <span class="pill sc">Estimate</span></dd>
            <dt>Beds</dt><dd>${fmt.int(P.beds_est)} <span class="pill sc">Estimate</span></dd>
            ${selRow && html`<dt>This month</dt><dd>${fmt.int(selRow.estimate)} est. cases (norm ${fmt.int(selRow.normal)})</dd>`}
          </dl><p class="small muted" style="margin:8px 0 0">Doctors, nurses and beds apply WHO national densities to the provincial population; they are planning estimates, not a census.</p></div>
        </div></${Panel}></div>`}
      ${sel && P && html`<${MobilityPanel} cls="s12" scope="vn" unitId=${sel} name=${P.name} />`}
      <div class="s8"><${Panel} title=${html`${frame ? monthLabel(frame.month) : "Map"} ${frame && html`<span class=${"pill " + (frame.kind === "OBSERVED" ? "obs" : "fc")}>${frame.kind === "OBSERVED" ? "Observed" : "Model estimate"}</span>`}`}
        note=${frame?.kind === "OBSERVED" ? "Reported cases (Project Tycho via OpenDengue), alerts by leave-one-year-out endemic channel (mean + 2 SD)" : "Climate model × national calibration · alert = ratio to seasonal norm and incidence"} flush>
        <${VnMap} colors=${view.colors} selected=${sel} onSelect=${(id) => pickProvince(id === sel ? null : id)} height=${600} pulses=${pulses} flows=${flows} labels=${labels}
          tooltip=${(id, name) => { const v = view.val[id], lv = view.lv[id]; const p = pById[id];
            return html`<b>${name}</b> <${LevelPill} l=${lv || "no_data"} small /><br/>${v == null ? html`<span class="muted">No data this month</span>` : html`${layer === "inc" ? `${fmt.d1(v)} per 100k` : `${fmt.int(v)} cases`} <span class="muted">${frame?.kind === "OBSERVED" ? "reported" : "estimated"}</span>`}
              ${p && html`<br/><span class="muted">pop ${fmt.compact(p.population)} · ${p.former_provinces.length > 1 ? `merged: ${p.former_provinces.join(", ")}` : "unchanged 2025"}</span>`}`; }}>
          <div class="maplegend">${layer === "level" ? ["alert", "warning", "watch", "normal"].map((l) => html`<span><i style=${`background:${LEVEL[l].c}`}></i>${LEVEL[l].label}</span>`)
            : html`${(layer === "inc" ? HEAT : SEQ).slice(1).map((c) => html`<i style=${`background:${c}`}></i>`)}<span class="muted">${layer === "inc" ? "per 100k" : "cases"} · quintiles</span>`}
            ${fx.flow && html`<span><i class="dotflow"></i>spread pressure to neighbours</span>`}</div>
        </${VnMap}>
        ${frames.length > 0 && ix != null && html`<${Playback} n=${frames.length} value=${ix} onChange=${setIx} playing=${playing} setPlaying=${setPlaying} speed=${speed} setSpeed=${setSpeed} fps=${4}
          labelFor=${(i) => monthLabel(frames[i].month)} kind=${html`<span class="small muted">${frames[ix].kind === "OBSERVED" ? "observed 1997–2010" : "model estimate 2011–now"}</span>`}
          right=${html`<button class="btn sm" disabled=${isNow} onClick=${() => { setPlaying(false); setIx(frames.length - 1); }}>Now</button>`} />`}
        <div class="tlstrip" aria-hidden="true">${frames.map((f, i) => { const a = Object.values(f.lv).filter((x) => x === "alert").length, w = Object.values(f.lv).filter((x) => x === "warning").length;
          return html`<i class=${i === ix ? "on" : ""} style=${`height:${4 + Math.min(22, a * 3 + w * 1.2)}px;background:${a ? LEVEL.alert.c : w ? LEVEL.warning.c : "var(--control)"}`} title=${`${monthLabel(f.month)}: ${a} alert, ${w} warning`} onClick=${() => { setPlaying(false); setIx(i); }}></i>`; })}</div>
      </${Panel}></div>
      <div class="s4 stack">
        <${Panel} title="Needs attention" note=${now ? `${monthLabel(now.month)} · sorted by level, then incidence` : ""} flush>
          <${Loadable} q=${st} sk=${html`<${SkRows} n=${6} cols=${2} />`}>${() => html`<div class="alertlist">${rows.filter((r) => r.level !== "normal").slice(0, 12).map((r) => html`
            <button class=${"al lv-" + r.level + (r.province_id === sel ? " sel" : "")} onClick=${() => pickProvince(r.province_id === sel ? null : r.province_id)}>
              <div class="row" style="gap:8px"><${LevelPill} l=${r.level} /><b>${r.name}</b><span class="spacer"></span>
                <span class="num small">${fmt.d1(r.incidence_per_100k)}<span class="muted">/100k</span></span>
                ${r.trend != null && html`<span class=${"small num " + (r.trend > 0.05 ? "up-bad" : r.trend < -0.05 ? "down-good" : "muted")}>${r.trend > 0 ? "▲" : r.trend < 0 ? "▼" : "•"}${fmt.pct(Math.abs(r.trend))}</span>`}</div>
              <div class="small muted" style="margin-top:4px;text-align:left">${r.reason}</div></button>`)}
            ${!rows.some((r) => r.level !== "normal") && html`<div style="padding:12px 20px"><${Empty}>No province above normal this month.</${Empty}></div>`}</div>`}</${Loadable}>
        </${Panel}>
        <${Panel} title="How alerts are set" note="Ordinal levels; thresholds are fixed, not tuned per province">
          <dl class="kv small">${Object.entries(st.meta?.levels || {}).map(([l, d]) => html`<dt><${LevelPill} l=${l} small /></dt><dd>${d}</dd>`)}</dl>
        </${Panel}>
      </div>
      <div class="s7"><${Panel} title="National reports" note="OBSERVED · WHO WPRO weekly (OpenDengue V1.3)">
        <${Loadable} q=${nat} sk=${html`<${SkBlock} h=${200} />`}>${(n) => n.weekly.length ? html`<${MultiLine} x=${n.weekly.map((w) => w.t)} height=${210} label="National weekly dengue" unit="reported cases per week"
          series=${[{ name: "Reported / week", color: "var(--text)", values: n.weekly.map((w) => w.y) }]} />` : html`<${Empty}>No weekly reports.</${Empty}>`}</${Loadable}>
      </${Panel}></div>
      <div class="s5"><${Panel} title="Does the model track reality?" note=${info ? `Monthly, 2011–2025 · r = ${fmt.d2(info.national_shape_corr_2011_on)} (yearly level re-estimated)` : ""}>
        <${Loadable} q=${nat} sk=${html`<${SkBlock} h=${200} />`}>${(n) => html`<${MultiLine} x=${n.calibration.map((c) => c.month)} height=${210} label="Model vs WHO national" unit="cases per month"
          series=${[{ name: "WHO reported", color: "var(--text)", values: n.calibration.map((c) => c.reported) }, { name: "Model (sum of provinces)", color: "var(--chart-1)", dash: true, values: n.calibration.map((c) => c.model_fit) }]} />
          ${info && html`<${Prov} items=${[`holdout 2010 MAE ${fmt.d1(info.holdout_2010.mae)} vs ${fmt.d1(info.holdout_2010.mae_seasonal_only)} seasonal-only`, `deviance explained ${fmt.d2(info.holdout_2010.deviance_explained)}`, `κ ${fmt.d2(info.kappa)}`]} />`}`}</${Loadable}>
      </${Panel}></div>
      <div class="s12"><${Panel} title="AI analyst" note="Planning brief grounded on this month's state, the model's skill and the latest national reports">
        <${AnalystStream} body=${now ? { kind: "dashboard", month: now.month } : null} status=${status.data}
          suggestions=${["Where should we pre-position teams?", "Why is the top province flagged?", "What could make this assessment wrong?"]} />
      </${Panel}></div>
      <div class="s12"><${Prov} items=${[`Admin basis: ${status.data?.manifest?.admin_basis || "—"}`, `built ${ago(status.data?.manifest?.built_at)}`,
        ...Object.keys(status.data?.manifest?.sources || {}).map((s) => s.replace("vn_", "")), info?.method]} /></div>
    </div>
  </div>`;
}
