// Feature store — question: "Which features are point-in-time safe to train on, and which are blocked and why?"
import { html, useState } from "../../../vendor/preact-htm.js";
import { useApi, setQuery, navigate } from "../../api.js";
import { Panel, TitleRow, Loadable, Status, Link, Empty, Seg, ago, fmt, label, Icon, SkRows, Kpis } from "../../ui.js";
import { Guarded } from "./_shared.js";

export const lagText = (d) => {
  const p = d.params || {};
  if (d.template === "rolling") return `${p.window_weeks} wk ${p.agg || ""} ending ${p.end_lag_weeks} wk before issue`;
  if (p.lag_weeks != null) return `${p.lag_weeks} wk before issue`;
  if (d.template === "holiday_window") return `target ${p.start_offset_days >= 0 ? "+" : ""}${p.start_offset_days}…${p.end_offset_days} days (known ahead)`;
  if (d.template === "calendar") return "target week (known ahead)";
  if (d.template === "seasonal_lag") return "target week − 52 weeks";
  if (d.template === "static_asof") return "as of the prior year";
  return "—";
};

export default function Features({ q }) {
  const text = q.get("q") || "";
  const group = q.get("group") || "";
  const r = useApi(`/admin/features?${new URLSearchParams({ ...(text ? { q: text } : {}), ...(group ? { group } : {}) })}`);
  const all = useApi("/admin/features");
  const [draft, setDraft] = useState(text);
  const counts = (all.data || []).reduce((m, d) => ((m[d.leakage_status] = (m[d.leakage_status] || 0) + 1), m), {});
  const groups = [...new Set((all.data || []).map((d) => d.feature_group))];
  return html`<div class="page">
    <${TitleRow} title="Feature store" sub=${all.data ? html`<b>${counts.pass || 0} of ${all.data.length}</b> definitions pass the point-in-time validator; ${counts.leak_blocked || 0} would leak future data and ${counts.source_missing || 0} wait on a missing source.` : "Loading definitions…"}
      right=${html`<${Link} to="/admin/feature-sets" cls="btn">Feature sets</${Link}><${Guarded} perm="feature.write" cls="btn pri" onClick=${() => navigate("/admin/features/new")}><${Icon} n="plus" s=${14} />New feature</${Guarded}>`} />
    <div class="filters">
      <input type="search" aria-label="Search features" placeholder="Search name or description" style="width:280px" value=${draft}
        onInput=${(e) => setDraft(e.target.value)} onKeyDown=${(e) => e.key === "Enter" && setQuery({ q: draft || null })} onBlur=${() => setQuery({ q: draft || null })} />
      <select aria-label="Group" value=${group} onChange=${(e) => setQuery({ group: e.target.value || null })}>
        <option value="">All groups</option>${groups.map((g) => html`<option value=${g}>${label(g)}</option>`)}</select>
    </div>
    <${Panel} title="Definitions" note=${r.data ? `${r.data.length} shown` : null} flush>
      <${Loadable} q=${r} sk=${html`<${SkRows} n=${8} cols=${6} />`}>${(rows) => rows.length ? html`<div class="tablewrap"><table class="tbl cards">
        <thead><tr><th>Feature</th><th>Template</th><th>Window and lag</th><th>Unit</th><th>Sources</th><th>PIT validation</th><th>State</th><th class="num">Used by</th></tr></thead>
        <tbody>${rows.map((d) => html`<tr class="click" onClick=${() => navigate(`/admin/features/${d.feature_id}`)}>
          <td><div class="two"><${Link} to=${`/admin/features/${d.feature_id}`} cls="idc">${d.feature_id}</${Link}><div class="s" style="max-width:360px;white-space:normal">${d.description}</div></div></td>
          <td class="hide-sm muted">${d.template}</td><td class="sub muted">${lagText(d)}</td><td class="hide-sm muted">${d.unit || "—"}</td>
          <td class="hide-sm muted">${(d.source_refs || []).join(", ") || "derived"}</td>
          <td><${Status} s=${d.leakage_status} /></td><td class="hide-sm"><${Status} s=${d.state} /> <span class="small muted">v${d.version}</span></td>
          <td class="num hide-sm">${d.referenced_by.length ? d.referenced_by.join(", ") : html`<span class="faint">—</span>`}</td></tr>`)}</tbody></table></div>`
        : html`<div style="padding:0 20px"><${Empty}>No feature matches.</${Empty}></div>`}</${Loadable}>
    </${Panel}>
  </div>`;
}
