// Role sign-in for the local pilot: seeded demo accounts, one per role. Roles are enforced by the API, not by this page.
import { html } from "../../vendor/preact-htm.js";
import { useApi, setToken, navigate } from "../api.js";
import { Avatar, Loadable, label, Notices, TitleRow } from "../ui.js";

const WHAT = {
  viewer: "Read aggregated sources, forecasts and model metrics.",
  data_operator: "Configure connectors, run and retry ingestion, quarantine releases.",
  data_scientist: "Define features, build PIT datasets, train and propose models, run scenarios.",
  epi_reviewer: "Clinical sign-off on models, review alerts and scenarios.",
  platform_admin: "Platform sign-off, deploy and roll back models, users, settings.",
};

export default function Login() {
  const acc = useApi("/auth/demo-accounts");
  return html`<div class="login">
    <${TitleRow} title="Choose a role" sub="Local pilot accounts. Each role sees and can change only what the server allows it to." />
    <${Notices} kind="info" items=${["Demo roles exist only in the local environment; production uses your organisation's identity provider."]} />
    <${Loadable} q=${acc}>${(rows) => html`
      <button class="acct" onClick=${() => { setToken(null); navigate("/overview"); }}><${Avatar} role="anonymous" name="Public" />
        <span style="flex:1"><b>Public viewer</b><div class="small muted">No sign-in. Product screens only.</div></span></button>
      ${rows.map((u) => html`<button class="acct" onClick=${() => { setToken(u.token); navigate(u.role === "viewer" ? "/overview" : "/admin"); }}>
        <${Avatar} role=${u.role} name=${u.display_name} /><span style="flex:1"><b>${u.display_name}</b><div class="small muted">${WHAT[u.role]}</div></span>
        <span class="pill">${label(u.role)}</span></button>`)}`}</${Loadable}>
  </div>`;
}
