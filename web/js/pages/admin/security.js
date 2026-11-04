// Access & audit — question: "Who can do what, under which licenses, and what has been changed by whom?"
import { html, useState } from "../../../vendor/preact-htm.js";
import { useApi, setQuery } from "../../api.js";
import { useSession } from "../../app.js";
import { Panel, TitleRow, Loadable, Link, Empty, Tabs, Avatar, Confirm, ftime, fmt, label, SkRows, ErrorBox } from "../../ui.js";
import { useAction } from "./_shared.js";

function Audit({ q }) {
  const action = q.get("action") || "", actor = q.get("actor") || "", target = q.get("target") || "";
  const offset = +(q.get("offset") || 0);
  const lim = 50;
  const r = useApi(`/admin/audit?limit=${lim}&offset=${offset}${action ? `&action=${encodeURIComponent(action)}` : ""}${actor ? `&actor_id=${actor}` : ""}${target ? `&target_id=${encodeURIComponent(target)}` : ""}`);
  const [open, setOpen] = useState(null);
  const total = r.meta?.total ?? 0;
  return html`<div class="filters">
      <input type="search" aria-label="Action prefix" placeholder="Action prefix, e.g. model." value=${action} onChange=${(e) => setQuery({ action: e.target.value || null, offset: null })} />
      <select aria-label="Actor" value=${actor} onChange=${(e) => setQuery({ actor: e.target.value || null, offset: null })}><option value="">Any actor</option>
        ${["u_viewer", "u_operator", "u_scientist", "u_reviewer", "u_admin"].map((u) => html`<option value=${u}>${u}</option>`)}</select>
      <input type="search" aria-label="Target id" placeholder="Target id" value=${target} onChange=${(e) => setQuery({ target: e.target.value || null, offset: null })} />
    </div>
    <${Panel} title="Audit log" note=${total ? `${offset + 1}–${Math.min(offset + lim, total)} of ${fmt.int(total)} · immutable, secrets redacted` : "immutable, secrets redacted"} flush>
      <${Loadable} q=${r} sk=${html`<${SkRows} n=${8} />`}>${(rows) => rows.length ? html`<div class="tablewrap"><table class="tbl cards">
        <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Target</th><th>Change</th><th>Reason</th></tr></thead>
        <tbody>${rows.map((e) => html`<tr class="click" onClick=${() => setOpen(open === e.event_id ? null : e.event_id)}>
          <td class="sub">${ftime(e.ts)}</td><td>${e.actor_id} <span class="small muted">${label(e.actor_role)}</span></td><td class="idc">${e.action}</td>
          <td class="hide-sm"><span class="small muted">${e.target_type}</span> <span class="idc">${(e.target_id || "").slice(0, 22)}</span></td>
          <td class="hide-sm small">${e.old_version || e.new_version ? `${e.old_version ?? "—"} → ${e.new_version ?? "—"}` : ""}</td><td class="hide-sm small">${e.reason || ""}</td></tr>
          ${open === e.event_id && html`<tr><td colspan="6" style="height:auto;padding:10px 20px;background:var(--raised)"><div class="mono" style="font-size:12px;white-space:pre-wrap">${JSON.stringify(e.metadata, null, 2)}</div>
            <div class="small muted">request ${e.request_id || "—"} · event ${e.event_id}</div></td></tr>`}`)}</tbody></table></div>
        <div class="row" style="padding:12px 20px 4px"><span class="spacer"></span><button class="btn sm" disabled=${offset === 0} onClick=${() => setQuery({ offset: Math.max(0, offset - lim) || null })}>Previous</button>
          <button class="btn sm" disabled=${offset + lim >= total} onClick=${() => setQuery({ offset: offset + lim })}>Next</button></div>`
        : html`<div style="padding:0 20px"><${Empty}>No audit event matches.</${Empty}></div>`}</${Loadable}>
    </${Panel}>`;
}

function Access() {
  const r = useApi("/admin/security");
  const { me } = useSession();
  const [chg, setChg] = useState(null);
  const a = useAction();
  return html`<${Loadable} q=${r} sk=${html`<${SkRows} n=${6} />`}>${(d) => html`<div class="grid">
    <div class="s5"><${Panel} title="Users" note="Roles are enforced by the API on every request" flush><table class="tbl"><tbody>${d.users.map((u) => html`<tr>
      <td><div class="row"><${Avatar} role=${u.role} name=${u.display_name} /><div class="two">${u.display_name}<div class="s idc">${u.user_id}</div></div></div></td>
      <td class="num"><label class="sr" for=${"role-" + u.user_id}>Role of ${u.display_name}</label><select id=${"role-" + u.user_id} value=${u.role} disabled=${u.user_id === me?.user_id}
        title=${u.user_id === me?.user_id ? "You cannot change your own role" : ""} onChange=${(e) => setChg({ u, role: e.target.value })}>${d.roles.map((x) => html`<option value=${x}>${label(x)}</option>`)}</select></td></tr>`)}</tbody></table></${Panel}></div>
    <div class="s7"><${Panel} title="Permission matrix" flush><div class="tablewrap"><table class="tbl dense"><thead><tr><th>Permission</th>${d.roles.map((x) => html`<th class="num">${label(x)}</th>`)}</tr></thead>
      <tbody>${Object.entries(d.permissions).map(([p, roles]) => html`<tr><td class="idc">${p}</td>${d.roles.map((x) => html`<td class="num">${roles.includes(x) ? "●" : html`<span class="faint">·</span>`}</td>`)}</tr>`)}</tbody></table></div></${Panel}></div>
    <div class="s8"><${Panel} title="Source licenses and sensitivity" flush><div class="tablewrap"><table class="tbl dense"><thead><tr><th>Source</th><th>Sensitivity</th><th>License and attribution</th></tr></thead>
      <tbody>${d.licenses.map((l) => html`<tr><td><${Link} to=${`/admin/sources/${l.source_id}`}>${l.display_name}</${Link}></td><td><span class=${"pill " + (l.pii_class === "restricted_personal" ? "warn" : l.pii_class === "synthetic" ? "syn" : "")}>${label(l.pii_class)}</span></td>
        <td class="small">${l.license_ref}</td></tr>`)}</tbody></table></div></${Panel}></div>
    <div class="s4"><${Panel} title="Secret references"><p class="small muted" style="margin:0 0 8px">${d.secret_store}</p>
      ${d.secret_refs.length ? d.secret_refs.map((s) => html`<div class="idc">${s}</div>`) : html`<${Empty}>No source uses a secret reference.</${Empty}>`}</${Panel}></div>
    <${Confirm} open=${!!chg} busy=${a.busy} title=${chg ? `Change ${chg.u.display_name} to ${label(chg.role)}?` : ""} body="Takes effect on the user's next request." confirm="Change role" reason="Reason (recorded in the audit log)"
      onCancel=${() => setChg(null)} onConfirm=${async (reason) => { const x = await a.run(`/admin/security/users/${chg.u.user_id}`, { method: "PATCH", body: { role: chg.role, reason } }, "Role changed"); setChg(null); if (x) r.reload(); }} />
  </div>`}</${Loadable}>`;
}

export default function Security({ q }) {
  const { can } = useSession();
  const tab = q.get("tab") || (can("admin.security") ? "access" : "audit");
  return html`<div class="page">
    <${TitleRow} title="Access & audit" sub="Server-side role permissions, data licenses, secret references (names only) and the immutable audit trail." />
    <${Tabs} tabs=${[["access", "Access"], ["audit", "Audit log"]]} value=${tab} onChange=${(t) => setQuery({ tab: t })} />
    ${tab === "access" ? (can("admin.security") ? html`<${Access} />` : html`<${ErrorBox} error=${{ status: 403 }} />`)
      : can("audit.read") ? html`<${Audit} q=${q} />` : html`<${ErrorBox} error=${{ status: 403 }} />`}
  </div>`;
}
