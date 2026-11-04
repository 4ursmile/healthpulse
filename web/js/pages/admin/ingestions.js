// Ingestion runs — question: "Which ingestion attempts failed or are still running, and can I retry them?"
import { html } from "../../../vendor/preact-htm.js";
import { useApi, setQuery, navigate } from "../../api.js";
import { Panel, TitleRow, Loadable, Status, Link, Empty, ago, ftime, fmt, label, Seg, SkRows } from "../../ui.js";
import { useAction, Guarded } from "./_shared.js";

const TYPES = [["ingest,import_file,probe,synthetic_sivep", "All"], ["ingest", "Ingest"], ["import_file", "File import"], ["probe", "Test"], ["synthetic_sivep", "Synthetic"]];

const canRetry = (j) => ["failed", "timed_out", "canceled", "quarantined"].includes(j.status) && j.job_type !== "import_file";

export default function Ingestions({ q }) {
  const status = q.get("status") || "";
  const source = q.get("source_id") || "";
  const type = q.get("type") || TYPES[0][0];
  const offset = +(q.get("offset") || 0);
  const limit = 25;
  const src = useApi("/admin/sources");
  const r = useApi(`/admin/ingestions?job_type=${type}&limit=${limit}&offset=${offset}${status ? `&status=${status}` : ""}${source ? `&source_id=${source}` : ""}`);
  const a = useAction();
  const total = r.meta?.total ?? 0;
  return html`<div class="page">
    <${TitleRow} title="Ingestion runs" sub=${r.data ? `${fmt.int(total)} attempt${total === 1 ? "" : "s"} match. A retry is a new attempt linked to the original; audit records are never reset.` : "Loading…"} />
    <div class="filters">
      <${Seg} label="Job type" value=${type} onChange=${(v) => setQuery({ type: v, offset: null })} options=${TYPES} />
      <select aria-label="Status" value=${status} onChange=${(e) => setQuery({ status: e.target.value, offset: null })}>
        <option value="">Any status</option>${["queued", "running", "succeeded", "succeeded_with_warnings", "quarantined", "failed", "canceled", "timed_out"].map((s) => html`<option value=${s}>${label(s)}</option>`)}</select>
      <select aria-label="Source" value=${source} onChange=${(e) => setQuery({ source_id: e.target.value, offset: null })}>
        <option value="">Any source</option>${(src.data || []).map((s) => html`<option value=${s.source_id}>${s.display_name}</option>`)}</select>
    </div>
    <${Panel} title="Attempts" note=${total ? `${offset + 1}–${Math.min(offset + limit, total)} of ${fmt.int(total)}` : null} flush>
      <${Loadable} q=${r} sk=${html`<${SkRows} n=${6} cols=${5} />`}>${(rows) => rows.length ? html`<div class="tablewrap"><table class="tbl cards">
        <thead><tr><th>Job</th><th>Source</th><th>Status</th><th class="num">Stages</th><th class="num">Duration</th><th class="num">Created</th>${rows.some(canRetry) ? html`<th class="num">Action</th>` : ""}</tr></thead>
        <tbody>${rows.map((j) => html`<tr class="click" onClick=${() => navigate(`/admin/ingestions/${j.job_id}`)}>
          <td><div class="two"><${Link} to=${`/admin/ingestions/${j.job_id}`}>${label(j.job_type)}</${Link}><div class="s"><span class="idc">${j.job_id.slice(0, 18)}…</span>${j.attempt_no > 1 ? ` · attempt ${j.attempt_no}` : ""}</div></div></td>
          <td class="muted">${j.target_id || "—"}</td><td><${Status} s=${j.status} /></td>
          <td class="num sub muted">${["queued", "running"].includes(j.status) ? j.stage : (j.stages?.length ? `${j.stages.length} stages` : "—")}</td><td class="num hide-sm">${j.elapsed_s != null ? `${fmt.d1(j.elapsed_s)} s` : "—"}</td>
          <td class="num hide-sm">${ftime(j.created_at)}</td>
          ${rows.some(canRetry) && html`<td class="num hide-sm">${canRetry(j)
            ? html`<${Guarded} perm="source.ingest" cls="btn sm" busy=${a.busy} onClick=${async (e) => { e.stopPropagation(); const x = await a.run(`/admin/ingestions/${j.job_id}/retry`, {}, "Retry queued"); if (x) navigate(`/admin/ingestions/${x.data.job_id}`); }}>Retry</${Guarded}>` : ""}</td>`}
        </tr>`)}</tbody></table></div>
        <div class="row" style="padding:12px 20px 4px"><span class="small muted">${ago(r.at)}</span><span class="spacer"></span>
          <button class="btn sm" disabled=${offset === 0} onClick=${() => setQuery({ offset: Math.max(0, offset - limit) || null })}>Previous</button>
          <button class="btn sm" disabled=${offset + limit >= total} onClick=${() => setQuery({ offset: offset + limit })}>Next</button></div>`
        : html`<div style="padding:0 20px"><${Empty}>No attempt matches these filters.</${Empty}></div>`}</${Loadable}>
    </${Panel}>
  </div>`;
}
