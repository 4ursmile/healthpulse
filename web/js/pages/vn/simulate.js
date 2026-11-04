// Viet Nam preparedness simulator — question: "If transmission rises, where do hospitals overflow, and where should teams go?"
import { html, useEffect, useMemo, useRef, useState } from "../../../vendor/preact-htm.js";
import { api, useApi } from "../../api.js";
import { Panel, TitleRow, Kpis, Loadable, SkBlock, Empty, Icon, Seg, Prov, fmt, fdate, toast } from "../../ui.js";
import { VnMap, LEVEL, HEAT, SEQ, classBreaks, colorFor, teamPosition } from "../../vnmap.js";
import { MultiLine, Playback, AnalystStream, monthLabel } from "./_vn.js";
import { scopeOf } from "../../scope.js";

const DEFAULTS = { days: 150, trials: 120, seed: 7, start_month: null, transmission: 1.35, seasonality: 1, mobility: 0.04, mobility_reduction: 0,
  immune_share: 0.65, outbreak_province: null, outbreak_infections: 0, reporting_rate: 0.15, hosp_rate: 0.3, los_days: 4,
  dengue_bed_share: 0.08, dengue_nurse_share: 0.06, patients_per_nurse: 6, surge_pct: 0, teams: 12, team_capacity: 25, team_staff: 10,
  team_vector_effect: 0.1, max_teams_per_province: 6, speed_kmh: 45, mobilize_hours: 6, dispatch: "auto", review_every_days: 7, trigger_load: 0.85,
  manual: [], bases: null };
const PRESETS = [
  ["Stress test", {}],
  ["As fitted (1.0×)", { transmission: 1.0 }],
  ["Hot rainy season", { transmission: 1.5, seasonality: 0.6 }],
  ["New serotype", { immune_share: 0.4, transmission: 1.25 }],
  ["Big-city outbreak", { outbreak_province: "VN79", outbreak_infections: 30000, transmission: 1.3 }],
  ["Staff surge only", { teams: 0, surge_pct: 0.3, transmission: 1.45 }],
  ["Limit travel", { mobility_reduction: 0.6, transmission: 1.45 }],
];
const TEAM_COL = ["#2f70bb", "#6a4fc2", "#1e7b4f", "#a86f0c"];

function Slider({ label, k, spec, set, min, max, step, fmtv = (v) => v, hint }) {
  return html`<label class="sl"><span class="row"><span>${label}</span><span class="spacer"></span><b class="num">${fmtv(spec[k])}</b></span>
    <input type="range" min=${min} max=${max} step=${step} value=${spec[k]} onInput=${(e) => set({ [k]: +e.target.value })} aria-label=${label} />
    ${hint && html`<span class="small faint">${hint}</span>`}</label>`;
}

function Group({ title, open = true, children, badge }) {
  return html`<details class="cgroup" open=${open}><summary><span>${title}</span>${badge}<${Icon} n="chevron" s=${12} /></summary><div class="cbody">${children}</div></details>`;
}

export default function VnSimulate({ q }) {
  const status = useApi("/vn/status");
  const provs = useApi(status.data?.built ? "/vn/provinces" : null);
  const pv = provs.data || [];
  const pById = useMemo(() => Object.fromEntries(pv.map((p) => [p.province_id, p])), [pv]);
  const [spec, setSpec] = useState(() => {
    const o = q.get("origin") || scopeOf(q).region;
    try { const s = JSON.parse(sessionStorage.getItem("hp_vn_spec") || "null");
      if (s) return { ...DEFAULTS, ...s, ...(o ? { outbreak_province: o, outbreak_infections: s.outbreak_infections || 20000 } : {}) }; } catch (e) {}
    return o ? { ...DEFAULTS, outbreak_province: o, outbreak_infections: 20000 } : DEFAULTS;
  });
  const set = (p) => setSpec((s) => ({ ...s, ...p }));
  const [live, setLive] = useState(true);
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [day, setDay] = useState(() => +(q.get("day") || 0));
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(2);
  const [arm, setArm] = useState("plan");
  const [metric, setMetric] = useState("load");
  const [sel, setSel] = useState(null);
  const seq = useRef(0);
  useEffect(() => { try { sessionStorage.setItem("hp_vn_spec", JSON.stringify(spec)); } catch (e) {} }, [spec]);

  const run = async (s = spec) => {
    const my = ++seq.current;
    setBusy(true); setErr(null);
    try {
      const r = await api("/vn/simulate", { method: "POST", body: s });
      if (my !== seq.current) return;
      setRes(r.data);
      setDay((d) => Math.min(d, r.data.days - 1));
    } catch (e) { if (my === seq.current) { setErr(e.message); toast(e.message, "bad"); } }
    if (my === seq.current) setBusy(false);
  };
  useEffect(() => { if (!status.data?.built || !live) return; const t = setTimeout(() => run(spec), 350); return () => clearTimeout(t); }, [JSON.stringify(spec), status.data?.built, live]);

  const A = res ? res[arm] : null;
  const P = res?.provinces || [];
  const ids = P.map((p) => p.province_id);
  const d = Math.min(Math.floor(day), (res?.days || 1) - 1);
  // map values for the chosen day
  const view = useMemo(() => {
    if (!A) return { colors: {}, val: {} };
    const val = {}, colors = {};
    const occ = A.series.occ[d], cap = A.series.cap[d], cases = A.series.cases[d], un = A.series.unmet[d];
    P.forEach((p, i) => { val[p.province_id] = metric === "load" ? occ[i] / Math.max(1, cap[i]) : metric === "cases" ? (cases[i] / Math.max(1, p.population)) * 1e5 * 7 : un[i]; });
    if (metric === "load") for (const [id, v] of Object.entries(val)) colors[id] = v >= 1 ? LEVEL.alert.c : v >= 0.85 ? LEVEL.warning.c : v >= 0.6 ? LEVEL.watch.c : LEVEL.normal.c;
    else { const all = metric === "cases" ? A.series.cases.flatMap((row) => row.map((c, i) => (c / Math.max(1, P[i].population)) * 1e5 * 7)) : A.series.unmet.flat(); const br = classBreaks(all.filter((x) => x > 0));
      for (const [id, v] of Object.entries(val)) colors[id] = v > 0 ? colorFor(v, br, metric === "cases" ? SEQ : HEAT) : LEVEL.normal.c; }
    return { colors, val, occ, cap };
  }, [A, d, metric]);
  const teamsNow = useMemo(() => (A?.teams || []).map((t) => ({ t, pos: teamPosition(t, day, pById) })), [A, day, pById]);
  if (status.data && !status.data.built) return html`<div class="page"><${TitleRow} title="Viet Nam — simulator" /><${Empty}>Run <code>.venv/bin/hp vn-fetch</code> first.</${Empty}></div>`;
  const movers = teamsNow.map(({ t, pos }) => ({ lon: pos.lon + (pos.state === "deployed" ? ((t.team_id % 3) - 1) * 0.07 : 0), lat: pos.lat + (pos.state === "deployed" ? ((Math.floor(t.team_id / 3) % 3) - 1) * 0.07 : 0),
    c: TEAM_COL[["nihe", "pasteur_nt", "tihe", "pasteur_hcm"].indexOf(t.base_id)] || "#2f70bb" }));
  const routes = teamsNow.filter(({ pos }) => pos.state === "travel").map(({ pos }) => ({ from: [pById[pos.from].lon, pById[pos.from].lat], to: [pById[pos.to].lon, pById[pos.to].lat] }));
  const pulses = A ? P.map((p, i) => (A.series.unmet[d][i] > 0 ? { lon: p.lon, lat: p.lat, level: "alert" } : view.occ?.[i] / Math.max(1, view.cap?.[i]) >= 0.85 ? { lon: p.lon, lat: p.lat, level: "warning" } : null)).filter(Boolean) : [];
  const labels = A ? P.map((p, i) => ({ p, i, l: view.occ[i] / Math.max(1, view.cap[i]) })).filter((x) => x.l >= 0.85 || x.p.province_id === sel).sort((a, b) => b.l - a.l).slice(0, 12)
    .map(({ p, l }) => ({ id: p.province_id, lon: p.lon, lat: p.lat, level: l >= 1 ? "alert" : "warning", text: `${p.name} ${fmt.pct(l)}` })) : [];
  const events = (A?.events || []).filter((e) => e.day <= d).slice(-60).reverse();
  const nat = (k) => A ? A.series[k].map((row) => row.reduce((a, b) => a + b, 0)) : [];
  const natB = (k) => res ? res.baseline.series[k].map((row) => row.reduce((a, b) => a + b, 0)) : [];
  const kb = res?.baseline.kpi, kp = res?.plan.kpi;
  const sub = res ? html`Over ${res.days} days from ${monthLabel(res.start_month)}, the plan leaves <b>${fmt.compact(kp.unmet_bed_days.p50)}</b> unmet bed-days against <b>${fmt.compact(kb.unmet_bed_days.p50)}</b> without teams
    (${kb.provinces_overloaded} → ${kp.provinces_overloaded} provinces likely overloaded). Scenario under the listed assumptions.` : busy ? "Simulating…" : "Adjust the levers; the scenario reruns automatically.";
  const selI = sel ? ids.indexOf(sel) : -1;

  return html`<div class="page vn">
    <${TitleRow} title="Viet Nam — preparedness simulator" sub=${sub}
      right=${html`<span class=${"stamp" + (busy ? " busy" : "")}><span class=${"dot" + (busy ? " stale" : "")}></span>${busy ? "running…" : res ? `${res.elapsed_ms} ms · ${spec.trials} trials` : "—"}</span>
        <label class="pill toggle-pill"><input type="checkbox" checked=${live} onChange=${(e) => setLive(e.target.checked)} />Live update</label>
        <button class="btn pri" disabled=${busy} onClick=${() => run()}><${Icon} n="play" s=${13} />Run</button>`} />
    <div class="filters"><span class="small muted">Presets</span>${PRESETS.map(([n, p]) => html`<button class="btn sm" onClick=${() => { setSpec({ ...DEFAULTS, ...p }); setDay(0); }}>${n}</button>`)}
      <span class="sep"></span><button class="btn sm ghost" onClick=${() => setSpec(DEFAULTS)}>Reset</button></div>
    ${err && html`<div class="notice warn"><${Icon} n="warn" /><div>${err}</div></div>`}
    <div class="grid">
      <div class="s3 sim-side"><div class="panel controls">
        <${Group} title="Epidemic">
          <${Slider} label="Transmission × fitted R" k="transmission" spec=${spec} set=${set} min=${0.6} max=${2.2} step=${0.05} fmtv=${(v) => `${v.toFixed(2)}×`} hint="1 = the province's fitted seasonal R" />
          <${Slider} label="Follow seasonal curve" k="seasonality" spec=${spec} set=${set} min=${0} max=${1.5} step=${0.1} fmtv=${(v) => fmt.pct(v)} />
          <${Slider} label="Population immune" k="immune_share" spec=${spec} set=${set} min=${0.2} max=${0.9} step=${0.05} fmtv=${(v) => fmt.pct(v)} hint="lower = new serotype" />
          <${Slider} label="Days" k="days" spec=${spec} set=${set} min=${30} max=${300} step=${10} fmtv=${(v) => `${v} d`} />
          <label class="sl"><span>Outbreak seed</span><div class="row" style="gap:6px">
            <select value=${spec.outbreak_province || ""} onChange=${(e) => set({ outbreak_province: e.target.value || null, outbreak_infections: e.target.value ? spec.outbreak_infections || 20000 : 0 })} style="flex:1" aria-label="Outbreak province">
              <option value="">None</option>${pv.map((p) => html`<option value=${p.province_id}>${p.name}</option>`)}</select>
            <input type="number" min="0" step="1000" value=${spec.outbreak_infections} disabled=${!spec.outbreak_province} onInput=${(e) => set({ outbreak_infections: +e.target.value || 0 })} style="width:90px" aria-label="Seed infections" /></div></label>
        </${Group}>
        <${Group} title="Mobility">
          <${Slider} label="Contacts outside home province" k="mobility" spec=${spec} set=${set} min=${0} max=${0.15} step=${0.005} fmtv=${(v) => fmt.pct(v, 1)} />
          <${Slider} label="Travel reduction" k="mobility_reduction" spec=${spec} set=${set} min=${0} max=${1} step=${0.05} fmtv=${(v) => fmt.pct(v)} />
        </${Group}>
        <${Group} title="Hospitals & workforce" badge=${html`<span class="pill sc">Estimate</span>`}>
          <${Slider} label="Beds usable for dengue" k="dengue_bed_share" spec=${spec} set=${set} min=${0.02} max=${0.3} step=${0.01} fmtv=${(v) => fmt.pct(v)} />
          <${Slider} label="Nurses on dengue wards" k="dengue_nurse_share" spec=${spec} set=${set} min=${0.02} max=${0.3} step=${0.01} fmtv=${(v) => fmt.pct(v)} />
          <${Slider} label="Patients per nurse (shift)" k="patients_per_nurse" spec=${spec} set=${set} min=${2} max=${12} step=${1} />
          <${Slider} label="Staff surge (overtime)" k="surge_pct" spec=${spec} set=${set} min=${0} max=${0.6} step=${0.05} fmtv=${(v) => `+${fmt.pct(v)}`} />
          <${Slider} label="Hospitalised per reported case" k="hosp_rate" spec=${spec} set=${set} min=${0.05} max=${0.8} step=${0.05} fmtv=${(v) => fmt.pct(v)} />
          <${Slider} label="Length of stay" k="los_days" spec=${spec} set=${set} min=${2} max=${10} step=${0.5} fmtv=${(v) => `${v} d`} />
        </${Group}>
        <${Group} title="Rapid-response teams">
          <${Slider} label="Teams available" k="teams" spec=${spec} set=${set} min=${0} max=${60} step=${1} />
          <${Slider} label="Patients a team can manage" k="team_capacity" spec=${spec} set=${set} min=${5} max=${100} step=${5} />
          <${Slider} label="Vector control per team" k="team_vector_effect" spec=${spec} set=${set} min=${0} max=${0.4} step=${0.02} fmtv=${(v) => `−${fmt.pct(v)} R`} hint="after 7 days on site" />
          <${Slider} label="Dispatch trigger (load)" k="trigger_load" spec=${spec} set=${set} min=${0.5} max=${1.5} step=${0.05} fmtv=${(v) => fmt.pct(v)} />
          <label class="sl"><span>Dispatch policy</span><${Seg} label="Dispatch" value=${spec.dispatch} onChange=${(v) => set({ dispatch: v })} options=${[["auto", "Auto"], ["manual", "Manual"], ["none", "None"]]} /></label>
          ${spec.dispatch === "manual" && html`<div class="stack" style="gap:6px">${spec.manual.map((m, i) => html`<div class="row" style="gap:4px">
              <select value=${m.province_id} onChange=${(e) => set({ manual: spec.manual.map((x, j) => (j === i ? { ...x, province_id: e.target.value } : x)) })} style="flex:1" aria-label="Province">${pv.map((p) => html`<option value=${p.province_id}>${p.name}</option>`)}</select>
              <input type="number" min="0" value=${m.day} style="width:52px" title="day" onInput=${(e) => set({ manual: spec.manual.map((x, j) => (j === i ? { ...x, day: +e.target.value || 0 } : x)) })} aria-label="Day" />
              <input type="number" min="1" value=${m.teams} style="width:44px" title="teams" onInput=${(e) => set({ manual: spec.manual.map((x, j) => (j === i ? { ...x, teams: Math.max(1, +e.target.value || 1) } : x)) })} aria-label="Teams" />
              <button class="btn sm ghost" onClick=${() => set({ manual: spec.manual.filter((_, j) => j !== i) })} aria-label="Remove"><${Icon} n="x" s=${12} /></button></div>`)}
            <button class="btn sm" onClick=${() => set({ manual: [...spec.manual, { province_id: sel || pv[0]?.province_id, day: d, teams: 2 }] })}><${Icon} n="plus" s=${12} />Add dispatch ${sel ? `to ${pById[sel]?.name}` : ""} on day ${d}</button></div>`}
          <div class="small muted">Bases: ${res?.bases.map((b, i) => html`<span class="row" style="display:inline-flex;gap:4px;margin-right:8px"><i class="sw" style=${`background:${TEAM_COL[i]}`}></i>${b.name}</span>`)}</div>
        </${Group}>
        <${Group} title="Simulation" open=${false}>
          <${Slider} label="Trials" k="trials" spec=${spec} set=${set} min=${20} max=${400} step=${20} />
          <${Slider} label="Reporting rate" k="reporting_rate" spec=${spec} set=${set} min=${0.05} max=${0.5} step=${0.01} fmtv=${(v) => fmt.pct(v)} />
          <label class="sl"><span>Seed</span><input type="number" value=${spec.seed} onInput=${(e) => set({ seed: +e.target.value || 0 })} /></label>
        </${Group}>
      </div></div>
      <div class="s9 stack">
        ${res ? html`<${Kpis} items=${[
          { label: "Unmet bed-days, plan", sem: "SCENARIO", lead: true, value: fmt.compact(kp.unmet_bed_days.p50), sub: `P10–P90 ${fmt.compact(kp.unmet_bed_days.p10)}–${fmt.compact(kp.unmet_bed_days.p90)}` },
          { label: "Without teams", value: fmt.compact(kb.unmet_bed_days.p50), sub: html`avoided <b>${fmt.compact(res.avoided_unmet_bed_days.p50)}</b> (${fmt.compact(res.avoided_unmet_bed_days.p10)}–${fmt.compact(res.avoided_unmet_bed_days.p90)})` },
          { label: "Reported cases", value: fmt.compact(kp.reported_cases.p50), sub: `P10–P90 ${fmt.compact(kp.reported_cases.p10)}–${fmt.compact(kp.reported_cases.p90)}` },
          { label: "Peak in hospital", value: fmt.compact(kp.peak_occupancy.p50), sub: `day ${kp.peak_occupancy.day} · ${fdate(res.dates[kp.peak_occupancy.day])}` },
          { label: "Provinces overloaded", value: html`${kb.provinces_overloaded} → ${kp.provinces_overloaded}`, sub: `team staff-days ${fmt.compact(kp.team_staff_days)}` },
        ]} />` : html`<${SkBlock} h=${96} />`}
        <${Panel} title=${res ? html`Day ${d} · ${fdate(res.dates[d])} <span class="pill sc">Scenario</span>` : "Map"} flush
          right=${html`<${Seg} label="Arm" value=${arm} onChange=${setArm} options=${[["plan", "With teams"], ["baseline", "No teams"]]} />
            <${Seg} label="Map metric" value=${metric} onChange=${setMetric} options=${[["load", "Hospital load"], ["cases", "Cases/100k/wk"], ["unmet", "Unmet"]]} />`}>
          <${VnMap} colors=${view.colors} selected=${sel} onSelect=${(id) => setSel(id === sel ? null : id)} height=${560} pulses=${pulses} movers=${movers} routes=${routes} labels=${labels}
            tooltip=${(id, name) => { const i = ids.indexOf(id); if (i < 0 || !A) return html`<b>${name}</b>`; const p = P[i];
              const here = teamsNow.filter(({ pos }) => pos.state === "deployed" && pos.at === id).length;
              return html`<b>${name}</b><br/>${fmt.int(A.series.occ[d][i])} in hospital / ${fmt.int(A.series.cap[d][i])} slots (${fmt.pct(A.series.occ[d][i] / Math.max(1, A.series.cap[d][i]))})
                <br/>${fmt.int(A.series.cases[d][i])} new reported cases today${A.series.unmet[d][i] > 0 ? html`<br/><b style="color:${LEVEL.alert.c}">${fmt.int(A.series.unmet[d][i])} without a bed</b>` : ""}
                <br/><span class="muted">${here ? `${here} team${here > 1 ? "s" : ""} on site · ` : ""}capacity bound by ${p.binding} · P(overload) ${fmt.pct(A.p_overload[i])}</span>`; }}>
            <div class="maplegend">${metric === "load" ? [["alert", "≥100% (overflow)"], ["warning", "85–100%"], ["watch", "60–85%"], ["normal", "< 60%"]].map(([l, t]) => html`<span><i style=${`background:${LEVEL[l].c}`}></i>${t}</span>`)
              : html`${(metric === "cases" ? SEQ : HEAT).slice(1).map((c) => html`<i style=${`background:${c}`}></i>`)}<span class="muted">${metric === "cases" ? "cases per 100k per week" : "patients without a bed"}</span>`}
              <span><i class="sw round" style="background:#2f70bb"></i>team (moving = travelling)</span></div>
          </${VnMap}>
          ${res && html`<${Playback} n=${res.days} value=${d} onChange=${setDay} playing=${playing} setPlaying=${setPlaying} speed=${speed} setSpeed=${setSpeed} fps=${24} step=${0.25}
            labelFor=${(i) => `Day ${i}`} kind=${html`<span class="small muted">${fdate(res.dates[d])}</span>`}
            right=${html`<span class="small muted num">${teamsNow.filter((x) => x.pos.state === "travel").length} moving · ${teamsNow.filter((x) => x.pos.state === "deployed" && x.pos.at !== x.t.base_province).length} deployed</span>`} />`}
        </${Panel}>
        <div class="grid">
          <div class="s8"><${Panel} title=${selI >= 0 ? `${P[selI].name}: patients vs capacity` : "Nation: patients in hospital vs capacity"} note="Median of trials; band P10–P90 (national only). Click the chart to jump to a day.">
            ${res ? html`<${MultiLine} x=${res.dates} cursor=${d} onPick=${(i) => { setPlaying(false); setDay(i); }} height=${230} label="Occupancy vs capacity" unit="patients"
              series=${selI >= 0 ? [
                { name: "With teams", color: "var(--chart-1)", values: res.plan.series.occ.map((r) => r[selI]) },
                { name: "No teams", color: "var(--chart-muted)", values: res.baseline.series.occ.map((r) => r[selI]) },
                { name: "Capacity (plan)", color: "var(--text)", dash: true, width: 1.5, values: res.plan.series.cap.map((r) => r[selI]) }]
                : [{ name: "With teams", color: "var(--chart-1)", values: res.plan.band.occ.map((b) => b[1]), band: res.plan.band.occ.map((b) => [b[0], b[2]]) },
                  { name: "No teams", color: "var(--chart-muted)", values: res.baseline.band.occ.map((b) => b[1]) },
                  { name: "Unmet (plan)", color: LEVEL.alert.c, values: res.plan.band.unmet.map((b) => b[1]) },
                  { name: "Unmet (no teams)", color: LEVEL.warning.c, dash: true, values: res.baseline.band.unmet.map((b) => b[1]) }]} />` : html`<${SkBlock} h=${230} />`}
          </${Panel}></div>
          <div class="s4"><${Panel} title="Live events" note=${`up to day ${d}`} flush>
            <div class="feed" aria-live="polite">${events.length ? events.map((e, i) => html`<button class=${"ev k-" + e.kind + (i === 0 && e.day === d ? " fresh" : "")} onClick=${() => { setPlaying(false); setDay(e.day); setSel(e.province_id); }}>
              <span class="num small muted">d${e.day}</span><span class="evi"></span><span>${e.text}</span></button>`) : html`<div style="padding:12px 20px" class="small muted">No events yet — press play.</div>`}</div>
          </${Panel}></div>
          <div class="s8"><${Panel} title="Workforce & capacity by province" note=${`Day ${d} · ${arm === "plan" ? "with teams" : "no teams"} · estimates from WHO national densities × population`} flush>
            ${res ? html`<div class="tablewrap" style="max-height:420px"><table class="tbl dense sticky">
              <thead><tr><th>Province</th><th class="num">Load</th><th class="num">In bed / slots</th><th>Limit</th><th class="num hide-sm">Drs · nurses</th><th class="num">Teams</th><th class="num">P(over) none→plan</th></tr></thead>
              <tbody>${P.map((p, i) => ({ p, i, l: A.series.occ[d][i] / Math.max(1, A.series.cap[d][i]) })).sort((a, b) => b.l - a.l).map(({ p, i, l }) => html`
                <tr class=${"click" + (p.province_id === sel ? " sel" : "")} onClick=${() => setSel(p.province_id === sel ? null : p.province_id)}>
                  <td style="white-space:nowrap">${p.name}</td>
                  <td class="num"><div class="loadbar"><i style=${`width:${Math.min(100, l * 100)}%;background:${l >= 1 ? LEVEL.alert.c : l >= 0.85 ? LEVEL.warning.c : l >= 0.6 ? LEVEL.watch.c : "var(--chart-1)"}`}></i></div>${fmt.pct(l)}</td>
                  <td class="num">${fmt.int(A.series.occ[d][i])} / ${fmt.int(A.series.cap[d][i])}</td>
                  <td><span class=${"pill " + (p.binding === "staff" ? "warn" : "")}>${p.binding}</span></td>
                  <td class="num muted hide-sm">${fmt.compact(p.doctors_est)} · ${fmt.compact(p.nurses_est)}</td>
                  <td class="num">${A.series.teams[d][i] || ""}</td>
                  <td class="num">${fmt.pct(res.baseline.p_overload[i])} → <b>${fmt.pct(res.plan.p_overload[i])}</b></td></tr>`)}</tbody></table></div>` : html`<${SkBlock} h=${300} />`}
          </${Panel}></div>
          <div class="s4"><${Panel} title="Team roster" note=${`Day ${d}`} flush>
            <div class="feed roster">${teamsNow.length ? teamsNow.map(({ t, pos }) => html`<div class="ev">
              <span class="sw round" style=${`background:${TEAM_COL[["nihe", "pasteur_nt", "tihe", "pasteur_hcm"].indexOf(t.base_id)]}`}></span>
              <span><b>Team ${t.team_id + 1}</b> <span class="muted small">${t.base_name}</span><br/><span class="small">${pos.state === "travel" ? `travelling ${pById[pos.from]?.name} → ${pById[pos.to]?.name}`
                : pos.at === t.base_province ? html`<span class="muted">at base, available</span>` : `deployed in ${pById[pos.at]?.name}`}</span></span></div>`)
              : html`<div style="padding:12px 20px" class="small muted">${spec.teams ? "Dispatch is off." : "No teams in this scenario."}</div>`}</div>
          </${Panel}></div>
          <div class="s12"><${Panel} title="AI analyst" note="Explains this scenario from its KPIs, the most pressured provinces, the team plan and the event log">
            <${AnalystStream} body=${res ? { kind: "simulation", sim_id: res.sim_id } : null} status=${status.data}
              suggestions=${["Is the team plan good enough?", "Which lever helps most: teams, surge staff or travel limits?", "What should we do in the first two weeks?"]} />
          </${Panel}></div>
          <div class="s12">${res && html`<${Prov} items=${Object.entries(res.basis).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`)} />`}</div>
        </div>
      </div>
    </div>
  </div>`;
}
