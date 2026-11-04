// Shared helpers for admin screens: async actions with real API calls, guarded buttons, metric tables, check lists.
import { html, useState } from "../../../vendor/preact-htm.js";
import { api, newKey } from "../../api.js";
import { useSession } from "../../app.js";
import { Icon, fmt, toast, Status, label } from "../../ui.js";

// Run an API mutation with busy state; errors become a toast and are returned (never swallowed silently).
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const run = async (path, opts = {}, okMsg) => {
    setBusy(true); setError(null);
    try {
      const r = await api(path, { method: "POST", ...opts });
      if (okMsg) toast(okMsg);
      (r.warnings || []).forEach((w) => toast(w));
      return r;
    } catch (e) {
      setError(e);
      toast(e.status === 403 ? `Not allowed for your role: ${e.message}` : e.message, "bad");
      return null;
    } finally { setBusy(false); }
  };
  return { busy, error, run };
}

export { newKey };

// A button that is disabled with a stated reason when the role lacks the permission.
export function Guarded({ perm, reason, onClick, busy, cls = "btn", children, disabled, title }) {
  const { can } = useSession();
  const allowed = !perm || can(perm);
  const why = !allowed ? `Your role cannot ${perm.replace(/\./g, " ")}` : disabled ? reason : title;
  return html`<button class=${cls} disabled=${!allowed || disabled || busy} title=${why || ""} aria-label=${typeof children === "string" ? children : undefined}
    onClick=${onClick}>${busy ? "Working…" : children}</button>`;
}

// Pass/fail check list (validations, gates)
export const Checks = ({ items, nameKey = "check" }) => html`<div>${items.map((c) => html`<div class="check">
  <span class=${c.passed ? "ok" : c.hard === false || c.severity === "soft" ? "soft" : "no"}><${Icon} n=${c.passed ? "check" : c.hard === false || c.severity === "soft" ? "warn" : "x"} s=${14} /></span>
  <div style="flex:1;min-width:0"><b style="font-weight:500">${label(c[nameKey] || c.gate || c.check)}</b>${c.hard === false || c.severity === "soft" ? html` <span class="small muted">soft</span>` : ""}
    <div class="muted">${c.detail}</div></div></div>`)}</div>`;

// Metrics table: rows = recipes (or slices), cols = horizons; one metric at a time.
export const METRICS = [["wis", "WIS", fmt.d1], ["mae", "MAE", fmt.d1], ["wape", "WAPE", (v) => fmt.pct(v, 1)], ["bias", "Bias", (v) => fmt.signed(v, fmt.d1)],
  ["coverage80", "80% coverage", (v) => fmt.pct(v, 0)], ["interval_width", "Interval width", fmt.d1], ["poisson_deviance", "Poisson deviance", fmt.d1],
  ["outbreak_recall", "Outbreak recall", (v) => fmt.pct(v, 0)], ["outbreak_precision", "Outbreak precision", (v) => fmt.pct(v, 0)]];

export function MetricSelect({ value, onChange, keys }) {
  return html`<select aria-label="Metric" value=${value} onChange=${(e) => onChange(e.target.value)}>
    ${METRICS.filter(([k]) => !keys || keys.includes(k)).map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select>`;
}
export const metricFmt = (k) => (METRICS.find((m) => m[0] === k) || [0, 0, fmt.d1])[2];
export const metricLabel = (k) => (METRICS.find((m) => m[0] === k) || [0, k])[1];

export function MetricGrid({ rows, horizons, metric, best = "min", rowLabel = (r) => label(r.key) }) {
  const f = metricFmt(metric);
  const bestOf = (h) => { const vals = rows.map((r) => r.byH[h]?.[metric]).filter((v) => v != null); return vals.length ? (best === "min" ? Math.min(...vals) : Math.max(...vals)) : null; };
  return html`<div class="tablewrap"><table class="tbl"><thead><tr><th>${rows.title || "Recipe"}</th>${horizons.map((h) => html`<th class="num">+${h} wk</th>`)}<th class="num">Rows</th></tr></thead>
    <tbody>${rows.map((r) => html`<tr><td>${rowLabel(r)}</td>${horizons.map((h) => { const v = r.byH[h]?.[metric]; const b = bestOf(h);
      return html`<td class="num" style=${v != null && v === b && metric !== "bias" ? "font-weight:600" : ""}>${f(v)}</td>`; })}
      <td class="num muted">${fmt.int(r.byH[horizons[0]]?.n)}</td></tr>`)}</tbody></table></div>`;
}

export const yesno = (b) => html`<${Status} s=${b ? "pass" : "failed"} />`;

export function Field({ label: l, hint, id, children }) {
  return html`<div class="field"><label for=${id}>${l}</label>${children}${hint && html`<div class="hint">${hint}</div>`}</div>`;
}

export const pct = (v) => fmt.pct(v, 0);
