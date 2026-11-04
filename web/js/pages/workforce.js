// Workforce — question: "Which sectors employ the formal workforce of this municipality (exposure context, not infection)?"
import { html } from "../../vendor/preact-htm.js";
import { useApi, setQuery, linkWith } from "../api.js";
import { Panel, TitleRow, Notices, Loadable, SkRows, Empty, Link, Sem, Kpis, fmt } from "../ui.js";
import { BarList } from "../charts.js";
import { RegionSelect, useRegions } from "./_product_shared.js";
import { useSession } from "../app.js";

export default function Workforce({ q }) {
  const regions = useRegions();
  const region = q.get("region") || regions.find((r) => r.has_disease_data)?.municipality_id;
  const wf = useApi(region ? `/regions/${region}/workforce` : null);
  const { can } = useSession();
  const name = regions.find((r) => r.municipality_id === region)?.name;
  return html`<div class="page">
    <${TitleRow} title="Workforce" sub="Formal workforce by economic sector — an exposure and service-planning context. It is not a sector-specific infection rate and never identifies workers."
      right=${html`<${RegionSelect} value=${region} onChange=${(v) => setQuery({ region: v })} />`} />
    <${Loadable} q=${wf} sk=${html`<${SkRows} n=${5} cols=${2} />`}>${(d, meta, warnings) => d.sectors.length ? html`
      <${Kpis} items=${[{ label: "Formal workers", sem: "OBSERVED", lead: true, value: fmt.int(d.total_formal_workers), sub: `${name} · ${d.year}` },
        { label: "Largest sector share", value: fmt.pct(d.sectors[0].share, 1), sub: d.sectors[0].cnae_label || d.sectors[0].cnae_section }, { label: "Sectors", value: fmt.int(d.sectors.length), sub: "CNAE sections" }]} />
      <div class="grid" style="margin-top:var(--gap)"><div class="s8"><${Panel} title="Workers by sector" note=${html`<${Sem} k="OBSERVED" /> IBGE CEMPRE (SIDRA 6450), salaried workers, ${d.year}`}>
        <${BarList} rows=${d.sectors.map((s) => ({ label: s.cnae_label ? `${s.cnae_section} · ${s.cnae_label}` : s.cnae_section, value: s.formal_workers, sub: s.formal_workers == null ? "suppressed by IBGE" : fmt.pct(s.share, 1) }))} />
      </${Panel}></div><div class="s4"><${Panel} title="How to read this">
        <p class="small" style="margin:0;line-height:18px">${meta.caveat} Outdoor and commuting exposure proxies need a documented construction before they enter the vulnerability index; none is applied here.</p>
      </${Panel}></div></div>` : html`
      <${Notices} items=${warnings} />
      <div class="grid"><div class="s7"><${Panel} title=${`Workforce for ${name || "this municipality"}`}>
        <${Empty} action=${can("source.ingest") ? html`<${Link} to="/admin/sources/ibge_cempre" cls="btn">Ingest from IBGE SIDRA</${Link}>` : html`<span class="small muted">A data operator can import it.</span>`}>
          No sector data has been ingested yet. Run the IBGE CEMPRE source (SIDRA table 6450) — shares are never estimated or substituted.</${Empty}>
        <h3 style="margin:20px 0 8px">What appears here after ingest</h3>
        <dl class="kv"><dt>Workers by sector</dt><dd>Formal workers per CNAE section, with shares of the municipal total.</dd>
          <dt>Largest sectors</dt><dd>Ranked list for targeted, aggregated workplace communication.</dd>
          <dt>Year</dt><dd>Latest CEMPRE reference year available at the time of import.</dd></dl>
      </${Panel}></div>
      <div class="s5"><${Panel} title="Expected file">
        <dl class="kv"><dt>Source</dt><dd>IBGE SIDRA table 6450, Cadastro Central de Empresas (downloaded automatically)</dd>
          <dt>Grain</dt><dd>Municipality × year × CNAE 2.0 section, salaried workers</dd><dt>Privacy</dt><dd>Aggregated counts only; no individual workers</dd></dl>
        <p class="small muted" style="margin:12px 0 0">${meta.caveat || "Sector exposure is contextual; it is not a sector-specific infection rate."} <${Link} to=${linkWith("/data-lab")} cls="link">Source status</${Link}></p>
      </${Panel}></div></div>
      `}</${Loadable}>
  </div>`;
}
