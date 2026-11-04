// Experiment wizard — six steps: problem, data, features, algorithm, validation, review & start.
import { html, useState, useEffect } from "../../../vendor/preact-htm.js";
import { useApi, navigate } from "../../api.js";
import { Panel, TitleRow, Loadable, Status, Link, Notices, Empty, fdate, fmt, label, SkBlock } from "../../ui.js";
import { useAction, Guarded, Field, Checks, newKey } from "./_shared.js";
import { usePro, MoreInPro } from "../../mode.js";

// Benchmark set: the recommended ensemble plus the two naive references every forecasting hub reports.
const BENCH = { persistence: {}, damped_trend: {}, ensemble: {} };

const STEPS = ["Problem", "Data", "Features", "Algorithm", "Validation", "Review & start"];

export default function Wizard({ q }) {
  const dsq = useApi("/admin/training-datasets");
  const rq = useApi("/admin/model-recipes");
  const [step, setStep] = useState(0);
  const [f, set] = useState({ name: "", dataset_id: q.get("dataset_id") || "", horizons: [1, 2, 3, 4], exclude_features: [],
    recipes: { ...BENCH }, seed: 42, max_cpu: 2, timeout_minutes: 30, nominal: 0.8, ablation: true });
  const [created, setCreated] = useState(null);
  const [val, setVal] = useState(null);
  const a = useAction(), b = useAction();
  const ds = (dsq.data || []).find((d) => d.dataset_id === f.dataset_id) || (dsq.data || [])[0];
  useEffect(() => { if (ds && ds.dataset_id !== f.dataset_id) set((x) => ({ ...x, dataset_id: ds.dataset_id })); }, [ds?.dataset_id]);
  const feats = ds ? Object.entries(ds.pit_report_json?.features || {}).filter(([, v]) => v.status === "ok").map(([k]) => k) : [];
  const rec = rq.data || {};
  const pro = usePro();
  const toggleRecipe = (k) => { const r = { ...f.recipes }; k in r ? delete r[k] : (r[k] = Object.fromEntries(Object.entries(rec[k].params).map(([p, s]) => [p, s.default]))); set({ ...f, recipes: r }); };
  const toggle = (k, v) => set({ ...f, [k]: f[k].includes(v) ? f[k].filter((x) => x !== v) : [...f[k], v].sort() });
  const hasBaseline = Object.keys(f.recipes).some((k) => rec[k]?.family === "baseline");
  const config = () => ({ dataset_id: f.dataset_id, name: f.name || undefined, horizons: f.horizons, exclude_features: f.exclude_features,
    recipes: Object.entries(f.recipes).map(([recipe, params]) => ({ recipe, params })), seed: +f.seed, ablation: f.ablation,
    calibration: { method: "out_of_fold_residual", nominal_coverage: +f.nominal }, resources: { max_cpu: +f.max_cpu, timeout_minutes: +f.timeout_minutes, max_memory_gb: 4 } });
  const createAndValidate = async () => {
    const r = await a.run("/admin/experiments", { body: config() }, "Experiment created");
    if (!r) return;
    setCreated(r.data);
    const v = await a.run(`/admin/experiments/${r.data.experiment_id}/validate`);
    if (v) setVal(v.data);
  };
  const start = async () => {
    const r = await b.run(`/admin/experiments/${created.experiment_id}/run`, { idem: newKey() }, "Training queued");
    if (r) navigate(`/admin/training-runs/${r.data.run_id}`);
  };
  const fits = ds ? Object.keys(f.recipes).length * f.horizons.length * ((ds.splits_json.folds || []).length + 2) : 0;
  const body = () => {
    if (!ds) return html`<${Empty} action=${html`<${Link} to="/admin/datasets/new" cls="btn sm">Build dataset</${Link}>`}>Training needs an immutable dataset first.</${Empty}>`;
    switch (step) {
      case 0: return html`<div class="fgrid">
        <${Field} label="Experiment name" id="wz-n"><input id="wz-n" type="text" placeholder=${`${label(ds.disease_id)} backtest`} value=${f.name} onInput=${(e) => set({ ...f, name: e.target.value })} /></${Field}>
        <${Field} label="Target signal" id="wz-t" hint="Fixed by the dataset; dengue notifications and SARI cases are never mixed"><input id="wz-t" type="text" readonly value=${label(ds.target_signal)} /></${Field}></div>
        <div class="field"><span style="font-weight:500">Horizons</span><div class="row">${ds.horizons_json.map((h) => html`<label class="row" style="gap:6px"><input type="checkbox" checked=${f.horizons.includes(h)} onChange=${() => toggle("horizons", h)} />+${h} wk</label>`)}</div>
          <div class="hint">One model per horizon. Geography: ${ds.regions_json.length} municipalities, municipality × week.</div></div>`;
      case 1: return html`<${Field} label="Training dataset" id="wz-d"><select id="wz-d" value=${f.dataset_id} onChange=${(e) => set({ ...f, dataset_id: e.target.value, exclude_features: [] })}>
          ${dsq.data.map((d) => html`<option value=${d.dataset_id}>${d.name} · ${d.pit_status}</option>`)}</select></${Field}>
        <dl class="kv"><dt>PIT status</dt><dd><${Status} s=${ds.pit_status} /></dd><dt>Rows</dt><dd>${fmt.int(ds.row_count)} (${fmt.int(ds.pit_report_json?.labeled_rows)} labeled)</dd>
          <dt>Issue weeks</dt><dd>${fdate(ds.issue_range_json.start)} – ${fdate(ds.issue_range_json.end)}</dd><dt>Label cutoff</dt><dd>${fdate(ds.label_cutoff)}</dd>
          <dt>Hash</dt><dd class="mono">${(ds.hash || "").slice(0, 20)}…</dd></dl>
        ${ds.pit_report_json?.note && html`<div style="margin-top:12px"><${Notices} items=${[ds.pit_report_json.note]} /></div>`}`;
      case 2: return html`<p class="small muted" style="margin:0 0 8px">Features in this dataset passed the PIT validator. Untick to exclude; ablation groups (cases only → +climate → +holiday → +mobility) are evaluated on the test window.</p>
        <div>${feats.map((k) => html`<label class="check" style="cursor:pointer"><input type="checkbox" checked=${!f.exclude_features.includes(k)} onChange=${() => toggle("exclude_features", k)} />
          <span class="idc" style="flex:1">${k}</span><span class="small muted">null ${fmt.pct(ds.pit_report_json.features[k].null_rate, 1)}</span></label>`)}</div>
        <label class="check"><input type="checkbox" checked=${f.ablation} onChange=${(e) => set({ ...f, ablation: e.target.checked })} /><span>Run the feature-group ablation</span></label>`;
      case 3: return html`<div class="row" style="justify-content:space-between;margin-bottom:8px">
          <button class="btn sm" onClick=${() => set({ ...f, recipes: Object.fromEntries(Object.keys(BENCH).map((k) => [k, Object.fromEntries(Object.entries(rec[k]?.params || {}).map(([p, s]) => [p, s.default]))])) })}>Use the benchmark set (recommended)</button>
          <${MoreInPro}>Show all recipes and hyper-parameters</${MoreInPro}></div>
        <div>${Object.entries(rec).filter(([k]) => pro || k in BENCH || k in f.recipes).map(([k, r]) => html`<div class="check" style="align-items:flex-start">
          <input type="checkbox" aria-label=${r.label} checked=${k in f.recipes} onChange=${() => toggleRecipe(k)} />
          <div style="flex:1"><b style="font-weight:500">${r.label}</b> <span class="pill">${r.family}</span><div class="muted">${r.description}</div>
            ${pro && k in f.recipes && Object.keys(r.params).length > 0 && html`<div class="fgrid3" style="margin-top:8px">${Object.entries(r.params).map(([p, s]) => html`
              <${Field} label=${label(p)} id=${`wz-${k}-${p}`} hint=${`${s.min} – ${s.max}`}><input id=${`wz-${k}-${p}`} type="number" min=${s.min} max=${s.max} step=${s.type === "int" ? 1 : "any"}
                value=${f.recipes[k][p]} onInput=${(e) => set({ ...f, recipes: { ...f.recipes, [k]: { ...f.recipes[k], [p]: +e.target.value } } })} /></${Field}>`)}</div>`}</div></div>`)}</div>
        ${!hasBaseline && html`<${Notices} kind="bad" items=${["Include at least one transparent baseline; validation requires it."]} />`}`;
      case 4: return html`<div class="fgrid">
          <${Field} label="Nominal interval coverage" id="wz-c" hint="Calibrated from out-of-fold residuals"><select id="wz-c" value=${f.nominal} onChange=${(e) => set({ ...f, nominal: +e.target.value })}>${[0.5, 0.8, 0.9].map((x) => html`<option value=${x}>${fmt.pct(x)}</option>`)}</select></${Field}>
          <${Field} label="Random seed" id="wz-s"><input id="wz-s" type="number" value=${f.seed} onInput=${(e) => set({ ...f, seed: e.target.value })} /></${Field}>
          <${Field} label="CPU limit" id="wz-cpu" hint="1 – 8 threads"><input id="wz-cpu" type="number" min="1" max="8" value=${f.max_cpu} onInput=${(e) => set({ ...f, max_cpu: e.target.value })} /></${Field}>
          <${Field} label="Timeout (minutes)" id="wz-to" hint="Hard-killed by the worker after this"><input id="wz-to" type="number" min="1" max="120" value=${f.timeout_minutes} onInput=${(e) => set({ ...f, timeout_minutes: e.target.value })} /></${Field}></div>
        <h3>Split (frozen in the dataset)</h3><table class="tbl dense" style="margin:4px -20px 0;width:calc(100% + 40px)"><tbody>
          ${ds.splits_json.folds.map((x) => html`<tr><td>Fold ${x.fold}</td><td class="num">${fdate(x.validation_start)} – ${fdate(x.validation_end)}</td><td class="num muted">train labels ≤ ${fdate(x.train_label_cutoff)}</td></tr>`)}
          <tr><td>Test</td><td class="num">${fdate(ds.splits_json.test.start)} – ${fdate(ds.splits_json.test.end)}</td><td class="num muted">${ds.splits_json.embargo_weeks}-week embargo</td></tr></tbody></table>`;
      default: return html`<dl class="kv"><dt>Dataset</dt><dd class="mono">${f.dataset_id}</dd><dt>Recipes</dt><dd>${Object.keys(f.recipes).join(", ")}</dd>
          <dt>Horizons</dt><dd>${f.horizons.map((h) => `+${h}`).join(", ")}</dd><dt>Excluded features</dt><dd>${f.exclude_features.join(", ") || "none"}</dd>
          <dt>Resources</dt><dd>${f.max_cpu} CPU · ${f.timeout_minutes} min</dd><dt>Estimated fits</dt><dd>${fmt.int(fits)} (non-binding)</dd></dl>
        <div class="row" style="margin-top:16px">${!created ? html`<${Guarded} perm="experiment.run" cls="btn" busy=${a.busy} disabled=${!hasBaseline || !f.horizons.length || !Object.keys(f.recipes).length}
            reason="Pick recipes (with a baseline) and horizons" onClick=${createAndValidate}>Create and validate</${Guarded}>`
          : html`<span class="small muted">Experiment <span class="idc">${created.experiment_id}</span> · config hash <span class="idc">${created.config_hash.slice(0, 12)}…</span></span>`}
          <span class="spacer"></span>${created && html`<${Guarded} perm="experiment.run" cls="btn pri" busy=${b.busy} disabled=${!val?.valid} reason="Validation must pass" onClick=${start}>Start training</${Guarded}>`}</div>
        ${val && html`<div style="margin-top:12px"><${Checks} items=${val.checks} /></div>`}`;
    }
  };
  return html`<div class="page">
    <${TitleRow} crumb=${html`<${Link} to="/admin/experiments" cls="link">Experiments</${Link}>`} title="New experiment" sub="Allowlisted recipes only; training runs in the worker, never inside an API request." />
    <div class="steps" role="tablist">${STEPS.map((s, i) => html`<button role="tab" aria-selected=${i === step} class=${i === step ? "on" : i < step ? "done" : ""} onClick=${() => setStep(i)}>${s}</button>`)}</div>
    <${Loadable} q=${dsq} sk=${html`<${SkBlock} h=${240} />`}>${() => html`<div class="grid"><div class="s8"><${Panel} title=${STEPS[step]}>${body()}
      <div class="row" style="margin-top:20px"><button class="btn" disabled=${step === 0} onClick=${() => setStep(step - 1)}>Back</button><span class="spacer"></span>
        ${step < STEPS.length - 1 && html`<button class="btn pri" disabled=${!ds} onClick=${() => setStep(step + 1)}>Next</button>`}</div></${Panel}></div>
      <div class="s4"><${Panel} title="Run manifest" note="What will be pinned"><dl class="kv">
        <dt>Dataset</dt><dd class="mono">${ds ? ds.dataset_id.slice(0, 18) + "…" : "—"}</dd><dt>PIT</dt><dd>${ds ? label(ds.pit_status) : "—"}</dd>
        <dt>Feature set</dt><dd class="mono">${ds ? `${ds.feature_set_id}@${ds.feature_set_version}` : "—"}</dd><dt>Features</dt><dd>${feats.length - f.exclude_features.length}</dd>
        <dt>Recipes</dt><dd>${Object.keys(f.recipes).length}</dd><dt>Horizons</dt><dd>${f.horizons.length}</dd><dt>Seed</dt><dd>${f.seed}</dd><dt>Fits</dt><dd>≈ ${fmt.int(fits)}</dd></dl></${Panel}></div></div>`}</${Loadable}>
  </div>`;
}
