// Simulation audit — question: "Does each saved scenario reproduce bit-for-bit from its pinned inputs?"
import { html, useState } from "../../../vendor/preact-htm.js";
import { useApi, api } from "../../api.js";
import { Panel, TitleRow, Loadable, Status, Link, Id, Empty, JobProgress, ftime, fmt, label, SkRows } from "../../ui.js";
import { useAction, Guarded } from "./_shared.js";

export default function SimAudit() {
  const q = useApi("/admin/simulation-audit");
  const [rep, setRep] = useState(null); // {job_id, run_id, reproduces, expected_sha256, actual}
  const a = useAction();
  const reproduce = async (r) => { const x = await a.run(`/admin/simulation-audit/${r.run_id}/reproduce`, {}, "Reproduction queued"); if (x) setRep({ ...x.data, name: r.name }); };
  const done = async () => { try { const r = await api(`/simulations/${rep.run_id}`); setRep((s) => ({ ...s, actual: r.data.result_sha256, status: r.data.status })); } catch (e) {} q.reload(); };
  return html`<div class="page">
    <${TitleRow} title="Simulation audit" sub="Every run pins its scenario version, forecast, model, random seed and solver; reproducing it must return the same result checksum." />
    ${rep && html`<${Panel} title=${`Reproducing ${rep.name || rep.reproduces}`} right=${html`<button class="btn sm" onClick=${() => setRep(null)}>Dismiss</button>`}>
      <${JobProgress} jobId=${rep.job_id} onDone=${done} compact />
      ${rep.actual !== undefined && html`<div class=${"notice " + (rep.actual === rep.expected_sha256 ? "info" : "bad")} style="margin-top:12px"><div>
        <b>${rep.actual === rep.expected_sha256 ? "Identical result" : "Result differs"}</b><br/>expected <span class="idc">${(rep.expected_sha256 || "").slice(0, 24)}…</span><br/>reproduced <span class="idc">${(rep.actual || "none").slice(0, 24)}…</span></div></div>`}
    </${Panel}>`}
    <div style=${rep ? "margin-top:var(--gap)" : ""}><${Panel} title="Runs" note=${q.data ? `${q.data.length} total` : null} flush>
      <${Loadable} q=${q} sk=${html`<${SkRows} n=${5} cols=${6} />`}>${(rows) => rows.length ? html`<div class="tablewrap"><table class="tbl cards">
        <thead><tr><th>Scenario</th><th>Status</th><th>Pinned forecast · model</th><th class="num">Seed · draws</th><th>Spec hash</th><th>Result SHA-256</th><th class="num">Finished</th><th class="num">Action</th></tr></thead>
        <tbody>${rows.map((r) => { const p = r.provenance_json || {}; const twin = rows.find((x) => x !== r && x.spec_hash === r.spec_hash && x.forecast_id === r.forecast_id && x.result_sha256 && r.result_sha256 && x.status === "completed");
          return html`<tr>
          <td><div class="two"><${Link} to=${`/simulation/runs/${r.run_id}`}>${r.name || r.scenario_id}</${Link}><div class="s idc">${r.run_id.slice(0, 16)}… · v${r.scenario_version}</div></div></td>
          <td><${Status} s=${r.status} />${p.hypothetical ? html` <span class="pill sc">Hypothetical</span>` : ""}${p.synthetic ? html` <span class="pill syn">Synthetic</span>` : ""}</td>
          <td class="sub"><div class="two"><span class="idc">${(r.forecast_id || "—").slice(0, 22)}</span><div class="s">${p.model_version || label(r.mode)}</div></div></td>
          <td class="num hide-sm">${p.random_seed ?? "—"} · ${fmt.int(p.draws)}</td><td class="hide-sm idc">${r.spec_hash.slice(0, 10)}…</td>
          <td class="hide-sm">${r.result_sha256 ? html`<span class="idc">${r.result_sha256.slice(0, 10)}…</span>${twin ? html` <span class=${"pill " + (twin.result_sha256 === r.result_sha256 ? "ok" : "bad")} title="Another run of the same spec and forecast">${twin.result_sha256 === r.result_sha256 ? "Matches twin" : "Differs from twin"}</span>` : ""}` : "—"}</td>
          <td class="num hide-sm">${ftime(r.finished_at)}</td>
          <td class="num">${r.status === "completed" ? html`<${Guarded} perm="scenario.run" cls="btn sm" busy=${a.busy} onClick=${() => reproduce(r)}>Reproduce</${Guarded}>` : ""}</td></tr>`; })}</tbody></table></div>`
        : html`<div style="padding:0 20px"><${Empty} action=${html`<${Link} to="/simulation" cls="btn sm">Simulation studio</${Link}>`}>No simulation run yet.</${Empty}></div>`}</${Loadable}>
    </${Panel}></div>
  </div>`;
}
