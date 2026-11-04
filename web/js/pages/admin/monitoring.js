// Model monitoring — question: "Now that outcomes have been released, is the serving model still as accurate as its backtest?"
import { html } from "../../../vendor/preact-htm.js";
import { useApi, setQuery } from "../../api.js";
import { Panel, TitleRow, Loadable, Status, Link, Empty, Kpis, Notices, fmt, label, SkBlock } from "../../ui.js";

export default function Monitoring({ q }) {
  const disease = q.get("disease") || "dengue";
  const r = useApi(`/admin/model-monitoring?disease_id=${disease}`);
  return html`<div class="page">
    <${TitleRow} title="Model monitoring" sub="Realized error on forecasts whose target weeks have since been released, compared with the backtest; feature drift between training and serving inputs."
      right=${html`<select aria-label="Disease" value=${disease} onChange=${(e) => setQuery({ disease: e.target.value })}>${["dengue", "chikungunya", "covid19_sari", "influenza_sari"].map((d) => html`<option value=${d}>${label(d)}</option>`)}</select>`} />
    <${Loadable} q=${r} sk=${html`<${SkBlock} h=${200} />`}>${(rows) => rows.length ? html`<div class="stack">${rows.map((x) => {
      const hs = Object.keys(x.backtest_test || {}).sort();
      const real = Object.fromEntries(x.realized_by_horizon.map((h) => [String(h.horizon), h]));
      const drifted = x.drift.filter((d) => d.psi != null && d.psi > 0.25);
      return html`<div class="stack">
        <div class="row wrap"><h2 class="idc" style="font-size:16px;overflow-wrap:anywhere">${x.model.model_id}@${x.model.version}</h2><${Status} s=${x.deployment.serving_alias} />
          <span class="small muted">${label(x.model.recipe_id)}</span><${Status} s=${x.model.pit_status} /><span class="spacer"></span>
          <${Link} to=${`/admin/registry/${x.model.model_id}/${x.model.version}`} cls="btn sm">Model card</${Link}></div>
        <${Kpis} items=${[
          { label: "Forecasts issued", value: fmt.int(x.forecasts), sub: "by this model version" },
          { label: "Realized forecast rows", value: fmt.int(x.realized_rows), sub: x.realized_rows ? "target week since released" : "no target week released yet" },
          { label: "Label delay", value: x.label_delay_weeks == null ? "—" : fmt.int(x.label_delay_weeks), unit: "weeks", sub: "today vs latest released week" },
          { label: "Features with PSI above 0.25", value: fmt.int(drifted.length), sub: `of ${x.drift.length} monitored` },
        ]} />
        <div class="grid">
          <div class="s7"><${Panel} title="Realized vs backtest" note="Same metric definitions; realized rows are few until outcomes accumulate" flush>
            <table class="tbl"><thead><tr><th>Horizon</th><th class="num">Backtest MAE</th><th class="num">Realized MAE</th><th class="num">Backtest coverage</th><th class="num">Realized coverage</th><th class="num">Realized rows</th></tr></thead>
              <tbody>${hs.map((h) => html`<tr><td>+${h} wk</td><td class="num">${fmt.d1(x.backtest_test[h]?.mae)}</td><td class="num">${real[h] ? fmt.d1(real[h].mae) : html`<span class="faint">—</span>`}</td>
                <td class="num">${fmt.pct(x.backtest_test[h]?.coverage80)}</td><td class="num">${real[h] ? fmt.pct(real[h].coverage80) : html`<span class="faint">—</span>`}</td><td class="num">${fmt.int(real[h]?.n ?? 0)}</td></tr>`)}</tbody></table>
            ${!x.realized_rows && html`<div style="padding:8px 20px 0"><${Empty}>No forecast target week has been released yet; realized error appears once surveillance catches up.</${Empty}></div>`}</${Panel}></div>
          <div class="s5"><${Panel} title="Input drift (PSI)" note="Training distribution vs serving features" flush>
            ${x.drift.length ? html`<table class="tbl dense"><thead><tr><th>Feature</th><th class="num">PSI</th><th>Reading</th></tr></thead><tbody>${x.drift.map((d) => html`<tr>
              <td class="idc">${d.feature}</td><td class="num">${d.psi == null ? "—" : fmt.d2(d.psi)}</td>
              <td>${d.psi == null ? html`<span class="small muted">too few serving rows</span>` : d.psi > 0.25 ? html`<span class="pill warn">Shifted</span>` : d.psi > 0.1 ? html`<span class="pill">Moderate</span>` : html`<span class="pill ok">Stable</span>`}</td></tr>`)}</tbody></table>`
              : html`<div style="padding:0 20px"><${Empty}>No serving features recorded yet.</${Empty}></div>`}</${Panel}></div>
        </div>
        <${Notices} kind="info" items=${x.notes} />
      </div>`; })}</div>` : html`<${Empty} action=${html`<${Link} to="/admin/deployments" cls="btn sm">Deployments</${Link}>`}>No active deployment for ${label(disease)}.</${Empty}>`}</${Loadable}>
  </div>`;
}
