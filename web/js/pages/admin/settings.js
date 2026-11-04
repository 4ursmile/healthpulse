// Settings — compute limits and retention (editable by platform admins); environment facts are read-only.
import { html, useState, useEffect } from "../../../vendor/preact-htm.js";
import { api, useApi } from "../../api.js";
import { Panel, TitleRow, Loadable, fmt, SkBlock, Seg, Icon, toast } from "../../ui.js";
import { useAction, Guarded, Field } from "./_shared.js";

const SECTIONS = [
  { title: "Workers", desc: "How many jobs run at once and how much CPU a training run may take. Applied by the worker supervisor on its next claim.",
    keys: [["worker_concurrency", "Concurrent jobs per worker", 1, 32], ["training_max_cpu", "Max CPU threads per training run", 1, 8], ["training_timeout_minutes", "Training timeout (minutes)", 1, 240]] },
  { title: "Simulation", desc: "Upper bound on Monte Carlo trials per scenario run, to keep runs bounded in time and memory.",
    keys: [["max_simulation_draws", "Max trials per run", 10, 100000]] },
  { title: "Retention and previews", desc: "Bronze snapshots referenced by a published forecast or saved scenario are never deleted, whatever the retention.",
    keys: [["retention_days_bronze", "Bronze retention (days)", 30, 36500], ["preview_row_cap", "Preview row cap", 10, 1000]] },
];

// AI analyst: provider / model / effort / on-off. Keys stay in .env; the API only reports whether each one is present.
function AiSettings() {
  const q = useApi("/admin/ai/llm");
  const [v, set] = useState(null);
  const [models, setModels] = useState(null);
  const [test, setTest] = useState(null), [testing, setTesting] = useState(false);
  const a = useAction();
  useEffect(() => { if (q.data) { const s = q.data.settings, c = q.data.config;
    set({ enabled: s.enabled ?? true, provider: s.provider || "auto", model: s.model || c.model || "", effort: s.effort || c.effort || "medium", base_url: s.base_url || "" }); } }, [q.data]);
  const loadModels = async () => { try { const r = await api("/admin/ai/llm/models"); setModels(r.data); } catch (e) { toast(e.message, "bad"); } };
  if (q.error) return null; // not an admin
  const c = q.data?.config;
  const prov = v?.provider === "auto" ? c?.provider : v?.provider;
  const list = models || q.data?.suggested?.[prov] || [];
  const body = () => ({ ...v, model: v.model || null, base_url: v.base_url || null });
  return html`<div class="setrow"><div class="setlbl"><h2>AI analyst</h2><p class="subline">The model behind the Viet Nam briefs, the simulator analyst and the sandbox advisor.
      API keys stay in <code>.env</code> and are never stored here. When the AI is off, unconfigured or failing, every screen falls back to the rule-based analyst — nothing breaks.</p></div>
    <div class="setpanel">${!v ? html`<${SkBlock} h=${160} />` : html`<div class="stack" style="gap:14px">
      <div class="row wrap" style="gap:8px">
        <span class=${"pill " + (c.available ? "ok" : c.configured ? "warn" : "")}>${c.available ? "AI available" : !c.enabled ? "AI off — rule-based" : !c.configured ? "Not configured — rule-based" : "Cooling down after an error"}</span>
        <span class="small muted">Keys: Anthropic ${c.keys.anthropic ? "✓ set" : "— missing"} · OpenAI-compatible ${c.keys.openai ? "✓ set" : "— missing"} · active ${c.provider}${c.model ? ` · ${c.model}` : ""} (from ${c.source})</span>
        ${c.health?.error && html`<span class="small" style="color:var(--negative)">last error: ${c.health.error}</span>`}
      </div>
      <label class="row" style="gap:8px"><input type="checkbox" checked=${v.enabled} onChange=${(e) => set({ ...v, enabled: e.target.checked })} />Use the AI (turn off to force rule-based analysis everywhere)</label>
      <div class="fgrid">
        <${Field} label="Provider" id="ai-prov"><${Seg} label="Provider" value=${v.provider} onChange=${(p) => { set({ ...v, provider: p, model: p === "anthropic" ? "claude-opus-5-5" : p === "openai" ? "" : v.model }); setModels(null); }}
          options=${[["auto", "Auto"], ["anthropic", "Claude"], ["openai", "OpenAI-compatible"]]} /></${Field}>
        <${Field} label="Effort (Claude)" id="ai-eff" hint="higher = deeper analysis, slower and costlier"><${Seg} label="Effort" value=${v.effort} onChange=${(e) => set({ ...v, effort: e })} options=${q.data.efforts.map((x) => [x, x])} /></${Field}>
        <${Field} label="Model" id="ai-model" hint=${models ? `${models.length} models available to this key` : "type an id or load the list from the provider"}>
          <div class="row" style="gap:6px"><input id="ai-model" list="ai-models" style="flex:1" value=${v.model} placeholder=${prov === "anthropic" ? "claude-opus-5-5" : "model id"} onInput=${(e) => set({ ...v, model: e.target.value.trim() })} />
            <datalist id="ai-models">${list.map((m) => html`<option value=${m} />`)}</datalist>
            <button class="btn sm" onClick=${loadModels} title="List models from the provider"><${Icon} n="refresh" s=${13} />List</button></div></${Field}>
        ${prov === "openai" && html`<${Field} label="Base URL" id="ai-base" hint="any OpenAI-compatible server (vLLM, Ollama, Azure…)"><input id="ai-base" value=${v.base_url} placeholder="https://api.openai.com/v1" onInput=${(e) => set({ ...v, base_url: e.target.value.trim() })} /></${Field}>`}
      </div>
      <div class="row wrap" style="gap:8px">
        <button class="btn" disabled=${testing} onClick=${async () => { setTesting(true); setTest(null); try { const r = await api("/admin/ai/llm/test", { method: "POST" }); setTest(r.data); q.reload(); } catch (e) { toast(e.message, "bad"); } setTesting(false); }}>${testing ? "Testing…" : "Test saved setting"}</button>
        ${test && html`<span class=${"small " + (test.ok ? "" : "muted")}>${test.ok ? `✓ replied “${test.reply}” in ${fmt.int(test.ms)} ms` : `✗ ${test.error} — screens will use the rule-based analyst`}</span>`}
        <span class="spacer"></span>
        <${Guarded} perm="admin.settings" cls="btn ghost" busy=${a.busy} onClick=${async () => { const r = await a.run("/admin/ai/llm", { method: "DELETE" }, "Reset to .env"); if (r) q.reload(); }}>Reset to .env</${Guarded}>
        <${Guarded} perm="admin.settings" cls="btn pri" busy=${a.busy} onClick=${async () => { const r = await a.run("/admin/ai/llm", { method: "PUT", body: body() }, "AI settings saved"); if (r) q.reload(); }}>Save</${Guarded}>
      </div></div>`}</div></div>`;
}

export default function Settings() {
  const q = useApi("/admin/settings");
  const [v, set] = useState(null);
  const a = useAction();
  useEffect(() => { if (q.data) set({ ...q.data.values }); }, [q.data]);
  return html`<div class="page" style="max-width:1200px">
    <${TitleRow} title="Settings" sub="Platform limits for this workspace. Every change is audited." />
    <${Loadable} q=${q} sk=${html`<${SkBlock} h=${300} />`}>${(d) => v && html`<div class="stack">
      ${SECTIONS.map((s) => html`<div class="setrow"><div class="setlbl"><h2>${s.title}</h2><p class="subline">${s.desc}</p></div>
        <div class="setpanel"><div class="fgrid">${s.keys.map(([k, l, mn, mx]) => html`<${Field} label=${l} id=${"st-" + k} hint=${`${fmt.int(mn)} – ${fmt.int(mx)}`}>
          <input id=${"st-" + k} type="number" min=${mn} max=${mx} value=${v[k]} onInput=${(e) => set({ ...v, [k]: +e.target.value })} /></${Field}>`)}</div>
          <div class="row"><span class="spacer"></span><${Guarded} perm="admin.settings" cls="btn pri" busy=${a.busy}
            onClick=${async () => { const body = Object.fromEntries(s.keys.map(([k]) => [k, v[k]])); const r = await a.run("/admin/settings", { method: "PUT", body }, "Saved"); if (r) q.reload(); }}>Save</${Guarded}></div></div></div>`)}
      <${AiSettings} />
      <div class="setrow"><div class="setlbl"><h2>Environment</h2><p class="subline">Set by deployment configuration (environment variables); read-only here.</p></div>
        <div class="setpanel"><dl class="kv"><dt>Environment</dt><dd>${d.environment}</dd><dt>Workspace</dt><dd>${d.workspace}</dd><dt>Offline mode</dt><dd>${d.offline ? "on — network fetches disabled" : "off"}</dd>
          <dt>Pilot municipalities</dt><dd class="mono">${d.pilot.join(", ")}</dd><dt>Approved hosts</dt><dd class="mono" style="line-height:20px">${d.approved_hosts.join(", ")}</dd></dl></div></div>
    </div>`}</${Loadable}>
  </div>`;
}
