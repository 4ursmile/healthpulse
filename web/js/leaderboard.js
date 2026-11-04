// Forecast leaderboard, ranked the way forecasting hubs (FluSight, Infodengue-Mosqlimate sprint) rank models:
// relative WIS against "same as last week" (persistence = 1.00, lower is better). Simple mode shows a ranked
// "% more accurate" bar per model; Pro adds the raw scores.
import { html } from "../vendor/preact-htm.js";
import { fmt, label } from "./ui.js";
import { usePro } from "./mode.js";

const NAMES = { persistence: "Same as last week", seasonal_naive: "Same week last year", historical_median: "Historical median",
  damped_trend: "Recent trend", glm_poisson: "Poisson GLM", hgb_poisson: "Boosting (counts)", ridge_growth: "Growth regression",
  hgb_growth: "Boosting (growth)", ensemble: "Ensemble" };
export const niceRecipe = (r) => NAMES[r] || label(r);

// Old runs have no WIS: fall back to MAE relative to persistence so the board still ranks something honest.
export function boardFrom(rep) {
  if (rep.leaderboard?.length) return rep.leaderboard;
  const t = Object.fromEntries(Object.entries(rep.recipes || rep.test || {}).map(([k, v]) => [k, v?.test || v]));
  const p = t.persistence || {};
  const hs = Object.keys(p);
  const rows = Object.entries(t).map(([rid, byH]) => {
    const rel = hs.map((h) => (byH?.[h]?.mae != null && p[h]?.mae ? byH[h].mae / p[h].mae : null)).filter((x) => x);
    const mae = hs.map((h) => byH?.[h]?.mae).filter((x) => x != null);
    return { recipe: rid, relative_mae: rel.length ? Math.exp(rel.reduce((a, b) => a + Math.log(b), 0) / rel.length) : null,
      mae: mae.length ? mae.reduce((a, b) => a + b, 0) / mae.length : null };
  }).sort((a, b) => (a.relative_mae ?? 1e9) - (b.relative_mae ?? 1e9));
  return rows.map((r, i) => ({ ...r, rank: i + 1 }));
}

export function Leaderboard({ rep, published, limit }) {
  const pro = usePro();
  const all = boardFrom(rep);
  const score = (r) => r.relative_wis ?? r.relative_mae;
  const byWis = all.some((r) => r.relative_wis != null);
  const rows = (pro || !limit ? all : all.filter((r, i) => i < limit || r.recipe === "persistence" || r.recipe === published)).filter((r) => score(r) != null);
  const best = Math.max(0.0001, ...rows.map((r) => Math.max(0, 1 - score(r))));
  return html`<div class=${"lboard" + (pro ? " pro" : "")}>
    ${pro && html`<div class="lrow hd small muted"><span></span><span></span><span></span><span></span>
      <span class="px">${byWis ? "rel. WIS" : "rel. MAE"}</span><span class="px">MAE</span><span class="px">CV MAE</span><span class="px">80% cov.</span></div>`}
    ${rows.map((r) => {
      const s = score(r), gain = 1 - s, isBase = r.recipe === "persistence";
      return html`<div class=${"lrow" + (r.rank === 1 ? " win" : "") + (isBase ? " base" : "")}>
        <span class="rk num">${r.rank === 1 ? "★" : r.rank}</span>
        <span class="nm">${niceRecipe(r.recipe)}${r.recipe === published ? html` <span class="pill ok">Published</span>` : ""}${isBase ? html` <span class="pill">Reference</span>` : ""}</span>
        <span class="tr" aria-hidden="true"><i style=${`width:${Math.max(0, (gain / best) * 100)}%`}></i></span>
        <span class="gn num">${isBase ? "—" : gain >= 0 ? `${fmt.int(gain * 100)}% better` : `${fmt.int(-gain * 100)}% worse`}</span>
        ${pro && html`<span class="px num muted" title=${byWis ? "relative WIS (persistence = 1)" : "relative MAE (persistence = 1)"}>${s.toFixed(3)}</span>
          <span class="px num muted" title="mean test MAE over horizons">${fmt.d1(r.mae)}</span>
          <span class="px num muted" title="mean rolling-origin CV MAE">${r.cv_mae != null ? fmt.d1(r.cv_mae) : "—"}</span>
          <span class="px num muted" title="80% interval coverage">${r.coverage80 != null ? fmt.pct(r.coverage80) : "—"}</span>`}
      </div>`;
    })}
    <div class="small muted" style="margin-top:8px">${byWis
      ? "Scored with the weighted interval score (WIS), the metric forecasting hubs use: it rewards being close and being honestly uncertain. Compared with “same as last week”, averaged over 1–4 week horizons on held-out weeks."
      : "This run predates WIS scoring, so models are compared on mean absolute error relative to “same as last week”."}</div>
  </div>`;
}

// one plain-language sentence for the top of a page
export function headline(rep, published) {
  const all = boardFrom(rep);
  const r = all.find((x) => x.recipe === published) || all[0];
  if (!r) return null;
  const s = r.relative_wis ?? r.relative_mae;
  if (s == null) return null;
  const pct = Math.round(Math.abs(1 - s) * 100);
  return s < 1 ? `${niceRecipe(r.recipe)} forecasts are ${pct}% more accurate than assuming next week looks like last week.`
    : `${niceRecipe(r.recipe)} does not beat “same as last week” on held-out weeks (${pct}% worse) — treat it as experimental.`;
}
