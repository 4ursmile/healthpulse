// Training datasets — question: "Which immutable, PIT-audited datasets can I train on?"
import { html } from "../../../vendor/preact-htm.js";
import { useApi, navigate } from "../../api.js";
import { Panel, TitleRow, Loadable, Status, Link, Empty, ago, fdate, fmt, label, Icon, SkRows } from "../../ui.js";
import { Guarded } from "./_shared.js";

export default function Datasets() {
  const q = useApi("/admin/training-datasets");
  return html`<div class="page">
    <${TitleRow} title="Training datasets" sub="Frozen per forecast issue week: features use only releases available at issue time; labels only releases available by the label cutoff."
      right=${html`<${Guarded} perm="dataset.build" cls="btn pri" onClick=${() => navigate("/admin/datasets/new")}><${Icon} n="plus" s=${14} />Build dataset</${Guarded}>`} />
    <${Panel} title="Datasets" note=${q.data ? `${q.data.length} total` : null} flush>
      <${Loadable} q=${q} sk=${html`<${SkRows} n=${4} cols=${6} />`}>${(rows) => rows.length ? html`<div class="tablewrap"><table class="tbl cards">
        <thead><tr><th>Dataset</th><th>Target</th><th>Feature set</th><th>PIT</th><th class="num">Issue weeks</th><th class="num">Rows</th><th class="num">Labeled</th><th class="num">Created</th></tr></thead>
        <tbody>${rows.map((d) => html`<tr class="click" onClick=${() => navigate(`/admin/datasets/${d.dataset_id}`)}>
          <td><div class="two"><${Link} to=${`/admin/datasets/${d.dataset_id}`}>${d.name}</${Link}><div class="s idc">${d.dataset_id.slice(0, 20)}…</div></div></td>
          <td class="muted">${label(d.target_signal)}</td><td class="idc hide-sm">${d.feature_set_id}@${d.feature_set_version}</td><td><${Status} s=${d.pit_status} /></td>
          <td class="num sub">${fdate(d.issue_range_json.start)} – ${fdate(d.issue_range_json.end)}</td><td class="num hide-sm">${fmt.int(d.row_count)}</td>
          <td class="num hide-sm">${fmt.int(d.pit_report_json?.labeled_rows)}</td><td class="num hide-sm">${ago(d.created_at)}</td></tr>`)}</tbody></table></div>`
        : html`<div style="padding:0 20px"><${Empty} action=${html`<${Link} to="/admin/datasets/new" cls="btn sm">Build</${Link}>`}>No dataset yet.</${Empty}></div>`}</${Loadable}>
    </${Panel}>
  </div>`;
}
