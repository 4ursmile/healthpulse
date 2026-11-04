// Model lab — question: "Does the published model beat transparent baselines on held-out weeks, and are its intervals honest?"
import { html, useState } from "../../vendor/preact-htm.js";
import { useApi, setQuery } from "../api.js";
import { Panel, TitleRow, Kpis, Notices, Status, Loadable, SkBlock, SkRows, Empty, Tabs, Link, fmt, fdate } from "../ui.js";
import { CatLines, Calibration, BarList } from "../charts.js";
import { DiseaseSelect } from "./_product_shared.js";
import { usePro, MoreInPro } from "../mode.js";
import { Leaderboard, headline, niceRecipe } from "../leaderboard.js";

const BASE = ["persistence", "seasonal_naive", "historical_median", "damped_trend"];
const nice = niceRecipe;

function Metrics({ m }) {
  const [h, setH] = useState("1");
  const rid = m.model.recipe_id;
  const hs = Object.keys(m.test[rid] || {}).sort();
  const recipes = Object.keys(m.test);
  const bestBase = (hh) => BASE.filter((b) => m.test[b]?.[hh]?.mae != null).sort((a, b) => m.test[a][hh].mae - m.test[b][hh].mae)[0];
  const bb = bestBase("1");
  const card = m.model.model_card_json || {};
  const cal = [rid, bb].filter(Boolean).map((r, i) => ({ name: nice(r), color: i ? "var(--text)" : "var(--chart-1)", pts: m.calibration?.[`${r}|${h}`] || [] }));
  const t1 = m.test[rid]?.["1"] || {};
  const comp = m.comparison?.["1"]?.[rid];
  const abl = Object.entries(m.ablation || {});
  const pro = usePro();
  return html`<div class="stack">
    <${Panel} title="How good is the forecast?" note="All models scored on the same held-out weeks" right=${html`<${MoreInPro}>Show scores & diagnostics</${MoreInPro}>`}>
      <p class="hero-line">${headline(m, rid)}</p>
      <${Leaderboard} rep=${m} published=${rid} limit=${4} />
    </${Panel}>
    <${Kpis} items=${[
      { label: "Model MAE, +1 week", lead: true, value: fmt.d1(t1.mae), unit: "cases", sub: `held-out test, ${fmt.int(t1.n)} region-weeks` },
      { label: "Skill vs best baseline", value: comp ? fmt.signed(comp.skill_vs_baseline * 100, fmt.int) + "%" : "—", sub: comp ? `vs ${nice(comp.baseline)}, +1 week` : "—" },
      { label: "80% interval coverage", value: fmt.pct(t1.coverage80), sub: "empirical on the test window (+1 week)" },
      { label: "Evidence level", value: html`<${Status} s=${m.model.pit_status} />`, sub: `test ${fdate(m.split?.test?.start)} – ${fdate(m.split?.test?.end)}` },
    ]} />
    <div class="grid">
      <div class="s7"><${Panel} title="Mean absolute error by horizon" note=${html`<div class="ckey" style="margin-top:4px"><span><i></i>${nice(rid)} (published)</span>${bb && html`<span><i style="border-top-color:var(--text)" class="dash"></i>${nice(bb)}</span>`}<span><i style="border-top-color:var(--chart-muted)"></i>other baselines</span></div>`}>
        <${CatLines} cats=${hs.map((x) => `+${x} wk`)} height=${220} yfmt=${fmt.int} label="MAE by horizon"
          series=${[...BASE.filter((b) => b !== bb && m.test[b]).map((b) => ({ name: nice(b), color: "var(--chart-muted)", values: hs.map((x) => m.test[b][x]?.mae) })),
            bb && { name: nice(bb), color: "var(--text)", dash: true, values: hs.map((x) => m.test[bb][x]?.mae) },
            { name: nice(rid), color: "var(--chart-1)", values: hs.map((x) => m.test[rid][x]?.mae) }].filter(Boolean)} />
        <div class="small muted" style="margin-top:6px">Matched rolling-origin folds; lower is better. Same held-out issue weeks for every recipe.</div>
      </${Panel}></div>
      <div class="s5"><${Panel} title="Interval calibration" right=${html`<select aria-label="Horizon" value=${h} onChange=${(e) => setH(e.target.value)}>${hs.map((x) => html`<option value=${x}>+${x} week</option>`)}</select>`}
        note=${html`<div class="ckey" style="margin-top:4px"><span><i></i>${nice(rid)}</span>${bb && html`<span><i style="border-top-color:var(--text)"></i>${nice(bb)}</span>`}<span><i class="ref"></i>perfect</span></div>`}>
        <${Calibration} points=${cal} height=${220} />
      </${Panel}></div>
      ${pro && html`<div class="s12"><${Panel} title="Held-out test metrics" note="Per recipe and horizon; WAPE = Σ|error| / Σ observed; outbreak scores use a provisional per-municipality threshold" flush>
        <div class="tablewrap"><table class="tbl dense">
          <thead><tr><th>Recipe</th><th class="num">Horizon</th><th class="num">MAE</th><th class="num">WAPE</th><th class="num">Bias</th><th class="num">80% coverage</th><th class="num">Interval width</th><th class="num">Outbreak precision</th><th class="num">Recall</th><th class="num">n</th></tr></thead>
          <tbody>${recipes.flatMap((r) => hs.map((x) => { const v = m.test[r][x] || {}; return html`<tr class=${r === rid ? "sel" : ""}>
            <td>${x === hs[0] ? html`${nice(r)}${r === rid ? html` <span class="pill ok">Published</span>` : BASE.includes(r) ? html` <span class="pill">Baseline</span>` : ""}` : ""}</td>
            <td class="num">+${x}</td><td class="num">${fmt.d1(v.mae)}</td><td class="num">${fmt.pct(v.wape, 1)}</td><td class="num">${fmt.signed(v.bias, fmt.d1)}</td>
            <td class="num">${fmt.pct(v.coverage80)}</td><td class="num">${fmt.int(v.interval_width)}</td><td class="num">${v.outbreak_events ? fmt.pct(v.outbreak_precision) : "—"}</td>
            <td class="num">${v.outbreak_events ? fmt.pct(v.outbreak_recall) : "—"}</td><td class="num">${fmt.int(v.n)}</td></tr>`; }))}</tbody></table></div>
      </${Panel}></div>
      <div class="s6"><${Panel} title="Ablation" note="Gradient boosting on the test window, MAE by feature group" flush>
        ${abl.length ? html`<table class="tbl dense"><thead><tr><th>Feature groups</th>${hs.map((x) => html`<th class="num">+${x} wk</th>`)}</tr></thead>
          <tbody>${abl.map(([g, v]) => html`<tr><td>${g === "cases_only" ? "Cases only" : g}</td>${v.status === "ok" ? hs.map((x) => html`<td class="num">${fmt.d1(v.test[x]?.mae)}</td>`)
            : html`<td colspan=${hs.length} class="muted">Unavailable — ${v.reason}</td>`}</tr>`)}</tbody></table>
          <div class="small muted" style="padding:8px 20px 4px">Adding a group does not prove a causal effect; a higher error means the group did not help on these weeks.</div>`
          : html`<div style="padding:0 20px"><${Empty}>No ablation recorded.</${Empty}></div>`}
      </${Panel}></div>
      <div class="s6"><${Panel} title="Permutation importance" note=${m.importance ? `${m.importance.method}, +${m.importance.horizon} week` : ""}>
        ${m.importance ? html`<${BarList} format=${fmt.d1} rows=${m.importance.features.slice(0, 8).map((f) => ({ label: f.feature, value: Math.max(0, f.importance) }))} />
          <div class="small muted" style="margin-top:8px">${m.importance.caveat}</div>` : html`<${Empty}>No importance recorded for this recipe.</${Empty}>`}
      </${Panel}></div>
      <div class="s12"><${Panel} title="Model card">
        <dl class="kv"><dt>Model</dt><dd>${m.model.model_id} v${m.model.version} · ${card.model}</dd><dt>Target</dt><dd>${card.target_definition} (${card.signal_type})</dd>
          <dt>Training window</dt><dd>${fdate(card.training_window?.start)} – ${fdate(card.training_window?.end)} · labels cut at ${fdate(card.label_cutoff)}</dd>
          <dt>Validation</dt><dd>${card.validation?.method}; ${m.split?.folds?.length} folds, embargo ${m.split?.embargo_weeks} weeks</dd>
          <dt>Uncertainty</dt><dd>${card.uncertainty}</dd><dt>Populations</dt><dd>${card.populations_represented}</dd>
          <dt>Use</dt><dd>${(card.usage_restrictions || []).join(" ")}</dd>${card.limitations?.length > 0 && html`<dt>Limitations</dt><dd>${card.limitations.join(" ")}</dd>`}
          <dt>Approval</dt><dd><${Status} s=${m.model.approval_state} /></dd></dl>
      </${Panel}></div>`}
    </div>
  </div>`;
}

export default function ModelLab({ q }) {
  const disease = q.get("disease") || "dengue";
  const mm = useApi(`/models/metrics?disease_id=${disease}`);
  const [alias, setAlias] = useState("champion");
  const rows = mm.data || [];
  const cur = rows.find((r) => r.alias === alias) || rows[0];
  return html`<div class="page">
    <${TitleRow} title="Model lab" sub="Backtests on held-out issue weeks, side by side with transparent baselines on identical folds."
      right=${html`<${DiseaseSelect} value=${disease} onChange=${(v) => setQuery({ disease: v })} />`} />
    <${Notices} items=${cur && cur.model.pit_status !== "PIT_VERIFIED" ? ["Historical source vintages were not archived before this pilot, so first-release times are approximated by publication lags (PIT unverified). Prospective performance is not claimed."] : []} />
    ${rows.length > 1 && html`<${Tabs} value=${cur?.alias} onChange=${setAlias} tabs=${rows.map((r) => [r.alias, `${r.alias === "champion" ? "Champion" : "Shadow"} · ${r.model.model_id} v${r.model.version}`])} />`}
    <${Loadable} q=${mm} sk=${html`<${SkBlock} h=${300} />`}>${() => cur ? html`<${Metrics} m=${cur} key=${cur.alias} />`
      : html`<${Empty} action=${html`<${Link} to="/admin/experiments" cls="btn">Model studio</${Link}>`}>No deployed model for ${disease}. Train, review and promote one in the model studio.</${Empty}>`}</${Loadable}>
  </div>`;
}
