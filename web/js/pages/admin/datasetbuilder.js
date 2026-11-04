// Dataset builder — target, feature set, issue range, horizons, split and PIT policy → an immutable dataset job.
import { html, useState, useEffect } from "../../../vendor/preact-htm.js";
import { useApi } from "../../api.js";
import { Panel, TitleRow, Loadable, Status, Link, Notices, JobProgress, label, SkBlock, fmt } from "../../ui.js";
import { useAction, Guarded, Field, newKey } from "./_shared.js";

export default function DatasetBuilder({ q }) {
  const dis = useApi("/diseases");
  const fsq = useApi("/admin/feature-sets");
  const [f, set] = useState({ target: "dengue_reported_cases", feature_set_id: q.get("feature_set_id") || "br_dengue", feature_set_version: q.get("version") ? +q.get("version") : null,
    exclude_features: [], issue_start: "", issue_end: "", horizons: [1, 2, 3, 4], pit_policy: "publication_lag", municipalities: "",
    split: { folds: 4, validation_weeks: 26, test_weeks: 26, embargo_weeks: 5 }, name: "" });
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const a = useAction();
  const targets = (dis.data || []).flatMap((d) => (d.targets || []).map((t) => ({ t, d })));
  const tgt = targets.find((x) => x.t === f.target);
  const sets = (fsq.data || []).filter((s) => !tgt || s.disease_id === tgt.d.disease_id);
  const fs = sets.find((s) => s.feature_set_id === f.feature_set_id && (f.feature_set_version == null || s.version === f.feature_set_version)) || sets[0];
  useEffect(() => { if (fs && (fs.feature_set_id !== f.feature_set_id || f.feature_set_version !== fs.version)) set((x) => ({ ...x, feature_set_id: fs.feature_set_id, feature_set_version: fs.version })); }, [fs?.feature_set_id, fs?.version]);
  const unsafe = fs ? fs.readiness.unsafe.filter((x) => !f.exclude_features.includes(x)) : [];
  const toggle = (k, v) => set({ ...f, [k]: f[k].includes(v) ? f[k].filter((x) => x !== v) : [...f[k], v].sort() });
  const sp = (k) => (e) => set({ ...f, split: { ...f.split, [k]: +e.target.value } });
  const maxH = Math.max(...f.horizons, 1);
  const submit = async () => {
    const body = { ...f, municipalities: f.municipalities ? f.municipalities.split(/[\s,]+/).filter(Boolean) : undefined,
      issue_start: f.issue_start || undefined, issue_end: f.issue_end || undefined, name: f.name || undefined };
    const r = await a.run("/admin/training-datasets", { body, idem: newKey() }, "Dataset build queued");
    if (r) { setJob(r.data.job_id); setResult(null); }
  };
  return html`<div class="page">
    <${TitleRow} crumb=${html`<${Link} to="/admin/datasets" cls="link">Training datasets</${Link}>`} title="Build a training dataset"
      sub="The build materializes every feature point-in-time on an issue-week spine, attaches labels released by the cutoff, and freezes rolling-origin splits." />
    <${Loadable} q=${fsq} sk=${html`<${SkBlock} h=${300} />`}>${() => html`<div class="grid">
      <div class="s7 stack">
        <${Panel} title="Problem and features">
          <div class="fgrid">
            <${Field} label="Target signal" id="db-t" hint=${tgt ? `${tgt.d.label} · ${tgt.d.signal_type.replace(/_/g, " ")}` : ""}>
              <select id="db-t" value=${f.target} onChange=${(e) => set({ ...f, target: e.target.value, feature_set_version: null })}>${targets.map((x) => html`<option value=${x.t}>${label(x.t)}${x.d.data_status === "no_data" ? " (no data)" : ""}</option>`)}</select></${Field}>
            <${Field} label="Feature set" id="db-fs"><select id="db-fs" value=${fs ? `${fs.feature_set_id}@${fs.version}` : ""} onChange=${(e) => { const [i, v] = e.target.value.split("@"); set({ ...f, feature_set_id: i, feature_set_version: +v }); }}>
              ${sets.length ? sets.map((s) => html`<option value=${`${s.feature_set_id}@${s.version}`}>${s.feature_set_id}@${s.version}</option>`) : html`<option value="">No feature set for this disease</option>`}</select></${Field}>
            <${Field} label="Dataset name (optional)" id="db-n"><input id="db-n" type="text" value=${f.name} onInput=${(e) => set({ ...f, name: e.target.value })} /></${Field}>
            <${Field} label="PIT policy" id="db-p" hint=${f.pit_policy === "vintage" ? "Only actually archived releases: release-accurate, but needs historical vintages" : "Documented publication lags where no vintage was archived: dataset is PIT unverified"}>
              <select id="db-p" value=${f.pit_policy} onChange=${(e) => set({ ...f, pit_policy: e.target.value })}><option value="publication_lag">Vintages + publication-lag approximation</option><option value="vintage">Archived vintages only</option></select></${Field}>
          </div>
          ${fs && html`<h3 style="margin-top:4px">Members</h3><p class="small muted" style="margin:4px 0 8px">Untick to exclude. Features blocked by a missing source are excluded automatically; leaking or unapproved members must be excluded.</p>
            <div>${fs.readiness.members.map((m) => html`<label class="check" style="cursor:pointer"><input type="checkbox" disabled=${m.status === "blocked"}
              checked=${m.status !== "blocked" && !f.exclude_features.includes(m.feature_id)} onChange=${() => toggle("exclude_features", m.feature_id)} />
              <span class="idc" style="flex:1">${m.feature_id}</span><span class="small muted">${m.group}</span><${Status} s=${m.status} /></label>`)}</div>`}
        </${Panel}>
        <${Panel} title="Issue range, horizons and split">
          <div class="fgrid">
            <${Field} label="First issue week" id="db-is" hint="Default: 60 weeks after the first case week"><input id="db-is" type="date" value=${f.issue_start} onInput=${(e) => set({ ...f, issue_start: e.target.value })} /></${Field}>
            <${Field} label="Last issue week" id="db-ie" hint="Default: the week after the latest release"><input id="db-ie" type="date" value=${f.issue_end} onInput=${(e) => set({ ...f, issue_end: e.target.value })} /></${Field}>
          </div>
          <div class="field"><span style="font-weight:500">Horizons (weeks ahead)</span><div class="row">${[1, 2, 3, 4].map((h) => html`<label class="row" style="gap:6px"><input type="checkbox" checked=${f.horizons.includes(h)} onChange=${() => toggle("horizons", h)} />+${h}</label>`)}</div></div>
          <div class="fgrid">
            <${Field} label="Rolling-origin folds" id="db-f"><input id="db-f" type="number" min="1" max="10" value=${f.split.folds} onInput=${sp("folds")} /></${Field}>
            <${Field} label="Validation weeks per fold" id="db-v"><input id="db-v" type="number" min="4" max="104" value=${f.split.validation_weeks} onInput=${sp("validation_weeks")} /></${Field}>
            <${Field} label="Test weeks (held out)" id="db-te"><input id="db-te" type="number" min="4" max="104" value=${f.split.test_weeks} onInput=${sp("test_weeks")} /></${Field}>
            <${Field} label="Embargo weeks" id="db-e" hint=${`Must exceed the longest horizon (+${maxH})`}><input id="db-e" type="number" min="0" max="26" value=${f.split.embargo_weeks} onInput=${sp("embargo_weeks")} /></${Field}>
          </div>
          <${Field} label="Municipalities (optional)" id="db-m" hint="IBGE codes separated by commas; default is the pilot scope"><input id="db-m" type="text" class="mono" value=${f.municipalities} onInput=${(e) => set({ ...f, municipalities: e.target.value })} /></${Field}>
        </${Panel}>
      </div>
      <div class="s5 stack">
        <${Panel} title="Review and build">
          <dl class="kv"><dt>Target</dt><dd>${label(f.target)}</dd><dt>Feature set</dt><dd class="mono">${fs ? `${fs.feature_set_id}@${fs.version}` : "—"}</dd>
            <dt>Members used</dt><dd>${fs ? fmt.int(fs.readiness.members.filter((m) => m.status !== "blocked" && !f.exclude_features.includes(m.feature_id)).length) : "—"}</dd>
            <dt>Horizons</dt><dd>${f.horizons.map((h) => `+${h}`).join(", ") || "none"}</dd><dt>Split</dt><dd>${f.split.folds} folds × ${f.split.validation_weeks} wk, ${f.split.test_weeks} wk test, ${f.split.embargo_weeks} wk embargo</dd></dl>
          <div style="margin-top:12px"><${Notices} kind="bad" items=${[unsafe.length ? `Exclude or approve before building: ${unsafe.join(", ")}` : null,
            f.split.embargo_weeks <= maxH ? "Embargo must be longer than the longest horizon; otherwise training labels overlap the evaluation window." : null,
            !f.horizons.length ? "Pick at least one horizon." : null].filter(Boolean)} /></div>
          <div class="row"><span class="spacer"></span><${Guarded} perm="dataset.build" cls="btn pri" busy=${a.busy || (job && !result)} disabled=${!fs || unsafe.length || !f.horizons.length || f.split.embargo_weeks <= maxH}
            reason="Resolve the issues above first" onClick=${submit}>Build dataset</${Guarded}></div>
        </${Panel}>
        ${job && html`<${Panel} title="Build job"><${JobProgress} jobId=${job} onDone=${(j) => setResult(j)} />
          ${result?.result?.dataset_id && html`<div class="row" style="margin-top:12px"><${Status} s=${result.result.pit_status} /><span class="small muted">${fmt.int(result.result.rows)} rows · ${fmt.int(result.result.labeled_rows)} labeled</span><span class="spacer"></span>
            <${Link} to=${`/admin/datasets/${result.result.dataset_id}`} cls="btn sm">Open dataset</${Link}></div>`}</${Panel}>`}
      </div></div>`}</${Loadable}>
  </div>`;
}
