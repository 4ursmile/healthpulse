// Data sources — question: "Which sources are live, stale or blocked, and which can I ingest now?"
import { html, useState } from "../../../vendor/preact-htm.js";
import { useApi, navigate } from "../../api.js";
import { Panel, TitleRow, Loadable, Status, Link, Drawer, Empty, ago, fdate, fmt, Icon, Notices, Seg } from "../../ui.js";
import { useAction, Guarded, Field } from "./_shared.js";

const GROUPS = ["disease", "climate", "calendar", "mobility", "population", "geography", "healthcare", "workforce", "fixture", "other"];

function Register({ open, onClose, done }) {
  const [f, set] = useState({ source_id: "", display_name: "", source_group: "disease", connector_type: "file_upload", provenance_url: "",
    license_ref: "", pii_class: "aggregated", expected_grain: "", expected_cadence: "", auth_secret_ref: "", freshness_days: 30 });
  const a = useAction();
  const up = (k) => (e) => set({ ...f, [k]: e.target.value });
  const submit = async () => {
    const r = await a.run("/admin/sources", { body: { ...f, freshness_days: +f.freshness_days || null, auth_secret_ref: f.auth_secret_ref || null } }, "Source registered");
    if (r) { done(); onClose(); navigate(`/admin/sources/${r.data.source_id}`); }
  };
  return html`<${Drawer} open=${open} title="Register a source" onClose=${onClose}
    footer=${html`<button class="btn" onClick=${onClose}>Cancel</button><${Guarded} perm="source.write" cls="btn pri" busy=${a.busy} disabled=${!f.source_id}
      reason="A source id is required" onClick=${submit}>Register</${Guarded}>`}>
    <${Notices} kind="info" items=${["New sources start as “manual import” until a reviewed connector module exists. Credentials are never entered here — name a secret reference."]} />
    <${Field} label="Source id" id="rs-id" hint="Immutable lowercase slug, e.g. state_lab_reports"><input id="rs-id" type="text" value=${f.source_id} onInput=${up("source_id")} /></${Field}>
    <${Field} label="Display name" id="rs-name"><input id="rs-name" type="text" value=${f.display_name} onInput=${up("display_name")} /></${Field}>
    <div class="fgrid">
      <${Field} label="Group" id="rs-g"><select id="rs-g" value=${f.source_group} onChange=${up("source_group")}>${GROUPS.map((g) => html`<option>${g}</option>`)}</select></${Field}>
      <${Field} label="Connector type" id="rs-c"><select id="rs-c" value=${f.connector_type} onChange=${up("connector_type")}>
        ${["file_upload", "http_json", "http_csv", "bulk_archive", "georaster"].map((g) => html`<option>${g}</option>`)}</select></${Field}>
      <${Field} label="Sensitivity" id="rs-p"><select id="rs-p" value=${f.pii_class} onChange=${up("pii_class")}>
        ${["public", "aggregated", "restricted_personal"].map((g) => html`<option>${g}</option>`)}</select></${Field}>
      <${Field} label="Freshness threshold (days)" id="rs-f"><input id="rs-f" type="number" min="1" value=${f.freshness_days} onInput=${up("freshness_days")} /></${Field}>
      <${Field} label="Grain" id="rs-gr"><input id="rs-gr" type="text" placeholder="municipality-week" value=${f.expected_grain} onInput=${up("expected_grain")} /></${Field}>
      <${Field} label="Cadence" id="rs-cd"><input id="rs-cd" type="text" placeholder="weekly" value=${f.expected_cadence} onInput=${up("expected_cadence")} /></${Field}>
    </div>
    <${Field} label="Provenance URL" id="rs-u"><input id="rs-u" type="text" value=${f.provenance_url} onInput=${up("provenance_url")} /></${Field}>
    <${Field} label="License and attribution" id="rs-l"><input id="rs-l" type="text" value=${f.license_ref} onInput=${up("license_ref")} /></${Field}>
    <${Field} label="Secret reference (optional)" id="rs-s" hint="The name of a stored secret, never the secret itself"><input id="rs-s" type="text" value=${f.auth_secret_ref} onInput=${up("auth_secret_ref")} /></${Field}>
  </${Drawer}>`;
}

export default function Sources() {
  const q = useApi("/admin/sources");
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("all");
  return html`<div class="page">
    <${Loadable} q=${q}>${(rows) => {
      const groups = [...new Set(rows.map((r) => r.source_group))].sort((a, b) => GROUPS.indexOf(a) - GROUPS.indexOf(b));
      const vis = rows.filter((r) => filter === "all" || (filter === "live" ? r.status_detail.state === "healthy" : !["healthy"].includes(r.status_detail.state)));
      const live = rows.filter((r) => r.status_detail.state === "healthy").length;
      const blocked = rows.filter((r) => ["needs_authorization", "manual_import_required"].includes(r.status_detail.state));
      return html`
        <${TitleRow} title="Data sources" sub=${html`<b>${live} of ${rows.length}</b> sources have a published release. ${blocked.length} need an authorization or a manual file; no data is substituted for them.`}
          right=${html`<${Guarded} perm="source.write" cls="btn pri" onClick=${() => setOpen(true)}><${Icon} n="plus" s=${14} />Register source</${Guarded}>`} />
        <div class="filters"><${Seg} label="Filter" value=${filter} onChange=${setFilter} options=${[["all", `All ${rows.length}`], ["live", `Published ${live}`], ["attention", `Not published ${rows.length - live}`]]} /></div>
        <div class="stack">${groups.map((g) => { const items = vis.filter((r) => r.source_group === g); return items.length ? html`
          <${Panel} title=${g.charAt(0).toUpperCase() + g.slice(1)} note=${`${items.length} source${items.length > 1 ? "s" : ""}`} flush>
            <div class="tablewrap"><table class="tbl cards fixed"><colgroup><col style="width:27%" /><col style="width:10%" /><col style="width:15%" /><col style="width:21%" /><col style="width:8%" /><col style="width:10%" /><col style="width:9%" /></colgroup><thead><tr><th>Source</th><th>Connector</th><th>State</th><th class="num">Coverage</th><th class="num">Rows</th><th class="num">30-day success</th><th class="num">Last success</th></tr></thead>
            <tbody>${items.map((s) => { const st = s.status_detail; return html`<tr class="click" onClick=${() => navigate(`/admin/sources/${s.source_id}`)}>
              <td><div class="two"><${Link} to=${`/admin/sources/${s.source_id}`}>${s.display_name}</${Link}><div class="s"><span class="idc">${s.source_id}</span> · ${s.pii_class}</div></div></td>
              <td class="hide-sm muted">${s.connector_type}</td>
              <td><${Status} s=${st.state} />${st.is_synthetic_only ? html` <span class="pill syn">Synthetic</span>` : ""}${!s.enabled ? html` <span class="pill">Disabled</span>` : ""}</td>
              <td class="num sub">${st.coverage_start ? `${fdate(st.coverage_start)} – ${fdate(st.coverage_end)}` : "—"}</td>
              <td class="num hide-sm">${st.rows_latest ? fmt.int(st.rows_latest) : "—"}</td>
              <td class="num hide-sm">${st.success_rate_30d == null ? "—" : `${fmt.pct(st.success_rate_30d)} (${st.attempts_30d})`}</td>
              <td class="num hide-sm">${st.last_success_at ? ago(st.last_success_at) : "never"}</td></tr>`; })}</tbody></table></div>
          </${Panel}>` : null; })}
          ${!vis.length && html`<${Empty}>No source matches this filter.</${Empty}>`}</div>`;
    }}</${Loadable}>
    <${Register} open=${open} onClose=${() => setOpen(false)} done=${q.reload} />
  </div>`;
}
