// Catalog & quality — question: "What data exists at each layer, what failed its gates, and where did a published number come from?"
import { html, useState } from "../../../vendor/preact-htm.js";
import { useApi, setQuery, navigate } from "../../api.js";
import { Panel, TitleRow, Loadable, Status, Link, Id, Empty, Tabs, Drawer, ago, ftime, fmt, label, SkRows } from "../../ui.js";

const KIND_ORDER = ["source_vintage", "feature_set", "training_dataset", "training_run", "model", "forecast", "simulation"];
const KIND_LABEL = { source_vintage: "Source vintages", feature_set: "Feature set", training_dataset: "Training dataset", training_run: "Training run",
  model: "Model", forecast: "Forecast", simulation: "Simulation" };

function CatalogTab() {
  const q = useApi("/admin/catalog");
  const [schema, setSchema] = useState(null);
  return html`<${Loadable} q=${q} sk=${html`<${SkRows} n=${8} />`}>${(d) => html`<div class="stack">
    <${Panel} title="Silver tables" note="Canonical typed tables; vintaged tables keep every source release for point-in-time replay" flush>
      <div class="tablewrap"><table class="tbl cards"><thead><tr><th>Table</th><th>Grain</th><th>Natural key</th><th>Mode</th><th class="num">Vintages</th><th class="num">Current rows</th><th>Sources</th><th class="num">Last published</th></tr></thead>
        <tbody>${d.silver.map((t) => html`<tr class="click" onClick=${() => setSchema(t)}>
          <td><span class="idc">${t.table}</span></td><td class="sub muted">${t.grain}</td><td class="hide-sm mono" style="font-size:12px">${t.keys.join(" + ")}</td>
          <td class="hide-sm">${t.mode}</td><td class="num">${fmt.int(t.vintages)}</td><td class="num">${t.rows_current ? fmt.int(t.rows_current) : html`<span class="faint">—</span>`}</td>
          <td class="hide-sm muted">${t.sources.join(", ") || "none"}</td><td class="num hide-sm">${t.last_published ? ago(t.last_published) : "—"}</td></tr>`)}</tbody></table></div>
    </${Panel}>
    <div class="grid">
      <div class="s4"><${Panel} title="Feature sets" note="Gold" flush>${d.gold.feature_sets.length ? html`<table class="tbl dense"><tbody>${d.gold.feature_sets.map((f) => html`<tr>
        <td><${Link} to="/admin/feature-sets" cls="idc">${f.feature_set_id}@${f.version}</${Link}><div class="small muted">${label(f.disease_id)}</div></td><td class="num"><${Status} s=${f.status} /></td></tr>`)}</tbody></table>`
        : html`<div style="padding:0 20px"><${Empty}>No feature set.</${Empty}></div>`}</${Panel}></div>
      <div class="s4"><${Panel} title="Training datasets" note="Gold, immutable" flush>${d.gold.training_datasets.length ? html`<table class="tbl dense"><tbody>${d.gold.training_datasets.map((x) => html`<tr>
        <td><${Link} to=${`/admin/datasets/${x.dataset_id}`}>${x.name}</${Link}><div class="small muted">${fmt.int(x.row_count)} rows · ${ago(x.created_at)}</div></td><td class="num"><${Status} s=${x.pit_status} /></td></tr>`)}</tbody></table>`
        : html`<div style="padding:0 20px"><${Empty}>No dataset.</${Empty}></div>`}</${Panel}></div>
      <div class="s4"><${Panel} title="Forecasts" note="Latest 20, immutable" flush>${d.gold.forecasts.length ? html`<table class="tbl dense"><tbody>${d.gold.forecasts.slice(0, 8).map((x) => html`<tr class="click" onClick=${() => setQuery({ tab: "lineage", forecast_id: x.forecast_id, run_id: null })}>
        <td><span class="idc">${x.forecast_id.slice(0, 22)}…</span><div class="small muted">${x.model_id}@${x.model_version} · ${ago(x.created_at)}</div></td><td class="num">${x.is_public ? html`<span class="pill ok">Public</span>` : html`<span class="pill">Internal</span>`}</td></tr>`)}</tbody></table>`
        : html`<div style="padding:0 20px"><${Empty}>No forecast.</${Empty}></div>`}</${Panel}></div>
    </div>
    <${Drawer} open=${!!schema} title=${schema ? schema.table : ""} onClose=${() => setSchema(null)}>${schema && html`
      <dl class="kv"><dt>Grain</dt><dd>${schema.grain}</dd><dt>Key</dt><dd class="mono">${schema.keys.join(" + ")}</dd><dt>Mode</dt><dd>${schema.mode}</dd><dt>Privacy</dt><dd>${schema.privacy}</dd></dl>
      <table class="tbl dense" style="margin-top:16px"><thead><tr><th>Column</th><th>Type</th></tr></thead><tbody>${Object.entries(schema.schema).map(([c, t]) => html`<tr><td class="idc">${c}</td><td class="muted">${t}</td></tr>`)}</tbody></table>`}
    </${Drawer}></div>`}</${Loadable}>`;
}

function QualityTab() {
  const q = useApi("/admin/quality");
  const [sev, setSev] = useState("all");
  return html`<${Loadable} q=${q} sk=${html`<${SkRows} n=${6} />`}>${(d) => {
    const inc = d.incidents.filter((i) => sev === "all" || i.severity === sev);
    return html`<div class="grid">
      <div class="s8"><${Panel} title="Quality incidents" note=${`${d.incidents.filter((i) => i.severity === "hard").length} hard (quarantine) · ${d.incidents.filter((i) => i.severity === "soft").length} soft (warning)`}
        right=${html`<select aria-label="Severity" value=${sev} onChange=${(e) => setSev(e.target.value)}><option value="all">All severities</option><option value="hard">Hard</option><option value="soft">Soft</option></select>`} flush>
        ${inc.length ? html`<div class="tablewrap"><table class="tbl cards"><thead><tr><th>Gate</th><th>Source · table</th><th>Severity</th><th>Detail</th><th class="num">Retrieved</th></tr></thead>
          <tbody>${inc.map((i) => html`<tr><td>${label(i.gate)}</td><td class="muted"><${Link} to=${`/admin/sources/${i.source_id}`}>${i.source_id}</${Link}> · ${i.table}</td>
            <td><span class=${"pill " + (i.severity === "hard" ? "bad" : "warn")}>${i.severity === "hard" ? "Hard" : "Soft"}</span>${i.published ? "" : html` <span class="pill">Not published</span>`}</td>
            <td class="sub" style="max-width:420px">${i.detail}</td><td class="num hide-sm">${ftime(i.retrieved_at)}</td></tr>`)}</tbody></table></div>`
          : html`<div style="padding:0 20px"><${Empty}>No failed gates.</${Empty}></div>`}
      </${Panel}></div>
      <div class="s4"><${Panel} title="Stale or quarantined sources" flush>${d.stale_sources.length ? html`<table class="tbl dense"><tbody>${d.stale_sources.map((s) => html`<tr>
        <td><${Link} to=${`/admin/sources/${s.source_id}`}>${s.source_id}</${Link}><div class="small muted">last success ${ago(s.last_success_at)}</div></td><td class="num"><${Status} s=${s.state} /></td></tr>`)}</tbody></table>`
        : html`<div style="padding:0 20px"><${Empty}>Every published source is within its freshness threshold.</${Empty}></div>`}</${Panel}></div>
    </div>`;
  }}</${Loadable}>`;
}

function LineageTab({ q }) {
  const fid = q.get("forecast_id"), rid = q.get("run_id");
  const fc = useApi("/admin/forecasts");
  const sims = useApi("/admin/simulation-audit");
  const lin = useApi(fid || rid ? `/admin/lineage?${rid ? `run_id=${rid}` : `forecast_id=${fid}`}` : null);
  return html`<div class="stack">
    <div class="filters" style="margin:0">
      <label class="small muted" for="lin-f">Forecast</label>
      <select id="lin-f" value=${fid || ""} onChange=${(e) => setQuery({ forecast_id: e.target.value || null, run_id: null })}>
        <option value="">Choose a forecast</option>${(fc.data || []).map((f) => html`<option value=${f.forecast_id}>${f.forecast_id} · ${f.is_public ? "public" : "internal"}</option>`)}</select>
      <label class="small muted" for="lin-s">or simulation run</label>
      <select id="lin-s" value=${rid || ""} onChange=${(e) => setQuery({ run_id: e.target.value || null, forecast_id: null })}>
        <option value="">Choose a run</option>${(sims.data || []).map((s) => html`<option value=${s.run_id}>${s.name || s.run_id} · ${s.run_id.slice(0, 14)}…</option>`)}</select>
    </div>
    ${!(fid || rid) ? html`<${Empty}>Choose a forecast or a simulation run to trace it back to its source releases.</${Empty}>` : html`<${Loadable} q=${lin} sk=${html`<${SkRows} n=${3} />`}>${(g) => {
      const by = Object.fromEntries(KIND_ORDER.map((k) => [k, g.nodes.filter((n) => n.kind === k)]));
      return html`<${Panel} title="Provenance chain" note="Every published number resolves to immutable artifacts and checksummed source releases">
        <div class="chain" style="margin-bottom:16px">${KIND_ORDER.filter((k) => by[k].length).map((k) => html`<div class="lk"><span class="k">${KIND_LABEL[k]}</span>
          <span class="v" title=${by[k].map((n) => n.id).join("\n")}>${by[k].length > 1 ? `${by[k].length} releases` : by[k][0].label}</span></div>`)}</div>
        ${KIND_ORDER.filter((k) => by[k].length).map((k) => html`<h3 style="margin-top:16px">${KIND_LABEL[k]}</h3>
          <table class="tbl dense" style="margin:4px -20px 0;width:calc(100% + 40px)"><tbody>${by[k].map((n) => html`<tr>
            <td><${Id} id=${n.id} n=${30} /></td>
            <td class="muted small">${Object.entries(n).filter(([kk, v]) => !["id", "kind", "label"].includes(kk) && v !== null && v !== undefined).map(([kk, v]) =>
              `${label(kk)} ${typeof v === "boolean" ? (v ? "yes" : "no") : kk.includes("sha") || kk === "checksum" ? String(v).slice(0, 12) + "…" : kk.endsWith("_at") ? ftime(v) : v}`).join(" · ")}</td>
            ${k === "model" ? html`<td class="num"><${Link} to=${`/admin/registry/${n.id.split("@")[0]}/${n.id.split("@")[1]}`} cls="link">Open</${Link}></td>`
              : k === "training_run" ? html`<td class="num"><${Link} to=${`/admin/training-runs/${n.id}`} cls="link">Open</${Link}></td>`
              : k === "training_dataset" ? html`<td class="num"><${Link} to=${`/admin/datasets/${n.id}`} cls="link">Open</${Link}></td>` : html`<td></td>`}</tr>`)}</tbody></table>`)}
      </${Panel}>`;
    }}</${Loadable}>`}
  </div>`;
}

export default function Catalog({ q }) {
  const tab = q.get("tab") || (location.pathname.endsWith("/quality") ? "quality" : "catalog");
  return html`<div class="page">
    <${TitleRow} title="Catalog & quality" sub="Bronze snapshots are immutable; Silver tables keep source vintages; Gold artifacts are versioned and never overwritten." />
    <${Tabs} tabs=${[["catalog", "Catalog"], ["quality", "Quality"], ["lineage", "Lineage"]]} value=${tab} onChange=${(t) => setQuery({ tab: t })} />
    ${tab === "catalog" ? html`<${CatalogTab} />` : tab === "quality" ? html`<${QualityTab} />` : html`<${LineageTab} q=${q} />`}
  </div>`;
}
