// Experiments — question: "Which training runs are going, which finished, and which can I compare?"
import { html, useState, useEffect } from "../../../vendor/preact-htm.js";
import { useApi, navigate } from "../../api.js";
import { Panel, TitleRow, Loadable, Status, Link, Empty, ago, fmt, label, Icon, SkRows } from "../../ui.js";
import { Guarded } from "./_shared.js";

export default function Experiments() {
  const q = useApi("/admin/experiments");
  const [sel, setSel] = useState([]);
  const active = (q.data || []).some((e) => e.runs.some((r) => ["queued", "preparing", "training", "evaluating", "packaging", "running"].includes(r.status)));
  useEffect(() => { if (!active) return; const t = setInterval(q.reload, 3000); return () => clearInterval(t); }, [active]);
  const toggle = (id) => setSel(sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id].slice(-3));
  return html`<div class="page">
    <${TitleRow} title="Experiments" sub="Bounded, allowlisted recipes run in the worker with rolling-origin backtests; every run is pinned to an immutable dataset and config hash."
      right=${html`<button class="btn" disabled=${sel.length < 2} title=${sel.length < 2 ? "Tick two or three completed runs" : ""} onClick=${() => navigate(`/admin/experiments/compare?ids=${sel.join(",")}`)}>Compare ${sel.length ? `(${sel.length})` : ""}</button>
        <${Guarded} perm="experiment.run" cls="btn pri" onClick=${() => navigate("/admin/experiments/new")}><${Icon} n="plus" s=${14} />New experiment</${Guarded}>`} />
    <${Loadable} q=${q} sk=${html`<${SkRows} n=${5} />`}>${(rows) => rows.length ? html`<${Panel} title="Runs" note=${`${rows.length} experiment${rows.length > 1 ? "s" : ""} · ${rows.reduce((n, e) => n + e.runs.length, 0)} runs${active ? " · refreshing while a run is active" : ""}`} flush>
      <div class="tablewrap"><table class="tbl cards"><thead><tr><th><span class="sr">Select</span></th><th>Experiment</th><th>Recipes</th><th>Run</th><th>Status</th><th class="num">Progress</th><th class="num">Started</th></tr></thead>
        <tbody>${rows.flatMap((e) => (e.runs.length ? e.runs : [null]).map((r, i) => html`<tr>
          <td>${r && r.status === "completed" ? html`<input type="checkbox" aria-label=${`Select ${r.run_id}`} checked=${sel.includes(r.run_id)} onChange=${() => toggle(r.run_id)} />` : ""}</td>
          <td>${i === 0 ? html`<div class="two">${e.name}<div class="s">${label(e.target_signal)} · <span class="idc">${e.dataset_id.slice(0, 14)}…</span></div></div>` : ""}</td>
          <td class="muted sub">${i === 0 ? (e.config_json.recipes || []).map((x) => x.recipe).join(", ") : ""}</td>
          <td>${r ? html`<${Link} to=${`/admin/training-runs/${r.run_id}`} cls="idc">${r.run_id.slice(0, 18)}…</${Link}>` : html`<span class="muted">not started</span>`}</td>
          <td>${r ? html`<${Status} s=${r.status} />` : html`<${Status} s=${e.status} />`}</td>
          <td class="num">${r && r.progress != null ? fmt.pct(r.progress) : "—"}</td><td class="num hide-sm">${r ? ago(r.created_at) : ago(e.created_at)}</td></tr>`))}</tbody></table></div>
    </${Panel}>` : html`<${Empty} action=${html`<${Link} to="/admin/experiments/new" cls="btn sm">New experiment</${Link}>`}>No experiment yet.</${Empty}>`}</${Loadable}>
  </div>`;
}
