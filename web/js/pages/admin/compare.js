// Compare runs — question: "On matched folds, does one run beat another, and is the comparison even valid?"
import { html, useState } from "../../../vendor/preact-htm.js";
import { useApi } from "../../api.js";
import { Panel, TitleRow, Loadable, Status, Link, Notices, Empty, label, SkRows } from "../../ui.js";
import { MetricSelect, metricFmt, metricLabel } from "./_shared.js";

export default function Compare({ q }) {
  const ids = q.get("ids") || "";
  const r = useApi(ids ? `/admin/experiments/compare?ids=${ids}` : null);
  const [metric, setMetric] = useState("mae");
  const f = metricFmt(metric);
  return html`<div class="page">
    <${TitleRow} crumb=${html`<${Link} to="/admin/experiments" cls="link">Experiments</${Link}>`} title="Compare runs"
      sub="Test-window metrics side by side. Superiority is only claimed when runs share the same folds, target and dataset split."
      right=${html`<${MetricSelect} value=${metric} onChange=${setMetric} />`} />
    ${!ids ? html`<${Empty}>Select two or three completed runs on the Experiments screen.</${Empty}>` : html`<${Loadable} q=${r} sk=${html`<${SkRows} n=${6} />`}>${(d, m, w) => {
      const hs = [...new Set(d.runs.flatMap((x) => Object.values(x.test).flatMap((t) => Object.keys(t || {}))))].sort();
      const recipes = [...new Set(d.runs.flatMap((x) => Object.keys(x.test)))];
      return html`<${Notices} kind=${d.matched_folds ? "info" : ""} items=${d.matched_folds ? ["Runs share identical folds and test window: differences are attributable to the configuration."] : w} />
        <${Panel} title=${`Test ${metricLabel(metric)} by recipe and horizon`} flush><div class="tablewrap"><table class="tbl">
          <thead><tr><th>Recipe</th><th>Run</th>${hs.map((h) => html`<th class="num">+${h} wk</th>`)}</tr></thead>
          <tbody>${recipes.flatMap((rc) => d.runs.filter((x) => x.test[rc]).map((x, i) => html`<tr>
            <td>${i === 0 ? label(rc) : ""}</td><td><${Link} to=${`/admin/training-runs/${x.run_id}`} cls="idc">${x.run_id.slice(0, 16)}…</${Link}> <${Status} s=${x.pit_status} /></td>
            ${hs.map((h) => html`<td class="num">${f(x.test[rc]?.[h]?.[metric])}</td>`)}</tr>`))}</tbody></table></div></${Panel}>`;
    }}</${Loadable}>`}
  </div>`;
}
