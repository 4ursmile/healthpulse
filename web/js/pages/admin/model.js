// Model version — question: "Has this model cleared every release gate and both sign-offs, and where does it serve?"
import { html, useState } from "../../../vendor/preact-htm.js";
import { useApi, navigate } from "../../api.js";
import { useSession } from "../../app.js";
import { Panel, TitleRow, Loadable, Status, Link, Id, Drawer, Confirm, Notices, Empty, ftime, fdate, fmt, label, SkBlock, Icon } from "../../ui.js";
import { useAction, Guarded, Checks, Field, MetricGrid } from "./_shared.js";

function Review({ m, open, onClose, done }) {
  const { me, can } = useSession();
  const kind = can("model.review.clinical") ? "clinical" : can("model.review.platform") ? "platform" : null;
  const [f, set] = useState({ decision: "approve", notes: "" });
  const a = useAction();
  const own = me?.user_id === m.created_by;
  return html`<${Drawer} open=${open} title=${kind ? `${kind === "clinical" ? "Epidemiology" : "Platform"} review` : "Review"} onClose=${onClose}
    footer=${html`<button class="btn" onClick=${onClose}>Cancel</button><button class="btn pri" disabled=${!kind || own || a.busy || f.notes.trim().length < 5}
      title=${!kind ? "Your role cannot sign off models" : own ? "The model's author cannot review it" : ""}
      onClick=${async () => { const r = await a.run(`/admin/registry/${m.model_id}/${m.version}/review`, { body: { kind, ...f } }, "Review recorded"); if (r) { done(); onClose(); } }}>${a.busy ? "Saving…" : "Record review"}</button>`}>
    <${Notices} kind="info" items=${[kind === "clinical" ? "Check the target definition, case signal, uncertainty and the model card's limitations against the intended use."
      : kind === "platform" ? "Check the artifact checksum, runtime, access and the inference contract." : "Only an epidemiology reviewer or a platform admin can sign off.", own ? "You registered this version, so you cannot review it." : null].filter(Boolean)} />
    <div class="field"><span style="font-weight:500">Decision</span><div class="seg" role="radiogroup" aria-label="Decision">${[["approve", "Approve"], ["request_changes", "Request changes"], ["reject", "Reject"]].map(([v, l]) => html`
      <button role="radio" aria-checked=${f.decision === v} class=${f.decision === v ? "on" : ""} onClick=${() => set({ ...f, decision: v })}>${l}</button>`)}</div></div>
    <${Field} label="Notes (required)" id="rv-n" hint="Recorded with your name in the model's review timeline and the audit log"><textarea id="rv-n" rows="5" value=${f.notes} onInput=${(e) => set({ ...f, notes: e.target.value })}></textarea></${Field}>
  </${Drawer}>`;
}

export default function Model({ params }) {
  const q = useApi(`/admin/registry/${params.model}/${params.version}`);
  const [rev, setRev] = useState(false);
  const [dep, setDep] = useState(null);
  const a = useAction();
  return html`<div class="page">
    <${Loadable} q=${q} sk=${html`<${SkBlock} h=${240} />`}>${(m) => {
      const c = m.model_card_json || {};
      const hard = m.gates.filter((g) => g.hard && !g.passed);
      const active = m.deployments.filter((d) => d.status === "active");
      const hs = (m.horizons_json || []).map(Number);
      return html`
        <${TitleRow} crumb=${html`<${Link} to="/admin/registry" cls="link">Model registry</${Link}>`} title=${`${m.model_id}@${m.version}`}
          sub=${html`<${Status} s=${m.approval_state} /> ${c.model} · ${c.target_definition} · registered ${ftime(m.created_at)} by ${m.created_by} · ${hard.length ? `${hard.length} hard gate${hard.length > 1 ? "s" : ""} open` : "all hard gates pass"}`}
          right=${html`<button class="btn" disabled=${["REJECTED", "RETIRED"].includes(m.approval_state)} onClick=${() => setRev(true)}>Review</button>
            <${Guarded} perm="model.deploy" disabled=${["REJECTED", "RETIRED", "DRAFT"].includes(m.approval_state)} reason=${`A ${label(m.approval_state)} model cannot be deployed`} onClick=${() => setDep("shadow")}>Deploy shadow</${Guarded}>
            <${Guarded} perm="model.deploy" cls="btn pri" disabled=${hard.length > 0 || active.some((d) => d.serving_alias === "champion")}
              reason=${active.some((d) => d.serving_alias === "champion") ? "Already the active champion" : `Release gates open: ${hard.map((g) => label(g.gate)).join(", ")}`} onClick=${() => setDep("champion")}>Promote to champion</${Guarded}>`} />
        <${Notices} items=${[m.pit_status !== "PIT_VERIFIED" ? "Limited evidence: backtests used publication-lag approximations, so prospective release-accurate performance is not claimed." : null,
          m.approval_state === "REJECTED" ? "Rejected versions stay in the registry for audit and cannot be deployed." : null].filter(Boolean)} />
        <div class="grid">
          <div class="s7 stack">
            <${Panel} title="Release gates" note="Hard gates block champion deployment; soft gates are reviewed"><${Checks} items=${m.gates.map((g) => ({ ...g, check: g.gate }))} /></${Panel}>
            <${Panel} title="Test metrics" note=${`Held-out issue weeks · ${c.validation?.method || ""}`} flush>
              <${MetricGrid} rows=${[{ key: m.recipe_id, byH: m.metrics_summary_json }]} horizons=${hs} metric="mae" />
              <${MetricGrid} rows=${[{ key: m.recipe_id, byH: m.metrics_summary_json }]} horizons=${hs} metric="coverage80" best="max" />
              <${MetricGrid} rows=${[{ key: m.recipe_id, byH: m.metrics_summary_json }]} horizons=${hs} metric="wape" />
              <div class="small muted" style="padding:8px 20px 0"><${Link} to=${`/admin/training-runs/${m.run_id}`} cls="link">Full evaluation report</${Link}></div></${Panel}>
            <${Panel} title="Reviews" note="Timeline of sign-offs">${m.reviews.length ? m.reviews.map((r) => html`<div class="check">
              <span class=${r.decision === "approve" ? "ok" : r.decision === "reject" ? "no" : "soft"}><${Icon} n=${r.decision === "approve" ? "check" : r.decision === "reject" ? "x" : "warn"} s=${14} /></span>
              <div style="flex:1"><b style="font-weight:500">${label(r.kind)} · ${label(r.decision)}</b> <span class="small muted">${r.reviewer_id} (${label(r.reviewer_role)}) · ${ftime(r.ts)}</span><div>${r.notes}</div></div></div>`)
              : html`<${Empty}>No review yet. Needs an epidemiology sign-off and a platform sign-off.</${Empty}>`}</${Panel}>
          </div>
          <div class="s5 stack">
            <${Panel} title="Model card"><dl class="kv">
              <dt>Target</dt><dd>${c.target_definition} (${c.signal_type?.replace(/_/g, " ")})</dd><dt>Disease definition</dt><dd>${c.disease}</dd>
              <dt>Geography</dt><dd>${c.populations_represented} · <span class="mono">${(c.geography || []).join(", ")}</span></dd>
              <dt>Training window</dt><dd>${c.training_window ? `${fdate(c.training_window.start)} – ${fdate(c.training_window.end)}` : "—"}</dd>
              <dt>Label cutoff</dt><dd>${ftime(c.label_cutoff)}</dd><dt>Sources</dt><dd>${(c.data_sources || []).join(", ")}</dd><dt>Feature set</dt><dd class="mono">${c.feature_set}</dd>
              <dt>Evidence</dt><dd><${Status} s=${c.pit_status} /></dd><dt>Uncertainty</dt><dd>${c.uncertainty}</dd>
              <dt>Validated</dt><dd>${ftime(c.validation_date)}</dd><dt>Contact</dt><dd>${c.responsible_contact}</dd></dl>
              <h3 style="margin-top:16px">Usage restrictions</h3><ul style="margin:6px 0 0;padding-left:18px">${(c.usage_restrictions || []).map((u) => html`<li>${u}</li>`)}</ul>
              ${(c.limitations || []).length > 0 && html`<h3 style="margin-top:12px">Limitations</h3><ul style="margin:6px 0 0;padding-left:18px">${c.limitations.map((u) => html`<li>${u}</li>`)}</ul>`}</${Panel}>
            <${Panel} title="Artifact"><dl class="kv"><dt>Family</dt><dd>${m.model_family}</dd><dt>Run</dt><dd><${Link} to=${`/admin/training-runs/${m.run_id}`} cls="link idc">${m.run_id.slice(0, 18)}…</${Link}></dd>
              <dt>Dataset</dt><dd><${Link} to=${`/admin/datasets/${m.dataset_id}`} cls="link idc">${m.dataset_id.slice(0, 18)}…</${Link}></dd><dt>URI</dt><dd class="mono">${m.artifact_uri}</dd><dt>SHA-256</dt><dd><${Id} id=${m.hash} n=${16} /></dd></dl></${Panel}>
            <${Panel} title="Deployments" note=${active.length ? `${active.length} active` : "not serving"} flush>${m.deployments.length ? html`<table class="tbl dense"><tbody>${m.deployments.map((d) => html`<tr>
              <td><div class="two"><${Status} s=${d.serving_alias} /> <${Status} s=${d.status} /><div class="s">${ftime(d.deployed_at)} by ${d.approved_by}</div></div></td><td class="num idc">${d.deployment_id.slice(0, 14)}…</td></tr>`)}</tbody></table>`
              : html`<div style="padding:0 20px"><${Empty}>Never deployed.</${Empty}></div>`}</${Panel}>
          </div>
        </div>
        <${Review} m=${m} open=${rev} onClose=${() => setRev(false)} done=${q.reload} />
        <${Confirm} open=${!!dep} busy=${a.busy} title=${dep === "champion" ? "Promote to champion?" : "Deploy as shadow?"}
          body=${dep === "champion" ? "Public forecasts will come from this model after its inference job finishes. The current champion becomes the rollback target; earlier forecasts stay unchanged."
            : "Shadow deployments produce internal forecasts for comparison and never replace public outputs."}
          confirm=${dep === "champion" ? "Promote" : "Deploy shadow"} reason="Reason (recorded in the audit log)" onCancel=${() => setDep(null)}
          onConfirm=${async (reason) => { const r = await a.run("/admin/deployments", { body: { model_id: m.model_id, version: m.version, alias: dep, reason } }, "Deployment created; inference queued");
            setDep(null); if (r) navigate(`/admin/deployments?job=${r.data.inference_job_id}`); }} />`;
    }}</${Loadable}>
  </div>`;
}
