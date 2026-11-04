// Simulation run — question: "How much assumed shortfall does the proposed allocation remove, where, and at what cost?"
import { html, useMemo, useState } from "../../vendor/preact-htm.js";
import { api, useApi, useJob, navigate, linkWith } from "../api.js";
import { Panel, TitleRow, Kpis, Notices, Sem, Status, Loadable, SkRows, SkBlock, Empty, Link, Icon, JobProgress, Prov, Id, fmt, fdate, ago, toast } from "../ui.js";
import { ChoroplethMap, Legend } from "../map.js";
import { Chain, chainFromLineage, Analyst } from "./_product_shared.js";
import { useSession } from "../app.js";

const band = (o) => (o ? `P10–P90 ${fmt.int(o.p10)}–${fmt.int(o.p90)}` : "—");

function breaksFor(vals) {
  const v = vals.filter((x) => x != null && x > 0).sort((a, b) => a - b);
  if (v.length < 2) return v.length ? [v[0] / 2] : [];
  const qs = [0.2, 0.4, 0.6, 0.8].map((p) => v[Math.min(v.length - 1, Math.floor(p * v.length))]);
  return [...new Set(qs)];
}

function Operational({ run, rows, names }) {
  const s = run.summary_json;
  const prov = run.provenance_json || {};
  const [week, setWeek] = useState("all");
  const per = useMemo(() => {
    const m = {};
    for (const r of rows) {
      if (week !== "all" && r.target_week !== week) continue;
      const x = (m[r.municipality_id] ||= { id: r.municipality_id, name: r.name, base: 0, prop: 0, demand: 0, cap_b: 0, cap_p: 0, teams: r.teams, over_b: 0, over_p: 0, n: 0 });
      x.base += r.unmet_baseline_p50; x.prop += r.unmet_proposed_p50; x.demand += r.demand_p50; x.cap_b += r.capacity_baseline; x.cap_p += r.capacity_proposed;
      x.w = r.equity_weight; x.over_b = Math.max(x.over_b, r.overload_prob_baseline); x.over_p = Math.max(x.over_p, r.overload_prob_proposed); x.n++;
    }
    return Object.values(m).sort((a, b) => b.base - a.base);
  }, [rows, week]);
  const weeks = [...new Set(rows.map((r) => r.target_week))].sort();
  const brk = breaksFor(per.flatMap((p) => [p.base, p.prop]));
  const vb = Object.fromEntries(per.map((p) => [p.id, p.base])), vp = Object.fromEntries(per.map((p) => [p.id, p.prop]));
  const ids = per.map((p) => p.id);
  const tip = (which) => (id, v) => html`<b>${names[id]?.name || id}</b><br/>${v == null ? html`<span class="muted">Not in scope</span>` : html`${fmt.int(v)} unmet ${s.demand_unit} <span class="muted">(${which}, median)</span>`}`;
  const unit = s.demand_unit;
  const ku = unit.includes("visits") ? "visits" : "cases";
  const util = s.expected_team_utilization;
  return html`<div class="stack">
    <${Kpis} items=${[
      { label: "Unmet, proposal", sem: "SCENARIO", lead: true, value: fmt.int(s.unmet_visits_proposed.p50), unit: ku, sub: band(s.unmet_visits_proposed) },
      { label: "Unmet, baseline", sem: "SCENARIO", value: fmt.int(s.unmet_visits_baseline.p50), unit: ku, sub: band(s.unmet_visits_baseline) },
      { label: "Simulated avoided shortfall", value: fmt.int(s.avoided_shortfall.p50), unit: ku, sub: `${band(s.avoided_shortfall)} · same draws` },
      { label: "Cost", value: fmt.brl(s.cost_brl), sub: s.budget_brl != null ? `of ${fmt.brl(s.budget_brl)} budget` : "no budget limit" },
      { label: "Regions over capacity", value: html`${fmt.int(s.regions_over_capacity_baseline)} → ${fmt.int(s.regions_over_capacity_proposed)}`,
        sub: `${s.teams_allocated} of ${s.teams_available} teams · utilisation ${util == null ? "—" : fmt.pct(util)}` },
    ]} />
    <div class="filters" style="margin:0"><span class="small muted">Maps and table show</span>
      <select aria-label="Week" value=${week} onChange=${(e) => setWeek(e.target.value)}><option value="all">All ${weeks.length} weeks (sum)</option>${weeks.map((w) => html`<option value=${w}>Week of ${fdate(w)}</option>`)}</select>
      <span class="small muted">Both maps share one scale</span></div>
    <div class="grid">
      <div class="s6"><${Panel} title="Baseline: unmet demand" note=${html`<${Sem} k="SCENARIO" /> median ${unit}, current assumed capacity`}>
        <${ChoroplethMap} values=${vb} breaks=${brk} focus=${ids} height=${320} label="Baseline unmet map" tooltip=${tip("baseline")} /></${Panel}></div>
      <div class="s6"><${Panel} title="Proposal: unmet demand" note=${html`<${Sem} k="SCENARIO" /> after ${s.teams_allocated} team(s) allocated`}>
        <${ChoroplethMap} values=${vp} breaks=${brk} focus=${ids} height=${320} label="Proposal unmet map" tooltip=${tip("proposal")} /></${Panel}></div>
      <div class="s12"><${Legend} breaks=${brk} unit=${unit} format=${fmt.int} note="Zero shortfall shows in the lightest class; out-of-scope regions are hatched" /></div>
      <div class="s12"><${Panel} title="By region" note=${`Medians over ${s.draws.toLocaleString()} seeded trials; overload = probability of any shortfall in a week`} flush>
        <div class="tablewrap"><table class="tbl cards">
          <thead><tr><th>Municipality</th><th class="num">Demand</th><th class="num">Capacity base → proposal</th><th class="num">Teams</th><th class="num">Unmet base</th><th class="num">Unmet proposal</th><th class="num">Overload</th></tr></thead>
          <tbody>${per.map((p) => html`<tr><td><div class="two">${p.name || p.id}<div class="s">equity weight ×${fmt.d2(p.w)}</div></div></td>
            <td class="num hide-sm">${fmt.int(p.demand)}</td><td class="num hide-sm">${fmt.int(p.cap_b)} → ${fmt.int(p.cap_p)}</td><td class="num sub">${p.teams} team${p.teams === 1 ? "" : "s"}</td>
            <td class="num hide-sm">${fmt.int(p.base)}</td><td class="num">${fmt.int(p.prop)}</td><td class="num hide-sm">${fmt.pct(p.over_b)} → ${fmt.pct(p.over_p)}</td></tr>`)}</tbody></table></div>
      </${Panel}></div>
      <div class="s7"><${Panel} title="Team assignments" note="Optimizer output for review — no team is dispatched from HealthPulse" flush>
        ${s.assignments.length ? html`<table class="tbl"><thead><tr><th>From base</th><th>To</th><th class="num">Teams</th><th class="num">Distance</th><th class="num">Travel</th></tr></thead>
          <tbody>${s.assignments.map((a) => html`<tr><td>${names[a.base]?.name || a.base}</td><td>${names[a.municipality_id]?.name || a.municipality_id}</td>
            <td class="num">${a.teams}</td><td class="num">${fmt.int(a.distance_km)} km</td><td class="num">${fmt.int(a.travel_minutes)} min</td></tr>`)}</tbody></table>`
          : html`<div style="padding:0 20px 8px"><${Empty}>No team was assigned${s.optimizer.binding_constraints?.length ? `: ${s.optimizer.binding_constraints.join(", ")}` : ""}.</${Empty}></div>`}
      </${Panel}></div>
      <div class="s5"><${Panel} title="Optimizer">
        <dl class="kv"><dt>Method</dt><dd>${s.optimizer.method} · ${s.optimizer.solver || "—"}</dd><dt>Optimality</dt><dd>${s.optimizer.optimality || "—"}</dd>
          <dt>Binding</dt><dd>${s.optimizer.binding_constraints?.length ? s.optimizer.binding_constraints.join("; ") : "none reported"}</dd>
          <dt>Equity floor</dt><dd>${s.optimizer.equity_floor_regions?.length ? `${s.optimizer.equity_floor_regions.length} region(s)` : "not used"}</dd>
          <dt>Case multiplier</dt><dd>× ${fmt.d2(s.case_multiplier)} <span class="muted">(stressors, assumption)</span></dd>
          <dt>Visit rate</dt><dd>${prov.assumptions?.visit_rate ?? "—"} <span class="muted">${prov.assumptions?.visit_rate_basis?.replace(/_/g, " ")}</span></dd>
          <dt>Capacity basis</dt><dd>${(prov.capacity_basis || "").replace(/_/g, " ")}</dd></dl>
      </${Panel}></div>
    </div>
  </div>`;
}

function Transmission({ run, rows, names }) {
  const s = run.summary_json;
  const per = {};
  for (const r of rows) { const x = (per[r.municipality_id] ||= { a: 0, b: 0, lo: 0, hi: 0 }); x.a += r.reported_p50; x.b += r.reported_int_p50; x.lo += r.reported_p10; x.hi += r.reported_p90; }
  return html`<div class="stack">
    <div class="notice"><${Sem} k="HYPOTHETICAL" /><div>${s.label}. Differences between arms come from the assumed contact reduction, not from measured programme effects.</div></div>
    <${Kpis} items=${[
      { label: "Infections, no intervention", sem: "HYPOTHETICAL", lead: true, value: fmt.compact(s.infections_no_intervention.p50), sub: band(s.infections_no_intervention) },
      { label: "Infections, with assumed reduction", sem: "HYPOTHETICAL", value: fmt.compact(s.infections_intervention.p50), sub: band(s.infections_intervention) },
      { label: "Relative change, median", value: fmt.signed(s.relative_change_p50 * 100, fmt.d1) + "%", sub: "same seeds both arms" },
      { label: "Individuals simulated", value: fmt.compact(s.individuals_simulated), sub: `${s.regions} regions · ${s.trials} trials · ${s.days} days` },
    ]} />
    <${Panel} title="Reported cases by region and week" note="Median with P10–P90, both arms" flush><div class="tablewrap"><table class="tbl dense">
      <thead><tr><th>Municipality</th><th class="num">Week</th><th class="num">No intervention</th><th class="num">P10–P90</th><th class="num">With reduction</th><th class="num">P10–P90</th></tr></thead>
      <tbody>${rows.map((r) => html`<tr><td>${names[r.municipality_id]?.name || r.municipality_id}</td><td class="num">${r.week}</td><td class="num">${fmt.int(r.reported_p50)}</td>
        <td class="num muted">${fmt.int(r.reported_p10)}–${fmt.int(r.reported_p90)}</td><td class="num">${fmt.int(r.reported_int_p50)}</td><td class="num muted">${fmt.int(r.reported_int_p10)}–${fmt.int(r.reported_int_p90)}</td></tr>`)}</tbody></table></div></${Panel}>
  </div>`;
}

function Compare({ run }) {
  const runs = useApi("/simulations?limit=50");
  const [other, setOther] = useState("");
  const cmp = useApi(other ? `/simulations/${run.run_id}/compare?baseline_id=${other}` : null);
  const opts = (runs.data || []).filter((r) => r.run_id !== run.run_id && r.status === "completed" && r.mode === run.mode);
  return html`<div class="stack" style="gap:12px">
    <select aria-label="Compare with run" value=${other} onChange=${(e) => setOther(e.target.value)}><option value="">Choose a completed run</option>
      ${opts.map((r) => html`<option value=${r.run_id}>${r.name || r.scenario_id} · ${ago(r.created_at)}</option>`)}</select>
    ${other && html`<${Loadable} q=${cmp} sk=${html`<${SkRows} n=${2} />`}>${(c, m, w) => html`
      ${w.length > 0 && html`<${Notices} items=${w} />`}
      <div class="small muted">${c.comparison_kind} · ${c.interpretation}</div>
      ${c.comparable && html`<table class="tbl dense"><thead><tr><th>Metric</th><th class="num">Other run</th><th class="num">This run</th></tr></thead><tbody>
        <tr><td>Unmet, proposal p50</td><td class="num">${fmt.int(c.a.unmet_visits_proposed?.p50)}</td><td class="num">${fmt.int(c.b.unmet_visits_proposed?.p50)}</td></tr>
        <tr><td>Avoided shortfall p50</td><td class="num">${fmt.int(c.a.avoided_shortfall?.p50)}</td><td class="num">${fmt.int(c.b.avoided_shortfall?.p50)}</td></tr>
        <tr><td>Cost</td><td class="num">${fmt.brl(c.a.cost_brl)}</td><td class="num">${fmt.brl(c.b.cost_brl)}</td></tr>
        <tr><td>Teams</td><td class="num">${c.a.teams_allocated}</td><td class="num">${c.b.teams_allocated}</td></tr></tbody></table>`}`}</${Loadable}>`}
    ${!opts.length && html`<span class="small muted">No other completed run of the same mode.</span>`}
  </div>`;
}

export default function SimRun({ params }) {
  const { can } = useSession();
  const [n, setN] = useState(0);
  const r = useApi(`/simulations/${params.run}?_=${n}`);
  const run = r.data;
  const done = run?.status === "completed";
  const res = useApi(done ? `/simulations/${params.run}/results` : null);
  const lin = useApi(done && run.forecast_id ? `/admin/lineage?run_id=${params.run}` : null);
  const regs = useApi("/metadata/regions?scope=pilot&limit=200");
  const names = useMemo(() => Object.fromEntries((regs.data || []).map((x) => [x.municipality_id, x])), [regs.data]);
  const prov = run?.provenance_json || {};
  const s = run?.summary_json;
  const research = run?.mode === "transmission_research";
  const sub = done && s && !research ? html`Proposal leaves a median of <b>${fmt.int(s.unmet_visits_proposed.p50)}</b> unmet ${s.demand_unit} against ${fmt.int(s.unmet_visits_baseline.p50)} at baseline, over ${s.weeks} weeks, for ${fmt.brl(s.cost_brl)}. Scenario estimate under listed assumptions, not observed impact.`
    : done && research ? "Hypothetical transmission research output." : run ? `Run is ${run.status}.` : null;
  const reproduce = async () => { try { const x = await api(`/admin/simulation-audit/${params.run}/reproduce`, { method: "POST" }); navigate(`/simulation/runs/${x.data.run_id}`); } catch (e) { toast(e.message, "bad"); } };
  return html`<div class="page">
    <${TitleRow} crumb=${html`<${Link} to="/simulation" cls="link">Simulation studio</${Link}>${run && html` / <${Link} to=${`/simulation/${run.scenario_id}`} cls="link">scenario</${Link}>`}`}
      title=${run ? `Run ${run.run_id.slice(0, 14)}…` : "Simulation run"} sub=${sub}
      right=${done && html`<a class="btn" href=${`/api/v1/simulations/${params.run}/export?fmt=csv`}><${Icon} n="download" s=${14} />CSV</a>
        <a class="btn" href=${`/api/v1/simulations/${params.run}/export?fmt=json`}>JSON</a>
        <button class="btn" disabled=${!can("scenario.run")} onClick=${reproduce} title="Re-run the same pinned inputs and seed">Reproduce</button>`} />
    <${Notices} items=${run?.warnings_json || []} />
    <${Loadable} q=${r} sk=${html`<${SkBlock} h=${96} />`}>${() => !done ? html`<${Panel} title="Progress">
        ${run.job_id ? html`<${JobProgress} jobId=${run.job_id} onDone=${() => setN((x) => x + 1)} />` : html`<${Empty}>No job attached.</${Empty}>`}</${Panel}>`
      : html`<${Loadable} q=${res} sk=${html`<${SkBlock} h=${300} />`}>${(rows) => research ? html`<${Transmission} run=${run} rows=${rows} names=${names} />` : html`<${Operational} run=${run} rows=${rows} names=${names} />`}</${Loadable}>`}</${Loadable}>
    ${done && html`<div class="grid" style="margin-top:var(--gap)">
      <div class="s7"><${Panel} title="Explain the trade-off" note="Grounded in this run's verified outputs">
        <${Analyst} context=${{ run_id: params.run, disease_id: prov.disease_id }} suggestions=${["Explain the trade-off", "How many lives will this save?"]} /></${Panel}></div>
      <div class="s5"><${Panel} title="Compare with another run"><${Compare} run=${run} /></${Panel}></div>
      <div class="s12"><${Panel} title="Provenance" note="Rerunning these pinned inputs with the same seed returns the same result hash">
        ${lin.data ? html`<${Chain} links=${chainFromLineage(lin.data, [{ k: "Scenario run", v: params.run }])} />` : prov.forecast_id ? html`<${SkBlock} h=${36} />` : null}
        <${Prov} items=${[html`Forecast <${Id} id=${prov.forecast_id} n=${18} />`, prov.model_version && `model ${prov.model_version}`, `seed ${prov.random_seed}`, `${fmt.int(prov.draws)} trials`,
          html`result sha256 <${Id} id=${run.result_sha256} n=${12} />`, prov.engine_version, prov.optimizer_version, prov.forecast_quality, prov.synthetic && html`<${Sem} k="SYNTHETIC" />`]} />
      </${Panel}></div>
    </div>`}
  </div>`;
}
