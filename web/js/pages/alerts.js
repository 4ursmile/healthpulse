// Alert review — question: "Which rule-triggered drafts need an epidemiologist's decision?"
import { html, useState } from "../../vendor/preact-htm.js";
import { api, useApi, linkWith } from "../api.js";
import { Panel, TitleRow, Notices, Status, Sem, Loadable, SkRows, Empty, Link, Confirm, Seg, toast, fmt, fdate, ago, Id } from "../ui.js";
import { useSession } from "../app.js";
import { scopeOf } from "../scope.js";

export default function Alerts({ q }) {
  const { can } = useSession();
  const [state, setState] = useState("draft");
  const sc = scopeOf(q);
  const uf = sc.country === "br" && sc.region ? sc.region : null;
  const al = useApi(`/alerts?${state === "all" ? "" : `state=${state}`}${uf ? `&uf=${uf}` : ""}`);
  const allq = useApi(`/alerts${uf ? `?uf=${uf}` : ""}`);
  const counts = (allq.data || []).reduce((m, a) => ({ ...m, [a.state]: (m[a.state] || 0) + 1 }), {});
  const [act, setAct] = useState(null);
  const [busy, setBusy] = useState(false);
  const reviewer = can("alert.review");
  const doReview = async (note) => {
    setBusy(true);
    try { await api(`/alerts/${act.a.alert_id}/review`, { method: "POST", body: { decision: act.d, note } }); toast("Review recorded"); setAct(null); al.reload(); }
    catch (e) { toast(e.message, "bad"); }
    setBusy(false);
  };
  return html`<div class="page">
    <${TitleRow} title="Alert review" sub="Draft alerts are raised when a published forecast median exceeds a municipality's provisional threshold. Approving records the review; HealthPulse sends no warning."
      right=${html`<${Seg} label="State" value=${state} onChange=${setState} options=${[["draft", "Drafts"], ["held", "Held"], ["approved", "Approved"], ["rejected", "Rejected"], ["all", "All"]]} />`} />
    ${!reviewer && html`<${Notices} kind="info" items=${["Only epidemiology reviewers and platform admins can decide on alerts. You can read the evidence."]} />`}
    <div class="grid"><div class="s8">
    <${Panel} title=${state === "all" ? "All alerts" : `${state.charAt(0).toUpperCase() + state.slice(1)} alerts`} note=${html`<${Sem} k="FORECAST" /> evidence from the forecast that triggered each draft`} flush>
      <${Loadable} q=${al} sk=${html`<${SkRows} n=${4} />`}>${(rows) => rows.length ? html`<div class="tablewrap"><table class="tbl cards">
        <thead><tr><th>Municipality</th><th>Target week</th><th class="num">Forecast median</th><th class="num">Threshold</th><th>Forecast</th><th>State</th><th></th></tr></thead>
        <tbody>${rows.map((a) => html`<tr>
          <td><div class="two"><${Link} to=${linkWith(`/regions/${a.municipality_id}`, { region: a.municipality_id, disease: a.disease_id })} cls="link">${a.name || a.municipality_id}</${Link}>
            <div class="s">${a.disease_id} · +${a.evidence.horizon} wk · raised ${ago(a.created_at)}</div></div></td>
          <td class="hide-sm">${fdate(a.target_week)}</td>
          <td class="num"><div class="two">${fmt.int(a.evidence.p50)}<div class="s">P10–P90 ${fmt.int(a.evidence.p10)}–${fmt.int(a.evidence.p90)}</div></div></td>
          <td class="num hide-sm">${fmt.int(a.evidence.threshold)}</td><td class="hide-sm"><${Id} id=${a.forecast_id} n=${14} /></td>
          <td class="sub"><${Status} s=${a.state} />${a.review_note && html` <span class="small muted">${a.review_note}</span>`}</td>
          <td class="num">${reviewer && ["draft", "held"].includes(a.state) && html`<div class="row" style="justify-content:flex-end;gap:6px">
            <button class="btn sm" onClick=${() => setAct({ a, d: "approve" })}>Approve</button><button class="btn sm" onClick=${() => setAct({ a, d: "hold" })}>Hold</button>
            <button class="btn sm danger" onClick=${() => setAct({ a, d: "reject" })}>Reject</button></div>`}</td></tr>`)}</tbody></table></div>
        <div class="small muted" style="padding:10px 20px">Rule: ${rows[0].evidence.rule}. The threshold must be co-defined with local epidemiologists before operational use.</div>`
        : html`<div style="padding:0 20px 8px"><${Empty}>No ${state === "all" ? "" : state} alerts. The latest published forecast stayed below every municipality's threshold.</${Empty}></div>`}</${Loadable}>
    </${Panel}></div>
    <div class="s4 stack"><${Panel} title="Queue by state" flush><table class="tbl dense"><tbody>
      ${["draft", "held", "approved", "rejected"].map((k) => html`<tr class="click" onClick=${() => setState(k)}><td><${Status} s=${k} /></td><td class="num">${fmt.int(counts[k] || 0)}</td></tr>`)}
    </tbody></table></${Panel}>
    <${Panel} title="How drafts are raised"><ol class="small" style="margin:0;padding-left:18px;line-height:20px">
      <li>A reviewed model is promoted to champion and publishes a forecast.</li>
      <li>Each municipality-horizon whose forecast median exceeds that municipality's training 90th percentile becomes a draft.</li>
      <li>An epidemiology reviewer approves, holds or rejects with a written note; the decision is audited.</li>
      <li>No public warning or dispatch is sent from HealthPulse.</li></ol></${Panel}></div></div>
    <${Confirm} open=${!!act} busy=${busy} title=${act ? `${act.d.charAt(0).toUpperCase() + act.d.slice(1)} alert for ${act.a.name || act.a.municipality_id}` : ""}
      body="Your decision and note are written to the audit log." reason="Review note (required)" confirm=${act?.d === "reject" ? "Reject" : "Record decision"} danger=${act?.d === "reject"}
      onCancel=${() => setAct(null)} onConfirm=${doReview} />
  </div>`;
}
