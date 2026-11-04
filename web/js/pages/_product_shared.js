// Helpers shared by the product screens (not part of the core kit).
import { html, useState } from "../../vendor/preact-htm.js";
import { api, useApi } from "../api.js";
import { Icon, Id, Status, fmt, short, toast } from "../ui.js";
import { stateParam } from "../scope.js";

export { DiseaseSelect } from "./overview.js";

// Pilot municipalities that have a series (names + ids) for selects.
export function useRegions() {
  const sp = stateParam(new URLSearchParams(location.search));
  const r = useApi(`/metadata/regions?scope=pilot&limit=${sp ? 1000 : 200}${sp}`);
  return r.data || [];
}

export function RegionSelect({ value, onChange, label = "Municipality" }) {
  const rows = useRegions();
  return html`<select aria-label=${label} value=${value || ""} onChange=${(e) => onChange(e.target.value)}>
    ${!value && html`<option value="">Choose a municipality</option>`}
    ${rows.map((r) => html`<option value=${r.municipality_id}>${r.name} · ${r.state_abbr}${r.has_disease_data ? "" : " (no series)"}</option>`)}
  </select>`;
}

// Provenance chain (signature element): source vintages -> feature set -> dataset -> model -> forecast [-> scenario]
export function Chain({ links }) {
  return html`<div class="chain" aria-label="Provenance chain">${links.filter(Boolean).map((l) => html`
    <div class="lk"><span class="k">${l.k}</span><span class="v" title=${l.title || l.v}>${l.v}</span></div>`)}</div>`;
}

export function chainFromLineage(lin, extra = []) {
  if (!lin) return [];
  const by = (kind) => lin.nodes.filter((n) => n.kind === kind);
  const vint = by("source_vintage");
  return [
    vint.length && { k: `${vint.length} source vintage${vint.length > 1 ? "s" : ""}`, v: [...new Set(vint.map((v) => v.label))].join(", "), title: vint.map((v) => v.id).join("\n") },
    by("feature_set")[0] && { k: "Feature set", v: by("feature_set")[0].id },
    by("training_dataset")[0] && { k: `Dataset · ${by("training_dataset")[0].pit_status === "PIT_VERIFIED" ? "PIT verified" : "PIT unverified"}`, v: short(by("training_dataset")[0].id, 16), title: by("training_dataset")[0].id },
    by("model")[0] && { k: "Model", v: by("model")[0].id },
    by("forecast")[0] && { k: `Forecast${by("forecast")[0].public ? " · public" : ""}`, v: short(by("forecast")[0].id, 20), title: by("forecast")[0].id },
    ...extra,
  ];
}

// Grounded analyst panel: shows answer, citations, caveats and constrained recommendation objects.
export function Analyst({ context, suggestions = [] }) {
  const [qtext, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const ask = async (question) => {
    if (!question.trim()) return;
    setBusy(true);
    try { const r = await api("/assistant/explain", { method: "POST", body: { question, ...context } }); setRes(r.data); }
    catch (e) { toast(e.message, "bad"); }
    setBusy(false);
  };
  return html`<div class="stack" style="gap:12px">
    <div class="row wrap">${suggestions.map((s) => html`<button class="btn sm" disabled=${busy} onClick=${() => { setQ(s); ask(s); }}>${s}</button>`)}</div>
    <form class="row" onSubmit=${(e) => { e.preventDefault(); ask(qtext); }}>
      <input type="text" style="flex:1" aria-label="Question for the analyst" placeholder="Ask about this forecast or run" value=${qtext} onInput=${(e) => setQ(e.target.value)} maxlength="2000" />
      <button class="btn" disabled=${busy || !qtext.trim()}>${busy ? "Reading…" : "Ask"}</button>
    </form>
    ${res && html`<div class="well stack" style="gap:10px">
      <div style="line-height:22px">${res.answer}</div>
      ${res.draft_scenario && html`<div class="small muted">Draft scenario prepared — open the Simulation studio to review it; nothing has been computed.</div>`}
      ${res.citations?.length > 0 && html`<div class="row wrap small"><span class="muted">Evidence</span>${res.citations.map((c) => { const [k, v] = Object.entries(c)[0] || []; return v ? html`<span class="row" style="gap:4px"><span class="muted">${k.replace(/_/g, " ")}</span><${Id} id=${v} n=${14} /></span>` : null; })}</div>`}
      ${res.caveats?.length > 0 && html`<ul class="small muted" style="margin:0;padding-left:18px">${res.caveats.map((c) => html`<li>${c}</li>`)}</ul>`}
      ${(res.recommendations || []).map((r) => html`<div style="border-top:1px solid var(--rule);padding-top:10px" class="small">
        <div class="row"><b style="font-weight:500">${r.suggested_action}</b><span class="spacer"></span>${r.requires_review && html`<span class="pill warn">Requires review</span>`}</div>
        <div class="muted" style="margin-top:4px">${r.rationale}. Estimated effect: ${r.estimated_effect?.value == null ? "not estimated" : `${fmt.int(r.estimated_effect.value)} ${r.estimated_effect.metric.replace(/_/g, " ")} (${r.estimated_effect.status.replace(/_/g, " ")})`}.</div></div>`)}
      <div class="small faint">${res.confidence?.replace(/_/g, " ")} · tools: ${(res.tools || []).join(", ") || "none"} · rule-based, tool-gated analyst</div>
    </div>`}
  </div>`;
}

export const SemNote = ({ children }) => html`<span class="small muted">${children}</span>`;
export { Icon, Status };
