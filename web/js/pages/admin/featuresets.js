// Feature sets — question: "Which pinned feature sets are ready to train, and which members block them?"
import { html, useState } from "../../../vendor/preact-htm.js";
import { useApi, navigate } from "../../api.js";
import { Panel, TitleRow, Loadable, Status, Link, Drawer, Empty, Notices, ago, label, Icon, SkRows } from "../../ui.js";
import { useAction, Guarded, Field } from "./_shared.js";

function Lineage({ fs }) {
  const q = useApi(`/admin/feature-sets/${fs.feature_set_id}/lineage?version=${fs.version}`);
  return html`<${Loadable} q=${q}>${(g) => {
    const srcs = g.nodes.filter((n) => n.kind === "source"), tables = g.nodes.filter((n) => n.kind === "silver_table"), feats = g.nodes.filter((n) => n.kind === "feature");
    return html`<div class="chain" style="margin-bottom:12px"><div class="lk"><span class="k">Sources</span><span class="v">${srcs.length}</span></div>
      <div class="lk"><span class="k">Silver tables</span><span class="v">${tables.length}</span></div><div class="lk"><span class="k">Features</span><span class="v">${feats.length}</span></div>
      <div class="lk"><span class="k">Feature set</span><span class="v">${fs.feature_set_id}@${fs.version}</span></div></div>
      <table class="tbl dense"><thead><tr><th>Silver table</th><th>From sources</th><th>Feeds features</th></tr></thead><tbody>${tables.map((t) => html`<tr>
        <td class="idc">${t.id}</td><td class="muted">${(t.sources || []).join(", ") || html`<span class="pill warn">No published source</span>`}</td>
        <td class="small">${g.edges.filter((e) => e[0] === t.id).map((e) => e[1].split("@")[0]).join(", ")}</td></tr>`)}</tbody></table>`;
  }}</${Loadable}>`;
}

function Create({ open, onClose, done }) {
  const feats = useApi(open ? "/admin/features?state=approved" : null);
  const [f, set] = useState({ feature_set_id: "", disease_id: "dengue", description: "", members: [] });
  const a = useAction();
  const toggle = (id) => set({ ...f, members: f.members.includes(id) ? f.members.filter((x) => x !== id) : [...f.members, id] });
  return html`<${Drawer} open=${open} title="New feature set version" onClose=${onClose}
    footer=${html`<button class="btn" onClick=${onClose}>Cancel</button><${Guarded} perm="feature.write" cls="btn pri" busy=${a.busy} disabled=${!f.feature_set_id || !f.members.length}
      reason="Name the set and pick members" onClick=${async () => { const r = await a.run("/admin/feature-sets", { body: f }, "Feature set saved"); if (r) { done(); onClose(); } }}>Save version</${Guarded}>`}>
    <${Notices} kind="info" items=${["A feature set pins exact feature versions; an identical member list reuses the existing version."]} />
    <div class="fgrid"><${Field} label="Feature set id" id="fs-id"><input id="fs-id" type="text" value=${f.feature_set_id} onInput=${(e) => set({ ...f, feature_set_id: e.target.value.trim() })} /></${Field}>
      <${Field} label="Disease" id="fs-d"><select id="fs-d" value=${f.disease_id} onChange=${(e) => set({ ...f, disease_id: e.target.value })}>${["dengue", "chikungunya", "covid19_sari", "influenza_sari"].map((d) => html`<option value=${d}>${label(d)}</option>`)}</select></${Field}></div>
    <${Field} label="Description" id="fs-ds"><input id="fs-ds" type="text" value=${f.description} onInput=${(e) => set({ ...f, description: e.target.value })} /></${Field}>
    <h3>Members (${f.members.length} selected)</h3>
    <${Loadable} q=${feats}>${(rows) => html`<div>${rows.map((d) => html`<label class="check" style="cursor:pointer">
      <input type="checkbox" checked=${f.members.includes(d.feature_id)} onChange=${() => toggle(d.feature_id)} />
      <div style="flex:1"><span class="idc">${d.feature_id}</span> <span class="small muted">v${d.version} · ${d.feature_group}</span></div><${Status} s=${d.leakage_status} /></label>`)}</div>`}</${Loadable}>
  </${Drawer}>`;
}

export default function FeatureSets() {
  const q = useApi("/admin/feature-sets");
  const [open, setOpen] = useState(false);
  const [lin, setLin] = useState(null);
  return html`<div class="page">
    <${TitleRow} crumb=${html`<${Link} to="/admin/features" cls="link">Feature store</${Link}>`} title="Feature sets"
      sub="Each version pins exact feature versions. Blocked members are excluded from datasets with a reduced-model warning; leaking or unapproved members stop dataset builds."
      right=${html`<${Guarded} perm="feature.write" cls="btn pri" onClick=${() => setOpen(true)}><${Icon} n="plus" s=${14} />New version</${Guarded}>`} />
    <${Loadable} q=${q} sk=${html`<${SkRows} n=${6} />`}>${(rows) => rows.length ? html`<div class="stack">${rows.map((fs) => {
      const r = fs.readiness;
      const cnt = (s) => r.members.filter((m) => m.status === s).length;
      return html`<${Panel} title=${html`<span class="idc" style="font-size:15px">${fs.feature_set_id}@${fs.version}</span> <span class="muted" style="font-weight:400">· ${label(fs.disease_id)}</span>`}
        note=${`${fs.description || ""} · ${ago(fs.created_at)} by ${fs.created_by}`}
        right=${html`<${Status} s=${r.ready ? "ready" : "blocked"} /><button class="btn sm" onClick=${() => setLin(lin === fs.feature_set_id + fs.version ? null : fs.feature_set_id + fs.version)}>Lineage</button>
          <${Guarded} perm="dataset.build" cls="btn sm" onClick=${() => navigate(`/admin/datasets/new?feature_set_id=${fs.feature_set_id}&version=${fs.version}`)}>Build dataset</${Guarded}>`}>
        <div class="small muted" style="margin-bottom:8px">${cnt("pit_safe")} PIT safe · ${cnt("blocked")} blocked by a missing source · ${cnt("leak_blocked")} leak blocked · ${cnt("draft") + cnt("unchecked")} draft or unchecked</div>
        <div class="tablewrap"><table class="tbl dense"><thead><tr><th>Member</th><th>Group</th><th>Readiness</th>${Object.keys(fs.ablation_groups || {}).map((g) => html`<th class="num">${g}</th>`)}</tr></thead>
          <tbody>${r.members.map((m) => html`<tr><td><${Link} to=${`/admin/features/${m.feature_id}`} cls="idc">${m.feature_id}</${Link}> <span class="small muted">v${m.version}</span></td>
            <td class="muted">${m.group}</td><td><${Status} s=${m.status} /></td>
            ${Object.values(fs.ablation_groups || {}).map((ids) => html`<td class="num">${ids.includes(m.feature_id) ? "●" : html`<span class="faint">·</span>`}</td>`)}</tr>`)}</tbody></table></div>
        ${lin === fs.feature_set_id + fs.version && html`<div style="margin-top:16px"><${Lineage} fs=${fs} /></div>`}
      </${Panel}>`; })}</div>` : html`<${Empty}>No feature set yet.</${Empty}>`}</${Loadable}>
    <${Create} open=${open} onClose=${() => setOpen(false)} done=${q.reload} />
  </div>`;
}
