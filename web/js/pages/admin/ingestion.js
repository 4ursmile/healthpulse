// One ingestion attempt — question: "What did this attempt do, stage by stage, and what did it publish?"
import { html } from "../../../vendor/preact-htm.js";
import { useApi, navigate } from "../../api.js";
import { Panel, TitleRow, Loadable, Status, Link, Id, Empty, JobProgress, ftime, fmt, label, SkBlock } from "../../ui.js";
import { useAction, Guarded } from "./_shared.js";

export default function Ingestion({ params }) {
  const q = useApi(`/admin/ingestions/${params.id}`);
  const a = useAction();
  return html`<div class="page">
    <${Loadable} q=${q} sk=${html`<${SkBlock} h=${200} />`}>${(j) => {
      const active = ["queued", "running"].includes(j.status);
      return html`
        <${TitleRow} crumb=${html`<${Link} to="/admin/ingestions" cls="link">Ingestion runs</${Link}>`} title=${`${label(j.job_type)} · ${j.target_id || ""}`}
          sub=${html`<${Status} s=${j.status} /> attempt ${j.attempt_no}${j.retry_of ? html` · retry of <${Link} to=${`/admin/ingestions/${j.retry_of}`} cls="link idc">${j.retry_of.slice(0, 16)}…</${Link}>` : ""} · created ${ftime(j.created_at)} by ${j.created_by || "system"}`}
          right=${html`${j.target_type === "source" && html`<${Link} to=${`/admin/sources/${j.target_id}`} cls="btn">Source</${Link}>`}
            ${["failed", "timed_out", "canceled", "quarantined"].includes(j.status) && j.job_type !== "import_file" && html`<${Guarded} perm="source.ingest" cls="btn pri" busy=${a.busy}
              onClick=${async () => { const x = await a.run(`/admin/ingestions/${j.job_id}/retry`, {}, "Retry queued"); if (x) navigate(`/admin/ingestions/${x.data.job_id}`); }}>Retry as new attempt</${Guarded}>`}`} />
        <div class="grid">
          <div class="s8"><${Panel} title="Stages and logs" note="Recorded by the worker; nothing here is estimated">
            ${active ? html`<${JobProgress} jobId=${j.job_id} onDone=${q.reload} />` : html`
              <table class="tbl dense" style="margin:0 -20px"><thead><tr><th>Stage</th><th>Status</th><th class="num">Counts</th><th class="num">Started</th></tr></thead>
                <tbody>${(j.stages || []).map((s) => html`<tr><td>${s.name}</td><td><${Status} s=${s.status === "done" ? "succeeded" : s.status} /></td>
                  <td class="num muted small">${Object.entries(s).filter(([k]) => !["name", "status", "started_at", "finished_at"].includes(k)).map(([k, v]) => `${k} ${Array.isArray(v) ? v.length : typeof v === "number" ? fmt.int(v) : v}`).join(" · ") || "—"}</td>
                  <td class="num">${(s.started_at || "").slice(11, 19)}</td></tr>`)}</tbody></table>
              ${j.error && html`<div class="notice bad" style="margin-top:12px">${j.error}</div>`}
              <div class="logs" style="margin-top:12px">${j.logs.length ? j.logs.map((l) => html`<div class=${l.level === "warning" ? "w" : l.level === "error" ? "e" : ""}>${l.ts.slice(11, 19)}  ${l.message}</div>`) : "No log lines."}</div>`}
          </${Panel}></div>
          <div class="s4 stack">
            <${Panel} title="Published snapshots" flush>${j.snapshots.length ? html`<table class="tbl dense"><tbody>${j.snapshots.map((s) => html`<tr>
              <td><div class="two">${s.silver_table}<div class="s"><span class="idc">${s.snapshot_id.slice(0, 22)}…</span></div></div></td>
              <td class="num"><div class="two"><${Status} s=${s.dq_status} /><div class="s">${fmt.int(s.row_count)} rows${s.published ? "" : " · not published"}</div></div></td></tr>`)}</tbody></table>`
              : html`<div style="padding:0 20px"><${Empty}>${active ? "Snapshots appear when the publish stage finishes." : "This attempt published no snapshot."}</${Empty}></div>`}</${Panel}>
            <${Panel} title="Attempt"><dl class="kv"><dt>Job</dt><dd><${Id} id=${j.job_id} n=${16} /></dd><dt>Duration</dt><dd>${j.elapsed_s != null ? `${fmt.d1(j.elapsed_s)} s` : "—"}</dd>
              <dt>Worker</dt><dd class="mono">${j.worker_id || "—"}</dd><dt>Timeout</dt><dd>${fmt.int(j.timeout_s)} s</dd>
              <dt>Parameters</dt><dd class="mono">${JSON.stringify(j.payload?.params || {})}</dd>
              <dt>Result</dt><dd class="mono" style="font-size:12px">${j.result ? JSON.stringify(j.result.dq || j.result).slice(0, 400) : "—"}</dd></dl>
              ${j.retries.length > 0 && html`<h3 style="margin-top:16px">Retries</h3>${j.retries.map((r) => html`<div class="row" style="margin-top:6px"><${Link} to=${`/admin/ingestions/${r.job_id}`} cls="link idc">${r.job_id.slice(0, 18)}…</${Link}><${Status} s=${r.status} /><span class="small muted">attempt ${r.attempt_no}</span></div>`)}`}
            </${Panel}>
          </div>
        </div>`;
    }}</${Loadable}>
  </div>`;
}
