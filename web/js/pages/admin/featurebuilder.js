// Feature builder — controlled editor over allowlisted templates; saves a draft, then the PIT validator decides.
import { html, useState, useMemo } from "../../../vendor/preact-htm.js";
import { useApi, navigate } from "../../api.js";
import { Panel, TitleRow, Loadable, Link, Notices, label, SkBlock } from "../../ui.js";
import { useAction, Guarded, Field, Checks } from "./_shared.js";

const CASE_COLS = ["observed_cases", "estimated_cases"];
const DEFAULTS = { lag_weeks: 1, end_lag_weeks: 2, window_weeks: 4, agg: "mean", min_periods: 3, transform: "", span_weeks: 2,
  relative_to: "target", start_offset_days: 0, end_offset_days: 7, fn: "sin" };

function explain(t, p, unit) {
  const col = p.column || "the value";
  const tf = p.transform === "log1p" ? "log(1 + x) of " : p.transform === "log" ? "log of " : "";
  const tbl = p.table === "cases" ? "the feature set's disease series" : p.table;
  switch (t) {
    case "lag": return `${tf}${col} from ${tbl} for the week ${p.lag_weeks} week(s) before the issue week.`;
    case "rolling": return `${tf}the ${p.agg} of ${col} from ${tbl} over ${p.window_weeks} weeks, the last of which ends ${p.end_lag_weeks} week(s) before the issue week; null unless at least ${p.min_periods} weeks were released.`;
    case "growth": return `log((${col} at lag ${p.lag_weeks} + 1) / (${col} at lag ${+p.lag_weeks + +p.span_weeks} + 1)) from ${tbl}.`;
    case "incidence": return `Reported cases ${p.lag_weeks} week(s) before issue per 100,000 residents (population as of the prior year).`;
    case "static_asof": return `${tf}${col} from ${p.table} as published for the year before the issue year. Refused if the table has more than one row per municipality.`;
    case "seasonal_lag": return `${tf}${col} from ${tbl} in the same epi-week one year before the target week.`;
    case "holiday_window": return `Count of national public holidays from ${p.start_offset_days} to ${p.end_offset_days} days around the ${p.relative_to === "target" ? "target week" : "issue instant"} (scheduled, known ahead).`;
    case "calendar": return `${p.fn === "sin" ? "Sine" : "Cosine"} of the target week's position in the year.`;
    case "neighbor_pressure": return "Commuter-weighted lagged cases in neighbouring municipalities. Needs the IBGE commuting OD source.";
    default: return "";
  }
}

export default function FeatureBuilder() {
  const tq = useApi("/admin/feature-templates");
  const cat = useApi("/admin/catalog");
  const [f, set] = useState({ feature_id: "", group: "epidemiology", template: "lag", description: "", unit: "", params: { table: "cases", column: "observed_cases", lag_weeks: 1, transform: "log1p" } });
  const [res, setRes] = useState(null);
  const a = useAction(), v = useAction();
  const tpl = tq.data?.[f.template];
  const schemas = useMemo(() => Object.fromEntries((cat.data?.silver || []).map((t) => [t.table, t.schema])), [cat.data]);
  const numericCols = (t) => t === "cases" ? CASE_COLS : Object.entries(schemas[t] || {}).filter(([, ty]) => /Int|Float|UInt/.test(ty)).map(([c]) => c);
  const setTpl = (t) => {
    const ps = Object.fromEntries((tq.data[t].params || []).map((k) => [k, k === "table" ? (["static_asof"].includes(t) ? "population_annual" : "cases") : k === "column" ? (t === "static_asof" ? "population" : "observed_cases") : DEFAULTS[k] ?? ""]));
    set({ ...f, template: t, params: ps });
  };
  const setP = (k, val) => set({ ...f, params: { ...f.params, [k]: val } });
  const lagDays = tq.meta?.publication_lag_days || {};
  const tableReal = f.params.table === "cases" ? "disease_weekly" : f.params.table;
  const lag = +(f.params.lag_weeks ?? f.params.end_lag_weeks ?? 0);
  const pubLag = lagDays[tableReal];
  const risky = pubLag != null && ["lag", "rolling", "growth", "incidence"].includes(f.template) && pubLag > 3.5 + (lag - 1) * 7;
  const save = async () => {
    const params = Object.fromEntries(Object.entries(f.params).filter(([, x]) => x !== "").map(([k, x]) => [k, ["table", "column", "agg", "transform", "relative_to", "fn"].includes(k) ? x : +x]));
    const r = await a.run("/admin/features", { body: { ...f, params } }, "Draft saved");
    if (!r) return;
    const val = await v.run(`/admin/features/${r.data.feature_id}/validate`, { body: { version: r.data.version } });
    setRes({ def: r.data, val: val?.data });
  };
  const ok = /^[a-z][a-z0-9_]{2,60}$/.test(f.feature_id);
  return html`<div class="page">
    <${TitleRow} crumb=${html`<${Link} to="/admin/features" cls="link">Feature store</${Link}>`} title="New feature" sub="Choose an allowlisted template; no code is executed. Saving creates a draft version that must pass the point-in-time validator before approval." />
    <${Loadable} q=${tq} sk=${html`<${SkBlock} h=${300} />`}>${(tpls) => html`<div class="grid">
      <div class="s7"><${Panel} title="Definition">
        <div class="fgrid">
          <${Field} label="Feature id" id="fb-id" hint="Lowercase slug, e.g. rain_sum_6w_lag3"><input id="fb-id" type="text" value=${f.feature_id} onInput=${(e) => set({ ...f, feature_id: e.target.value.trim() })} /></${Field}>
          <${Field} label="Group" id="fb-g"><select id="fb-g" value=${f.group} onChange=${(e) => set({ ...f, group: e.target.value })}>${(tq.meta.groups || []).map((g) => html`<option value=${g}>${label(g)}</option>`)}</select></${Field}>
          <${Field} label="Template" id="fb-t"><select id="fb-t" value=${f.template} onChange=${(e) => setTpl(e.target.value)}>${Object.keys(tpls).map((t) => html`<option value=${t}>${t.replace(/_/g, " ")} (${tpls[t].level} level)</option>`)}</select></${Field}>
          <${Field} label="Unit" id="fb-u"><input id="fb-u" type="text" placeholder="mm, °C, log(1+cases)" value=${f.unit} onInput=${(e) => set({ ...f, unit: e.target.value })} /></${Field}>
          ${tpl?.params.includes("table") && html`<${Field} label="Source table" id="fb-tb"><select id="fb-tb" value=${f.params.table} onChange=${(e) => set({ ...f, params: { ...f.params, table: e.target.value, ...(tpl.params.includes("column") ? { column: numericCols(e.target.value)[0] || "" } : {}) } })}>
            <option value="cases">cases (disease of the feature set)</option>${(tq.meta.tables || []).filter((t) => numericCols(t).length).map((t) => html`<option value=${t}>${t}</option>`)}</select></${Field}>`}
          ${tpl?.params.includes("column") && html`<${Field} label="Column" id="fb-c"><select id="fb-c" value=${f.params.column} onChange=${(e) => setP("column", e.target.value)}>${numericCols(f.params.table).map((c) => html`<option value=${c}>${c}</option>`)}</select></${Field}>`}
          ${["lag_weeks", "end_lag_weeks", "window_weeks", "min_periods", "span_weeks", "start_offset_days", "end_offset_days"].filter((k) => tpl?.params.includes(k)).map((k) => html`
            <${Field} label=${label(k)} id=${"fb-" + k}><input id=${"fb-" + k} type="number" value=${f.params[k]} onInput=${(e) => setP(k, e.target.value)} /></${Field}>`)}
          ${tpl?.params.includes("agg") && html`<${Field} label="Aggregation" id="fb-a"><select id="fb-a" value=${f.params.agg} onChange=${(e) => setP("agg", e.target.value)}>${["mean", "sum", "max"].map((x) => html`<option>${x}</option>`)}</select></${Field}>`}
          ${tpl?.params.includes("transform") && html`<${Field} label="Transform" id="fb-x"><select id="fb-x" value=${f.params.transform} onChange=${(e) => setP("transform", e.target.value)}><option value="">none</option><option value="log1p">log(1 + x)</option><option value="log">log</option></select></${Field}>`}
          ${tpl?.params.includes("relative_to") && html`<${Field} label="Relative to" id="fb-r"><select id="fb-r" value=${f.params.relative_to} onChange=${(e) => setP("relative_to", e.target.value)}><option value="target">target week</option><option value="issue">issue instant</option></select></${Field}>`}
          ${tpl?.params.includes("fn") && html`<${Field} label="Function" id="fb-fn"><select id="fb-fn" value=${f.params.fn} onChange=${(e) => setP("fn", e.target.value)}><option value="sin">sine</option><option value="cos">cosine</option></select></${Field}>`}
        </div>
        <${Field} label="Description" id="fb-d"><textarea id="fb-d" rows="2" value=${f.description} onInput=${(e) => set({ ...f, description: e.target.value })}></textarea></${Field}>
        <div class="row"><span class="small muted">${ok ? "" : "Enter a valid feature id to save."}</span><span class="spacer"></span>
          <${Guarded} perm="feature.write" cls="btn pri" busy=${a.busy || v.busy} disabled=${!ok} reason="Enter a valid feature id" onClick=${save}>Save draft and validate</${Guarded}></div>
      </${Panel}></div>
      <div class="s5 stack">
        <${Panel} title="What it computes"><p style="margin:0">${explain(f.template, f.params, f.unit)}</p>
          ${pubLag != null && html`<p class="small muted" style="margin:12px 0 0">${tableReal} is released about ${pubLag} days after each week ends; forecasts are issued Wednesday 12:00 UTC.</p>`}</${Panel}>
        <${Notices} kind="bad" items=${risky ? [`With a ${lag}-week lag this feature would read ${tableReal} weeks not yet released at issue time. The validator will block it; use a lag of at least ${Math.ceil((pubLag - 3.5) / 7) + 1} weeks.`] : []} />
        ${res && html`<${Panel} title="Saved draft" right=${html`<${Link} to=${`/admin/features/${res.def.feature_id}`} cls="btn sm">Open</${Link}>`}>
          <div class="small muted" style="margin-bottom:8px">${res.def.feature_id} v${res.def.version} · validation ${label(res.val?.status || "not run")}</div>
          ${res.val && html`<${Checks} items=${res.val.checks} />`}</${Panel}>`}
      </div></div>`}</${Loadable}>
  </div>`;
}
