// Shared UI: icons, formatting, semantics pills, panels, KPI strip, tables, states, tabs, drawer, confirm, toasts, job progress.
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.js";
import { navigate, useJob, api } from "./api.js";

// ---------------------------------------------------------------- icons (outline, 1.5px)
const P = {
  overview: "M3 13h4v-2H3v2zm0 4h4v-2H3v2zm0-8h4V7H3v2zm6 8h12V7H9v10z",
  pin: "M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21zm0-9a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z",
  map: "M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2zm0 0v14m6-12v14",
  disease: "M12 3v4m0 10v4M3 12h4m10 0h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  people: "M16 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-5A3.5 3.5 0 0 0 4 18.5V20M10 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zm10 9v-1.5a3.5 3.5 0 0 0-2.5-3.35M15 4.15a3.5 3.5 0 0 1 0 6.7",
  facility: "M4 21V7l8-4 8 4v14M9 21v-5h6v5M12 8v4m-2-2h4",
  sim: "M4 19h16M7 16V9m5 7V5m5 11v-4",
  alert: "M12 4 3 20h18L12 4zm0 6v4m0 3v.5",
  model: "M4 18 9 12l4 3 7-9M20 6v5m0-5h-5",
  data: "M4 6c0-1.1 3.6-2 8-2s8 .9 8 2-3.6 2-8 2-8-.9-8-2zm0 0v12c0 1.1 3.6 2 8 2s8-.9 8-2V6M4 12c0 1.1 3.6 2 8 2s8-.9 8-2",
  control: "M4 6h10M18 6h2M4 12h4m4 0h8M4 18h12m4 0h0M14 4v4M8 10v4m8 2v4",
  source: "M12 3v12m0 0-4-4m4 4 4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2",
  runs: "M5 4v16l14-8L5 4z",
  catalog: "M5 4h11l3 3v13H5V4zm3 6h8m-8 4h8m-8 4h5",
  feature: "M4 7h16M4 12h10M4 17h6m9-4 2 2-5 5h-2v-2l5-5z",
  dataset: "M4 5h16v4H4zM4 11h16v4H4zM4 17h16v2H4z",
  exp: "M9 3h6m-5 0v6L4 19a1 1 0 0 0 .9 1.5h14.2A1 1 0 0 0 20 19l-6-10V3",
  registry: "M6 3h9l3 3v15H6V3zm3 8 2 2 4-4",
  deploy: "M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3zm0 0v18M4 7.5l8 4.5 8-4.5",
  monitor: "M3 12h4l3-7 4 14 3-7h4",
  audit: "M12 3 5 6v6c0 4.4 3 8 7 9 4-1 7-4.6 7-9V6l-7-3zm-3 9 2 2 4-4",
  settings: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zm8.2 3a8 8 0 0 0-.1-1.3l2-1.6-2-3.4-2.4 1a8 8 0 0 0-2.2-1.3L15 2h-4l-.4 2.4A8 8 0 0 0 8.3 5.7l-2.4-1-2 3.4 2 1.6a8 8 0 0 0 0 2.6l-2 1.6 2 3.4 2.4-1a8 8 0 0 0 2.3 1.3L11 22h4l.4-2.4a8 8 0 0 0 2.2-1.3l2.4 1 2-3.4-2-1.6c.1-.4.2-.9.2-1.3z",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zm9 3-4.5-4.5",
  menu: "M4 6h16M4 12h16M4 18h16",
  close: "M6 6l12 12M18 6 6 18",
  check: "M5 12l4 4 10-10",
  x: "M7 7l10 10M17 7 7 17",
  warn: "M12 4 3 20h18L12 4zm0 6v4m0 3v.5",
  info: "M12 8v.5M12 11v5M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z",
  refresh: "M20 11a8 8 0 0 0-14.9-3M4 4v4h4m-4 5a8 8 0 0 0 14.9 3M20 20v-4h-4",
  copy: "M8 8h11v11H8zM5 16V5h11",
  chevron: "M9 6l6 6-6 6",
  back: "M15 6l-6 6 6 6",
  download: "M12 4v11m0 0-4-4m4 4 4-4M5 20h14",
  play: "M7 5v14l11-7L7 5z",
  stop: "M7 7h10v10H7z",
  plus: "M12 5v14M5 12h14",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm-7 9a7 7 0 0 1 14 0",
  moon: "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z",
  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2m0 18v2M4.2 4.2l1.4 1.4m12.8 12.8 1.4 1.4M1 12h2m18 0h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  lineage: "M6 5a2 2 0 1 0 0 .1M6 7v10m0 0a2 2 0 1 0 0 .1M18 10a2 2 0 1 0 0 .1M6 12h6a6 6 0 0 0 6-2",
};
export const Icon = ({ n, s = 16, cls = "" }) => html`<svg class=${"ic " + cls} width=${s} height=${s} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d=${P[n] || P.info}/></svg>`;

// ---------------------------------------------------------------- formatting
const lang = "en-US";
const NF0 = new Intl.NumberFormat(lang, { maximumFractionDigits: 0 });
const NF1 = new Intl.NumberFormat(lang, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const NF2 = new Intl.NumberFormat(lang, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const MINUS = "−";
export const fmt = {
  int: (v) => (v === null || v === undefined || Number.isNaN(v) ? "—" : NF0.format(Math.round(v))),
  d1: (v) => (v === null || v === undefined || Number.isNaN(v) ? "—" : NF1.format(v)),
  d2: (v) => (v === null || v === undefined || Number.isNaN(v) ? "—" : NF2.format(v)),
  pct: (v, d = 0) => (v === null || v === undefined || Number.isNaN(v) ? "—" : new Intl.NumberFormat(lang, { style: "percent", minimumFractionDigits: d, maximumFractionDigits: d }).format(v)),
  brl: (v) => (v === null || v === undefined ? "—" : "BRL " + NF0.format(v)),
  signed: (v, f = (x) => NF0.format(x)) => (v === null || v === undefined || Number.isNaN(v) ? "—" : (v > 0 ? "+" : v < 0 ? MINUS : "") + f(Math.abs(v))),
  compact: (v) => (v === null || v === undefined ? "—" : new Intl.NumberFormat(lang, { notation: "compact", maximumFractionDigits: 1 }).format(v)),
  bytes: (v) => (v == null ? "—" : v > 1e9 ? NF1.format(v / 1e9) + " GB" : v > 1e6 ? NF1.format(v / 1e6) + " MB" : v > 1e3 ? NF0.format(v / 1e3) + " kB" : v + " B"),
};
export function fdate(s, opts = { day: "numeric", month: "short", year: "numeric" }) {
  if (!s) return "—";
  const d = new Date(s.length <= 10 ? s + "T00:00:00Z" : s);
  return d.toLocaleDateString(lang, { ...opts, timeZone: "UTC" });
}
export function ftime(s) {
  if (!s) return "—";
  const d = new Date(s);
  return d.toLocaleString(lang, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
export function ago(s) {
  if (!s) return "never";
  const sec = (Date.now() - new Date(s).getTime()) / 1000;
  if (sec < 90) return "just now";
  if (sec < 3600) return `${Math.round(sec / 60)} min ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)} h ago`;
  const d = Math.round(sec / 86400);
  return d === 1 ? "yesterday" : `${d} days ago`;
}
export const weekLabel = (s) => (s ? `week of ${fdate(s, { day: "numeric", month: "short" })}` : "—");
export const short = (id, n = 10) => (id && id.length > n + 2 ? id.slice(0, n) + "…" : id || "—");

// ---------------------------------------------------------------- semantics + status pills
const SEM = { OBSERVED: ["obs", "Observed"], FORECAST: ["fc", "Forecast"], SCENARIO: ["sc", "Scenario"], SYNTHETIC: ["syn", "Synthetic"],
  HYPOTHETICAL: ["sc", "Hypothetical"], STALE: ["warn", "Data stale"], ASSUMPTION: ["sc", "Planner assumption"], PROXY: ["obs", "Proxy"],
  ESTIMATE: ["fc", "Model estimate"], NOWCAST: ["fc", "Nowcast"] };
export const Sem = ({ k, title }) => { const [c, l] = SEM[k] || ["obs", k]; return html`<span class=${"pill " + c} title=${title || ""}>${l}</span>`; };

const STATUS = {
  healthy: "ok", succeeded: "ok", passed: "ok", pass: "ok", ok: "ok", ready: "ok", completed: "ok", approved: "ok", APPROVED: "ok", DEPLOYED: "ok",
  active: "ok", PIT_VERIFIED: "ok", pit_safe: "ok", published: "ok", reviewed: "ok", released_by_override: "warn",
  succeeded_with_warnings: "warn", passed_with_warnings: "warn", stale: "warn", PIT_UNVERIFIED: "warn", draft: "", queued: "", running: "fc",
  training: "fc", evaluating: "fc", packaging: "fc", preparing: "fc", validated: "", CANDIDATE: "", REVIEWED: "fc", unchecked: "", held: "warn",
  failed: "bad", quarantined: "bad", timed_out: "bad", rejected: "bad", REJECTED: "bad", leak_blocked: "bad", invalid: "bad", canceled: "",
  needs_authorization: "warn", manual_import_required: "", source_missing: "warn", blocked: "warn", disabled: "", available: "",
  superseded: "", rolled_back: "warn", insufficient_data: "warn", shadow: "fc", champion: "ok",
};
const LABEL = { needs_authorization: "Needs authorization", manual_import_required: "Manual import", succeeded_with_warnings: "Succeeded with warnings",
  passed_with_warnings: "Passed with warnings", PIT_VERIFIED: "PIT verified", PIT_UNVERIFIED: "PIT unverified", leak_blocked: "Leak blocked",
  source_missing: "Source missing", pit_safe: "PIT safe", timed_out: "Timed out", insufficient_data: "Insufficient data", rolled_back: "Rolled back",
  released_by_override: "Released by override" };
export const label = (s) => LABEL[s] || (s ? (s === s.toUpperCase() ? s.charAt(0) + s.slice(1).toLowerCase() : s.charAt(0).toUpperCase() + s.slice(1)).replace(/_/g, " ") : "—");
export const Status = ({ s, title }) => html`<span class=${"pill " + (STATUS[s] ?? "")} title=${title || ""}>${label(s)}</span>`;

// ---------------------------------------------------------------- layout primitives
export const Panel = ({ title, note, right, children, flush, cls = "", id }) => html`
  <section class=${"panel " + cls} id=${id} aria-label=${typeof title === "string" ? title : undefined}>
    ${(title || right) && html`<div class="ph"><div class="grow"><h2>${title}</h2>${note && html`<div class="note">${note}</div>`}</div>${right}</div>`}
    <div class=${"pb" + (flush ? " flush" : "")}>${children}</div>
  </section>`;

export const TitleRow = ({ title, sub, right, crumb }) => html`
  <div class="titlerow">
    <div class="tt">
      ${crumb && html`<div class="small muted" style="margin-bottom:4px">${crumb}</div>`}
      <h1>${title}</h1>
      ${sub && html`<p class="subline">${sub}</p>`}
    </div>
    ${right && html`<div class="actions">${right}</div>`}
  </div>`;

export function Kpis({ items }) {
  return html`<div class="kpis" role="list">${items.map((k) => html`
    <div class="kpi" role="listitem">
      <div class="l">${k.label}${k.sem && html` <${Sem} k=${k.sem} />`}</div>
      <div class=${"v num" + (k.lead ? " lead" : "")}>${k.value}${k.unit && html`<small>${k.unit}</small>`}</div>
      ${k.sub && html`<div class="d">${k.sub}</div>`}
    </div>`)}</div>`;
}

export const Notices = ({ items = [], kind = "" }) => (items.length ? html`<div class="notices">${items.map((t) => html`
  <div class=${"notice " + kind} role="status"><${Icon} n=${kind === "info" ? "info" : "warn"} /><div>${t}</div></div>`)}</div>` : null);

export const Empty = ({ children, action, icon = "info" }) => html`<div class="empty" role="status"><span><${Icon} n=${icon} s=${15} /><span>${children}</span></span>${action}</div>`;

export const ErrorBox = ({ error, retry, last }) => html`<div class="errbox" role="alert"><${Icon} n="warn" />
  <span>${error?.status === 403 ? "Your role cannot view this." : error?.status === 401 ? "Sign in to view this." : `Couldn't load: ${error?.message || "error"}.`}
  ${last ? ` Last good data ${ago(last)}.` : ""}</span>${retry && html`<button class="link" onClick=${retry}>Retry</button>`}</div>`;

export const SkRows = ({ n = 4, cols = 4 }) => html`<div aria-busy="true">${Array.from({ length: n }, () => html`<div class="skrow">${Array.from({ length: cols }, (_, i) => html`<span class="sk" style=${`height:12px;width:${i === 0 ? 30 : 12}%`}></span>`)}</div>`)}</div>`;
export const SkBlock = ({ h = 200 }) => html`<div class="sk" style=${`display:block;height:${h}px;width:100%`} aria-busy="true"></div>`;

// state wrapper for a data hook: loading skeleton on first load, error in place, children when ready
export function Loadable({ q, children, sk = html`<${SkRows} />`, empty }) {
  if (q.error && !q.data) return html`<${ErrorBox} error=${q.error} retry=${q.reload} />`;
  if (!q.data) return sk;
  if (empty && empty(q.data)) return empty === true ? null : null;
  return html`${q.error && html`<${ErrorBox} error=${q.error} retry=${q.reload} last=${q.at} />`}${children(q.data, q.meta, q.warnings)}`;
}

export function Tabs({ tabs, value, onChange }) {
  return html`<div class="tabs" role="tablist">${tabs.map(([k, l]) => html`
    <button role="tab" aria-selected=${value === k} class=${value === k ? "on" : ""} onClick=${() => onChange(k)}>${l}</button>`)}</div>`;
}

export const Seg = ({ options, value, onChange, label: aria }) => html`<div class="seg" role="radiogroup" aria-label=${aria}>${options.map(([v, l]) => html`
  <button role="radio" aria-checked=${String(value) === String(v)} class=${String(value) === String(v) ? "on" : ""} onClick=${() => onChange(v)}>${l}</button>`)}</div>`;

export const Link = ({ to, children, cls = "", title, tabIndex }) => html`<a href=${to} class=${cls} title=${title} tabIndex=${tabIndex} onClick=${(e) => {
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.button) return; e.preventDefault(); navigate(to); }}>${children}</a>`;

export function Copy({ text }) {
  const [ok, set] = useState(false);
  return html`<button class="btn ghost sm" title="Copy" aria-label="Copy id" onClick=${(e) => { e.stopPropagation(); navigator.clipboard?.writeText(text); set(true); setTimeout(() => set(false), 1200); }}>
    <${Icon} n=${ok ? "check" : "copy"} s=${13} /></button>`;
}
export const Id = ({ id, n = 12 }) => html`<span class="row" style="gap:2px;display:inline-flex"><span class="idc" title=${id}>${short(id, n)}</span>${id && html`<${Copy} text=${id} />`}</span>`;

export function Drawer({ open, title, onClose, children, footer }) {
  useEffect(() => {
    if (!open) return;
    const f = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, [open]);
  if (!open) return null;
  return html`<div class="scrim" onClick=${onClose}></div>
    <aside class="drawer" role="dialog" aria-modal="true" aria-label=${title}>
      <div class="dh"><h2 style="flex:1">${title}</h2><button class="btn ghost sm" aria-label="Close" onClick=${onClose}><${Icon} n="close" /></button></div>
      <div class="db">${children}</div>${footer && html`<div class="df">${footer}</div>`}
    </aside>`;
}

// Confirm dialog for destructive or consequential actions; optional required reason.
export function Confirm({ open, title, body, confirm = "Confirm", danger, reason, onCancel, onConfirm, busy }) {
  const [text, set] = useState("");
  useEffect(() => set(""), [open]);
  if (!open) return null;
  return html`<div class="scrim" onClick=${onCancel}></div>
    <div class="modal" role="alertdialog" aria-modal="true" aria-label=${title}>
      <h2>${title}</h2><div class="subline" style="margin:8px 0 16px">${body}</div>
      ${reason && html`<div class="field"><label for="cf-reason">${reason}</label><textarea id="cf-reason" rows="3" value=${text} onInput=${(e) => set(e.target.value)}></textarea></div>`}
      <div class="actions" style="justify-content:flex-end"><button class="btn" onClick=${onCancel}>Cancel</button>
        <button class=${"btn " + (danger ? "danger" : "pri")} disabled=${busy || (reason && text.trim().length < 5)} onClick=${() => onConfirm(text)}>${busy ? "Working…" : confirm}</button></div>
    </div>`;
}

// toasts
let pushToast = () => {};
export const toast = (msg, kind = "") => pushToast({ msg, kind, id: Math.random() });
export function Toasts() {
  const [items, set] = useState([]);
  pushToast = (t) => { set((x) => [...x, t]); setTimeout(() => set((x) => x.filter((y) => y.id !== t.id)), 5000); };
  return html`<div class="toasts" aria-live="polite">${items.map((t) => html`<div class=${"toast " + t.kind}>${t.msg}</div>`)}</div>`;
}

// Job progress: real stages, progress and logs from the worker; cancel when allowed.
export function JobProgress({ jobId, onDone, compact, canCancel = true }) {
  const { job, logs } = useJob(jobId, onDone);
  const box = useRef(null);
  useEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; }, [logs.length]);
  if (!jobId) return null;
  if (!job) return html`<div class="small muted">Waiting for the queue…</div>`;
  const active = job.status === "queued" || job.status === "running";
  return html`<div class="stack" style="gap:10px">
    <div class="row wrap"><${Status} s=${job.status} /><span class="small muted">${job.stage}${job.elapsed_s != null ? ` · ${fmt.d1(job.elapsed_s)} s` : ""}</span>
      <span class="spacer"></span><${Id} id=${job.job_id} n=${14} />
      ${active && canCancel && html`<button class="btn sm" onClick=${() => api(`/jobs/${jobId}/cancel`, { method: "POST" }).catch((e) => toast(e.message, "bad"))}><${Icon} n="stop" s=${12} />Cancel</button>`}</div>
    <div class="prog" role="progressbar" aria-valuenow=${Math.round((job.progress || 0) * 100)} aria-valuemin="0" aria-valuemax="100"><i style=${`width:${Math.round((job.progress || 0) * 100)}%`}></i></div>
    ${!compact && job.stages?.length > 0 && html`<div class="stages">${job.stages.map((s) => html`<span class=${"pill " + (s.status === "done" ? "ok" : s.status === "failed" ? "bad" : s.status === "warning" ? "warn" : "fc")}>${s.name}</span>`)}</div>`}
    ${job.error && html`<div class="notice bad">${job.error}</div>`}
    ${!compact && html`<div class="logs" ref=${box}>${logs.length ? logs.map((l) => html`<div class=${l.level === "warning" ? "w" : l.level === "error" ? "e" : ""}>${l.ts.slice(11, 19)}  ${l.message}</div>`) : "No log lines yet."}</div>`}
  </div>`;
}

// Provenance footer: where a number came from
export const Prov = ({ items }) => html`<div class="prov">${items.filter(Boolean).map((x) => html`<span>${x}</span>`)}</div>`;

// Avatar tint per role: stable hue per person, pale (identity, not data)
const ROLE_HUE = { viewer: 210, data_operator: 150, data_scientist: 30, epi_reviewer: 330, platform_admin: 270, anonymous: 0 };
export const Avatar = ({ role, name }) => {
  const h = ROLE_HUE[role] ?? 200;
  return html`<span class="av" style=${`background:hsl(${h} 45% 92%);color:hsl(${h} 45% 28%)`}>${(name || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase()}</span>`;
};
