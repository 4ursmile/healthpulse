// Live response — incident command for a real outbreak (or a drill). Key in cases, deaths, vaccinations and tests as they
// arrive; see clusters and their estimated area; draw affected / quarantine / buffer / safe / vaccination zones for REAL or as
// SIMULATED what-ifs; compare baseline / current / plan projections; optimise where teams, vaccine, kits, beds and ambulances
// go; and ask a tool-using AI analyst. Other controllers' entries arrive over a live stream (SSE) within about a second.
import { html, useEffect, useMemo, useRef, useState } from "../../../vendor/preact-htm.js";
import { api, useApi, getToken, setQuery } from "../../api.js";
import { Panel, TitleRow, Icon, Seg, Sem, fmt, fdate, ago, toast, Empty, SkBlock, Kpis } from "../../ui.js";
import { VnMap, HEAT } from "../../vnmap.js";
import { MultiLine, md } from "./_vn.js";
import { Bars } from "../../charts.js";
import { useSession } from "../../app.js";

const KIND_C = { case: "#d6403a", suspected: "#ee9a3a", death: "#1f1f1f", recovered: "#1e7b4f", vaccinated: "#2f70bb", tested: "#8a94a0" };
const KIND_L = { case: "Confirmed case", suspected: "Suspected", death: "Death", recovered: "Recovered", vaccinated: "Vaccinated (doses)", tested: "Tested" };
const ZONE_C = { affected: "#d6403a", quarantine: "#7a3db8", buffer: "#ee9a3a", safe: "#1e7b4f", vaccination: "#2f70bb" };
const ZONE_L = { affected: "Affected", quarantine: "Quarantine", buffer: "Buffer", safe: "Safe zone", vaccination: "Vaccination ring" };
const RES_L = { team: "Response teams", vaccine: "Vaccine doses", test_kit: "Test kits", bed: "Field beds", ambulance: "Ambulances" };
const SAFE_FILL = "#cfe8d6";
const nf = (v) => (v == null ? "—" : Math.abs(v) < 10000 ? fmt.int(v) : fmt.compact(v));
const todayISO = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);

// POST/GET + server-sent events over fetch (EventSource cannot send the Authorization header)
async function sse(url, { method = "GET", body, onEvent, signal }) {
  const h = { Accept: "text/event-stream" };
  if (body) h["Content-Type"] = "application/json";
  if (getToken()) h.Authorization = `Bearer ${getToken()}`;
  const r = await fetch("/api/v1" + url, { method, headers: h, body: body ? JSON.stringify(body) : undefined, signal });
  if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(typeof j?.error?.detail === "string" ? j.error.detail : r.statusText); }
  const rd = r.body.getReader(), dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await rd.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let k;
    while ((k = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, k); buf = buf.slice(k + 2);
      const ev = (block.match(/^event: (.*)$/m) || [])[1] || "message";
      const data = (block.match(/^data: (.*)$/m) || [])[1];
      if (data) onEvent(ev, JSON.parse(data));
    }
  }
}

const KM_LAT = 110.574, kmLon = (lat) => 111.32 * Math.cos((lat * Math.PI) / 180);
function circleGeom(lat, lon, km, n = 64) {
  const ring = [];
  for (let i = 0; i < n; i++) { const t = (i / n) * 2 * Math.PI; ring.push([lon + (km * Math.cos(t)) / kmLon(lat), lat + (km * Math.sin(t)) / KM_LAT]); }
  ring.push(ring[0]);
  return { type: "Polygon", coordinates: [ring] };
}
const distKm = (a, b) => Math.hypot((a.lon - b.lon) * kmLon(a.lat), (a.lat - b.lat) * KM_LAT);
const FC = (features) => ({ type: "FeatureCollection", features });

// ------------------------------------------------------------------------------------------------ map with ops layers
function OpsMap({ an, reports, tool, draft, setDraft, onPoint, onZone, selZone, plan, pulses, onProvince, fly }) {
  const mref = useRef(null), live = useRef({});
  live.current = { tool, draft, setDraft, onPoint, onZone };
  const [ready, setReady] = useState(false);
  const onReady = (m) => {
    mref.current = m;
    const add = (id, type, paint, filter, layout) => m.addLayer({ id, type, source: id.split(":")[0], paint, ...(filter ? { filter } : {}), ...(layout ? { layout } : {}) });
    for (const s of ["zones", "pts", "clusters", "draft"]) m.addSource(s, { type: "geojson", data: FC([]) });
    add("zones:fill", "fill", { "fill-color": ["get", "c"], "fill-opacity": ["case", ["get", "real"], 0.2, 0.08] });
    add("zones:real", "line", { "line-color": ["get", "c"], "line-width": ["case", ["get", "sel"], 3.2, 2] }, ["==", ["get", "real"], true]);
    add("zones:sim", "line", { "line-color": ["get", "c"], "line-width": ["case", ["get", "sel"], 3, 1.6], "line-dasharray": [2, 1.5] }, ["==", ["get", "real"], false]);
    add("clusters:hull", "line", { "line-color": "#d6403a", "line-width": 1, "line-opacity": 0.6, "line-dasharray": [1, 1] });
    add("pts:dot", "circle", { "circle-radius": ["get", "r"], "circle-color": ["get", "c"], "circle-opacity": ["get", "o"], "circle-stroke-color": "#fff", "circle-stroke-width": ["case", ["get", "fresh"], 1.6, 0.6] });
    add("draft:fill", "fill", { "fill-color": ["get", "c"], "fill-opacity": 0.15 }, ["==", ["geometry-type"], "Polygon"]);
    add("draft:line", "line", { "line-color": ["get", "c"], "line-width": 2, "line-dasharray": [1, 1] }, ["!=", ["geometry-type"], "Point"]);
    add("draft:pt", "circle", { "circle-radius": 6, "circle-color": ["get", "c"], "circle-stroke-color": "#fff", "circle-stroke-width": 2 }, ["==", ["geometry-type"], "Point"]);
    m.on("click", (e) => {
      const L = live.current, p = { lat: +e.lngLat.lat.toFixed(6), lon: +e.lngLat.lng.toFixed(6) };
      if (L.tool === "report" || L.tool === "stock") return L.onPoint(p);
      if (L.tool === "circle") {
        if (!L.draft?.center) return L.setDraft({ ...L.draft, center: p, km: 0.5 });
        return L.setDraft({ ...L.draft, km: Math.max(0.2, distKm(L.draft.center, p)), done: true });
      }
      if (L.tool === "polygon") return L.setDraft({ ...L.draft, pts: [...(L.draft?.pts || []), p] });
      if (L.tool === "inspect") {
        const f = m.queryRenderedFeatures(e.point, { layers: ["zones:fill"] })[0];
        if (f) L.onZone(f.properties.id);
      }
    });
    m.on("dblclick", (e) => { if (live.current.tool === "polygon") { e.preventDefault(); live.current.setDraft({ ...live.current.draft, done: true }); } });
    m.on("mousemove", (e) => {
      const L = live.current;
      if (L.tool === "circle" && L.draft?.center && !L.draft.done) L.setDraft({ ...L.draft, km: Math.max(0.2, distKm(L.draft.center, { lat: e.lngLat.lat, lon: e.lngLat.lng })) });
      m.getCanvas().style.cursor = L.tool === "inspect" ? "" : "crosshair";
    });
    setReady(true);
  };
  useEffect(() => { const m = mref.current; if (m && fly) m.flyTo({ center: [fly.lon, fly.lat], zoom: fly.zoom || 11, speed: 1.6 }); }, [fly]);
  useEffect(() => {
    const m = mref.current; if (!m || !ready) return;
    m.getSource("zones")?.setData(FC((an?.zones || []).filter((z) => z.status !== "lifted").map((z) => ({ type: "Feature", geometry: z.geometry,
      properties: { id: z.zone_id, c: ZONE_C[z.kind], real: z.basis === "real" && z.status === "active", sel: z.zone_id === selZone } }))));
    m.getSource("clusters")?.setData(FC((an?.clusters || []).map((c) => ({ type: "Feature", geometry: c.hull, properties: { id: c.id } }))));
  }, [an, selZone, ready]);
  useEffect(() => {
    const m = mref.current; if (!m || !ready) return;
    const asof = an?.as_of || todayISO(), t0 = new Date(asof + "T00:00:00Z").getTime();
    m.getSource("pts")?.setData(FC((reports || []).filter((r) => r.lat != null).map((r) => {
      const age = Math.max(0, (t0 - new Date(r.event_date + "T00:00:00Z").getTime()) / 864e5);
      const big = r.kind === "vaccinated" || r.kind === "tested";
      return { type: "Feature", geometry: { type: "Point", coordinates: [r.lon, r.lat] },
        properties: { c: KIND_C[r.kind], r: big ? 4 + Math.min(10, Math.log10(r.count + 1) * 2.5) : 2.6 + Math.min(10, Math.sqrt(r.count) * 1.4), o: big ? 0.45 : Math.max(0.35, 1 - age / 21), fresh: age < 2 } };
    })));
  }, [reports, an, ready]);
  useEffect(() => {
    const m = mref.current; if (!m || !ready) return;
    const c = ZONE_C[draft?.kind] || KIND_C.case, f = [];
    if (draft?.center) { f.push({ type: "Feature", geometry: { type: "Point", coordinates: [draft.center.lon, draft.center.lat] }, properties: { c } });
      if (draft.km) f.push({ type: "Feature", geometry: circleGeom(draft.center.lat, draft.center.lon, draft.km), properties: { c } }); }
    if (draft?.pts?.length) {
      const ring = draft.pts.map((p) => [p.lon, p.lat]);
      f.push({ type: "Feature", geometry: ring.length >= 3 ? { type: "Polygon", coordinates: [[...ring, ring[0]]] } : { type: "LineString", coordinates: ring.length > 1 ? ring : [ring[0], ring[0]] }, properties: { c } });
      draft.pts.forEach((p) => f.push({ type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] }, properties: { c } }));
    }
    if (draft?.point) f.push({ type: "Feature", geometry: { type: "Point", coordinates: [draft.point.lon, draft.point.lat] }, properties: { c: KIND_C[draft.kind] || "#2f70bb" } });
    m.getSource("draft")?.setData(FC(f));
  }, [draft, ready]);

  const colors = useMemo(() => {
    const o = {};
    for (const p of an?.provinces || []) if (p.last7 > 0 || p.confirmed > 0) { const v = p.per100k_7d; o[p.province_id] = HEAT[v < 0.5 ? 0 : v < 2 ? 1 : v < 5 ? 2 : v < 15 ? 3 : v < 50 ? 4 : 5]; }
    for (const s of an?.safe_provinces || []) o[s.province_id] = SAFE_FILL;
    return o;
  }, [an]);
  const byId = useMemo(() => Object.fromEntries((an?.provinces || []).map((p) => [p.province_id, p])), [an]);
  const safeIds = useMemo(() => new Set((an?.safe_provinces || []).map((s) => s.province_id)), [an]);
  const labels = useMemo(() => (an?.clusters || []).slice(0, 10).map((c) => ({ id: c.id, lat: c.lat, lon: c.lon, text: `${c.id} · ${nf(c.last7)}/7d${c.trend === "growing" ? " ↑" : c.trend === "declining" ? " ↓" : ""}`,
    level: c.trend === "growing" ? "alert" : c.trend === "stable" ? "warning" : null })), [an]);
  const routes = useMemo(() => (plan?.allocations || []).map((a) => ({ from: [a.from_lon, a.from_lat], to: [a.to_lon, a.to_lat] })), [plan]);
  const movers = useMemo(() => (plan?.allocations || []).map((a) => ({ lon: a.to_lon, lat: a.to_lat, c: a.kind === "team" ? "#2f70bb" : a.kind === "vaccine" ? "#1e7b4f" : a.kind === "bed" ? "#6a4fc2" : "#a86f0c" })), [plan]);
  return html`<${VnMap} height=${600} maxZoom=${15} colors=${colors} labels=${labels} routes=${routes} movers=${movers} pulses=${pulses} onReady=${onReady}
    onSelect=${(id) => tool === "inspect" && onProvince?.(id)} label="Live response map"
    tooltip=${(id, name) => { const p = byId[id]; return html`<b>${name}</b>${p ? html`<div class="small num">${nf(p.confirmed)} confirmed · ${nf(p.last7)} in 7 d · ${p.per100k_7d}/100k</div>
      <div class="small muted num">${nf(p.deaths)} deaths · ${fmt.pct(p.coverage, 1)} vaccinated${p.bed_load != null ? ` · beds ${fmt.pct(p.bed_load)}` : ""}</div>` : safeIds.has(id) ? html`<div class="small" style="color:var(--positive)">Safe — no reports, far from clusters</div>` : html`<div class="small muted">No reports</div>`}`; }}>
    <div class="maplegend small">
      ${Object.entries(KIND_C).slice(0, 4).map(([k, c]) => html`<span><i class="dot" style=${`background:${c}`}></i>${KIND_L[k].split(" ")[0]}</span>`)}
      ${Object.entries(ZONE_C).map(([k, c]) => html`<span><i class="sq" style=${`border-color:${c}`}></i>${ZONE_L[k]}</span>`)}
      <span class="faint">solid = real · dashed = simulated/proposed</span>
    </div>
  </${VnMap}>`;
}

// ------------------------------------------------------------------------------------------------------- key-in form
function KeyIn({ iid, loc, setLoc, provinces, can, setTool, kind, setKind, onDone }) {
  const [count, setCount] = useState(1), [d, setD] = useState(todayISO()), [note, setNote] = useState(""), [pid, setPid] = useState("");
  const [busy, setBusy] = useState(false), [bulk, setBulk] = useState(false), [csv, setCsv] = useState("kind,count,lat,lon,event_date,note\ncase,1,10.776,106.701,,");
  const submit = async (e) => {
    e?.preventDefault();
    if (!loc && !pid) return toast("Click the map to place the report, or choose a province", "bad");
    setBusy(true);
    try {
      await api(`/ops/incidents/${iid}/reports`, { method: "POST", body: { reports: [{ kind, count: +count, event_date: d, note: note || null, ...(loc ? { lat: loc.lat, lon: loc.lon } : { province_id: pid }) }] } });
      toast(`${count} ${KIND_L[kind].toLowerCase()} keyed in`, "ok"); setNote(""); onDone?.();
    } catch (er) { toast(er.message, "bad"); }
    setBusy(false);
  };
  const sendBulk = async () => {
    setBusy(true);
    try { const r = await api(`/ops/incidents/${iid}/reports/bulk`, { method: "POST", body: { csv } }); toast(`${r.data.added} reports added${r.data.errors.length ? `, ${r.data.errors.length} lines skipped` : ""}`, r.data.errors.length ? "" : "ok"); onDone?.(); }
    catch (er) { toast(Array.isArray(er.detail) ? er.detail.join("; ") : er.message, "bad"); }
    setBusy(false);
  };
  if (!can) return html`<div class="small muted">Sign in as a data operator, scientist, reviewer or admin to key in reports.</div>`;
  return html`<form class="stack" style="gap:10px" onSubmit=${submit}>
    <div class="kindgrid">${Object.keys(KIND_L).map((k) => html`<button type="button" class=${"kindbtn" + (kind === k ? " on" : "")} onClick=${() => setKind(k)}>
      <i class="dot" style=${`background:${KIND_C[k]}`}></i>${KIND_L[k]}</button>`)}</div>
    <div class="row" style="gap:8px">
      <label class="fld" style="flex:1"><span>Count</span><input type="number" min="1" max="1000000" value=${count} onInput=${(e) => setCount(e.target.value)} /></label>
      <label class="fld" style="flex:1.4"><span>Date of ${kind === "vaccinated" ? "doses" : kind === "tested" ? "tests" : "onset / event"}</span><input type="date" value=${d} max=${todayISO()} onInput=${(e) => setD(e.target.value)} /></label>
    </div>
    <div class="fld"><span>Location</span>
      <div class="row" style="gap:6px">
        ${loc ? html`<span class="pill num">${loc.lat.toFixed(4)}, ${loc.lon.toFixed(4)}</span><button type="button" class="btn sm ghost" onClick=${() => setLoc(null)}>Clear</button>`
          : html`<button type="button" class="btn sm" onClick=${() => setTool("report")}><${Icon} n="pin" s=${12} />Click on the map</button><span class="small faint">or</span>
            <select value=${pid} onChange=${(e) => setPid(e.target.value)} aria-label="Province"><option value="">province only…</option>${provinces.map((p) => html`<option value=${p.province_id}>${p.name}</option>`)}</select>`}
      </div></div>
    <label class="fld"><span>Note (optional, no names or ID numbers)</span><input type="text" maxlength="500" value=${note} onInput=${(e) => setNote(e.target.value)} placeholder="e.g. household contact of earlier case, market worker" /></label>
    <div class="row"><button class="btn pri" disabled=${busy}><${Icon} n="plus" s=${13} />Key in</button><span class="small faint">Shortcut: with the Report tool on, each map click sets the location.</span></div>
    <details open=${bulk} onToggle=${(e) => setBulk(e.target.open)}><summary class="link small">Bulk paste (CSV)</summary>
      <div class="stack" style="gap:6px;margin-top:6px"><textarea rows="5" class="mono small" value=${csv} onInput=${(e) => setCsv(e.target.value)} aria-label="CSV rows"></textarea>
        <span class="small faint">Columns: kind, count, lat, lon, province_id (VNxx), event_date (YYYY-MM-DD), note. Any subset; kind defaults to case.</span>
        <div><button type="button" class="btn sm" disabled=${busy} onClick=${sendBulk}>Import rows</button></div></div></details>
  </form>`;
}

// --------------------------------------------------------------------------------------------------------- zones tab
function ZonesTab({ iid, an, can, canCmd, tool, setTool, draft, setDraft, selZone, setSelZone, refresh, setFly }) {
  const [busy, setBusy] = useState(false);
  const zk = draft?.kind || "quarantine";
  const sel = an.zones.find((z) => z.zone_id === selZone);
  const call = async (p, opt, msg) => { setBusy(true); try { await api(p, opt); msg && toast(msg, "ok"); refresh(); } catch (e) { toast(e.message, "bad"); } setBusy(false); };
  const saveDraft = async () => {
    const geom = draft.center ? circleGeom(draft.center.lat, draft.center.lon, draft.km) : { type: "Polygon", coordinates: [[...draft.pts.map((p) => [p.lon, p.lat]), [draft.pts[0].lon, draft.pts[0].lat]]] };
    await call(`/ops/incidents/${iid}/zones`, { method: "POST", body: { kind: zk, geometry: geom, basis: draft.basis || "simulated", status: draft.basis === "proposed" ? "proposed" : "active",
      ...(draft.basis === "proposed" ? { basis: "simulated" } : {}), contact_reduction: draft.cr ?? an.zone_defaults?.[zk] } }, "Zone saved");
    setDraft({ kind: zk, basis: draft.basis }); setTool("inspect");
  };
  const ready = draft && ((draft.center && draft.done) || (draft.pts?.length >= 3 && draft.done));
  const groups = [["Enforced (real)", an.zones.filter((z) => z.basis === "real" && z.status === "active")], ["Simulated what-ifs", an.zones.filter((z) => z.basis === "simulated" && z.status === "active")],
    ["Proposed — waiting for a decision", an.zones.filter((z) => z.status === "proposed")], ["Lifted", an.zones.filter((z) => z.status === "lifted")]];
  return html`<div class="stack" style="gap:14px">
    ${can && html`<div class="well stack" style="gap:8px">
      <div class="row wrap" style="gap:6px"><b class="small">Draw a zone</b><span class="spacer"></span>
        <button class=${"tool" + (tool === "circle" ? " on" : "")} onClick=${() => { setTool("circle"); setDraft({ kind: zk, basis: draft?.basis || "simulated", cr: draft?.cr }); }}>◯ Circle</button>
        <button class=${"tool" + (tool === "polygon" ? " on" : "")} onClick=${() => { setTool("polygon"); setDraft({ kind: zk, basis: draft?.basis || "simulated", cr: draft?.cr, pts: [] }); }}>⬠ Polygon</button></div>
      <div class="row wrap" style="gap:6px">${Object.keys(ZONE_C).map((k) => html`<button class=${"kindbtn sm" + (zk === k ? " on" : "")} onClick=${() => setDraft({ ...(draft || {}), kind: k, cr: undefined })}><i class="sq" style=${`border-color:${ZONE_C[k]}`}></i>${ZONE_L[k]}</button>`)}</div>
      <${Seg} label="Basis" value=${draft?.basis || "simulated"} onChange=${(v) => setDraft({ ...(draft || { kind: zk }), basis: v })}
        options=${[["simulated", "Simulate"], ["proposed", "Propose"], ...(canCmd ? [["real", "Enforce (real)"]] : [])]} />
      ${["affected", "quarantine", "buffer"].includes(zk) && html`<label class="sl"><span class="row"><span>Contact reduction inside</span><span class="spacer"></span><b class="num">${fmt.pct(draft?.cr ?? an.zone_defaults?.[zk] ?? 0.5)}</b></span>
        <input type="range" min="0" max="0.95" step="0.05" value=${draft?.cr ?? an.zone_defaults?.[zk] ?? 0.5} onInput=${(e) => setDraft({ ...(draft || { kind: zk }), cr: +e.target.value })} /></label>`}
      <span class="small faint">${tool === "circle" ? (draft?.center ? (draft.done ? `Radius ${draft.km.toFixed(2)} km — save or redraw.` : "Move to size it, click again to fix the radius.") : "Click the centre on the map.")
        : tool === "polygon" ? `${draft?.pts?.length || 0} point(s) — double-click (or Finish) to close.` : "Pick Circle or Polygon, then draw on the map."}</span>
      <div class="row" style="gap:6px">
        ${tool === "polygon" && draft?.pts?.length >= 3 && !draft.done && html`<button class="btn sm" onClick=${() => setDraft({ ...draft, done: true })}>Finish</button>`}
        <button class="btn sm pri" disabled=${!ready || busy} onClick=${saveDraft}>Save zone</button>
        ${(draft?.center || draft?.pts?.length) && html`<button class="btn sm ghost" onClick=${() => setDraft({ kind: zk, basis: draft.basis, cr: draft.cr, pts: tool === "polygon" ? [] : undefined })}>Redraw</button>`}
      </div></div>`}

    ${sel && html`<div class="well stack zonesel" style=${`gap:8px;border-left:3px solid ${ZONE_C[sel.kind]}`}>
      <div class="row"><b>${sel.name}</b><span class="spacer"></span><button class="btn sm ghost" onClick=${() => setSelZone(null)} aria-label="Close"><${Icon} n="x" s=${12} /></button></div>
      <div class="row wrap small" style="gap:6px"><span class=${"pill " + (sel.basis === "real" ? "bad" : "sc")}>${sel.basis === "real" ? "REAL" : "Simulated"}</span><span class="pill">${sel.status}</span><span class="pill">${sel.origin}</span></div>
      <dl class="kv small"><dt>Area</dt><dd class="num">${fmt.d1(sel.params.area_km2)} km²</dd><dt>People inside</dt><dd class="num">~${nf(sel.params.people_est)} <span class="faint">(estimate)</span></dd>
        <dt>Cases inside</dt><dd class="num">${nf(sel.stats.cases_inside)} · ${nf(sel.stats.last7_inside)} in 7 d</dd><dt>Clusters</dt><dd>${sel.stats.clusters.join(", ") || "—"}</dd>
        ${sel.params.note && html`<dt>Note</dt><dd>${sel.params.note}</dd>`}</dl>
      ${can && ["affected", "quarantine", "buffer"].includes(sel.kind) && html`<label class="sl"><span class="row"><span>Contact reduction</span><span class="spacer"></span><b class="num">${fmt.pct(sel.params.contact_reduction)}</b></span>
        <input type="range" min="0" max="0.95" step="0.05" value=${sel.params.contact_reduction} disabled=${sel.basis === "real" && !canCmd}
          onChange=${(e) => call(`/ops/incidents/${iid}/zones/${sel.zone_id}`, { method: "PATCH", body: { contact_reduction: +e.target.value } })} /></label>`}
      ${can && html`<div class="row wrap" style="gap:6px">
        ${sel.status !== "active" || sel.basis !== "simulated" ? html`<button class="btn sm" disabled=${busy || (sel.basis === "real" && sel.status === "active" && !canCmd)} onClick=${() => call(`/ops/incidents/${iid}/zones/${sel.zone_id}`, { method: "PATCH", body: { basis: "simulated", status: "active" } }, "Now a simulated what-if")}>Simulate</button>` : ""}
        ${canCmd && !(sel.basis === "real" && sel.status === "active") && html`<button class="btn sm danger" disabled=${busy} onClick=${() => confirm(`Enforce "${sel.name}" for REAL? About ${nf(sel.params.people_est)} people live inside.`) && call(`/ops/incidents/${iid}/zones/${sel.zone_id}`, { method: "PATCH", body: { basis: "real", status: "active" } }, "Zone enforced")}>Enforce for real</button>`}
        ${sel.status === "active" && html`<button class="btn sm" disabled=${busy || (sel.basis === "real" && !canCmd)} onClick=${() => call(`/ops/incidents/${iid}/zones/${sel.zone_id}`, { method: "PATCH", body: { status: "lifted" } }, "Zone lifted")}>Lift</button>`}
        ${!(sel.basis === "real" && sel.status !== "proposed") && html`<button class="btn sm ghost" disabled=${busy} onClick=${() => { call(`/ops/incidents/${iid}/zones/${sel.zone_id}`, { method: "DELETE" }, "Zone removed"); setSelZone(null); }}>Delete</button>`}
      </div>`}
    </div>`}

    ${an.suggestions.length > 0 && html`<div class="stack" style="gap:6px"><b class="small">Suggested from the clusters <span class="faint">(${an.suggestions.length})</span></b>
      ${an.suggestions.slice(0, 9).map((s, i) => html`<div class="zrow"><i class="sq" style=${`border-color:${ZONE_C[s.kind]}`}></i>
        <div class="grow"><div class="small"><b>${ZONE_L[s.kind]}</b> · ${s.cluster} · ${fmt.d1(s.area_km2)} km² · ~${nf(s.people_est)} people</div><div class="small faint">${s.why}</div></div>
        ${can && html`<div class="row" style="gap:4px"><button class="btn sm" disabled=${busy} onClick=${() => call(`/ops/incidents/${iid}/suggestions/accept`, { method: "POST", body: { index: i, basis: "simulated", status: "active" } }, "Added as a simulated what-if")}>Simulate</button>
          ${canCmd && html`<button class="btn sm danger" disabled=${busy} onClick=${() => confirm(`Enforce this ${ZONE_L[s.kind].toLowerCase()} zone for REAL (~${nf(s.people_est)} people)?`) && call(`/ops/incidents/${iid}/suggestions/accept`, { method: "POST", body: { index: i, basis: "real", status: "active" } }, "Zone enforced")}>Enforce</button>`}</div>`}
      </div>`)}</div>`}

    ${groups.map(([t, list]) => list.length > 0 && html`<div class="stack" style="gap:4px"><b class="small">${t} <span class="faint">(${list.length})</span></b>
      ${list.map((z) => html`<button class=${"zrow click" + (z.zone_id === selZone ? " on" : "")} onClick=${() => { setSelZone(z.zone_id); const c = z.geometry.coordinates[0][0]; setFly({ lon: c[0], lat: c[1], zoom: 11 }); }}>
        <i class="sq" style=${`border-color:${ZONE_C[z.kind]}${z.basis === "real" && z.status === "active" ? `;background:${ZONE_C[z.kind]}33` : ""}`}></i>
        <div class="grow"><div class="small"><b>${z.name}</b></div><div class="small faint num">${fmt.d1(z.params.area_km2)} km² · ~${nf(z.params.people_est)} people · ${nf(z.stats.cases_inside)} cases inside${z.params.contact_reduction ? ` · −${fmt.pct(z.params.contact_reduction)} contacts` : ""}</div></div>
        ${z.origin === "ai" && html`<span class="pill sc">AI</span>`}</button>`)}</div>`)}
    ${!an.zones.length && !an.suggestions.length && html`<${Empty}>No zones yet. Key in cases to get suggestions, or draw one.</${Empty}>`}
  </div>`;
}

// ----------------------------------------------------------------------------------------------------- resources tab
function ResourcesTab({ iid, an, can, canCmd, plan, setPlan, stockLoc, setStockLoc, setTool, refresh }) {
  const r = useApi(`/ops/incidents/${iid}/resources`, [an?.kpis?.reports, plan?.plan_id, an?.zones?.length]);
  const [kind, setKind] = useState("team"), [qty, setQty] = useState(2), [name, setName] = useState(""), [pid, setPid] = useState("");
  const [basis, setBasis] = useState("simulated"), [busy, setBusy] = useState(false);
  const stock = r.data?.stock || [], plans = r.data?.plans || [];
  const add = async () => {
    if (!stockLoc && !pid) return toast("Click the map for the depot, or choose a province", "bad");
    setBusy(true);
    try { await api(`/ops/incidents/${iid}/resources`, { method: "POST", body: { kind, qty: +qty, name: name || null, ...(stockLoc ? { lat: stockLoc.lat, lon: stockLoc.lon } : { province_id: pid }) } }); setName(""); setStockLoc(null); r.reload?.(); refresh(); }
    catch (e) { toast(e.message, "bad"); }
    setBusy(false);
  };
  const opt = async () => {
    setBusy(true);
    try { const o = await api(`/ops/incidents/${iid}/optimize`, { method: "POST", body: { basis, save: can } }); setPlan(o.data); toast(`${o.data.allocations.length} moves · ${o.meta.ms} ms`, "ok"); refresh(); }
    catch (e) { toast(e.message, "bad"); }
    setBusy(false);
  };
  const approve = async (id) => { setBusy(true); try { await api(`/ops/incidents/${iid}/plans/${id}/approve`, { method: "POST" }); toast("Plan approved for dispatch", "ok"); refresh(); r.reload?.(); } catch (e) { toast(e.message, "bad"); } setBusy(false); };
  const s = plan?.summary;
  const pidOf = plan?.plan_id;
  const approvedId = plans.find((p) => p.status === "approved")?.plan_id;
  return html`<div class="stack" style="gap:14px">
    <div class="stack" style="gap:4px"><b class="small">Stock by depot</b>
      ${stock.length ? html`<table class="tbl compact"><tbody>${stock.map((x) => html`<tr><td class="small">${x.name}<div class="faint">${RES_L[x.kind]}</div></td>
        <td class="num">${can ? html`<input type="number" class="qty" min="0" value=${x.qty} onChange=${async (e) => { try { await api(`/ops/incidents/${iid}/resources/${x.resource_id}`, { method: "PATCH", body: { qty: +e.target.value } }); r.reload?.(); } catch (er) { toast(er.message, "bad"); } }} />` : nf(x.qty)}</td></tr>`)}</tbody></table>`
        : html`<span class="small muted">No stock recorded yet.</span>`}</div>
    ${can && html`<details class="well"><summary class="link small">Add stock at a depot</summary><div class="stack" style="gap:8px;margin-top:8px">
      <div class="row" style="gap:6px"><select value=${kind} onChange=${(e) => setKind(e.target.value)} aria-label="Resource kind">${Object.entries(RES_L).map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select>
        <input type="number" class="qty" min="1" value=${qty} onInput=${(e) => setQty(e.target.value)} aria-label="Quantity" /></div>
      <input type="text" placeholder="Depot name (optional)" value=${name} onInput=${(e) => setName(e.target.value)} />
      <div class="row" style="gap:6px">${stockLoc ? html`<span class="pill num">${stockLoc.lat.toFixed(3)}, ${stockLoc.lon.toFixed(3)}</span><button class="btn sm ghost" onClick=${() => setStockLoc(null)}>Clear</button>`
        : html`<button class="btn sm" onClick=${() => setTool("stock")}><${Icon} n="pin" s=${12} />Click depot on map</button><select value=${pid} onChange=${(e) => setPid(e.target.value)} aria-label="Province">
          <option value="">or province…</option>${Object.values(an.provinces_all || {}).map((p) => html`<option value=${p.province_id}>${p.name}</option>`)}</select>`}</div>
      <div><button class="btn sm pri" disabled=${busy} onClick=${add}>Add stock</button></div></div></details>`}

    <div class="well stack" style="gap:8px"><div class="row"><b class="small">Optimise allocation</b><span class="spacer"></span>
      <${Seg} label="Basis" value=${basis} onChange=${setBasis} options=${[["simulated", "+ simulated zones"], ["real", "Real only"]]} /></div>
      <span class="small faint">Greedy marginal allocation: each team / dose block / kit block goes where it averts the most projected cases (from the nearest depot with stock, delayed by travel); beds and ambulances go where they avert the most deaths.</span>
      <div><button class="btn pri sm" disabled=${busy || !stock.length} onClick=${opt}><${Icon} n="model" s=${12} />${busy ? "Optimising…" : "Run optimiser"}</button></div></div>

    ${plan && html`<div class="stack" style="gap:8px">
      <div class="row wrap" style="gap:6px"><b class="small">Plan ${pidOf ? html`<span class="faint mono">${pidOf.slice(-6)}</span>` : ""}</b><${Sem} k="SCENARIO" />
        ${pidOf === approvedId ? html`<span class="pill ok">Approved</span>` : canCmd && pidOf && plan.allocations.length > 0 && html`<button class="btn sm danger" disabled=${busy} onClick=${() => confirm("Approve this plan for real dispatch?") && approve(pidOf)}>Approve for dispatch</button>`}
        <span class="spacer"></span><button class="btn sm ghost" onClick=${() => setPlan(null)}>Hide</button></div>
      ${s && html`<div class="minikpis"><div><span>Cases (28 d)</span><b class="num">${nf(s.cases_before)} → ${nf(s.cases_after)}</b></div><div><span>Averted</span><b class="num" style="color:var(--positive)">${nf(s.cases_averted)}</b></div>
        <div><span>Deaths</span><b class="num">${fmt.d1(s.deaths_before)} → ${fmt.d1(s.deaths_after)}</b></div><div><span>Peak no-bed</span><b class="num">${nf(s.peak_unmet_before)} → ${nf(s.peak_unmet_after)}</b></div></div>`}
      <ol class="moves">${plan.allocations.map((a) => html`<li><span class=${"pill k-" + a.kind}>${RES_L[a.kind]}</span> ${a.why}</li>`)}</ol>
      ${plan.reserve?.length > 0 && html`<span class="small muted">Kept in reserve: ${plan.reserve.map((x) => `${nf(x.left)} ${RES_L[x.kind].toLowerCase()} at ${x.name}`).join("; ")}.</span>`}
      ${plan.log?.map((l) => html`<span class="small faint">${l}</span>`)}
    </div>`}
    ${plans.length > 0 && html`<details><summary class="link small">Plan history (${plans.length})</summary><div class="stack" style="gap:4px;margin-top:6px">${plans.map((p) => html`<button class="zrow click" onClick=${() => setPlan({ ...p.plan, plan_id: p.plan_id })}>
      <div class="grow small"><b>${p.status}</b> · ${p.plan.allocations.length} moves · ~${nf(p.plan.summary?.cases_averted)} averted <span class="faint">· ${ago(p.created_at)} · ${p.created_by}</span></div></button>`)}</div></details>`}
  </div>`;
}

// ---------------------------------------------------------------------------------------------------------- AI tab
const QUICK = { en: ["What is the situation and what should we do next?", "Which cluster needs quarantine first, and how big should it be?", "Where should the teams and vaccine go?", "Will we run out of beds?"],
  vi: ["Tình hình hiện tại thế nào, nên làm gì tiếp?", "Ổ dịch nào cần phong tỏa trước, phạm vi bao nhiêu?", "Nên điều đội phản ứng và vắc xin đi đâu?", "Có thiếu giường bệnh không?"] };
function AiTab({ iid, refresh, meta }) {
  const [msgs, setMsgs] = useState([]), [q, setQ] = useState(""), [busy, setBusy] = useState(false);
  const [lang, setLang] = useState(() => { try { return localStorage.getItem("hp_vn_lang") || "en"; } catch (e) { return "en"; } });
  const ctl = useRef(null), box = useRef(null);
  useEffect(() => { try { localStorage.setItem("hp_vn_lang", lang); } catch (e) {} }, [lang]);
  useEffect(() => () => ctl.current?.abort(), []);
  useEffect(() => { box.current && (box.current.scrollTop = box.current.scrollHeight); }, [msgs]);
  const ask = async (question) => {
    ctl.current?.abort(); const c = new AbortController(); ctl.current = c;
    const hist = msgs.filter((m) => m.text).map((m) => ({ role: m.role, content: m.text })).slice(-6);
    const cur = { role: "assistant", text: "", steps: [], meta: null, err: null };
    setMsgs((m) => [...m, { role: "user", text: question || QUICK[lang][0] }, cur]);
    setBusy(true); setQ("");
    const upd = (f) => setMsgs((m) => { const n = [...m]; n[n.length - 1] = f({ ...n[n.length - 1] }); return n; });
    try {
      await sse(`/ops/incidents/${iid}/ask`, { method: "POST", body: { question: question || "", lang, history: hist }, signal: c.signal, onEvent: (ev, d) => {
        if (ev === "text") upd((x) => ({ ...x, text: x.text + d.t }));
        else if (ev === "meta") upd((x) => ({ ...x, meta: d }));
        else if (ev === "tool") upd((x) => ({ ...x, steps: [...x.steps, { name: d.name, input: d.input }] }));
        else if (ev === "tool_result") upd((x) => { const s = [...x.steps]; const i = s.map((y) => y.name).lastIndexOf(d.name); if (i >= 0) s[i] = { ...s[i], ok: d.ok, summary: d.summary }; return { ...x, steps: s }; });
        else if (ev === "proposal") { toast(`AI proposed ${d.name || "an allocation plan"} — review it`, ""); refresh(); }
        else if (ev === "error") upd((x) => ({ ...x, err: d.error }));
      } });
    } catch (e) { if (e.name !== "AbortError") upd((x) => ({ ...x, err: e.message })); }
    setBusy(false);
  };
  return html`<div class="stack aichat" style="gap:10px">
    <div class="row wrap" style="gap:6px"><span class="small muted">${meta?.ai?.available ? `Tool-using analyst · ${meta.ai.tools.length} tools over this incident's live data` : "No LLM configured — answers use the rule-based brief"}</span><span class="spacer"></span>
      <div class="seg sm" role="radiogroup" aria-label="Language"><button class=${lang === "en" ? "on" : ""} onClick=${() => setLang("en")}>EN</button><button class=${lang === "vi" ? "on" : ""} onClick=${() => setLang("vi")}>VI</button></div></div>
    <div class="aimsgs" ref=${box}>
      ${!msgs.length && html`<div class="stack" style="gap:6px">${QUICK[lang].map((s) => html`<button class="btn sm" style="justify-content:flex-start;height:auto;min-height:28px;text-align:left;white-space:normal" disabled=${busy} onClick=${() => ask(s)}>${s}</button>`)}</div>`}
      ${msgs.map((m, i) => m.role === "user" ? html`<div class="aiq">${m.text}</div>` : html`<div class="aia">
        ${m.steps.length > 0 && html`<div class="steps">${m.steps.map((s) => html`<span class=${"step" + (s.ok === false ? " bad" : s.ok ? " ok" : "")} title=${JSON.stringify(s.input)}>
          <${Icon} n=${s.ok == null ? "refresh" : s.ok ? "check" : "x"} s=${11} />${s.name.replace(/_/g, " ")}${s.summary ? html`<span class="faint"> · ${s.summary}</span>` : ""}</span>`)}</div>`}
        ${m.err && html`<div class="notice warn small"><${Icon} n="warn" s=${13} /><div>${m.err}</div></div>`}
        <div class=${"md" + (busy && i === msgs.length - 1 ? " streaming" : "")} dangerouslySetInnerHTML=${{ __html: md(m.text || "") + (busy && i === msgs.length - 1 ? '<span class="caret"></span>' : "") }}></div>
        ${m.meta && html`<div class="small faint">${m.meta.provider === "rule_based" ? `Rule-based (${m.meta.reason})` : `${m.meta.provider} · ${m.meta.model}`} · proposals wait for a human decision</div>`}
      </div>`)}
    </div>
    <form class="row" onSubmit=${(e) => { e.preventDefault(); if (q.trim()) ask(q.trim()); }}>
      <input type="text" style="flex:1" value=${q} maxlength="2000" onInput=${(e) => setQ(e.target.value)} placeholder=${lang === "vi" ? "Hỏi về ổ dịch, vùng cách ly, nguồn lực…" : "Ask about clusters, zones, resources, beds…"} aria-label="Question" />
      <button class="btn pri" disabled=${busy || !q.trim()}>Ask</button>
      ${busy && html`<button type="button" class="btn ghost" onClick=${() => { ctl.current?.abort(); setBusy(false); }}>Stop</button>`}
    </form>
  </div>`;
}

// -------------------------------------------------------------------------------------------------- new incident form
function NewIncident({ meta, canCmd, onMade }) {
  const [name, setName] = useState(""), [pathogen, setPathogen] = useState("covid"), [mode, setMode] = useState("drill"), [seed, setSeed] = useState(true), [busy, setBusy] = useState(false);
  const make = async (e) => {
    e.preventDefault(); setBusy(true);
    try {
      const r = await api("/ops/incidents", { method: "POST", body: { name: name || (mode === "drill" ? "Response drill" : "Live incident"), pathogen, mode } });
      if (mode === "drill" && seed) await api(`/ops/incidents/${r.data.incident_id}/drill`, { method: "POST", body: { action: "seed" } });
      onMade(r.data.incident_id);
    } catch (er) { toast(er.message, "bad"); }
    setBusy(false);
  };
  return html`<form class="stack" style="gap:10px;max-width:520px" onSubmit=${make}>
    <label class="fld"><span>Name</span><input type="text" value=${name} onInput=${(e) => setName(e.target.value)} placeholder="e.g. Measles — Thanh Hóa, Nov 2026" maxlength="120" /></label>
    <label class="fld"><span>Pathogen (sets incubation, infectious period, severity defaults)</span><select value=${pathogen} onChange=${(e) => setPathogen(e.target.value)}>${Object.entries(meta?.pathogens || {}).map(([k, v]) => html`<option value=${k}>${v}</option>`)}</select></label>
    <${Seg} label="Mode" value=${mode} onChange=${setMode} options=${[["drill", "Drill (exercise)"], ...(canCmd ? [["live", "LIVE (real outbreak)"]] : [])]} />
    <span class="small faint">${mode === "live" ? "Reports are treated as OBSERVED. Synthetic data is blocked; real zones and approved plans are audited." : "Reports are SYNTHETIC. You can seed a scenario and advance the drill clock day by day."}${!canCmd ? " Opening a LIVE incident needs an epidemiology reviewer or admin." : ""}</span>
    ${mode === "drill" && html`<label class="row small" style="gap:6px"><input type="checkbox" checked=${seed} onChange=${(e) => setSeed(e.target.checked)} />Seed a synthetic outbreak (3 urban clusters, sporadic cases, depots)</label>`}
    <div><button class="btn pri" disabled=${busy}><${Icon} n="plus" s=${13} />${busy ? "Opening…" : "Open incident"}</button></div>
  </form>`;
}

// --------------------------------------------------------------------------------------------------------------- page
export default function VnOps({ q }) {
  const { can, me } = useSession();
  const canKey = can("ops.report"), canCmd = can("ops.command");
  const status = useApi("/vn/status");
  const meta = useApi("/ops/meta").data;
  const list = useApi(status.data?.built ? "/ops/incidents" : null);
  const iid = q.get("inc") || list.data?.[0]?.incident_id || null;
  const [an, setAn] = useState(null), [reports, setReports] = useState([]), [err, setErr] = useState(null);
  const [tab, setTab] = useState("key"), [tool, setTool] = useState("inspect"), [draft, setDraft] = useState(null);
  const [loc, setLoc] = useState(null), [stockLoc, setStockLoc] = useState(null), [kind, setKind] = useState("case");
  const [selZone, setSelZone] = useState(null), [plan, setPlan] = useState(null), [fly, setFly] = useState(null);
  const [feed, setFeed] = useState([]), [conn, setConn] = useState("off"), [pulses, setPulses] = useState([]), [showNew, setShowNew] = useState(false);
  const [busy, setBusy] = useState(false);
  const tmr = useRef(0), seqRef = useRef(0);
  const provincesAll = useApi(status.data?.built ? "/vn/provinces" : null).data || [];

  const load = async () => {
    if (!iid) return;
    try {
      const [a, r] = await Promise.all([api(`/ops/incidents/${iid}`), api(`/ops/incidents/${iid}/reports`)]);
      setAn({ ...a.data, ms: a.meta.ms }); setReports(r.data); setErr(null);
    } catch (e) { setErr(e.message); }
  };
  const refresh = () => { clearTimeout(tmr.current); tmr.current = setTimeout(load, 350); };
  useEffect(() => { setAn(null); setReports([]); setFeed([]); setPlan(null); setSelZone(null); seqRef.current = 0; load(); }, [iid, meta]);

  // live stream: feed entries from every controller; any new entry refreshes the analysis
  useEffect(() => {
    if (!iid) return;
    let dead = false, ctl = null, retry = 0;
    (async function loop() {
      api(`/ops/incidents/${iid}/events?limit=60`).then((r) => { if (!dead) { setFeed(r.data.reverse()); seqRef.current = r.data[0]?.seq || 0; } }).catch(() => {});
      while (!dead) {
        ctl = new AbortController();
        try {
          setConn("connecting");
          await sse(`/ops/incidents/${iid}/stream?after=${seqRef.current}`, { signal: ctl.signal, onEvent: (ev, d) => {
            if (ev === "hello") { setConn("live"); if (!seqRef.current) seqRef.current = d.seq; retry = 0; }
            if (ev === "events" && d.length) {
              seqRef.current = d[d.length - 1].seq;
              setFeed((f) => [...d.slice().reverse().map((e) => ({ ...e, fresh: true })), ...f.map((e) => ({ ...e, fresh: false }))].slice(0, 200));
              const ps = d.filter((e) => e.kind === "report" && e.data?.lat != null).map((e) => ({ lat: e.data.lat, lon: e.data.lon, level: e.data.kind === "death" || e.data.kind === "case" ? "alert" : "warning" }));
              if (ps.length) { setPulses((p) => [...p, ...ps]); setTimeout(() => setPulses((p) => p.slice(ps.length)), 6000); }
              refresh();
            }
          } });
        } catch (e) { if (dead || e.name === "AbortError") break; }
        setConn("off");
        await new Promise((r) => setTimeout(r, Math.min(10000, 1000 * 2 ** retry++)));
      }
    })();
    return () => { dead = true; ctl?.abort(); };
  }, [iid]);

  // map clicks route by tool
  const onPoint = (p) => {
    if (tool === "stock") { setStockLoc(p); setTab("res"); setTool("inspect"); setDraft(null); return; }
    setLoc(p); setTab("key"); setDraft({ point: p, kind });
  };
  useEffect(() => { if (tool === "report" && loc) setDraft({ point: loc, kind }); }, [kind]);
  useEffect(() => { const f = (e) => { if (e.key === "Escape") { setTool("inspect"); setDraft(null); } }; window.addEventListener("keydown", f); return () => window.removeEventListener("keydown", f); }, []);

  const drill = async (action, days = 1) => { setBusy(true); try { const r = await api(`/ops/incidents/${iid}/drill`, { method: "POST", body: { action, days } }); toast(action === "seed" ? `Seeded ${r.data.reports} reports` : `Drill +${days} d: ${fmt.int(r.data.new_cases)} new cases`, "ok"); refresh(); } catch (e) { toast(e.message, "bad"); } setBusy(false); };

  if (status.data && !status.data.built) return html`<${Empty}>Viet Nam data is not built yet — run <code>hp vn-fetch</code>.</${Empty}>`;
  const incs = list.data || [];
  if (list.data && (!incs.length || showNew)) return html`<div class="stack">
    <${TitleRow} title="Live response" sub="Incident command: key in cases and vaccinations as they arrive, see the affected area, set real or simulated zones, optimise resources, ask the AI."
      right=${incs.length > 0 && html`<button class="btn ghost" onClick=${() => setShowNew(false)}>Cancel</button>`} />
    <${Panel} title="Open an incident">${canKey ? html`<${NewIncident} meta=${meta} canCmd=${canCmd} onMade=${(id) => { setShowNew(false); list.reload?.(); setQuery({ inc: id }); }} />`
      : html`<span class="small muted">Sign in (top right) as a data operator, scientist, reviewer or admin to open an incident. Viewers can follow open incidents.</span>`}</${Panel}>
  </div>`;
  if (!an) return err ? html`<${Empty} icon="warn">${err}</${Empty}>` : html`<${SkBlock} h=${600} />`;

  const inc = an.incident, k = an.kpis, pr = an.projection;
  const live = inc.mode === "live";
  const shown = plan || null;
  const hist = an.days.slice(-35), hv = an.series.confirmed.slice(-35);
  const xs = [...hist, ...pr.days];
  const pad = (a, before) => (before ? [...Array(hist.length).fill(null), ...a] : [...a, ...Array(pr.days.length).fill(null)]);
  const band = (s) => pad(s.cases.lo.map((lo, i) => [lo, s.cases.hi[i]]), true);
  // the upper band of an exponential scenario can be 10× the median: scale to the medians so the lines stay readable
  const ymax = 1.15 * Math.max(1, ...hv, ...pr.baseline.cases.mid, ...pr.current.cases.mid, ...pr.plan.cases.mid);
  const an2 = { ...an, provinces_all: Object.fromEntries(provincesAll.map((p) => [p.province_id, p])) };

  return html`<div class="stack sbx ops">
    <${TitleRow} title=${html`<span class="row" style="gap:10px">${live ? html`<span class="livebadge"><i></i>LIVE</span>` : html`<span class="pill sc">DRILL</span>`}${inc.name}</span>`}
      sub=${html`${inc.pathogen_spec.name} · as of ${fdate(an.as_of)} · <${Sem} k=${inc.semantics} /> reports, <${Sem} k="SCENARIO" /> projections ·
        <span class=${"conn " + conn}>${conn === "live" ? "● streaming" : conn === "connecting" ? "○ connecting…" : "○ offline — retrying"}</span>`}
      right=${html`<select class="incsel" value=${iid} onChange=${(e) => setQuery({ inc: e.target.value })} aria-label="Incident">${incs.map((x) => html`<option value=${x.incident_id}>${x.mode === "live" ? "● " : ""}${x.name} (${nf(x.cases)})</option>`)}</select>
        ${canKey && html`<button class="btn" onClick=${() => setShowNew(true)}><${Icon} n="plus" s=${13} />New</button>`}
        ${!live && canKey && html`<button class="btn" disabled=${busy} onClick=${() => drill("advance", 1)} title="Generate the next day of synthetic reports from the current projection"><${Icon} n="play" s=${12} />Drill +1 day</button>
          <button class="btn ghost" disabled=${busy} onClick=${() => drill("advance", 7)}>+7</button>`}`} />

    <${Kpis} items=${[
      { label: "Confirmed", value: nf(k.confirmed), sub: `${nf(k.today)} today · ${nf(k.suspected)} suspected` },
      { label: "Last 7 days", value: nf(k.last7), sub: k.change_7d != null ? `${k.change_7d > 0 ? "+" : ""}${fmt.pct(k.change_7d)} vs prior week` : "—" },
      { label: "R (reproduction)", value: k.R.toFixed(2), sub: `${k.R_lo}–${k.R_hi} · ${k.r_basis}${k.doubling_days ? ` · doubles ${k.doubling_days} d` : ""}` },
      { label: "Clusters", value: k.clusters, sub: `${k.growing_clusters} growing · ${k.provinces_affected} provinces` },
      { label: "Deaths", value: nf(k.deaths), sub: `${nf(k.recovered)} recovered` },
      { label: "Vaccinated", value: nf(k.vaccinated), sub: k.positivity != null ? `${nf(k.tested)} tested · ${fmt.pct(k.positivity, 1)} positive` : `${nf(k.tested)} tested` },
      { label: "In real quarantine", value: nf(k.people_in_real_quarantine), sub: `${k.zones_active} active zone(s)` },
    ]} />
    ${an.warnings.length > 0 && html`<div class="notice info small" role="status"><${Icon} n="info" s=${14} /><div>${an.warnings.join(" · ")}</div></div>`}

    <div class="grid">
      <div class="s8"><${Panel} flush cls="mappanel">
        <div class="toolbar">
          ${[["inspect", "Inspect", "search"], ["report", "Key in at click", "pin"], ["circle", "Circle zone", "plus"], ["polygon", "Polygon zone", "map"]].map(([t, l, ic]) => html`<button class=${"tool" + (tool === t ? " on" : "")}
            disabled=${t !== "inspect" && !canKey} onClick=${() => { setTool(t); setDraft(t === "circle" ? { kind: draft?.kind || "quarantine", basis: draft?.basis } : t === "polygon" ? { kind: draft?.kind || "quarantine", basis: draft?.basis, pts: [] } : null); if (t === "circle" || t === "polygon") setTab("zones"); if (t === "report") setTab("key"); }}>
            <${Icon} n=${ic} s=${13} />${l}</button>`)}
          <span class="spacer"></span>
          ${an.clusters.slice(0, 5).map((c) => html`<button class="tool" title=${c.label} onClick=${() => setFly({ lat: c.lat, lon: c.lon, zoom: 12 })}>${c.id}</button>`)}
          <button class="tool" onClick=${() => setFly({ lat: 16, lon: 106, zoom: 4.6 })}>All</button>
        </div>
        <${OpsMap} an=${an} reports=${reports} tool=${tool} draft=${draft} setDraft=${setDraft} onPoint=${onPoint} onZone=${(id) => { setSelZone(id); setTab("zones"); }}
          selZone=${selZone} plan=${shown} pulses=${pulses} fly=${fly} />
      </${Panel}></div>

      <div class="s4"><${Panel} flush cls="opsside">
        <div class="toolbar">${[["key", "Key in"], ["zones", `Zones${an.zones.filter((z) => z.status !== "lifted").length ? ` (${an.zones.filter((z) => z.status !== "lifted").length})` : ""}`], ["res", "Resources"], ["ai", "AI analyst"]].map(([t, l]) => html`<button class=${"tool" + (tab === t ? " on" : "")} onClick=${() => setTab(t)}>${l}</button>`)}</div>
        <div class="sidebody">
          ${tab === "key" && html`<${KeyIn} iid=${iid} loc=${loc} setLoc=${(v) => { setLoc(v); setDraft(null); }} provinces=${provincesAll} can=${canKey && inc.status === "open"} setTool=${setTool} kind=${kind} setKind=${setKind} onDone=${refresh} />`}
          ${tab === "zones" && html`<${ZonesTab} iid=${iid} an=${{ ...an, zone_defaults: meta?.zone_defaults }} can=${canKey} canCmd=${canCmd} tool=${tool} setTool=${setTool} draft=${draft} setDraft=${setDraft} selZone=${selZone} setSelZone=${setSelZone} refresh=${refresh} setFly=${setFly} />`}
          ${tab === "res" && html`<${ResourcesTab} iid=${iid} an=${an2} can=${canKey} canCmd=${canCmd} plan=${plan} setPlan=${setPlan} stockLoc=${stockLoc} setStockLoc=${setStockLoc} setTool=${setTool} refresh=${refresh} />`}
          ${tab === "ai" && html`<${AiTab} iid=${iid} refresh=${refresh} meta=${meta} />`}
        </div>
      </${Panel}></div>

      <div class="s8"><${Panel} title="Cases: observed and the next 28 days" note=${html`Baseline = no new measures · Current = real zones + approved plan · Plan = current + simulated zones + latest proposed plan. Band = growth-rate 95% interval.`}>
        <div class="small muted" style="margin-bottom:2px">Observed daily confirmed cases, last 35 days</div>
        <${Bars} data=${hist.map((t, i) => ({ t, y: hv[i] }))} height=${90} label="Observed daily confirmed cases" unit="cases" color="#d6403a" />
        <div class="small muted" style="margin:10px 0 2px">Projected daily cases (scenario)</div>
        <${MultiLine} x=${xs} height=${230} label="Observed and projected daily cases" ymax=${ymax} markers=${[{ from: hist.length - 1, to: xs.length - 1, label: "scenario" }]} series=${[
          { name: "Observed (confirmed)", color: "var(--text)", values: pad(hv, false) },
          { name: "Baseline", color: "#8a94a0", dash: true, values: pad(pr.baseline.cases.mid, true) },
          { name: "Current", color: "#ee9a3a", values: pad(pr.current.cases.mid, true), band: band(pr.current) },
          { name: "Plan", color: "#2f70bb", values: pad(pr.plan.cases.mid, true), band: band(pr.plan) }]} />
        <div class="minikpis" style="margin-top:10px">
          ${[["Baseline", pr.baseline], ["Current", pr.current], ["Plan", pr.plan]].map(([l, s]) => html`<div><span>${l} · 28 d</span><b class="num">${nf(s.cum_cases)}</b>
            <span class="faint num">${nf(s.cum_lo)}–${nf(s.cum_hi)} · ${fmt.d1(s.deaths)} deaths · peak no-bed ${nf(s.peak_unmet)}${s.averted_vs_baseline ? ` · averts ${nf(s.averted_vs_baseline)}` : ""}</span></div>`)}</div>
        <details style="margin-top:8px"><summary class="link small">Assumptions</summary><ul class="small muted">${pr.assumptions.map((a) => html`<li>${a}</li>`)}</ul>
          <div class="small faint">Methods: ${Object.entries(an.method).map(([kk, v]) => `${kk}: ${v}`).join(" · ")} · computed in ${an.ms} ms</div></details>
      </${Panel}></div>

      <div class="s4"><${Panel} title="Live feed" flush note=${`${feed.length} entries · every controller`}>
        <div class="feed" aria-live="polite" style="max-height:330px">${feed.map((e) => html`<div class=${"ev k-" + e.kind + (e.fresh ? " fresh" : "")}>
          <span class="evi"></span><span style="flex:1">${e.text}<div class="small faint">${ago(e.ts)}${e.actor ? ` · ${e.actor}` : ""}</div></span></div>`)}
          ${!feed.length && html`<div style="padding:12px 20px" class="small muted">Nothing yet.</div>`}</div>
      </${Panel}></div>

      <div class="s7"><${Panel} title="Clusters" flush note="DBSCAN on report locations · area = buffered hull · R per cluster from its own growth">
        ${an.clusters.length ? html`<table class="tbl compact"><thead><tr><th>Cluster</th><th class="num">Cases</th><th class="num">7 d</th><th class="num">R</th><th>Trend</th><th class="num">Area</th><th class="num">People (est.)</th><th>Zones</th></tr></thead>
          <tbody>${an.clusters.map((c) => html`<tr class="click" onClick=${() => setFly({ lat: c.lat, lon: c.lon, zoom: 12 })}><td><b>${c.id}</b> <span class="muted">${c.province}</span></td>
            <td class="num">${nf(c.cases)}</td><td class="num">${nf(c.last7)}</td><td class="num" title=${`${c.growth.R_lo.toFixed(2)}–${c.growth.R_hi.toFixed(2)} (${c.growth.basis})`}>${c.growth.R.toFixed(2)}</td>
            <td><span class=${"pill " + (c.trend === "growing" ? "bad" : c.trend === "stable" ? "warn" : "ok")}>${c.trend}${c.doubling_days ? ` · ×2 ${c.doubling_days}d` : ""}</span></td>
            <td class="num">${fmt.d1(c.affected_km2)} km²</td><td class="num">${nf(c.affected_people_est)}</td><td class="small">${c.zones?.length || html`<span class="faint">none</span>`}</td></tr>`)}</tbody></table>`
          : html`<div style="padding:12px 20px" class="small muted">No cluster yet (needs ≥ 3 cases within ${inc.settings.cluster_km} km).</div>`}
      </${Panel}></div>

      <div class="s5"><${Panel} title="Provinces" flush note=${`${an.safe_provinces.length} safe province(s) shaded green on the map`}>
        <table class="tbl compact"><thead><tr><th>Province</th><th class="num">7 d</th><th class="num">/100k</th><th class="num">Beds est.</th><th class="num">Vacc.</th></tr></thead>
          <tbody>${an.provinces.slice(0, 10).map((p) => html`<tr class="click" onClick=${() => setFly({ lat: p.lat, lon: p.lon, zoom: 8 })}><td>${p.name}</td><td class="num">${nf(p.last7)}</td><td class="num">${p.per100k_7d}</td>
            <td class="num" title="occupied (est.) / free beds for this outbreak">${nf(p.hosp_est)}/${nf(p.beds_free_est)}</td><td class="num">${fmt.pct(p.coverage, 1)}</td></tr>`)}</tbody></table>
      </${Panel}></div>
    </div>
  </div>`;
}
