// Dataset detail — question: "What exactly is in this frozen dataset, and how strong is its point-in-time evidence?"
import { html } from "../../../vendor/preact-htm.js";
import { useApi, navigate } from "../../api.js";
import { Panel, TitleRow, Loadable, Status, Link, Id, Kpis, Notices, fdate, ftime, fmt, label, SkBlock } from "../../ui.js";
import { Guarded } from "./_shared.js";

export default function Dataset({ params }) {
  const q = useApi(`/admin/training-datasets/${params.id}`);
  return html`<div class="page">
    <${Loadable} q=${q} sk=${html`<${SkBlock} h=${200} />`}>${(d) => {
      const p = d.pit_report_json || {};
      const s = d.splits_json;
      const feats = Object.entries(p.features || {});
      return html`
        <${TitleRow} crumb=${html`<${Link} to="/admin/datasets" cls="link">Training datasets</${Link}>`} title=${d.name}
          sub=${html`<${Id} id=${d.dataset_id} n=${24} /> · ${label(d.target_signal)} · <span class="idc">${d.feature_set_id}@${d.feature_set_version}</span> · created ${ftime(d.created_at)} by ${d.created_by}`}
          right=${html`<${Guarded} perm="experiment.run" cls="btn pri" onClick=${() => navigate(`/admin/experiments/new?dataset_id=${d.dataset_id}`)}>New experiment</${Guarded}>`} />
        <${Notices} items=${[p.note, ...(p.warnings || [])].filter(Boolean)} />
        <${Kpis} items=${[
          { label: "PIT status", value: label(d.pit_status), sub: `policy ${p.policy?.replace(/_/g, " ")}` },
          { label: "Rows", value: fmt.int(d.row_count), sub: `${fmt.int(p.labeled_rows)} labeled` },
          { label: "Approximate-availability rows", value: fmt.int(p.approx_feature_rows), sub: "use the publication-lag rule" },
          { label: "Issue weeks", value: `${d.issue_range_json.start.slice(0, 4)}–${d.issue_range_json.end.slice(0, 4)}`, sub: `${fdate(d.issue_range_json.start)} – ${fdate(d.issue_range_json.end)} · labels to ${fdate(d.label_cutoff)}` },
        ]} />
        <div class="grid" style="margin-top:var(--gap)">
          <div class="s7"><${Panel} title="Features" note="Null rate after PIT filtering, distribution and approximation" flush>
            <div class="tablewrap"><table class="tbl cards"><thead><tr><th>Feature</th><th>Status</th><th class="num">Null</th><th class="num">Approx. rows</th><th class="num">Mean</th><th class="num">Range</th></tr></thead>
              <tbody>${feats.map(([k, v]) => html`<tr><td><${Link} to=${`/admin/features/${k}`} cls="idc">${k}</${Link}></td>
                <td><${Status} s=${v.status === "ok" ? "pass" : v.status} title=${v.reason || ""} /></td>
                <td class="num">${v.null_rate != null ? fmt.pct(v.null_rate, 1) : "—"}</td><td class="num hide-sm">${fmt.int(v.approx_rows)}</td>
                <td class="num hide-sm">${fmt.d2(v.mean)}</td><td class="num sub">${v.min != null ? `${fmt.d1(v.min)} – ${fmt.d1(v.max)}` : "—"}</td></tr>`)}</tbody></table></div></${Panel}></div>
          <div class="s5 stack">
            <${Panel} title="Audit invariants"><dl class="kv">
              <dt>Inputs released by issue</dt><dd>${p.max_input_available_at_le_issue_at ? "asserted for every row" : "not asserted"}</dd>
              <dt>Label week after anchor</dt><dd>${p.label_after_anchor ? "asserted" : "not asserted"}</dd>
              <dt>Unique keys</dt><dd class="mono">${(p.unique_keys || []).join(" + ")}</dd>
              <dt>Sources</dt><dd>${(p.sources || []).join(", ")}</dd><dt>Content hash</dt><dd class="mono">${(d.hash || "").slice(0, 20)}…</dd>
              <dt>Materialization</dt><dd><${Id} id=${d.materialization_id} n=${18} /></dd><dt>Regions</dt><dd class="mono">${d.regions_json.join(", ")}</dd></dl></${Panel}>
            <${Panel} title="Labels by horizon" flush><table class="tbl dense"><thead><tr><th>Horizon</th><th class="num">Rows</th><th class="num">Labeled</th><th class="num">Mean</th><th class="num">Max</th></tr></thead>
              <tbody>${(d.label_summary || []).map((r) => html`<tr><td>+${r.horizon} wk</td><td class="num">${fmt.int(r.rows)}</td><td class="num">${fmt.int(r.labeled)}</td><td class="num">${fmt.d1(r.mean)}</td><td class="num">${fmt.int(r.max)}</td></tr>`)}</tbody></table></${Panel}>
          </div>
          <div class="s7"><${Panel} title="Rolling-origin split" note=${s.rule} flush><table class="tbl dense">
            <thead><tr><th>Window</th><th class="num">Evaluation issue weeks</th><th class="num">Train labels up to</th></tr></thead>
            <tbody>${s.folds.map((x) => html`<tr><td>Fold ${x.fold}</td><td class="num">${fdate(x.validation_start)} – ${fdate(x.validation_end)}</td><td class="num">${fdate(x.train_label_cutoff)}</td></tr>`)}
              <tr><td><b style="font-weight:500">Test</b></td><td class="num">${fdate(s.test.start)} – ${fdate(s.test.end)}</td><td class="num">${fdate(s.test.train_label_cutoff)}</td></tr></tbody></table>
            <div class="small muted" style="padding:8px 20px 0">${s.embargo_weeks}-week embargo · ${s.validation_weeks} weeks per fold${s.geography_holdout?.length ? ` · geography holdout ${s.geography_holdout.join(", ")}` : " · no geography holdout"}</div></${Panel}></div>
          <div class="s5"><${Panel} title="Rows per issue year" flush><table class="tbl dense"><tbody>${(d.issue_week_counts || []).map((r) => html`<tr><td>${r.year}</td><td class="num">${fmt.int(r.rows)}</td></tr>`)}</tbody></table></${Panel}></div>
          <div class="s12"><${Panel} title="Source vintages" note=${`${(p.source_vintages || []).length} feature vintages · ${(d.label_vintages_json || []).length} label vintages`}>
            <div class="mono small" style="line-height:20px">${(p.source_vintages || []).map((v) => html`<div>${v}</div>`)}</div></${Panel}></div>
        </div>`;
    }}</${Loadable}>
  </div>`;
}
