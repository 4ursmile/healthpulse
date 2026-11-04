// Training run — question: "Did the model beat transparent baselines on held-out issue weeks, with honest intervals?"
import { html, useState } from "../../../vendor/preact-htm.js";
import { useApi, navigate } from "../../api.js";
import { Panel, TitleRow, Loadable, Status, Link, Id, Kpis, Notices, Empty, Tabs, JobProgress, Confirm, ftime, fdate, fmt, label, SkBlock, Icon } from "../../ui.js";
import { CatLines, Calibration, BarList, useWidth, ticks } from "../../charts.js";
import { useAction, Guarded, MetricGrid, MetricSelect, metricFmt, metricLabel } from "./_shared.js";
import { usePro, MoreInPro } from "../../mode.js";
import { Leaderboard, boardFrom, headline, niceRecipe } from "../../leaderboard.js";

const BASE = ["persistence", "seasonal_naive", "historical_median", "damped_trend"];
let WINNER = null;
const HUE = (rid) => (BASE.includes(rid) ? "var(--chart-muted)" : rid === WINNER ? "var(--chart-1)" : "var(--chart-2)");

function BacktestChart({ rows }) {
  const [ref, W] = useWidth();
  const [hv, setHv] = useState(null);
  const H = 240, pad = { l: 48, r: 16, t: 10, b: 24 };
  if (!rows.length) return html`<${Empty}>No predictions for this selection.</${Empty}>`;
  const t = (s) => new Date(s + "T00:00:00Z").getTime();
  const x0 = t(rows[0].target_week), x1 = t(rows[rows.length - 1].target_week);
  const yt = ticks(0, Math.max(1, ...rows.map((r) => Math.max(r.label ?? 0, r.p90 ?? r.pred ?? 0))), 4);
  const iw = Math.max(10, W - pad.l - pad.r), ih = H - pad.t - pad.b;
  const X = (d) => pad.l + ((t(d) - x0) / Math.max(1, x1 - x0)) * iw, Y = (v) => pad.t + ih - (v / yt[yt.length - 1]) * ih;
  const line = (k) => rows.filter((r) => r[k] != null).map((r, i) => `${i ? "L" : "M"}${X(r.target_week)},${Y(r[k])}`).join("");
  const band = rows.filter((r) => r.p10 != null);
  const poly = [...band.map((r) => `${X(r.target_week)},${Y(r.p90)}`), ...band.slice().reverse().map((r) => `${X(r.target_week)},${Y(r.p10)}`)].join(" ");
  const folds = [...new Set(rows.map((r) => r.fold))];
  return html`<div ref=${ref} style="position:relative">${W > 0 && html`<svg class="chart" width=${W} height=${H} role="img" aria-label="Held-out predictions against reported labels"
    onMouseMove=${(e) => { const b = e.currentTarget.getBoundingClientRect(); const tt = x0 + ((e.clientX - b.left - pad.l) / iw) * (x1 - x0); let best = 0; rows.forEach((r, i) => { if (Math.abs(t(r.target_week) - tt) < Math.abs(t(rows[best].target_week) - tt)) best = i; }); setHv(best); }}
    onMouseLeave=${() => setHv(null)}>
    ${yt.map((v) => html`<line x1=${pad.l} x2=${W - pad.r} y1=${Y(v)} y2=${Y(v)} stroke="var(--grid)" /><text x=${pad.l - 8} y=${Y(v) + 4} text-anchor="end">${fmt.compact(v)}</text>`)}
    ${folds.map((fo) => { const fr = rows.filter((r) => r.fold === fo); const xa = X(fr[0].target_week); return html`<line x1=${xa} x2=${xa} y1=${pad.t} y2=${pad.t + ih} stroke="var(--control)" stroke-dasharray="2 3" />
      <text x=${xa + 4} y=${pad.t + 10}>${fo === "test" ? "test" : `fold ${fo}`}</text>`; })}
    ${band.length && html`<polygon points=${poly} fill="var(--chart-1)" opacity="0.16" />`}
    <path d=${line("label")} fill="none" stroke="var(--text)" stroke-width="1.75" />
    <path d=${line("pred")} fill="none" stroke="var(--chart-1)" stroke-width="2" stroke-dasharray="5 4" />
    ${hv !== null && html`<line x1=${X(rows[hv].target_week)} x2=${X(rows[hv].target_week)} y1=${pad.t} y2=${pad.t + ih} stroke="var(--control)" />`}
    <line x1=${pad.l} x2=${W - pad.r} y1=${Y(0)} y2=${Y(0)} stroke="var(--control)" />
    <text x=${pad.l} y=${H - 6}>${fdate(rows[0].target_week)}</text><text x=${W - pad.r} y=${H - 6} text-anchor="end">${fdate(rows[rows.length - 1].target_week)}</text>
  </svg>`}${hv !== null && html`<div class="ctip" style=${`left:${Math.min(W - 240, X(rows[hv].target_week) + 8)}px;top:8px`}>${fdate(rows[hv].target_week)} · reported ${fmt.int(rows[hv].label)} · predicted ${fmt.int(rows[hv].pred)} (${fmt.int(rows[hv].p10)}–${fmt.int(rows[hv].p90)})</div>`}
  <div class="ckey" style="margin-top:8px"><span><i></i>Reported (label)</span><span><i class="dash"></i>Held-out prediction</span><span><i class="band"></i>Calibrated interval</span></div></div>`;
}

function Predictions({ rid, rep }) {
  const munis = Object.keys(rep.by_region[Object.keys(rep.by_region)[0]]?.["1"] || {});
  const recipes = Object.keys(rep.recipes);
  const [m, setM] = useState(munis[0]);
  const [rc, setRc] = useState(WINNER && recipes.includes(WINNER) ? WINNER : recipes[0]);
  const [h, setH] = useState(1);
  const q = useApi(`/admin/training-runs/${rid}/predictions?recipe=${rc}&horizon=${h}&municipality_id=${m}`);
  return html`<div class="filters" style="margin-bottom:12px">
      <select aria-label="Municipality" value=${m} onChange=${(e) => setM(e.target.value)}>${munis.map((x) => html`<option value=${x}>${x}</option>`)}</select>
      <select aria-label="Recipe" value=${rc} onChange=${(e) => setRc(e.target.value)}>${recipes.map((x) => html`<option value=${x}>${label(x)}</option>`)}</select>
      <select aria-label="Horizon" value=${h} onChange=${(e) => setH(+e.target.value)}>${[1, 2, 3, 4].map((x) => html`<option value=${x}>+${x} weeks</option>`)}</select></div>
    <${Loadable} q=${q} sk=${html`<${SkBlock} h=${240} />`}>${(rows) => html`<${BacktestChart} rows=${rows} />`}</${Loadable}>`;
}

function Report({ run, rep }) {
  const pro = usePro();
  const [tab0, setTab] = useState("overview");
  const tab = pro || ["overview", "predictions"].includes(tab0) ? tab0 : "overview";
  const hasWis = Object.values(rep.recipes).some((r) => r.test?.["1"]?.wis != null);
  const [metric, setMetric] = useState(hasWis ? "wis" : "mae");
  const [reg, setReg] = useState(null);
  const a = useAction();
  const recipes = Object.keys(rep.recipes);
  const hs = Object.keys(rep.comparison).map(Number).sort();
  const board = boardFrom(rep);
  const model = board.find((r) => !BASE.includes(r.recipe))?.recipe || recipes.find((r) => !BASE.includes(r)) || recipes[0];
  WINNER = model;
  const comp = rep.comparison;
  const skill = (h) => comp[h]?.[model]?.skill_vs_baseline;
  const rows = recipes.map((rc) => ({ key: rc, byH: rep.recipes[rc].test }));
  const cov = hs.map((h) => rep.recipes[model].test[h]?.coverage80);
  return html`
    <${Kpis} items=${[
      { label: `${label(model)} vs best baseline, +1 wk`, value: skill(1) == null ? "—" : fmt.signed(skill(1) * 100, fmt.int) + "%", sub: `MAE ${fmt.d1(comp[1]?.[model]?.mae)} vs ${label(comp[1]?.[model]?.baseline || "")}` },
      { label: `vs best baseline, +${hs[hs.length - 1]} wk`, value: skill(hs[hs.length - 1]) == null ? "—" : fmt.signed(skill(hs[hs.length - 1]) * 100, fmt.int) + "%", sub: `MAE ${fmt.d1(comp[hs[hs.length - 1]]?.[model]?.mae)} vs ${label(comp[hs[hs.length - 1]]?.[model]?.baseline || "")}` },
      { label: `${fmt.pct(rep.nominal_coverage)} interval coverage`, value: cov.some((x) => x != null) ? `${fmt.pct(Math.min(...cov.filter((x) => x != null)))}–${fmt.pct(Math.max(...cov.filter((x) => x != null)))}` : "—",
        sub: `test window, by horizon: ${cov.map((x, i) => `+${hs[i]} ${fmt.pct(x)}`).join(", ")}` },
      { label: "Point-in-time evidence", value: rep.pit_status === "PIT_VERIFIED" ? "Verified" : "Unverified", sub: `test ${fdate(rep.split.test.start)} – ${fdate(rep.split.test.end)}` },
    ]} />
    <div style="margin-top:var(--gap)"><${Tabs} tabs=${pro ? [["overview", "Overview"], ["folds", "Temporal folds"], ["errors", "Error analysis"], ["intervals", "Intervals"], ["ablation", "Ablation"], ["predictions", "Predictions"], ["lineage", "Lineage & config"]]
      : [["overview", "Overview"], ["predictions", "Predictions"]]} value=${tab} onChange=${setTab} /></div>
    ${tab === "overview" && html`<div class="grid">
      <div class="s12"><${Panel} title="Leaderboard" note="Every recipe on the same held-out weeks" right=${html`<${MoreInPro}>Show scores</${MoreInPro}>`}>
        <p class="hero-line">${headline(rep, model)}</p>
        <${Leaderboard} rep=${rep} limit=${4} />
        ${!pro && html`<div class="row" style="margin-top:16px;gap:12px"><${Guarded} perm="model.propose" cls="btn pri" onClick=${() => setReg(model)}>Register ${niceRecipe(model)} for review</${Guarded}>
          <span class="small muted">Registering creates a candidate; it still needs a clinical and a platform sign-off before it is published.</span></div>`}
      </${Panel}></div>
      ${pro && html`<div class="s8"><${Panel} title="Test metrics" note="Held-out issue weeks; the best value per horizon is in bold" right=${html`<${MetricSelect} value=${metric} onChange=${setMetric} />`} flush>
        <${MetricGrid} rows=${rows} horizons=${hs} metric=${metric} best=${["coverage80", "outbreak_recall", "outbreak_precision"].includes(metric) ? "max" : "min"} /></${Panel}></div>
      <div class="s4"><${Panel} title=${`${metricLabel(metric)} by horizon`} note=${html`<div class="ckey" style="margin-top:4px">${recipes.map((rc) => html`<span><i style=${`border-top-color:${HUE(rc)}`}></i>${label(rc)}</span>`)}</div>`}>
        <${CatLines} cats=${hs.map((h) => `+${h}`)} series=${recipes.map((rc) => ({ name: label(rc), color: HUE(rc), dash: BASE.includes(rc), values: hs.map((h) => rep.recipes[rc].test[h]?.[metric] ?? null) }))} yfmt=${metricFmt(metric)} /></${Panel}></div>
      <div class="s12"><${Panel} title="Register a model" note="Creates an immutable candidate version for review; nothing is deployed" flush>
        <table class="tbl"><thead><tr><th>Recipe</th><th>Family</th><th class="num">MAE +1</th><th class="num">Skill +1</th><th class="num">Artifact</th><th class="num">Action</th></tr></thead>
          <tbody>${recipes.map((rc) => html`<tr><td>${label(rc)}</td><td class="muted">${BASE.includes(rc) ? "baseline" : "model"}</td><td class="num">${fmt.d1(rep.recipes[rc].test[1]?.mae)}</td>
            <td class="num">${comp[1]?.[rc] ? fmt.signed(comp[1][rc].skill_vs_baseline * 100, fmt.int) + "%" : "—"}</td>
            <td class="num"><span class="idc">${(run.artifacts_json?.models?.[rc]?.sha256 || "").slice(0, 10)}…</span></td>
            <td class="num"><${Guarded} perm="model.propose" cls="btn sm" onClick=${() => setReg(rc)}>Register</${Guarded}></td></tr>`)}</tbody></table></${Panel}></div>`}</div>`}
    ${tab === "folds" && html`<${Panel} title="Fold metrics" note="Rolling origin; each fold trains only on labels released before it" right=${html`<${MetricSelect} value=${metric} onChange=${setMetric} />`} flush>
      <div class="tablewrap"><table class="tbl"><thead><tr><th>Recipe</th><th>Fold</th>${hs.map((h) => html`<th class="num">+${h} wk</th>`)}</tr></thead>
        <tbody>${recipes.flatMap((rc) => [...new Set(rep.recipes[rc].folds.map((x) => x.fold))].map((fo, i) => html`<tr><td>${i === 0 ? label(rc) : ""}</td><td>${fo}</td>
          ${hs.map((h) => html`<td class="num">${metricFmt(metric)(rep.recipes[rc].folds.find((x) => x.fold === fo && x.horizon === h)?.[metric])}</td>`)}</tr>`))}</tbody></table></div>
      <div class="small muted" style="padding:8px 20px 0">${rep.split.folds.map((x) => `fold ${x.fold}: ${fdate(x.validation_start)} – ${fdate(x.validation_end)}`).join(" · ")}</div></${Panel}>`}
    ${tab === "errors" && html`<div class="grid">
      <div class="s7"><${Panel} title=${`Failure slices · ${label(model)}`} note="Test window" right=${html`<${MetricSelect} value=${metric} onChange=${setMetric} />`} flush>
        <${MetricGrid} rows=${Object.keys(rep.recipes[model].test_slices[hs[0]] || {}).map((sl) => ({ key: sl, byH: Object.fromEntries(hs.map((h) => [h, rep.recipes[model].test_slices[h]?.[sl]])) }))} horizons=${hs} metric=${metric} /></${Panel}></div>
      <div class="s5"><${Panel} title=${`Error by municipality · +1 wk`} note=${label(metricLabel(metric))} flush>
        <div style="padding:0 20px"><${BarList} rows=${Object.entries(rep.by_region[model]?.["1"] || {}).map(([mu, v]) => ({ label: mu, value: v[metric], sub: `n ${v.n}` })).sort((x, y) => (y.value ?? 0) - (x.value ?? 0))} format=${metricFmt(metric)} /></div></${Panel}></div>
      ${rep.importance && html`<div class="s12"><${Panel} title="Permutation importance" note=${`${rep.importance.method} · +${rep.importance.horizon} wk · ${rep.importance.caveat}`}>
        <${BarList} rows=${rep.importance.features.slice(0, 10).map((x) => ({ label: x.feature, value: x.importance }))} format=${fmt.d1} /></${Panel}></div>`}</div>`}
    ${tab === "intervals" && html`<div class="grid">
      <div class="s6"><${Panel} title="Calibration" note="Nominal vs empirical coverage on the test window, by horizon (model)">
        <${Calibration} points=${hs.map((h, i) => ({ name: `+${h} wk`, color: ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-muted)"][i], pts: rep.calibration[`${model}|${h}`] || [] }))} />
        <div class="ckey">${hs.map((h, i) => html`<span><i style=${`border-top-color:${["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-muted)"][i]}`}></i>+${h} wk</span>`)}<span><i class="ref"></i>Perfect calibration</span></div></${Panel}></div>
      <div class="s6"><${Panel} title="Coverage and width" note=${`${fmt.pct(rep.nominal_coverage)} nominal; multiplicative out-of-fold residual intervals`} flush>
        <${MetricGrid} rows=${rows} horizons=${hs} metric="coverage80" best="max" />
        <div style="height:12px"></div><${MetricGrid} rows=${rows} horizons=${hs} metric="interval_width" /></${Panel}></div></div>`}
    ${tab === "ablation" && html`<${Panel} title="Feature-group ablation" note="Gradient boosting on the test window; groups add sources cumulatively" flush>
      ${rep.ablation ? html`<table class="tbl"><thead><tr><th>Group</th><th class="num">Features</th>${hs.map((h) => html`<th class="num">MAE +${h}</th>`)}</tr></thead>
        <tbody>${Object.entries(rep.ablation).map(([g, v]) => html`<tr><td>${g}</td>${v.status === "ok" ? html`<td class="num">${v.features.length}</td>${hs.map((h) => html`<td class="num">${fmt.d1(v.test[h]?.mae)}</td>`)}`
          : html`<td colspan=${hs.length + 1} class="muted">Unavailable: ${v.reason}</td>`}</tr>`)}</tbody></table>
        <div class="small muted" style="padding:8px 20px 0">A higher MAE after adding a group means that group did not help on this window; associations are not causal.</div>`
        : html`<div style="padding:0 20px"><${Empty}>Ablation was not requested for this run.</${Empty}></div>`}</${Panel}>`}
    ${tab === "predictions" && html`<${Panel} title="Held-out predictions" note="Fold and test predictions against reported labels"><${Predictions} rid=${run.run_id} rep=${rep} /></${Panel}>`}
    ${tab === "lineage" && html`<div class="grid">
      <div class="s6"><${Panel} title="Pinned inputs"><dl class="kv"><dt>Experiment</dt><dd class="mono">${run.experiment_id}</dd><dt>Dataset</dt><dd><${Link} to=${`/admin/datasets/${run.dataset_id}`} cls="link idc">${run.dataset_id}</${Link}></dd>
        <dt>Config hash</dt><dd class="mono">${run.config_hash.slice(0, 24)}…</dd><dt>Fold predictions</dt><dd class="mono">${(run.artifacts_json?.fold_predictions_sha256 || "").slice(0, 24)}…</dd>
        <dt>Tracking</dt><dd>${run.mlflow_run_id ? html`MLflow <span class="idc">${run.mlflow_run_id}</span>` : "built-in registry (MLflow not configured)"}</dd></dl></${Panel}></div>
      <div class="s6"><${Panel} title="Environment"><dl class="kv">${Object.entries(run.env_json || {}).map(([k, v]) => html`<dt>${label(k)}</dt><dd class="mono">${v}</dd>`)}</dl></${Panel}></div>
      <div class="s12"><${Panel} title="Model artifacts" flush><table class="tbl dense"><tbody>${Object.entries(run.artifacts_json?.models || {}).map(([k, v]) => html`<tr><td>${label(k)}</td><td class="idc">${v.artifact_uri}</td><td class="num idc">${v.sha256.slice(0, 16)}…</td></tr>`)}</tbody></table></${Panel}></div></div>`}
    <${Confirm} open=${!!reg} busy=${a.busy} title=${`Register ${label(reg || "")}?`} body="The artifact becomes an immutable candidate version. It needs a clinical and a platform sign-off before it can serve as champion." confirm="Register"
      onCancel=${() => setReg(null)} onConfirm=${async () => { const r = await a.run("/admin/registry/models", { body: { run_id: run.run_id, recipe: reg } }, "Registered as candidate"); setReg(null);
        if (r) navigate(`/admin/registry/${r.data.model_id}/${r.data.version}`); }} />`;
}

export default function Run({ params }) {
  const q = useApi(`/admin/training-runs/${params.id}`);
  const done = q.data?.status === "completed";
  const rep = useApi(done ? `/admin/training-runs/${params.id}/report` : null);
  return html`<div class="page">
    <${Loadable} q=${q} sk=${html`<${SkBlock} h=${200} />`}>${(run) => html`
      <${TitleRow} crumb=${html`<${Link} to="/admin/experiments" cls="link">Experiments</${Link}>`} title="Training run"
        sub=${html`<${Id} id=${run.run_id} n=${22} /> <${Status} s=${run.status} /> · started ${ftime(run.created_at)} by ${run.created_by}${run.finished_at ? ` · finished ${ftime(run.finished_at)}` : ""}`}
        right=${html`<button class="btn" onClick=${() => { q.reload(); rep.reload(); }}><${Icon} n="refresh" s=${14} />Refresh</button>`} />
      ${!done && html`<${Panel} title="Progress" note="Stages and logs come from the training worker">${run.job_id ? html`<${JobProgress} jobId=${run.job_id} onDone=${() => q.reload()} />` : html`<${Empty}>Not queued.</${Empty}>`}</${Panel}>`}
      ${done && html`<${Loadable} q=${rep} sk=${html`<${SkBlock} h=${300} />`}>${(r) => html`<${Report} run=${run} rep=${r} />`}</${Loadable}>`}`}</${Loadable}>
  </div>`;
}
