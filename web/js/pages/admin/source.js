// Source detail — question: "Is this source's latest release usable, and what do I do next (test, ingest, import, quarantine)?"
import { html, useState, useEffect } from "../../../vendor/preact-htm.js";
import { useApi, api, invalidate } from "../../api.js";
import { Panel, TitleRow, Kpis, Loadable, Status, Link, Id, Empty, Notices, Confirm, JobProgress, Tabs, ago, fdate, ftime, fmt, Icon, toast, label, SkBlock } from "../../ui.js";
import { useAction, Guarded, Checks, Field, newKey } from "./_shared.js";

const EDIT_CFG = ["start_year", "diseases", "rate_limit_s", "batch_size", "min_weekly_coverage", "start_date", "years_ahead", "page_size",
  "max_pages_per_municipality", "delimiter", "simplify_tolerance_deg"];

function ConfigForm({ s, reload }) {
  const [f, set] = useState({});
  const [reason, setReason] = useState("");
  const a = useAction();
  useEffect(() => set({ enabled: !!s.enabled, freshness_days: s.freshness_days ?? "", publication_lag_days: s.publication_lag_days ?? "",
    schedule_cron: s.schedule_cron || "", auth_secret_ref: s.auth_secret_ref || "", cfg: Object.fromEntries(EDIT_CFG.filter((k) => k in s.config).map((k) => [k, JSON.stringify(s.config[k])])) }), [s.config_version]);
  if (!f.cfg) return null;
  const save = async () => {
    const cfg = {};
    try { for (const [k, v] of Object.entries(f.cfg)) if (JSON.stringify(s.config[k]) !== v) cfg[k] = JSON.parse(v); }
    catch (e) { toast("A config value is not valid JSON", "bad"); return; }
    const r = await a.run(`/admin/sources/${s.source_id}`, { method: "PATCH", body: { expected_version: s.config_version, enabled: f.enabled,
      freshness_days: f.freshness_days === "" ? null : +f.freshness_days, publication_lag_days: f.publication_lag_days === "" ? null : +f.publication_lag_days,
      schedule_cron: f.schedule_cron || null, auth_secret_ref: f.auth_secret_ref || null, config: cfg, reason } }, `Saved as config version ${s.config_version + 1}`);
    reload();
  };
  return html`<div class="fgrid">
      <div class="field inline"><label for="cf-en">Enabled</label><label class="toggle"><input id="cf-en" type="checkbox" checked=${f.enabled} onChange=${(e) => set({ ...f, enabled: e.target.checked })} /><span></span></label></div>
      <${Field} label="Schedule (cron)" id="cf-cron" hint="Recorded for the operator; pulls run when triggered"><input id="cf-cron" type="text" placeholder="0 6 * * 1" value=${f.schedule_cron} onInput=${(e) => set({ ...f, schedule_cron: e.target.value })} /></${Field}>
      <${Field} label="Freshness threshold (days)" id="cf-fr"><input id="cf-fr" type="number" min="0" value=${f.freshness_days} onInput=${(e) => set({ ...f, freshness_days: e.target.value })} /></${Field}>
      <${Field} label="Publication lag (days)" id="cf-pl"><input id="cf-pl" type="number" min="0" value=${f.publication_lag_days} onInput=${(e) => set({ ...f, publication_lag_days: e.target.value })} /></${Field}>
      <${Field} label="Secret reference" id="cf-sec" hint="Name only; values stay in the secrets store"><input id="cf-sec" type="text" value=${f.auth_secret_ref} onInput=${(e) => set({ ...f, auth_secret_ref: e.target.value })} /></${Field}>
      ${Object.keys(f.cfg).map((k) => html`<${Field} label=${label(k)} id=${"cfg-" + k} hint="JSON value"><input id=${"cfg-" + k} type="text" class="mono" value=${f.cfg[k]} onInput=${(e) => set({ ...f, cfg: { ...f.cfg, [k]: e.target.value } })} /></${Field}>`)}
    </div>
    <${Field} label="Reason for the change" id="cf-r"><input id="cf-r" type="text" value=${reason} onInput=${(e) => setReason(e.target.value)} /></${Field}>
    <div class="row"><span class="small muted">Config version ${s.config_version} · updated ${ago(s.updated_at)}</span><span class="spacer"></span>
      <${Guarded} perm="source.write" cls="btn pri" busy=${a.busy} onClick=${save}>Save revision</${Guarded}></div>`;
}

function Snapshots({ snaps, reload }) {
  const [openId, setOpen] = useState(null);
  const [act, setAct] = useState(null);
  const a = useAction();
  if (!snaps.length) return html`<div style="padding:0 20px"><${Empty}>No snapshot yet. Run an ingest or upload a file.</${Empty}></div>`;
  return html`<div class="tablewrap"><table class="tbl cards"><thead><tr><th>Snapshot</th><th>Table</th><th>Data quality</th><th class="num">Rows</th><th class="num">Coverage</th><th class="num">Retrieved</th><th class="num">Action</th></tr></thead>
    <tbody>${snaps.map((s) => { const gates = s.dq_report_json?.gates || []; const failed = gates.filter((g) => !g.passed); return html`
      <tr class="click" onClick=${() => setOpen(openId === s.snapshot_id ? null : s.snapshot_id)} aria-expanded=${openId === s.snapshot_id}>
        <td><${Id} id=${s.snapshot_id} n=${16} /></td><td class="muted">${s.silver_table}</td>
        <td><${Status} s=${s.dq_status} />${s.published ? "" : html` <span class="pill">Not published</span>`}${s.is_synthetic ? html` <span class="pill syn">Synthetic</span>` : ""}
          ${failed.length ? html`<div class="small muted">${failed.length} gate${failed.length > 1 ? "s" : ""} failed</div>` : ""}</td>
        <td class="num">${fmt.int(s.row_count)}</td><td class="num sub">${s.coverage_start ? `${fdate(s.coverage_start)} – ${fdate(s.coverage_end)}` : "—"}</td>
        <td class="num hide-sm">${ftime(s.retrieved_at)}</td>
        <td class="num hide-sm">${s.published ? html`<${Guarded} perm="source.write" cls="btn sm" onClick=${(e) => { e.stopPropagation(); setAct({ kind: "quarantine", s }); }}>Quarantine</${Guarded}>`
          : s.silver_uri ? html`<${Guarded} perm="admin.settings" cls="btn sm" onClick=${(e) => { e.stopPropagation(); setAct({ kind: "release", s }); }}>Release…</${Guarded}>` : ""}</td></tr>
      ${openId === s.snapshot_id && html`<tr><td colspan="7" style="height:auto;padding:12px 20px;background:var(--raised)">
        <div class="small muted" style="margin-bottom:6px">Bronze <span class="idc">${s.bronze_uri}</span> · checksum <span class="idc">${(s.checksum || "").slice(0, 16)}…</span></div>
        <${Checks} items=${gates} nameKey="gate" /></td></tr>`}`; })}</tbody></table></div>
    <${Confirm} open=${!!act} busy=${a.busy} danger=${act?.kind === "quarantine"}
      title=${act?.kind === "quarantine" ? "Quarantine this snapshot?" : "Release by override?"}
      body=${act?.kind === "quarantine" ? "Readers fall back to the previous published vintage. Published forecasts are immutable and do not change."
        : "Overrides the quality gates for this snapshot. Gates that would duplicate or mis-join counts cannot be overridden."}
      confirm=${act?.kind === "quarantine" ? "Quarantine" : "Release"} reason="Reason (recorded in the audit log)"
      onCancel=${() => setAct(null)} onConfirm=${async (reason) => {
        const r = await a.run(`/admin/snapshots/${act.s.snapshot_id}/${act.kind}`, { body: { reason } }, act.kind === "quarantine" ? "Snapshot quarantined" : "Snapshot released");
        if (r) { setAct(null); reload(); }
      }} />`;
}

function Preview({ sid }) {
  const [d, set] = useState(null);
  const [t, setT] = useState(null);
  const a = useAction();
  const load = async () => { const r = await a.run(`/admin/sources/${sid}/preview`, { body: { limit: 50 } }); if (r) { set(r.data); setT(Object.keys(r.data)[0] || null); } };
  if (!d) return html`<div class="row"><span class="muted small">Normalized Silver columns only, capped at 50 rows; raw restricted records are never previewed.</span><span class="spacer"></span>
    <button class="btn sm" disabled=${a.busy} onClick=${load}>${a.busy ? "Loading…" : "Load preview"}</button></div>`;
  const tables = Object.keys(d);
  if (!tables.length) return html`<${Empty}>Nothing published to preview.</${Empty}>`;
  const cur = d[t];
  const cols = cur.rows.length ? Object.keys(cur.rows[0]) : [];
  return html`<${Tabs} tabs=${tables.map((x) => [x, `${x} (${fmt.int(d[x].total)})`])} value=${t} onChange=${setT} />
    <div class="small muted" style="margin-bottom:8px">Missingness: ${Object.entries(cur.missingness).filter(([, v]) => v > 0).map(([k, v]) => `${k} ${fmt.pct(v, 1)}`).join(" · ") || "none"}</div>
    <div class="tablewrap" style="max-height:360px"><table class="tbl dense"><thead><tr>${cols.map((c) => html`<th>${c}</th>`)}</tr></thead>
      <tbody>${cur.rows.map((r) => html`<tr>${cols.map((c) => html`<td class=${typeof r[c] === "number" ? "num" : ""} style="white-space:nowrap">${r[c] === null ? html`<span class="faint">—</span>` : typeof r[c] === "number" ? (Number.isInteger(r[c]) ? fmt.int(r[c]) : fmt.d2(r[c])) : String(r[c]).slice(0, 40)}</td>`)}</tr>`)}</tbody></table></div>`;
}

export default function Source({ params }) {
  const q = useApi(`/admin/sources/${params.id}`);
  const [job, setJob] = useState(null);
  const [probe, setProbe] = useState(null);
  const [rows, setRows] = useState(1000000);
  const [file, setFile] = useState(null);
  const a = useAction();
  const onDone = (j) => { if (j.job_type === "probe") setProbe(j.result); invalidate("/admin"); q.reload(); };
  const start = async (path, body, kind, opts = {}) => {
    setProbe(null);
    const r = await a.run(path, { body, ...opts }, `${kind} queued`);
    if (r) setJob(r.data.job_id);
  };
  return html`<div class="page">
    <${Loadable} q=${q} sk=${html`<${SkBlock} h=${140} />`}>${(s) => {
      const st = s.status_detail;
      const upload = s.connector_type === "file_upload";
      const runnable = s.connector_implemented && !upload && !["needs_authorization", "manual_import_required"].includes(s.status) && s.enabled;
      const why = !s.enabled ? "Source is disabled" : s.status === "needs_authorization" ? "Needs an authorization before data can be fetched"
        : s.status === "manual_import_required" && !upload ? "Manual import required: no connector can fetch this source" : "";
      return html`
        <${TitleRow} crumb=${html`<${Link} to="/admin/sources" cls="link">Data sources</${Link}> / ${s.source_group}`} title=${s.display_name}
          sub=${html`<span class="idc">${s.source_id}</span> · ${s.connector_type} · ${s.expected_grain || "—"} · ${s.expected_cadence || "—"} · ${s.release_class.replace(/_/g, " ").toLowerCase()}`}
          right=${html`<${Guarded} perm="source.ingest" busy=${a.busy} onClick=${() => start(`/admin/sources/${s.source_id}/test`, undefined, "Connection test")}>Test</${Guarded}>
            ${!upload && html`<${Guarded} perm="source.ingest" cls="btn pri" busy=${a.busy} disabled=${!runnable} reason=${why}
              onClick=${() => start(`/admin/sources/${s.source_id}/ingest`, { params: {} }, "Ingest", { idem: newKey() })}><${Icon} n="play" s=${12} />Run ingest</${Guarded}>`}`} />
        <${Notices} items=${[s.status === "needs_authorization" ? "This connector is not connected: it needs a data-use authorization. No substitute data is generated." : null,
          s.status === "manual_import_required" && !upload ? "This source needs a manual file import and a reviewed connector; it is listed so coverage gaps stay visible." : null,
          st.is_synthetic_only ? "Every published snapshot of this source is a SYNTHETIC fixture." : null, s.config?.note].filter(Boolean)} />
        <${Kpis} items=${[
          { label: "State", value: label(st.state), sub: st.last_attempt ? `last attempt ${label(st.last_attempt.status).toLowerCase()} ${ago(st.last_attempt.created_at)}` : "never attempted" },
          { label: "Last success", value: st.last_success_at ? ago(st.last_success_at) : "never", sub: st.last_success_at ? ftime(st.last_success_at) : "—" },
          { label: "Coverage", value: st.coverage_end ? fdate(st.coverage_end) : "—", sub: st.coverage_start ? `from ${fdate(st.coverage_start)}` : "no published data" },
          { label: "Rows in latest release", value: fmt.int(st.rows_latest), sub: `${st.published_snapshots} published of ${st.snapshots} snapshots` },
          { label: "30-day success", value: st.success_rate_30d == null ? "—" : fmt.pct(st.success_rate_30d), sub: `${st.attempts_30d} finished attempts` },
        ]} />
        <div class="grid" style="margin-top:var(--gap)">
          ${(job || upload || s.source_id === "sivep_gripe") && html`<div class="s12"><${Panel} title=${job ? "Job" : "Import"}>
            ${upload && html`<div class="row wrap" style="margin-bottom:${job ? 16 : 0}px">
              <label class="sr" for="up-file">CSV file</label><input id="up-file" type="file" accept=".csv,.txt" onChange=${(e) => setFile(e.target.files[0])} />
              <${Guarded} perm="source.ingest" cls="btn" busy=${a.busy} disabled=${!file} reason="Choose a CSV file first" onClick=${() => {
                const fd = new FormData(); fd.append("file", file); start(`/admin/sources/${s.source_id}/upload`, undefined, "Upload", { form: fd }); }}>Upload and import</${Guarded}>
              ${s.source_id === "sivep_gripe" && html`<span class="spacer"></span><label for="syn-rows" class="small muted">Synthetic case rows</label>
                <input id="syn-rows" type="number" min="1000" max="50000000" step="100000" style="width:140px" value=${rows} onInput=${(e) => setRows(+e.target.value)} />
                <${Guarded} perm="source.ingest" cls="btn" busy=${a.busy} onClick=${() => start("/admin/synthetic/sivep", { rows }, "Synthetic SIVEP generation")}>
                  Generate SYNTHETIC load test</${Guarded}>`}
            </div>`}
            ${job && html`<${JobProgress} jobId=${job} onDone=${onDone} />`}
            ${probe && html`<div class="well" style="margin-top:12px"><b>${probe.ok ? "Probe succeeded" : "Probe did not succeed"}</b>
              <dl class="kv" style="margin-top:8px">${Object.entries(probe).filter(([k]) => k !== "source_id").map(([k, v]) => html`<dt>${label(k)}</dt><dd class=${Array.isArray(v) ? "mono" : ""}>${Array.isArray(v) ? v.join(", ") : String(v)}</dd>`)}</dl></div>`}
          </${Panel}></div>`}
          <div class="s12"><${Panel} title="Snapshots" note="Immutable Bronze/Silver vintages with their quality report; click a row for gates" flush>
            <${Snapshots} snaps=${s.snapshots} reload=${q.reload} /></${Panel}></div>
          <div class="s8"><${Panel} title="Preview"><${Preview} sid=${s.source_id} /></${Panel}></div>
          <div class="s4"><${Panel} title="Provenance and terms"><dl class="kv">
              <dt>Owner</dt><dd>${s.owner_id || "—"}</dd><dt>License</dt><dd>${s.license_ref || "verify at source"}</dd>
              <dt>Provenance</dt><dd>${s.provenance_url ? html`<a class="link" href=${s.provenance_url} target="_blank" rel="noopener">${s.provenance_url.replace(/^https:\/\//, "")}</a>` : "—"}</dd>
              <dt>Sensitivity</dt><dd>${s.pii_class}</dd><dt>Approved hosts</dt><dd class="mono">${(s.approved_hostnames || []).join(", ") || "none"}</dd>
              <dt>Publication lag</dt><dd>${s.publication_lag_days ?? "—"} days</dd><dt>Freshness</dt><dd>${s.freshness_days ?? "—"} days</dd>
              <dt>Secret reference</dt><dd class="mono">${s.auth_secret_ref || "none"}</dd></dl></${Panel}></div>
          <div class="s7"><${Panel} title="Configuration" note="Safe fields only; every save is a new audited revision"><${ConfigForm} s=${s} reload=${q.reload} /></${Panel}></div>
          <div class="s5"><${Panel} title="Schema" note="Silver contract and natural keys">${Object.keys(s.schema).length ? Object.entries(s.schema).map(([t, cols]) => html`
              <div style="margin-bottom:12px"><b style="font-weight:500">${t}</b> <span class="small muted">key ${(s.keys[t] || []).join(" + ")}</span>
                <div class="mono muted" style="margin-top:4px;line-height:20px;font-size:13px">${Object.entries(cols).map(([c, ty]) => `${c}: ${ty}`).join(" · ")}</div></div>`)
              : html`<span class="muted small">No Silver table published yet.</span>`}</${Panel}></div>
          <div class="s12"><${Panel} title="Recent jobs" note=${`${s.jobs_total} total`} right=${html`<${Link} to=${`/admin/ingestions?source_id=${s.source_id}`} cls="link">All runs</${Link}>`} flush>
            ${s.jobs.length ? html`<table class="tbl cards"><tbody>${s.jobs.slice(0, 8).map((j) => html`<tr>
              <td><div class="two"><${Link} to=${`/admin/ingestions/${j.job_id}`}>${label(j.job_type)}</${Link}><div class="s"><span class="idc">${j.job_id.slice(0, 16)}…</span> · attempt ${j.attempt_no}</div></div></td>
              <td><${Status} s=${j.status} /></td><td class="num sub">${j.elapsed_s != null ? `${fmt.d1(j.elapsed_s)} s` : "—"}</td><td class="num hide-sm">${ftime(j.created_at)}</td></tr>`)}</tbody></table>`
              : html`<div style="padding:0 20px"><${Empty}>No jobs yet.</${Empty}></div>`}</${Panel}></div>
        </div>`;
    }}</${Loadable}>
  </div>`;
}
