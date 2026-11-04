// Control center — question: "Is anything in the data-to-forecast pipeline blocked, stale or waiting on me?"
import { html } from "../../../vendor/preact-htm.js";
import { useApi, navigate } from "../../api.js";
import { Panel, TitleRow, Kpis, Loadable, SkBlock, Status, Link, Id, Empty, ago, fmt, fdate, label, Icon, Notices } from "../../ui.js";

const STATE_ORDER = ["quarantined", "stale", "needs_authorization", "manual_import_required", "available", "healthy", "disabled"];

export default function Control() {
  const q = useApi("/admin/overview");
  return html`<div class="page">
    <${Loadable} q=${q} sk=${html`<${SkBlock} h=${120} />`}>${(d) => {
      const srcs = d.sources.filter((s) => s.source_group !== "fixture");
      const bad = srcs.filter((s) => ["quarantined", "stale"].includes(s.status_detail.state));
      const finding = [
        d.failed_jobs.length ? `${d.failed_jobs.length} failed job${d.failed_jobs.length > 1 ? "s" : ""}` : null,
        bad.length ? `${bad.length} source${bad.length > 1 ? "s" : ""} stale or quarantined (${bad.map((s) => s.display_name).join(", ")})` : null,
        d.pending_reviews.length ? `${d.pending_reviews.length} model${d.pending_reviews.length > 1 ? "s" : ""} awaiting review` : null,
        d.stale_forecasts.length ? `${d.stale_forecasts.length} champion forecast${d.stale_forecasts.length > 1 ? "s" : ""} older than 8 days` : null,
      ].filter(Boolean);
      const healthy = srcs.filter((s) => s.status_detail.state === "healthy").length;
      const hbAge = d.worker.last_heartbeat ? (Date.now() - new Date(d.worker.last_heartbeat)) / 1000 : null;
      return html`
        <${TitleRow} title="Control center" sub=${finding.length ? html`Needs attention: <b>${finding.join(" · ")}</b>.` : "Nothing is blocked: no failed jobs, no stale sources, no reviews waiting."}
          right=${html`<span class="stamp"><span class="dot"></span>${d.workspace} · ${d.environment} · updated ${ago(q.at)}</span>
            <button class="btn" onClick=${q.reload}><${Icon} n="refresh" s=${14} />Refresh</button>`} />
        <${Kpis} items=${[
          { label: "Sources healthy", value: `${healthy}`, unit: `of ${srcs.length}`, sub: `${srcs.filter((s) => ["needs_authorization", "manual_import_required"].includes(s.status_detail.state)).length} need authorization or a manual file` },
          { label: "Queue", value: fmt.int(d.worker.queue_depth), unit: "queued", sub: `${d.active_jobs.length} active · worker ${d.worker.last_heartbeat ? `heartbeat ${ago(d.worker.last_heartbeat)}` : "no heartbeat yet"}` },
          { label: "Reviews waiting", value: fmt.int(d.pending_reviews.length), sub: html`<${Link} to="/admin/registry" cls="link">Open registry</${Link}>` },
          { label: "Audit events", value: fmt.int(d.audit_events_7d), unit: "7 days", sub: html`<${Link} to="/admin/security" cls="link">Audit log</${Link}>` },
        ]} />
        ${hbAge !== null && hbAge > 600 && d.active_jobs.some((j) => j.status === "running") ? html`<div style="margin-top:16px"><${Notices} items=${["Running jobs have not sent a heartbeat for over 10 minutes; the worker may be down."]} /></div>` : null}
        <div class="grid" style="margin-top:var(--gap)">
          <div class="s7"><${Panel} title="Source health" note="State, latest successful release and coverage" right=${html`<${Link} to="/admin/sources" cls="link">All sources</${Link}>`} flush>
            <div class="tablewrap"><table class="tbl cards"><thead><tr><th>Source</th><th>State</th><th class="num">Covers to</th><th class="num">Last success</th></tr></thead>
              <tbody>${[...srcs].sort((a, b) => STATE_ORDER.indexOf(a.status_detail.state) - STATE_ORDER.indexOf(b.status_detail.state)).map((s) => html`
                <tr class="click" onClick=${() => navigate(`/admin/sources/${s.source_id}`)}>
                  <td><div class="two"><${Link} to=${`/admin/sources/${s.source_id}`}>${s.display_name}</${Link}><div class="s">${s.source_group} · ${s.release_class.replace(/_/g, " ").toLowerCase()}</div></div></td>
                  <td><${Status} s=${s.status_detail.state} />${s.status_detail.is_synthetic_only ? html` <span class="pill syn">Synthetic</span>` : ""}</td>
                  <td class="num">${s.status_detail.coverage_end ? fdate(s.status_detail.coverage_end) : "—"}</td>
                  <td class="num sub">${s.status_detail.last_success_at ? ago(s.status_detail.last_success_at) : "never"}</td></tr>`)}</tbody></table></div>
          </${Panel}></div>
          <div class="s5 stack">
            <${Panel} title="Active jobs" right=${html`<${Link} to="/admin/ingestions" cls="link">All runs</${Link}>`} flush>
              ${d.active_jobs.length ? html`<table class="tbl dense"><tbody>${d.active_jobs.slice(0, 6).map((j) => html`<tr>
                <td><div class="two">${label(j.job_type)}<div class="s">${j.target_id || "—"} · ${ago(j.created_at)}</div></div></td>
                <td><${Status} s=${j.status} /></td><td class="num">${fmt.pct(j.progress || 0)}</td>
                <td class="num"><${Link} to=${`/admin/ingestions/${j.job_id}`} cls="link">View</${Link}></td></tr>`)}</tbody></table>`
              : html`<div style="padding:0 20px"><${Empty}>Nothing queued or running.</${Empty}></div>`}
            </${Panel}>
            <${Panel} title="Failed jobs" note="Latest 10" flush>
              ${d.failed_jobs.length ? html`<table class="tbl dense"><tbody>${d.failed_jobs.map((j) => html`<tr>
                <td><div class="two">${label(j.job_type)} · ${j.target_id || "—"}<div class="s" title=${j.error}>${(j.error || "").slice(0, 70) || label(j.status)}</div></div></td>
                <td class="num"><div class="two"><${Status} s=${j.status} /><div class="s">${ago(j.finished_at)}</div></div></td>
                <td class="num"><${Link} to=${`/admin/ingestions/${j.job_id}`} cls="link">Logs</${Link}></td></tr>`)}</tbody></table>`
              : html`<div style="padding:0 20px"><${Empty}>No failed jobs.</${Empty}></div>`}
            </${Panel}>
          </div>
          <div class="s7"><${Panel} title="Serving" note="Active deployments and their latest forecast" right=${html`<${Link} to="/admin/deployments" cls="link">Deployments</${Link}>`} flush>
            ${d.deployments.length ? html`<div class="tablewrap"><table class="tbl cards"><thead><tr><th>Model</th><th>Alias</th><th>Latest forecast</th><th class="num">Issued</th></tr></thead>
              <tbody>${d.deployments.map((x) => html`<tr>
                <td><div class="two"><${Link} to=${`/admin/registry/${x.model_id}/${x.version}`}><span class="idc">${x.model_id}@${x.version}</span></${Link}><div class="s">${label(x.disease_id)}</div></div></td>
                <td><${Status} s=${x.serving_alias} /></td>
                <td class="sub">${x.latest_forecast ? html`<${Id} id=${x.latest_forecast.forecast_id} n=${18} />` : "none yet"}</td>
                <td class="num">${x.latest_forecast ? html`<div class="two">${ago(x.latest_forecast.created_at)}<div class="s">anchor ${fdate(x.latest_forecast.anchor_week)}</div></div>` : "—"}</td></tr>`)}</tbody></table></div>`
            : html`<div style="padding:0 20px"><${Empty} action=${html`<${Link} to="/admin/registry" cls="btn sm">Registry</${Link}>`}>No model is deployed; public screens show observed data only.</${Empty}></div>`}
          </${Panel}></div>
          <div class="s5"><${Panel} title="Reviews and datasets" flush>
            ${d.pending_reviews.length ? html`<table class="tbl dense"><tbody>${d.pending_reviews.map((m) => html`<tr>
              <td><${Link} to=${`/admin/registry/${m.model_id}/${m.version}`}><span class="idc">${m.model_id}@${m.version}</span></${Link}></td><td class="num"><${Status} s=${m.approval_state} /></td></tr>`)}</tbody></table>`
              : html`<div style="padding:0 20px 8px" class="small muted">No model is waiting for review.</div>`}
            <table class="tbl dense"><thead><tr><th>Latest training datasets</th><th class="num">Rows</th></tr></thead><tbody>${d.datasets.length ? d.datasets.map((x) => html`<tr>
              <td><div class="two"><${Link} to=${`/admin/datasets/${x.dataset_id}`}>${x.name}</${Link}><div class="s"><${Status} s=${x.pit_status} /> · ${ago(x.created_at)}</div></div></td><td class="num">${fmt.int(x.row_count)}</td></tr>`)
              : html`<tr><td colspan="2" class="muted">No dataset built yet.</td></tr>`}</tbody></table>
          </${Panel}></div>
        </div>`;
    }}</${Loadable}>
  </div>`;
}
