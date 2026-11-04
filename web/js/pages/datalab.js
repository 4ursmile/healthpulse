// Data lab — question: "Where does each number come from, how fresh is it, and which releases passed quality gates?"
import { html } from "../../vendor/preact-htm.js";
import { useApi } from "../api.js";
import { Panel, TitleRow, Kpis, Notices, Status, Loadable, SkRows, SkBlock, Empty, Link, fmt, fdate, ago } from "../ui.js";
import { useSession } from "../app.js";

export default function DataLab() {
  const { me, can } = useSession();
  const src = useApi("/metadata/sources");
  const cat = useApi("/admin/catalog");
  const ql = useApi("/admin/quality");
  const rows = (src.data || []).filter((s) => s.group !== "fixture");
  const healthy = rows.filter((s) => s.state === "healthy").length;
  const blocked = rows.filter((s) => ["needs_authorization", "manual_import_required"].includes(s.state)).length;
  const inc = ql.data?.incidents || [];
  const hard = inc.filter((i) => i.severity === "hard").length;
  const operator = can("source.ingest");
  return html`<div class="page">
    <${TitleRow} title="Data lab" sub="Every source is a versioned release. Missing sources stay missing — nothing is substituted."
      right=${operator && html`<${Link} to="/admin/sources" cls="btn pri">Manage sources</${Link}>`} />
    <${Loadable} q=${src} sk=${html`<${SkBlock} h=${96} />`}>${() => html`<${Kpis} items=${[
      { label: "Sources delivering", lead: true, value: `${healthy}`, unit: `of ${rows.length}`, sub: "healthy, within freshness window" },
      { label: "Blocked by access", value: fmt.int(blocked), sub: "need authorization or a manual file" },
      { label: "Quality incidents", value: fmt.int(inc.length), sub: html`${fmt.int(hard)} hard (quarantined), ${fmt.int(inc.length - hard)} warnings` },
      { label: "Silver tables with data", value: fmt.int((cat.data?.silver || []).filter((t) => t.vintages).length), sub: `of ${(cat.data?.silver || []).length} canonical tables` }]} />`}</${Loadable}>
    <div class="grid" style="margin-top:var(--gap)">
      <div class="s12"><${Panel} title="Sources" note="Release class tells how often a source can change; last success is the newest published snapshot" flush>
        <${Loadable} q=${src} sk=${html`<${SkRows} n=${6} />`}>${() => html`<div class="tablewrap"><table class="tbl cards">
          <thead><tr><th>Source</th><th>Group</th><th>Release class</th><th>State</th><th class="num">Covers to</th><th class="num">Last success</th></tr></thead>
          <tbody>${rows.map((s) => html`<tr><td>${operator ? html`<${Link} to=${`/admin/sources/${s.source_id}`} cls="link">${s.display_name}</${Link}>` : s.display_name}</td>
            <td class="hide-sm muted">${s.group}</td><td class="hide-sm muted">${s.release_class.replace(/_/g, " ").toLowerCase()}</td><td class="sub"><${Status} s=${s.state} /></td>
            <td class="num hide-sm">${fdate(s.coverage_end)}</td><td class="num hide-sm">${s.last_success_at ? ago(s.last_success_at) : "—"}</td></tr>`)}</tbody></table></div>`}</${Loadable}>
      </${Panel}></div>
      <div class="s7"><${Panel} title="Canonical tables" note="Silver layer: typed, keyed, vintage-aware" flush>
        <${Loadable} q=${cat} sk=${html`<${SkRows} n=${6} />`}>${(c) => html`<table class="tbl dense">
          <thead><tr><th>Table</th><th>Grain</th><th class="num">Vintages</th><th class="num">Current rows</th></tr></thead>
          <tbody>${c.silver.map((t) => html`<tr><td><span class="idc">${t.table}</span></td><td class="muted">${t.grain}</td><td class="num">${fmt.int(t.vintages)}</td>
            <td class="num">${t.vintages ? fmt.int(t.rows_current) : html`<span class="muted">—</span>`}</td></tr>`)}</tbody></table>`}</${Loadable}>
      </${Panel}></div>
      <div class="s5"><${Panel} title="Quality gates" note="Hard failures quarantine a release; the last good release keeps serving" flush>
        <${Loadable} q=${ql} sk=${html`<${SkRows} n=${4} cols=${2} />`}>${() => inc.length ? html`<table class="tbl dense"><tbody>${inc.slice(0, 8).map((i) => html`<tr>
          <td><div class="two">${i.gate.replace(/_/g, " ")}<div class="s">${i.source_id} · ${i.table} · ${ago(i.retrieved_at)}</div></div></td>
          <td class="num"><span class=${"pill " + (i.severity === "hard" ? "bad" : "warn")}>${i.severity === "hard" ? "Quarantined" : "Warning"}</span></td></tr>`)}</tbody></table>
          ${inc.length > 8 && html`<div class="small muted" style="padding:8px 20px">${inc.length - 8} more in Catalog & quality</div>`}`
          : html`<div style="padding:0 20px"><${Empty}>No failed gates.</${Empty}></div>`}</${Loadable}>
      </${Panel}></div>
    </div>
    <div class="small muted" style="margin-top:var(--gap)">Patient-level SIVEP rows are aggregated on import and never served; previews show normalized aggregates only.</div>
  </div>`;
}
