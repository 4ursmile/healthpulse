// Story — the one demo path: "Would HealthPulse have warned us, what does it expect next, and what do we do?"
// 1 Proof (benchmark + 2023-24 replay) -> 2 Replay one state -> 3 2026-27 outlook -> 4 AI brief -> human decision (audited).
// Every number comes from `hp sprint` / `hp retro` outputs on the official IMDC files; nothing on this screen is synthetic.
import { html, useState, useEffect, useRef } from "../../vendor/preact-htm.js";
import { useApi, getToken, setQuery, navigate } from "../api.js";
import { Panel, TitleRow, Kpis, Notices, Loadable, SkBlock, Empty, Sem, fmt, fdate } from "../ui.js";
import { TimeChart } from "../charts.js";
import { scopeOf } from "../scope.js";
import { md } from "./vn/_vn.js";

const STEPS = ["1 · Would it have warned us?", "2 · Replay 2023-24", "3 · Next season", "4 · Brief & decide"];
const UFS = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "GO", "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO"];

const series = (rows) => ({
  observed: rows.filter((r) => r.observed != null).map((r) => ({ t: r.date, y: r.observed })),
  forecast: rows.map((r) => ({ t: r.date, p50: r.pred, p10: r.lower_80, p90: r.upper_80 })),
});

function StateSelect({ uf, onChange }) {
  return html`<select aria-label="State" value=${uf} onChange=${(e) => onChange(e.target.value)}>${UFS.map((u) => html`<option value=${u}>${u}</option>`)}</select>`;
}

function Proof({ retro, report }) {
  const s = retro.season_2023_24, h = retro.history_2015_2022;
  const board = report?.leaderboard || [];
  const champ = board.find((b) => b.champion), ref = board.find((b) => b.reference);
  return html`
    <p class="subline" style="margin:0 0 16px">Brazil's 2023-24 dengue season was the largest on record. We replayed it week by week for every state,
      using only the data each week would have had (2-week reporting delay), against threshold monitoring.</p>
    <${Kpis} items=${[
      { label: "Epidemic states warned early", value: `${s.detected_before_status_quo}/${s.epidemic}`, sub: "passed 300 cases / 100k in 2023-24", lead: true },
      { label: "Median warning gained", value: s.lead_weeks_median, unit: " weeks", sub: `flat carry-forward rule: ${s.flat_comparator.lead_weeks_median} weeks` },
      { label: "False alarms 2015-2022", value: fmt.pct(h.false_alarm_rate, 0), sub: `${h.false_alarms} of ${h.non_epidemic} non-epidemic state-seasons` },
      { label: "Epidemics missed 2015-2022", value: `${h.missed}/${h.epidemic}`, sub: "same rule, not tuned on 2023-24" },
    ]} />
    ${champ && ref && html`<div style="margin-top:20px">
      <h3 style="margin:0 0 6px">Season-ahead benchmark (IMDC 2026 protocol)</h3>
      <p class="small muted" style="margin:0 0 8px">${report.protocol.metric}. ${report.protocol.tuning}.</p>
      <table class="tbl"><thead><tr><th>Model</th><th class="num">rel WIS (states equal)</th><th class="num">mean WIS (cases)</th><th class="num">90% coverage</th></tr></thead>
        <tbody>${[champ, ref].map((b) => html`<tr><td>${b.label}</td><td class="num">${b.rel_wis.toFixed(2)}</td><td class="num">${fmt.int(b.mean_wis)}</td><td class="num">${fmt.pct(b.cov90, 0)}</td></tr>`)}</tbody></table>
      <p class="small muted" style="margin:8px 0 0">Honest reading: in June the season model beats climatology where the cases are (−${Math.round((1 - champ.mean_wis / ref.mean_wis) * 100)}% case-weighted WIS), not in every small state. The weekly warning is where the lead time comes from.</p>
    </div>`}`;
}

function Replay({ uf, setUf, retro }) {
  const st = retro.states_2023_24.find((x) => x.uf === uf);
  const q = useApi(`/sprint/forecast/${uf}?season=2023-2024`);
  const markers = st && st.crossing_date ? [
    { t: st.alert_date, line: true, color: "var(--chart-1)", short: "HealthPulse alert", label: `HealthPulse alert ${st.alert_date}`, row: 0 },
    { t: st.crossing_date, line: true, dash: "3 3", short: "passed 300/100k", label: `Passed the epidemic level ${st.crossing_date}`, row: 1 },
    { t: st.seen_by_status_quo, line: true, color: "var(--warning)", short: "status quo sees it", label: `Threshold monitoring sees it ${st.seen_by_status_quo}`, row: 2 },
  ] : st?.alert_date ? [{ t: st.alert_date, line: true, color: "var(--warning)", short: "false alarm", label: `False alarm ${st.alert_date}` }] : [];
  return html`
    <div class="row wrap" style="margin-bottom:12px"><${StateSelect} uf=${uf} onChange=${setUf} />
      ${st && (st.crossing_date
        ? html`<span><b>${st.lead_weeks} weeks</b> earlier than threshold monitoring <span class="muted">(flat rule: ${st.lead_weeks_flat})</span> · ${fmt.int(st.incidence)} cases / 100k <${Sem} k="observed" /></span>`
        : html`<span class="muted">Stayed below the epidemic level (${fmt.int(st.incidence)} / 100k)${st.alert_date ? " — the rule raised a false alarm" : " — no alert raised"}.</span>`)}</div>
    <${Loadable} q=${q} sk=${html`<${SkBlock} h=${260} />`}>${() => { const s = series(q.data); return html`
      <${TimeChart} observed=${s.observed} forecast=${s.forecast} markers=${markers} height=${280} unit="cases" label=${`Weekly dengue cases, ${uf}, 2023-24`} />
      <p class="small muted" style="margin:8px 0 0">Black: reported probable cases per week. Dashed and band: the season forecast made in June 2023 (median, 80% interval),
        which saw ${uf} heading above normal but could not foresee a record wave. Vertical lines: when each method would have known.</p>`; }}</${Loadable}>`;
}

function Outlook({ uf, setUf, report }) {
  const q = useApi(`/sprint/forecast/${uf}?season=2026-2027`);
  const top = (report?.target_forecast || []).slice(0, 8);
  return html`
    <div class="row wrap" style="margin-bottom:12px"><${StateSelect} uf=${uf} onChange=${setUf} /><span class="muted">Forecast made with data to EW25 2026 (June), for EW41 2026 – EW40 2027 <${Sem} k="forecast" /></span></div>
    <${Loadable} q=${q} sk=${html`<${SkBlock} h=${260} />`}>${() => { const s = series(q.data); return html`
      <${TimeChart} observed=${[]} forecast=${s.forecast} height=${260} unit="cases" label=${`2026-27 season forecast, ${uf}`} />`; }}</${Loadable}>
    <h3 style="margin:18px 0 6px">Largest expected seasons (median)</h3>
    <table class="tbl"><thead><tr><th>State</th><th class="num">Season total</th><th class="num">Peak week</th><th class="num">Cases at peak</th></tr></thead>
      <tbody>${top.map((r) => html`<tr class=${r.uf === uf ? "on" : ""} style="cursor:pointer" onClick=${() => setUf(r.uf)}><td>${r.uf}</td><td class="num">${fmt.int(r.season_total_median)}</td><td class="num">${fdate(r.peak_week)}</td><td class="num">${fmt.int(r.peak_median)}</td></tr>`)}</tbody></table>
    <p class="small muted" style="margin:8px 0 0">Planning baseline, not a trigger: season size is weakly predictable in June. The weekly alert rule (step 2) decides when to act.</p>`;
}

async function sse(url, body, onEvent, signal) {
  const h = { Accept: "text/event-stream", "Content-Type": "application/json" };
  if (getToken()) h.Authorization = `Bearer ${getToken()}`;
  const r = await fetch("/api/v1" + url, { method: "POST", headers: h, body: JSON.stringify(body), signal });
  if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(typeof j?.error?.detail === "string" ? j.error.detail : r.statusText); }
  const rd = r.body.getReader(), dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await rd.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let k;
    while ((k = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, k); buf = buf.slice(k + 2);
      const ev = (block.match(/^event: (.*)$/m) || [])[1] || "message";
      const data = (block.match(/^data: (.*)$/m) || [])[1];
      if (data) onEvent(ev, JSON.parse(data));
    }
  }
}

function Brief({ uf, setUf, me }) {
  const [text, setText] = useState(""), [meta, setMeta] = useState(null), [busy, setBusy] = useState(false), [err, setErr] = useState(null);
  const [lang, setLang] = useState("en"), [note, setNote] = useState(""), [done, setDone] = useState(null), [deciding, setDeciding] = useState(false);
  const ctl = useRef(null);
  const run = async () => {
    ctl.current?.abort(); const c = new AbortController(); ctl.current = c;
    setText(""); setMeta(null); setErr(null); setDone(null); setBusy(true);
    try {
      await sse("/sprint/brief", { uf, lang }, (ev, d) => {
        if (ev === "meta") setMeta(d); else if (ev === "error") setErr(d.error); else if (d.t) setText((t) => t + d.t);
      }, c.signal);
    } catch (e) { if (e.name !== "AbortError") setErr(e.message); }
    setBusy(false);
  };
  useEffect(() => { run(); return () => ctl.current?.abort(); }, [uf, lang]);
  const canDecide = me && ["epi_reviewer", "platform_admin"].includes(me.role);
  const decide = async (decision) => {
    setDeciding(true); setErr(null);
    try {
      const h = { "Content-Type": "application/json" };
      if (getToken()) h.Authorization = `Bearer ${getToken()}`;
      const r = await fetch("/api/v1/sprint/decision", { method: "POST", headers: h, body: JSON.stringify({ uf, decision, note: note || null, provider: meta?.provider }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j?.error?.detail || r.statusText);
      setDone(j.data);
    } catch (e) { setErr(e.message); }
    setDeciding(false);
  };
  const ai = meta && meta.provider !== "rule_based";
  return html`
    <div class="row wrap" style="margin-bottom:12px"><${StateSelect} uf=${uf} onChange=${setUf} />
      <div class="seg sm" role="radiogroup" aria-label="Language">${["en", "pt"].map((l) => html`<button class=${l === lang ? "on" : ""} onClick=${() => setLang(l)}>${l.toUpperCase()}</button>`)}</div>
      <span class="spacer"></span>
      ${meta && html`<span class=${"pill " + (ai ? "ok" : "")} title=${ai ? "" : `AI ${meta.why_rules}: set ANTHROPIC_API_KEY in .env`}>${ai ? `AI · ${meta.model}` : "Rule-based brief"}</span>`}
      <button class="btn sm" onClick=${run} disabled=${busy}>${busy ? "Writing…" : "Regenerate"}</button></div>
    ${meta && !ai && html`<${Notices} kind="info" items=${[`The AI analyst is ${meta.why_rules}. This brief is written by fixed rules from the same numbers. Add ANTHROPIC_API_KEY to .env (or pick a model in Admin → Settings → AI analyst) to have Claude write it.`]} />`}
    ${err && html`<${Notices} items=${[err]} />`}
    <div class="brief" aria-live="polite" style="min-height:160px">${text ? html`<div dangerouslySetInnerHTML=${{ __html: md(text) }} />` : busy ? html`<${SkBlock} h=${160} />` : null}</div>
    <details style="margin-top:8px"><summary class="small muted">What the model sees (the only data it may use)</summary>
      <pre class="small" style="white-space:pre-wrap;max-height:240px;overflow:auto">${meta ? JSON.stringify(meta.context, null, 1) : ""}</pre></details>
    <div class="panel" style="margin-top:16px;padding:14px">
      <b>Human decision</b> <span class="muted small">— the analyst only proposes; an epidemiology reviewer approves or rejects, and the decision is written to the audit log.</span>
      ${done ? html`<p style="margin:10px 0 0">${done.decision === "approve" ? "Approved" : "Rejected"} for ${done.uf} by ${done.by} (${done.role}). Recorded in Admin → Security → audit.</p>`
        : canDecide ? html`<div class="row wrap" style="margin-top:10px">
            <input type="text" placeholder="Note (optional)" value=${note} onInput=${(e) => setNote(e.target.value)} style="flex:1;min-width:200px" aria-label="Decision note" />
            <button class="btn" disabled=${deciding || busy || !text} onClick=${() => decide("reject")}>Reject</button>
            <button class="btn pri" disabled=${deciding || busy || !text} onClick=${() => decide("approve")}>Approve proposal</button></div>`
        : html`<div class="row" style="margin-top:10px"><span class="muted small">Sign in as an Epidemiology reviewer to decide.</span>
            <button class="btn sm" onClick=${() => navigate("/login")}>Switch role</button></div>`}
    </div>`;
}

export default function StoryPage({ q, me }) {
  const step = Math.min(STEPS.length - 1, Math.max(0, +(q.get("step") || 0)));
  const sc = scopeOf(q);
  const uf = (q.get("uf") || (sc.country === "br" && sc.region) || "MG").toUpperCase();
  const setStep = (i) => setQuery({ step: i || null });
  const setUf = (u) => setQuery({ uf: u });
  const retro = useApi("/sprint/retro");
  const report = useApi("/sprint/report");
  const missing = retro.error?.status === 404 || report.error?.status === 404;
  const body = () => step === 0 ? html`<${Proof} retro=${retro.data} report=${report.data} />`
    : step === 1 ? html`<${Replay} uf=${uf} setUf=${setUf} retro=${retro.data} />`
    : step === 2 ? html`<${Outlook} uf=${uf} setUf=${setUf} report=${report.data} />`
    : html`<${Brief} uf=${uf} setUf=${setUf} me=${me} />`;
  return html`<div class="page">
    <${TitleRow} title="Dengue early warning, end to end" sub="Brazil, all 26 states in the IMDC data (ES excluded) · official Infodengue-Mosqlimate sprint files" />
    <div class="steps" role="tablist">${STEPS.map((s, i) => html`<button role="tab" aria-selected=${i === step} class=${i === step ? "on" : i < step ? "done" : ""} onClick=${() => setStep(i)}>${s}</button>`)}</div>
    ${missing ? html`<${Empty}>No benchmark outputs yet. Run <code>hp sprint</code> then <code>hp retro</code> (about 30 s).</${Empty}>`
      : html`<${Loadable} q=${retro} sk=${html`<${SkBlock} h=${300} />`}>${() => html`<${Panel} title=${STEPS[step].slice(4)}>${body()}
        <div class="row" style="margin-top:20px"><button class="btn" disabled=${step === 0} onClick=${() => setStep(step - 1)}>Back</button><span class="spacer"></span>
          ${step < STEPS.length - 1 && html`<button class="btn pri" onClick=${() => setStep(step + 1)}>Next</button>`}</div></${Panel}>`}</${Loadable}>`}
  </div>`;
}
