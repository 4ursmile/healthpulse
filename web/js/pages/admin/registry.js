// Model registry — question: "Which model versions wait for review, which are approved, and which serves?"
import { html } from "../../../vendor/preact-htm.js";
import { useApi, navigate } from "../../api.js";
import { Panel, TitleRow, Loadable, Status, Link, Empty, ago, fmt, label, SkRows } from "../../ui.js";

export default function Registry() {
  const q = useApi("/admin/registry/models");
  return html`<div class="page">
    <${Loadable} q=${q} sk=${html`<${SkRows} n=${5} />`}>${(rows) => {
      const waiting = rows.filter((m) => ["CANDIDATE", "REVIEWED"].includes(m.approval_state));
      const serving = rows.filter((m) => m.deployments.some((d) => d.status === "active"));
      return html`<${TitleRow} title="Model registry" sub=${html`<b>${waiting.length}</b> version${waiting.length === 1 ? "" : "s"} awaiting review · ${serving.length} serving. A champion needs an epidemiology and a platform sign-off; authors cannot review their own models.`} />
        <${Panel} title="Versions" note=${`${rows.length} registered`} flush>${rows.length ? html`<div class="tablewrap"><table class="tbl cards">
          <thead><tr><th>Model</th><th>Recipe</th><th>Approval</th><th>Evidence</th><th>Serving</th><th class="num">MAE +1</th><th class="num">MAE +4</th><th class="num">Registered</th></tr></thead>
          <tbody>${rows.map((m) => { const act = m.deployments.filter((d) => d.status === "active"); return html`<tr class="click" onClick=${() => navigate(`/admin/registry/${m.model_id}/${m.version}`)}>
            <td><div class="two"><${Link} to=${`/admin/registry/${m.model_id}/${m.version}`} cls="idc">${m.model_id}@${m.version}</${Link}><div class="s">${label(m.target_signal)}</div></div></td>
            <td class="muted hide-sm">${label(m.recipe_id)}</td><td><${Status} s=${m.approval_state} /></td><td class="hide-sm"><${Status} s=${m.pit_status} /></td>
            <td>${act.length ? act.map((d) => html`<${Status} s=${d.serving_alias} /> `) : html`<span class="faint">—</span>`}</td>
            <td class="num">${fmt.d1(m.metrics_summary_json?.["1"]?.mae)}</td><td class="num hide-sm">${fmt.d1(m.metrics_summary_json?.["4"]?.mae)}</td>
            <td class="num sub">${ago(m.created_at)} · ${m.created_by}</td></tr>`; })}</tbody></table></div>`
          : html`<div style="padding:0 20px"><${Empty} action=${html`<${Link} to="/admin/experiments" cls="btn sm">Experiments</${Link}>`}>No model registered. Register one from a completed training run.</${Empty}></div>`}</${Panel}>`;
    }}</${Loadable}>
  </div>`;
}
