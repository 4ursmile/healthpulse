// Feature detail — question: "Is this definition point-in-time safe, what does it compute and what do its values look like?"
import { html, useState } from "../../../vendor/preact-htm.js";
import { useApi, invalidate } from "../../api.js";
import { Panel, TitleRow, Loadable, Status, Link, Empty, Notices, Confirm, ago, ftime, fdate, fmt, label, SkBlock } from "../../ui.js";
import { useWidth } from "../../charts.js";
import { useAction, Guarded, Checks } from "./_shared.js";
import { lagText } from "./features.js";

export function Histogram({ counts, edges, unit }) {
  const [ref, W] = useWidth();
  const H = 140, pad = { l: 40, r: 12, t: 8, b: 22 };
  const max = Math.max(1, ...counts);
  const iw = Math.max(10, W - pad.l - pad.r), ih = H - pad.t - pad.b, bw = iw / Math.max(1, counts.length);
  const [hv, setHv] = useState(null);
  return html`<div ref=${ref} style="position:relative">${W > 0 && html`<svg class="chart" width=${W} height=${H} role="img" aria-label="Distribution of feature values">
    ${[0, 0.5, 1].map((f) => html`<line x1=${pad.l} x2=${W - pad.r} y1=${pad.t + ih * (1 - f)} y2=${pad.t + ih * (1 - f)} stroke="var(--grid)" />
      <text x=${pad.l - 6} y=${pad.t + ih * (1 - f) + 4} text-anchor="end">${fmt.int(max * f)}</text>`)}
    ${counts.map((c, i) => html`<rect x=${pad.l + i * bw} y=${pad.t + ih - (c / max) * ih} width=${Math.max(1, bw - 1)} height=${(c / max) * ih}
      fill=${hv === i ? "var(--text)" : "var(--chart-1)"} onMouseEnter=${() => setHv(i)} onMouseLeave=${() => setHv(null)} />`)}
    <text x=${pad.l} y=${H - 6}>${fmt.d1(edges[0])}</text><text x=${W - pad.r} y=${H - 6} text-anchor="end">${fmt.d1(edges[edges.length - 1])} ${unit || ""}</text>
  </svg>`}${hv !== null && html`<div class="ctip" style=${`left:${Math.min(W - 180, pad.l + hv * bw)}px;top:0`}>${fmt.d2(edges[hv])}–${fmt.d2(edges[hv + 1])}: ${fmt.int(counts[hv])} rows</div>`}</div>`;
}

export default function Feature({ params }) {
  const list = useApi(`/admin/features?q=${encodeURIComponent(params.id)}`);
  const d0 = (list.data || []).find((x) => x.feature_id === params.id);
  const det = useApi(d0 ? `/admin/features/${params.id}/versions/${d0.version}` : null);
  const [val, setVal] = useState(null);
  const [prev, setPrev] = useState(null);
  const [approve, setApprove] = useState(false);
  const a = useAction(), b = useAction(), c = useAction();
  const validate = async () => { const r = await a.run(`/admin/features/${params.id}/validate`, { body: {} }); if (r) { setVal(r.data); invalidate("/admin/features"); det.reload(); list.reload(); } };
  const preview = async () => { const r = await b.run(`/admin/features/${params.id}/preview`, { body: {} }); if (r) setPrev(r.data); };
  return html`<div class="page">
    <${Loadable} q=${det.data ? det : list} sk=${html`<${SkBlock} h=${160} />`}>${() => {
      const d = det.data;
      if (!d) return list.data ? html`<${Empty}>No feature named ${params.id}.</${Empty}>` : html`<${SkBlock} h=${160} />`;
      const v = val || d.validation_json;
      const pit = v?.checks?.find((x) => x.check === "pit_available_at_le_issue_at");
      return html`
        <${TitleRow} crumb=${html`<${Link} to="/admin/features" cls="link">Feature store</${Link}> / ${d.feature_group}`} title=${d.feature_id}
          sub=${html`${d.description} <span class="muted">· v${d.version} · ${d.state} · owner ${d.owner_id}</span>`}
          right=${html`<button class="btn" disabled=${a.busy} onClick=${validate}>${a.busy ? "Validating…" : "Validate"}</button>
            <button class="btn" disabled=${b.busy} onClick=${preview}>${b.busy ? "Loading…" : "Preview values"}</button>
            ${d.state !== "approved" && html`<${Guarded} perm="feature.write" cls="btn pri" disabled=${d.leakage_status !== "pass"} reason=${`Validation is ${label(d.leakage_status)}; only PIT-validated definitions can be approved`}
              onClick=${() => setApprove(true)}>Approve</${Guarded}>`}`} />
        <${Notices} kind=${d.leakage_status === "leak_blocked" ? "bad" : ""} items=${[
          d.leakage_status === "leak_blocked" ? "Blocked: with its declared lag this feature would read source values released after the forecast issue time. Increase the lag." : null,
          d.leakage_status === "source_missing" ? "Blocked until its upstream source is published; no substitute data is generated." : null,
          d.referenced_by.length && d.state === "approved" ? `Used by ${d.referenced_by.join(", ")}: this version is immutable. Edits create a new version.` : null].filter(Boolean)} />
        <div class="grid">
          <div class="s5 stack">
            <${Panel} title="Definition"><dl class="kv">
              <dt>Template</dt><dd>${d.template}</dd><dt>Window and lag</dt><dd>${lagText(d)}</dd><dt>Grain</dt><dd>${d.grain}</dd>
              <dt>Entity</dt><dd class="mono">${d.entity_keys.join(", ")}</dd><dt>Type · unit</dt><dd>${d.dtype} · ${d.unit || "—"}</dd>
              <dt>Sources</dt><dd>${d.source_refs.join(", ") || "derived"}</dd><dt>Null policy</dt><dd>${d.null_policy.replace(/_/g, " ")}</dd>
              <dt>Privacy</dt><dd>${d.privacy_class}</dd><dt>Parameters</dt><dd class="mono">${Object.entries(d.params).map(([k, x]) => `${k}=${x}`).join(", ")}</dd>
              <dt>Expression hash</dt><dd class="mono">${d.hash.slice(0, 16)}…</dd><dt>Updated</dt><dd>${ftime(d.updated_at)}</dd></dl></${Panel}>
            <${Panel} title="Availability contract"><dl class="kv">${Object.entries(d.availability_json).map(([k, x]) => html`<dt>${label(k)}</dt><dd>${typeof x === "boolean" ? (x ? "yes" : "no") : x ?? "—"}</dd>`)}</dl>
              <p class="small muted" style="margin:12px 0 0">A value is used only if it was released before the forecast issue instant (Wednesday 12:00 UTC of the issue week). Releases without archived vintages fall back to the documented publication lag and are marked PIT unverified.</p></${Panel}>
          </div>
          <div class="s7 stack">
            <${Panel} title="Validation" note=${v ? `${label(v.status)} · ${v.window ? `${fdate(v.window.start)} – ${fdate(v.window.end)}` : ""} · ${v.validated_at ? ago(v.validated_at) : ""}` : "Not validated yet"}>
              ${v ? html`${pit && html`<div class=${"notice " + (pit.passed ? "info" : "bad")} style="margin-bottom:8px"><div><b>Point-in-time check: ${pit.passed ? "pass" : "fail"}</b><br/>${pit.detail}</div></div>`}
                <${Checks} items=${v.checks.filter((x) => x.check !== "pit_available_at_le_issue_at")} />`
                : html`<${Empty} action=${html`<button class="btn sm" onClick=${validate}>Validate</button>`}>Run PIT, cardinality, dtype, null and privacy checks.</${Empty}>`}
            </${Panel}>
            <${Panel} title="Value preview" note=${prev ? `${fmt.int(prev.rows.length)} of capped rows · pilot municipalities · last 52 issue weeks` : "Aggregated municipality-week values only"}>
              ${!prev ? html`<${Empty} action=${html`<button class="btn sm" disabled=${b.busy} onClick=${preview}>Preview</button>`}>Compute this feature for the recent year.</${Empty}>`
                : !prev.rows.length ? html`<${Empty}>${prev.report?.reason || "No values."}</${Empty}>` : html`
                <${Histogram} counts=${prev.histogram.counts} edges=${prev.histogram.edges} unit=${d.unit} />
                <div class="small muted" style="margin:8px 0">Null ${fmt.pct(prev.report.null_rate, 1)} · ${fmt.int(prev.report.approx_rows)} rows on the publication-lag approximation · non-null share ${prev.coverage.map((x) => `${x.municipality_id} ${fmt.pct(x.non_null)}`).join(", ")}</div>
                <div class="tablewrap" style="max-height:300px"><table class="tbl dense"><thead><tr><th>Municipality</th><th>Issue week</th><th class="num">Value</th><th class="num">Inputs released by</th><th class="num">Issue instant</th><th>Approx.</th></tr></thead>
                  <tbody>${prev.rows.slice(0, 60).map((r) => html`<tr><td class="idc">${r.municipality_id}</td><td>${fdate(r.issue_week)}</td>
                    <td class="num">${r[d.feature_id] == null ? html`<span class="faint">—</span>` : fmt.d2(r[d.feature_id])}</td>
                    <td class="num">${r.max_input_available_at ? ftime(r.max_input_available_at) : "—"}</td><td class="num">${ftime(r.issue_at)}</td>
                    <td>${r.pit_approx ? html`<span class="pill warn">Lag rule</span>` : html`<span class="pill">Vintage</span>`}</td></tr>`)}</tbody></table></div>`}
            </${Panel}>
          </div>
        </div>
        <${Confirm} open=${approve} busy=${c.busy} title=${`Approve ${d.feature_id} v${d.version}?`} body="Approved versions become immutable and can be used in feature sets." confirm="Approve"
          reason="Approval note" onCancel=${() => setApprove(false)} onConfirm=${async (reason) => {
            const r = await c.run(`/admin/features/${d.feature_id}/approve`, { body: { version: d.version, reason } }, "Feature approved");
            if (r) { setApprove(false); det.reload(); list.reload(); } }} />`;
    }}</${Loadable}>
  </div>`;
}
