// Simulation studio — question: "With these teams, budget and assumptions, where would assumed capacity fall short?"
import { html, useEffect, useMemo, useState } from "../../vendor/preact-htm.js";
import { api, useApi, navigate, newKey, invalidate } from "../api.js";
import { Panel, TitleRow, Notices, Sem, Status, Loadable, SkRows, SkBlock, Empty, Link, Icon, JobProgress, Confirm, toast, fmt, fdate, ago, Id, Seg } from "../ui.js";
import { useSession } from "../app.js";

const clone = (o) => JSON.parse(JSON.stringify(o));
const setIn = (o, path, v) => { const c = clone(o); let x = c; const p = path.split("."); for (let i = 0; i < p.length - 1; i++) x = x[p[i]] ||= {}; x[p[p.length - 1]] = v; return c; };

function Num({ label, hint, value, onChange, step = 1, min, max, unit, id }) {
  const fid = id || "f-" + label.replace(/\W+/g, "-").toLowerCase();
  return html`<div class="field"><label for=${fid}>${label}${unit && html` <span class="muted" style="font-weight:400">(${unit})</span>`}</label>
    <input id=${fid} type="number" step=${step} min=${min} max=${max} value=${value ?? ""} onInput=${(e) => onChange(e.target.value === "" ? null : +e.target.value)} />
    ${hint && html`<span class="hint">${hint}</span>`}</div>`;
}

function Checks({ v }) {
  if (!v) return null;
  return html`<div>${v.checks.map((c) => html`<div class="check"><span class=${c.passed ? "ok" : c.hard ? "no" : "soft"}><${Icon} n=${c.passed ? "check" : "x"} s=${14} /></span>
    <div><b style="font-weight:500">${c.check.replace(/_/g, " ")}</b><div class="muted">${c.detail}</div></div></div>`)}
    ${v.warnings.length > 0 && html`<div style="margin-top:12px"><${Notices} items=${v.warnings} /></div>`}</div>`;
}

function Builder({ initial, scenario, forecasts, names, onSaved }) {
  const { can } = useSession();
  const [spec, setSpec] = useState(initial);
  const [dirty, setDirty] = useState(!scenario);
  const [val, setVal] = useState(null);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState(null);
  const [runId, setRunId] = useState(null);
  const [review, setReview] = useState(null);
  useEffect(() => { setSpec(initial); setDirty(!scenario); setVal(null); }, [initial]);
  const up = (path) => (v) => { setSpec((s) => setIn(s, path, v)); setDirty(true); setVal(null); };
  const fq = useApi(spec.forecast_id ? `/map/regions?layer=forecast_p50&forecast_id=${spec.forecast_id}&disease_id=${spec.disease_id}&horizon=1` : null);
  const fRegions = Object.keys(fq.data?.values || {}).sort();
  const immutable = scenario && ["completed", "published"].includes(scenario.status);
  const research = spec.mode === "transmission_research";
  const canWrite = can("scenario.write");

  const validate = async () => {
    setBusy(true);
    try { const r = await api("/scenarios/validate", { method: "POST", body: spec }); setVal(r.data); }
    catch (e) { toast(e.message, "bad"); }
    setBusy(false);
  };
  const save = async () => {
    setBusy(true);
    try {
      let r;
      if (scenario && !immutable) r = await api(`/scenarios/${scenario.scenario_id}`, { method: "PUT", body: { ...spec, expected_version: scenario.version } });
      else r = await api("/scenarios", { method: "POST", body: { ...spec, ...(scenario ? { parent_scenario_id: scenario.scenario_id } : {}) } });
      invalidate("/scenarios");
      toast(`Saved version ${r.data.version}`);
      setDirty(false);
      onSaved(r.data);
      return r.data;
    } catch (e) {
      toast(e.status === 409 ? `${e.message}. Reload to see the newer version; your edits are still here.` : e.message, "bad");
      return null;
    } finally { setBusy(false); }
  };
  const run = async () => {
    let sc = scenario;
    if (dirty || immutable || !sc) { sc = await save(); if (!sc) return; }
    setBusy(true);
    try {
      const r = await api(`/scenarios/${sc.scenario_id}/run`, { method: "POST", idem: newKey() });
      setJob(r.data.job_id); setRunId(r.data.run_id);
    } catch (e) { toast(e.message, "bad"); }
    setBusy(false);
  };
  const doReview = async (decision, note) => {
    try { await api(`/scenarios/${scenario.scenario_id}/review`, { method: "POST", body: { decision, note } }); toast("Review recorded"); setReview(null); onSaved(null); }
    catch (e) { toast(e.message, "bad"); }
  };
  const S = spec, R = S.resources, C = S.capacity, A = S.assumptions, T = S.transmission || {};
  const toggleRegion = (id) => { const cur = new Set(S.region_ids); cur.has(id) ? cur.delete(id) : cur.add(id); up("region_ids")([...cur]); };
  const toggleBase = (id) => { const cur = new Set(R.team_bases); cur.has(id) ? cur.delete(id) : cur.add(id); up("resources.team_bases")([...cur]); };
  const scope = S.region_ids.length ? S.region_ids : fRegions;
  return html`<div class="stack">
    <${Panel} title="Scenario" right=${html`<${Seg} label="Mode" value=${S.mode} onChange=${(m) => { up("mode")(m); if (m === "transmission_research" && !S.transmission) up("transmission")({ r0_low: 1.1, r0_high: 1.6, latent_days: 4, infectious_days: 5, reporting_fraction: 0.1, coupling: 0.02, contact_reduction_pct: 20, intervention_start_week: 2, weeks: 8, immune_share: 0 }); }}
        options=${[["operational", "Operational"], ["transmission_research", "Transmission research"]]} />`}>
      ${research && html`<div class="notice" style="margin-bottom:16px"><${Sem} k="HYPOTHETICAL" /><div>Research mode simulates infections with uncalibrated assumptions. It is never an estimate of a real programme's effect.</div></div>`}
      <div class="fgrid">
        <div class="field"><label for="sc-name">Name</label><input id="sc-name" type="text" value=${S.name} onInput=${(e) => up("name")(e.target.value)} maxlength="160" /></div>
        <div class="field"><label for="sc-fc">Pinned forecast</label><select id="sc-fc" value=${S.forecast_id || ""} onChange=${(e) => { const f = forecasts.find((x) => x.forecast_id === e.target.value); up("forecast_id")(e.target.value || null); if (f) up("disease_id")(f.disease_id); }}>
          <option value="">No forecast</option>${forecasts.map((f) => html`<option value=${f.forecast_id}>${f.forecast_id} · ${f.disease_id}${f.is_public ? " · published" : ""} · ${fdate(f.created_at)}</option>`)}</select>
          <span class="hint">The run is bound to this forecast version; later model changes do not alter it.</span></div>
      </div>
      <div class="field"><label>Region scope <span class="muted" style="font-weight:400">(${S.region_ids.length ? `${S.region_ids.length} selected` : "all forecast regions"})</span></label>
        <div class="row wrap">${fRegions.length ? fRegions.map((id) => html`<label class="pill" style="cursor:pointer;height:28px"><input type="checkbox" checked=${S.region_ids.includes(id)} onChange=${() => toggleRegion(id)} /> ${names[id]?.name || id}</label>`)
          : html`<span class="small muted">${S.forecast_id ? "Loading forecast regions…" : "Choose a forecast first."}</span>`}</div></div>
      <div class="fgrid3">
        <${Num} label="Target weeks" value=${S.window.weeks} min=${1} max=${4} onChange=${up("window.weeks")} hint=${fq.meta?.target_week ? `from ${fdate(S.window.start_week || fq.meta.target_week)}` : null} />
        <${Num} label="Holiday surge" unit="% cases" value=${S.stressors.holiday_surge_pct} step=${5} min=${-50} max=${200} onChange=${up("stressors.holiday_surge_pct")} hint="Scenario assumption" />
        <${Num} label="Weather stress" unit="% cases" value=${S.stressors.weather_stress_pct} step=${5} min=${-50} max=${200} onChange=${up("stressors.weather_stress_pct")} hint="Scenario assumption, not a climate model" />
      </div>
    </${Panel}>
    ${research ? html`<${Panel} title="Transmission assumptions" note=${html`<${Sem} k="HYPOTHETICAL" /> uncalibrated, research only`}>
      <div class="fgrid3">
        <${Num} label="R0 low" value=${T.r0_low} step=${0.1} onChange=${up("transmission.r0_low")} /><${Num} label="R0 high" value=${T.r0_high} step=${0.1} onChange=${up("transmission.r0_high")} />
        <${Num} label="Weeks simulated" value=${T.weeks} min=${1} max=${26} onChange=${up("transmission.weeks")} />
        <${Num} label="Latent period" unit="days" value=${T.latent_days} step=${0.5} onChange=${up("transmission.latent_days")} />
        <${Num} label="Infectious period" unit="days" value=${T.infectious_days} step=${0.5} onChange=${up("transmission.infectious_days")} />
        <${Num} label="Reporting fraction" value=${T.reporting_fraction} step=${0.01} min=${0.01} max=${1} onChange=${up("transmission.reporting_fraction")} />
        <${Num} label="Contact reduction" unit="%" value=${T.contact_reduction_pct} min=${0} max=${90} onChange=${up("transmission.contact_reduction_pct")} />
        <${Num} label="Intervention from week" value=${T.intervention_start_week} min=${1} max=${26} onChange=${up("transmission.intervention_start_week")} />
        <${Num} label="Gravity coupling" value=${T.coupling} step=${0.01} min=${0} max=${1} onChange=${up("transmission.coupling")} hint="No OD data: assumed mixing kernel" />
      </div></${Panel}>` : html`
    <${Panel} title="Resources" note=${html`<${Sem} k="ASSUMPTION" /> planner inputs, verify before acting`}>
      <div class="fgrid3">
        <${Num} label="Mobile teams available" value=${R.mobile_teams_available} min=${0} onChange=${up("resources.mobile_teams_available")} />
        <${Num} label="Visits per team" unit="per week" value=${R.visits_per_team_per_week} min=${1} onChange=${up("resources.visits_per_team_per_week")} />
        <${Num} label="Teams withdrawn" value=${R.teams_removed} min=${0} onChange=${up("resources.teams_removed")} hint="Resource-shortage stressor" />
        <${Num} label="Cost per team-week" unit="BRL" value=${R.cost_per_team_week_brl} step=${100} min=${0} onChange=${up("resources.cost_per_team_week_brl")} />
        <${Num} label="Travel cost" unit="BRL per km" value=${R.cost_per_km_brl} step=${0.1} min=${0} onChange=${up("resources.cost_per_km_brl")} />
        <${Num} label="Total budget" unit="BRL" value=${R.total_budget_brl} step=${1000} min=${0} onChange=${up("resources.total_budget_brl")} hint="Empty = no budget limit" />
        <${Num} label="Max travel" unit="minutes" value=${R.max_travel_minutes} step=${15} min=${1} onChange=${up("resources.max_travel_minutes")} />
        <${Num} label="Travel speed" unit="km/h" value=${R.speed_kmh} step=${5} onChange=${up("resources.speed_kmh")} />
        <${Num} label="Max teams per region" value=${R.max_teams_per_region} min=${1} max=${200} onChange=${up("resources.max_teams_per_region")} />
      </div>
      <div class="field"><label>Team bases <span class="muted" style="font-weight:400">(${R.team_bases.length ? R.team_bases.length : "default: largest region"})</span></label>
        <div class="row wrap">${scope.map((id) => html`<label class="pill" style="cursor:pointer;height:28px"><input type="checkbox" checked=${R.team_bases.includes(id)} onChange=${() => toggleBase(id)} /> ${names[id]?.name || id}</label>`)}</div></div>
    </${Panel}>
    <${Panel} title="Capacity and service use" note=${html`<${Sem} k="ASSUMPTION" /> registered CNES beds are never used as operational capacity`}>
      <div class="fgrid3">
        <${Num} label="Dedicated visit slots" unit="per 10k residents / week" value=${C.visits_per_10k_residents_per_week} step=${0.01} min=${0} onChange=${up("capacity.visits_per_10k_residents_per_week")} />
        <${Num} label="Visit rate" unit="visits per reported case" value=${A.visit_rate} step=${0.05} min=${0} max=${1} onChange=${up("assumptions.visit_rate")} hint=${S.disease_id.endsWith("_sari") ? "Not applied to SARI signals" : "Scenario assumption"} />
        <${Num} label="Equity weight α" value=${A.equity_alpha} step=${0.1} min=${0} max=${5} onChange=${up("assumptions.equity_alpha")} hint="Weight on the facilities-per-capita access gap" />
        <${Num} label="Equity floor" unit="regions" value=${A.equity_floor_regions} min=${0} onChange=${up("assumptions.equity_floor_regions")} hint="Least-served regions guaranteed ≥1 team when short" />
        <${Num} label="Travel cost weight λ" value=${A.travel_cost_weight} step=${0.01} min=${0} onChange=${up("assumptions.travel_cost_weight")} />
      </div>
    </${Panel}>`}
    <${Panel} title="Simulation">
      <div class="fgrid3">
        <${Num} label="Trials" value=${S.simulation.draws} step=${100} min=${10} max=${100000} onChange=${up("simulation.draws")} hint="Seeded Monte Carlo draws from the forecast interval" />
        <${Num} label="Random seed" value=${S.simulation.random_seed} min=${0} onChange=${up("simulation.random_seed")} hint="Same seed + inputs → identical result" />
        <div class="field"><label for="sc-solver">Solver</label><select id="sc-solver" value=${S.simulation.solver} onChange=${(e) => up("simulation.solver")(e.target.value)}>
          <option value="auto">Auto (MILP when small)</option><option value="milp">MILP (HiGHS)</option><option value="greedy">Marginal greedy</option></select></div>
      </div>
      <div class="field"><label for="sc-notes">Notes</label><textarea id="sc-notes" rows="2" value=${S.notes || ""} onInput=${(e) => up("notes")(e.target.value || null)}></textarea></div>
      ${val && html`<${Checks} v=${val} />`}
      ${job && html`<div style="margin-top:16px"><${JobProgress} jobId=${job} onDone=${(j) => { if (j.status === "succeeded") navigate(`/simulation/runs/${runId}`); }} /></div>`}
      <div class="actions" style="margin-top:16px">
        ${!canWrite && html`<span class="small muted">Sign in as a planner role to save and run.</span>`}
        <button class="btn" disabled=${busy} onClick=${validate}>Validate</button>
        <button class="btn" disabled=${busy || !canWrite || (!dirty && !immutable)} onClick=${save}>${immutable ? "Save as new scenario" : scenario ? "Save version" : "Save draft"}</button>
        <button class="btn pri" disabled=${busy || !can("scenario.run") || !!job} onClick=${run}><${Icon} n="play" s=${12} />${dirty || immutable || !scenario ? "Save and run" : "Run"}</button>
        ${scenario && can("scenario.review") && html`<span class="spacer"></span><button class="btn" onClick=${() => setReview("approve")}>Mark reviewed</button><button class="btn danger" onClick=${() => setReview("reject")}>Reject</button>`}
      </div>
    </${Panel}>
    <${Confirm} open=${!!review} title=${review === "approve" ? "Mark scenario reviewed" : "Reject scenario"} reason="Review note (required)"
      body="The decision is recorded in the audit log. Nothing is dispatched from HealthPulse." confirm=${review === "approve" ? "Record review" : "Reject"} danger=${review === "reject"}
      onCancel=${() => setReview(null)} onConfirm=${(note) => doReview(review, note)} />
  </div>`;
}

export default function Simulation({ params, q }) {
  const { can } = useSession();
  const id = params.id;
  const disease = q.get("disease") || "dengue";
  const [bump, setBump] = useState(0);
  const list = useApi(`/scenarios?limit=50&_=${bump}`);
  const sc = useApi(id ? `/scenarios/${id}?_=${bump}` : null);
  const defs = useApi(`/scenarios/defaults?disease_id=${disease}`);
  const fcs = useApi("/admin/forecasts");
  const regs = useApi("/metadata/regions?scope=pilot&limit=200");
  const names = useMemo(() => Object.fromEntries((regs.data || []).map((r) => [r.municipality_id, r])), [regs.data]);
  const forecasts = fcs.data || [];
  const initial = useMemo(() => {
    if (id) return sc.data ? clone(sc.data.spec_json) : null;
    if (!defs.data) return null;
    const s = clone(defs.data);
    const f = q.get("forecast_id");
    if (f) s.forecast_id = f;
    const r = q.get("region");
    if (r) s.resources.team_bases = [r];
    s.name = r && names[r] ? `Preparedness scenario — ${names[r].name}` : "Preparedness scenario";
    s.resources.mobile_teams_available = 4;
    s.resources.total_budget_brl = 60000;
    s.resources.max_travel_minutes = 600;
    return s;
  }, [id, sc.data, defs.data, names]);
  const scenario = id ? sc.data : null;
  return html`<div class="page">
    <${TitleRow} title=${scenario ? scenario.name : "Simulation studio"} crumb=${id && html`<${Link} to="/simulation" cls="link">Simulation studio</${Link}> · version ${scenario?.version ?? "…"}`}
      sub=${scenario ? html`<${Status} s=${scenario.status} /> <${Status} s=${scenario.review_state} title="Review state" /> · pinned to <span class="idc">${scenario.spec_json.forecast_id || "—"}</span>`
        : "Operational what-if: forecast demand × planner assumptions against assumed capacity, with an optimized team allocation. Results are scenario estimates, not observed impact."}
      right=${id && html`<button class="btn" disabled=${!can("scenario.write")} onClick=${async () => { try { const r = await api(`/scenarios/${id}/clone`, { method: "POST" }); navigate(`/simulation/${r.data.scenario_id}`); } catch (e) { toast(e.message, "bad"); } }}><${Icon} n="copy" s=${14} />Clone</button>`} />
    ${!can("scenario.write") && html`<${Notices} kind="info" items=${["You are viewing as a read-only role: you can validate a scenario but not save or run it. Switch role from the account menu."]} />`}
    <div class="grid">
      <div class="s8">${initial ? html`<${Builder} initial=${initial} scenario=${scenario} forecasts=${forecasts} names=${names}
          onSaved=${(s) => { setBump((b) => b + 1); if (s && s.scenario_id !== id) navigate(`/simulation/${s.scenario_id}`); }} />`
        : sc.error ? html`<${Empty}>Scenario not found.</${Empty}>` : html`<${SkBlock} h=${420} />`}</div>
      <div class="s4 stack sim-side">
        ${scenario && html`<${Panel} title="Runs of this scenario" flush>${scenario.runs.length ? html`<table class="tbl dense"><tbody>${scenario.runs.map((r) => html`
          <tr class="click" onClick=${() => navigate(`/simulation/runs/${r.run_id}`)}><td><span class="idc">${r.run_id.slice(0, 16)}…</span></td><td><${Status} s=${r.status} /></td><td class="num muted">${ago(r.created_at)}</td></tr>`)}</tbody></table>`
          : html`<div style="padding:0 20px 8px"><${Empty}>Not run yet.</${Empty}></div>`}</${Panel}>`}
        <${Panel} title="Saved scenarios" flush right=${id && html`<${Link} to="/simulation" cls="btn sm"><${Icon} n="plus" s=${12} />New</${Link}>`}>
          <${Loadable} q=${list} sk=${html`<${SkRows} n=${4} cols=${2} />`}>${(rows) => rows.length ? html`<table class="tbl"><tbody>${rows.slice(0, 12).map((s) => html`
            <tr class=${"click" + (s.scenario_id === id ? " sel" : "")} onClick=${() => navigate(`/simulation/${s.scenario_id}`)}>
              <td><div class="two">${s.name}<div class="s">${s.mode === "transmission_research" ? "research · " : ""}v${s.version} · ${ago(s.updated_at)}</div></div></td>
              <td class="num"><div class="two">${s.last_run?.summary_json?.unmet_visits_proposed ? fmt.int(s.last_run.summary_json.unmet_visits_proposed.p50) : html`<${Status} s=${s.status} />`}
                <div class="s">${s.last_run?.summary_json?.unmet_visits_proposed ? "unmet, proposal p50" : ""}</div></div></td></tr>`)}</tbody></table>`
            : html`<div style="padding:0 20px 8px"><${Empty}>No scenarios saved yet.</${Empty}></div>`}</${Loadable}>
        </${Panel}>
        <${Panel} title="What the engine does">
          <ol class="small" style="margin:0;padding-left:18px;line-height:20px">
            <li>Draws reported cases per region-week from the pinned forecast's P10–P90 (lognormal, seeded, partly shared shock).</li>
            <li>Converts cases to visits with the visit-rate assumption and applies the stressors.</li>
            <li>Allocates mobile teams (MILP or greedy) to minimise access-weighted expected unmet visits within budget and travel limits.</li>
            <li>Evaluates baseline and proposal on the same draws.</li></ol>
        </${Panel}>
      </div>
    </div>
  </div>`;
}
