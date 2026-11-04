// Deployments — question: "Which model serves each disease, what did it last publish, and can I promote, re-run or roll back?"
import { html, useState } from "../../../vendor/preact-htm.js";
import { useApi, setQuery } from "../../api.js";
import { Panel, TitleRow, Loadable, Status, Link, Id, Empty, Confirm, JobProgress, Notices, ago, ftime, fdate, label, SkRows } from "../../ui.js";
import { useAction, Guarded } from "./_shared.js";

export default function Deployments({ q }) {
  const d = useApi("/admin/deployments");
  const job = q.get("job");
  const [act, setAct] = useState(null);
  const [week, setWeek] = useState("");
  const a = useAction();
  const go = async (path, body, msg) => { const r = await a.run(path, { body }, msg); setAct(null); if (r?.data?.inference_job_id || r?.data?.job_id) setQuery({ job: r.data.inference_job_id || r.data.job_id }); d.reload(); };
  return html`<div class="page">
    <${Loadable} q=${d} sk=${html`<${SkRows} n=${5} />`}>${(rows) => {
      const groups = [...new Set(rows.map((r) => `${r.disease_id}|${r.target_signal}`))];
      const active = rows.filter((r) => r.status === "active");
      return html`<${TitleRow} title="Deployments" sub=${html`<b>${active.filter((r) => r.serving_alias === "champion").length}</b> champion and ${active.filter((r) => r.serving_alias === "shadow").length} shadow deployment${active.length === 1 ? "" : "s"} active. Every change is a new record; forecasts already published never change.`} />
        ${job && html`<${Panel} title="Inference job" right=${html`<button class="btn sm" onClick=${() => setQuery({ job: null })}>Dismiss</button>`}><${JobProgress} jobId=${job} onDone=${d.reload} /></${Panel}>`}
        <div class="stack" style=${job ? "margin-top:var(--gap)" : ""}>${groups.length ? groups.map((g) => {
          const [dis, tgt] = g.split("|");
          const gr = rows.filter((r) => r.disease_id === dis && r.target_signal === tgt);
          const champ = gr.find((r) => r.serving_alias === "champion" && r.status === "active");
          return html`<${Panel} title=${`${label(dis)} · ${label(tgt)}`} note=${champ ? `champion ${champ.model_id}@${champ.version} since ${ftime(champ.deployed_at)}` : "no active champion"}
            right=${html`<${Guarded} perm="model.deploy" cls="btn sm danger" disabled=${!champ?.previous_deployment_id} reason="No previous champion to restore" onClick=${() => setAct({ kind: "rollback", dis, tgt })}>Roll back</${Guarded}>`} flush>
            <div class="tablewrap"><table class="tbl cards"><thead><tr><th>Model</th><th>Alias</th><th>Status</th><th>Latest forecast</th><th class="num">Deployed</th><th class="num">Actions</th></tr></thead>
              <tbody>${gr.map((r) => html`<tr>
                <td><div class="two"><${Link} to=${`/admin/registry/${r.model_id}/${r.version}`} cls="idc">${r.model_id}@${r.version}</${Link}><div class="s idc">${r.deployment_id.slice(0, 16)}…</div></div></td>
                <td><${Status} s=${r.serving_alias} /></td><td><${Status} s=${r.status} /></td>
                <td class="sub">${r.forecasts.length ? html`<div class="two"><${Id} id=${r.forecasts[0].forecast_id} n=${20} /><div class="s">anchor ${fdate(r.forecasts[0].anchor_week)} · ${r.forecasts[0].is_public ? "public" : "internal"} · ${ago(r.forecasts[0].created_at)}</div></div>` : html`<span class="faint">none</span>`}</td>
                <td class="num hide-sm">${ftime(r.deployed_at)}<div class="small muted">${r.approved_by}</div></td>
                <td class="num">${r.status === "active" ? html`<div class="row" style="justify-content:flex-end;gap:6px">
                  ${r.serving_alias === "shadow" && html`<${Guarded} perm="model.deploy" cls="btn sm" onClick=${() => setAct({ kind: "promote", r })}>Promote</${Guarded}>`}
                  <${Guarded} perm="model.infer" cls="btn sm" onClick=${() => setAct({ kind: "infer", r })}>Run inference</${Guarded}></div>` : ""}</td></tr>`)}</tbody></table></div>
          </${Panel}>`; }) : html`<${Empty} action=${html`<${Link} to="/admin/registry" cls="btn sm">Registry</${Link}>`}>No deployment yet.</${Empty}>`}</div>`;
    }}</${Loadable}>
    <${Confirm} open=${act?.kind === "rollback"} busy=${a.busy} danger title="Roll back the champion?" confirm="Roll back" reason="Reason for the rollback (required)"
      body="The previous champion is restored as a new deployment and inference runs with it. Forecasts published by the current champion stay as they are."
      onCancel=${() => setAct(null)} onConfirm=${(reason) => go("/admin/deployments/rollback", { disease_id: act.dis, target: act.tgt, reason }, "Rolled back; inference queued")} />
    <${Confirm} open=${act?.kind === "promote"} busy=${a.busy} title="Promote shadow to champion?" confirm="Promote" reason="Reason (recorded in the audit log)"
      body="Release gates are re-checked on the server; the current champion becomes the rollback target."
      onCancel=${() => setAct(null)} onConfirm=${(reason) => go(`/admin/deployments/${act.r.deployment_id}/promote`, { reason }, "Promoted; inference queued")} />
    <${Confirm} open=${act?.kind === "infer"} busy=${a.busy} title="Run batch inference?" confirm="Queue inference"
      body=${html`<p style="margin:0 0 12px">Leave the week empty for a live forecast anchored to the latest released week. A past issue week makes an internal hindcast (PIT unverified, never public).</p>
        <label class="small muted" for="inf-w">Hindcast issue week (optional)</label><input id="inf-w" type="date" style="display:block;margin-top:6px" value=${week} onInput=${(e) => setWeek(e.target.value)} />`}
      onCancel=${() => setAct(null)} onConfirm=${() => go(`/admin/deployments/${act.r.deployment_id}/infer`, week ? { issue_week: week } : {}, "Inference queued")} />
  </div>`;
}
