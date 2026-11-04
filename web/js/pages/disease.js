// Diseases — question: "What exactly does each signal measure, and how does this season compare with earlier ones?"
import { html, useEffect, useState } from "../../vendor/preact-htm.js";
import { api, useApi, navigate, linkWith } from "../api.js";
import { Panel, TitleRow, Notices, Sem, Status, Loadable, SkBlock, SkRows, Empty, Link, fmt, fdate, Kpis } from "../ui.js";
import { stateParam } from "../scope.js";
import { CatLines } from "../charts.js";
import { NowcastPanel } from "../nowcast.js";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function useSeasons(disease, regions) {
  const [s, set] = useState({ loading: true });
  useEffect(() => {
    if (!regions.length) { set({ loading: false, byMonth: {} }); return; }
    let dead = false;
    set({ loading: true });
    Promise.all(regions.map((r) => api(`/regions/${r}/timeseries?disease_id=${disease}&weeks=1000`).catch(() => null))).then((res) => {
      if (dead) return;
      const byMonth = {};
      let latest = null;
      for (const r of res) for (const o of r?.data?.observed || []) {
        if (o.observed_cases == null) continue;
        const y = +o.week_start.slice(0, 4), m = +o.week_start.slice(5, 7) - 1;
        byMonth[y] ||= Array(12).fill(null);
        byMonth[y][m] = (byMonth[y][m] || 0) + o.observed_cases;
        if (!latest || o.week_start > latest) latest = o.week_start;
      }
      set({ loading: false, byMonth, latest });
    });
    return () => { dead = true; };
  }, [disease, regions.join(",")]);
  return s;
}

function Detail({ id, list }) {
  const d = list.find((x) => x.disease_id === id);
  const sp = stateParam(new URLSearchParams(location.search));
  const regs = useApi(`/metadata/regions?scope=pilot&limit=${sp ? 1000 : 200}${sp}`);
  const withData = d?.rows ? (regs.data || []).filter((r) => r.has_disease_data).map((r) => r.municipality_id) : [];
  const seasons = useSeasons(id, withData);
  const mm = useApi(`/models/metrics?disease_id=${id}`);
  if (!d) return html`<${Empty}>Unknown disease.</${Empty}>`;
  const years = Object.keys(seasons.byMonth || {}).map(Number).sort().slice(-3);
  const cur = seasons.latest ? +seasons.latest.slice(0, 4) : new Date().getUTCFullYear();
  const colors = { [cur]: "var(--chart-1)", [cur - 1]: "var(--text)", [cur - 2]: "var(--chart-muted)" };
  const champ = (mm.data || []).find((m) => m.alias === "champion");
  const skill = champ ? Object.entries(champ.comparison || {}).map(([h, row]) => ({ h, s: row[champ.model.recipe_id]?.skill_vs_baseline, b: row[champ.model.recipe_id]?.baseline, mae: row[champ.model.recipe_id]?.mae })) : [];
  const ytd = years.includes(cur) ? seasons.byMonth[cur].reduce((a, b) => a + (b || 0), 0) : null;
  const prevSame = years.includes(cur - 1) && seasons.latest ? seasons.byMonth[cur - 1].slice(0, +seasons.latest.slice(5, 7)).reduce((a, b) => a + (b || 0), 0) : null;
  return html`<div class="stack">
    <${Kpis} items=${[
      { label: `Reported ${cur} to date, pilot`, sem: "OBSERVED", lead: true, value: ytd == null ? "—" : fmt.int(ytd), unit: ytd == null ? "" : "cases", sub: seasons.latest ? `through ${fdate(seasons.latest)}` : "no series" },
      { label: `Same months of ${cur - 1}`, sem: "OBSERVED", value: prevSame == null ? "—" : fmt.int(prevSame), unit: prevSame == null ? "" : "cases", sub: ytd != null && prevSame ? html`<span class=${ytd > prevSame ? "up-bad" : "down-good"}>${fmt.signed(ytd - prevSame)}</span> this year vs last` : "—" },
      { label: "Signal", value: d.signal_type === "sari_admission" ? "SARI cases" : "Notifications", sub: d.units },
      { label: "Published model", value: champ ? `v${champ.model.version}` : "—", sub: champ ? champ.model.recipe_id.replace(/_/g, " ") : "none promoted" },
    ]} />
    <div class="grid">
      <div class="s8"><${Panel} title=${`${d.metric_label}, monthly totals across pilot municipalities`}
        note=${years.length ? html`<div class="ckey" style="margin-top:4px">${years.slice().reverse().map((y) => html`<span><i style=${`border-top-color:${colors[y] || "var(--chart-muted)"}`}></i>${y}</span>`)}</div>` : null}>
        ${seasons.loading ? html`<${SkBlock} h=${220} />` : years.length ? html`<${CatLines} height=${220} cats=${MONTHS} label="Seasonal comparison"
          series=${years.map((y) => ({ name: String(y), color: colors[y] || "var(--chart-muted)", values: seasons.byMonth[y] }))} yfmt=${fmt.compact} />
          <div class="small muted" style="margin-top:6px">Weeks are assigned to the month their epidemiological week starts in; the latest month is partial.</div>`
          : html`<${Empty} action=${html`<${Link} to=${d.signal_type === "sari_admission" ? "/admin/sources/sivep_gripe" : "/admin/sources"} cls="btn sm">Open in Data sources</${Link}>`}>No ${d.label} series has been ingested${d.signal_type === "sari_admission" ? " yet (SIVEP-Gripe SARI)" : ""}. Nothing is estimated or substituted.</${Empty}>`}
      </${Panel}></div>
      <div class="s4"><${Panel} title="Case definition">
        <p style="margin:0 0 12px;line-height:20px">${d.definition}</p>
        <dl class="kv"><dt>Signal</dt><dd>${d.signal_type.replace(/_/g, " ")}</dd><dt>Unit</dt><dd>${d.units}</dd><dt>Targets</dt><dd>${d.targets.join(", ")}</dd>
          <dt>Rows</dt><dd>${fmt.int(d.rows)}</dd><dt>Latest week</dt><dd>${fdate(d.latest_week)}</dd></dl>
        ${d.signal_type === "sari_admission" && html`<div class="notice" style="margin-top:12px">Severe acute respiratory illness cases with this classification — not all ${d.label.split(" ")[0]} infections in the community.</div>`}
      </${Panel}></div>
      ${d.signal_type === "sari_admission" && html`<div class="s12"><${NowcastPanel} disease=${id} state=${sp ? sp.split("=")[1] : undefined} title="Nowcast: what the recent weeks will eventually count" /></div>`}
      <div class="s12"><${Panel} title="Backtest skill of the published model" note="Mean absolute error against the best transparent baseline on the same held-out weeks" flush>
        <${Loadable} q=${mm} sk=${html`<${SkRows} n=${3} />`}>${() => champ ? html`<table class="tbl">
          <thead><tr><th>Horizon</th><th class="num">Model MAE</th><th class="num">Skill vs baseline</th><th>Baseline</th><th>Evidence</th></tr></thead>
          <tbody>${skill.map((s) => html`<tr><td>+${s.h} week${s.h === "1" ? "" : "s"}</td><td class="num">${fmt.d1(s.mae)}</td>
            <td class="num">${s.s == null ? "—" : fmt.signed(s.s * 100, fmt.int) + "%"}</td><td class="muted">${(s.b || "—").replace(/_/g, " ")}</td><td><${Status} s=${champ.model.pit_status} /></td></tr>`)}</tbody></table>
          <div style="padding:8px 20px 4px"><${Link} to=${linkWith("/model-lab", { disease: id })} cls="link">Full evidence in the model lab</${Link}></div>`
          : html`<div style="padding:0 20px 8px"><${Empty}>No reviewed model is published for ${d.label}.</${Empty}></div>`}</${Loadable}>
      </${Panel}></div>
    </div>
  </div>`;
}

export default function Disease({ params }) {
  const ds = useApi("/diseases");
  const id = params.id;
  return html`<div class="page">
    <${TitleRow} title="Diseases" sub="Four separately defined surveillance signals. Dengue notifications and SARI cases are not comparable counts and are never mixed." />
    <${Notices} items=${ds.warnings || []} />
    <${Loadable} q=${ds} sk=${html`<${SkRows} n=${4} />`}>${(list) => { const cur = id || (list.find((d) => d.rows) || list[0])?.disease_id; return html`
      <div class="panel" style="margin-bottom:var(--gap)"><table class="tbl cards">
        <thead><tr><th>Disease</th><th>Signal</th><th>Data</th><th class="num">Rows</th><th class="num">Latest week</th><th>Published forecast</th></tr></thead>
        <tbody>${list.map((d) => html`<tr class=${"click" + (d.disease_id === cur ? " sel" : "")} onClick=${() => navigate(linkWith(`/diseases/${d.disease_id}`, { disease: d.disease_id }))}>
          <td><div class="two">${d.label}<div class="s">${d.metric_label}</div></div></td><td class="hide-sm">${d.signal_type.replace(/_/g, " ")}</td>
          <td class="sub">${d.data_status === "observed" ? html`<${Sem} k="OBSERVED" />` : d.data_status === "synthetic" ? html`<${Sem} k="SYNTHETIC" />` : html`<span class="pill">No data</span>`}</td>
          <td class="num hide-sm">${fmt.int(d.rows)}</td><td class="num">${fdate(d.latest_week)}</td>
          <td class="hide-sm">${d.public_forecast ? html`<span class="idc">${d.public_forecast.slice(0, 22)}</span>` : html`<span class="muted">None</span>`}</td></tr>`)}</tbody></table></div>
      ${cur && html`<h2 style="margin-bottom:12px">${list.find((d) => d.disease_id === cur)?.label}</h2><${Detail} id=${cur} list=${list} key=${cur} />`}`; }}</${Loadable}>
  </div>`;
}
