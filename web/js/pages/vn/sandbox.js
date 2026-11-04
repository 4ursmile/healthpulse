// Viet Nam outbreak sandbox — pick a pathogen, set weather / mobility / workforce / vaccines, seed it on the map, press play
// and intervene while it spreads. The server steps a stochastic metapopulation model; every number is a SCENARIO.
import { html, useEffect, useMemo, useRef, useState } from "../../../vendor/preact-htm.js";
import { api, useApi } from "../../api.js";
import { Panel, TitleRow, Empty, Icon, Seg, Prov, fmt, fdate, toast, SkBlock } from "../../ui.js";
import { VnMap, LEVEL, HEAT, SEQ } from "../../vnmap.js";
import { MultiLine, md } from "./_vn.js";
import { scopeOf } from "../../scope.js";
import { useMobilitySuggestion } from "../../geo_mobility.js";

const BASE_COL = { nihe: "#2f70bb", pasteur_nt: "#6a4fc2", tihe: "#1e7b4f", pasteur_hcm: "#a86f0c" };
const SPEEDS = [1, 2, 4, 8, 16];
const RT_COL = ["#3f7fbf", "#9cc3e4", "#e9ecea", "#f3d36b", "#ee9a3a", "#d6403a"];

// map tools: what a click on a province does. `p` = parameters the tool panel edits.
const TOOLS = [
  { k: "inspect", l: "Inspect", ic: "search", hint: "Click a province to see its numbers." },
  { k: "seed", l: "Seed", ic: "plus", hint: "Introduce infections where you click.", p: { value: 500 } },
  { k: "lockdown", l: "Distancing", ic: "people", hint: "Cut contacts in the province (0 lifts it).", p: { value: 0.5 } },
  { k: "border", l: "Close border", ic: "x", hint: "Close the province's travel links (0 reopens).", p: { value: 0.8 } },
  { k: "vaccinate", l: "Vaccinate", ic: "check", hint: "Start a campaign: doses per 1,000 people per day.", p: { value: 5, days: 30 } },
  { k: "team", l: "Send team", ic: "facility", hint: "Dispatch rapid-response teams from the nearest base (0 recalls).", p: { value: 1 } },
  { k: "staff", l: "Move nurses", ic: "deploy", hint: "Click the source province, then the destination.", p: { value: 200 } },
  { k: "vector_control", l: "Vector control", ic: "alert", hint: "Dengue only: cut Aedes transmission for N days.", p: { value: 0.25, days: 30 }, route: "vector" },
  { k: "weather", l: "Weather", ic: "sun", hint: "Trigger a weather event over the province.", p: { kind: "heatwave", days: 14 } },
  { k: "corridor", l: "Corridor", ic: "lineage", hint: "Click two provinces to add a travel corridor (daily trips).", p: { value: 30000 } },
];
const METRICS = [["active", "Infections/100k"], ["load", "Hospital load"], ["rt", "R(t)"], ["vacc", "Vaccinated"], ["deaths", "Deaths/100k"], ["climate", "Weather effect"]];

const nf = (v) => (v == null ? "—" : Math.abs(v) < 1000 ? fmt.int(Math.round(v)) : fmt.compact(v));
const PATH_KEYS = ["r0", "latent", "infectious", "report", "p_hosp", "los", "cfr_hosp", "untreated", "immune", "vax_inf", "vax_sev", "epidemic_scale", "shock_sd"];
const pathDefaults = (cat, key) => ({ key, ...Object.fromEntries(PATH_KEYS.filter((k) => cat.pathogens[key][k] != null).map((k) => [k, cat.pathogens[key][k]])), climate_sensitivity: 1 });

function Slider({ label, value, set, min, max, step, f = (v) => v, hint }) {
  return html`<label class="sl"><span class="row"><span>${label}</span><span class="spacer"></span><b class="num">${f(value)}</b></span>
    <input type="range" min=${min} max=${max} step=${step} value=${value} onInput=${(e) => set(+e.target.value)} aria-label=${label} />
    ${hint && html`<span class="small faint">${hint}</span>`}</label>`;
}
const Group = ({ title, open = false, children, badge }) => html`<details class="cgroup" open=${open}><summary><span>${title}</span>${badge}<${Icon} n="chevron" s=${12} /></summary><div class="cbody">${children}</div></details>`;

// Calibration provenance for the chosen pathogen: what Viet Nam's own outbreak data fixed (and what it cannot), reset buttons,
// the dengue year-amplitude scenarios and a back-test chart. Values the data did not identify stay labelled assumptions.
const CAL_LABEL = { p_hosp: ["Hospitalised per infection", (v) => fmt.pct(v, 2)], immune: ["Already immune", (v) => fmt.pct(v)], r0: ["R₀", (v) => v.toFixed(2)],
  shock_sd: ["Ensemble spread (shock SD)", (v) => v.toFixed(3)], epidemic_scale: ["Year amplitude", (v) => `×${v.toFixed(2)}`] };
function CalNote({ pa, pk, setP, scale }) {
  const c = pa.calibration;
  const [open, setOpen] = useState(false);
  const [bt, setBt] = useState(null);
  const name = c?.backtest || (pk === "dengue" && c ? "dengue_annual" : null);
  useEffect(() => { setBt(null); setOpen(false); }, [pk]);
  useEffect(() => { if (open && name && !bt) api(`/vn/calibration/backtest/${name}`).then((r) => setBt(r.data)).catch(() => setBt({ error: true })); }, [open, name]);
  const rows = c ? Object.entries(c.params).filter(([, v]) => v.kind !== "scenario_reference" && (v.applied || v.interval)) : [];
  const iv = (v, f) => (v.interval ? ` [${f(v.interval[0])}–${f(v.interval[1])}]` : "");
  const sty = "border:1px solid var(--border);border-radius:8px;padding:10px 12px;background:var(--raised);display:flex;flex-direction:column;gap:6px";
  if (!c) return html`<div class="small faint" style=${sty}><span><b>Literature defaults.</b> No Viet Nam outbreak data were used for this pathogen.</span></div>`;
  const wk = bt && !bt.error && bt.weeks;
  return html`<div style=${sty}>
    <span class="row" style="gap:6px"><span class="pill sc">Calibrated · ${c.status === "PARTIAL" ? "partial" : c.status}</span>${c.stale && html`<span class="pill" title="The sandbox engine changed since this calibration was made. Run hp vn-calibrate.">stale</span>`}</span>
    <span class="small muted">${c.note}</span>
    ${rows.length > 0 && html`<ul class="small" style="margin:0;padding-left:16px">${rows.map(([k, v]) => { const [l, f] = CAL_LABEL[k] || [k, String]; return html`<li><b>${l}</b>${" "}
      ${v.applied && v.value != null ? html`${f(v.value)}${iv(v, f)} <span class="faint">· ${v.kind === "identified" ? "identified" : "pinned combination"}</span>` : html`<span class="faint">not applied${v.interval ? ` · data range${iv(v, f)}` : ""}</span>`}
      <span class="faint"> — ${v.note}</span></li>`; })}</ul>`}
    ${c.scenarios && html`<span class="small muted">Year amplitude (not predictable from climate):</span>
      <span class="row" style="gap:6px;flex-wrap:wrap">${c.scenarios.map((s) => html`<button class=${"btn ghost" + (Math.abs((scale ?? 1) - s.value) < 0.005 ? " on" : "")} style="padding:2px 8px;font-size:12px;height:auto;min-height:0;line-height:1.5" title=${s.basis}
        onClick=${() => setP({ epidemic_scale: s.value })}>${s.label} ×${s.value.toFixed(2)}</button>`)}</span>`}
    <span class="row" style="gap:6px;flex-wrap:wrap">
      <button class="btn ghost" style="padding:2px 8px;font-size:12px;height:auto;min-height:0;line-height:1.5" onClick=${() => setP(pa.calibrated)}>Reset to calibrated</button>
      <button class="btn ghost" style="padding:2px 8px;font-size:12px;height:auto;min-height:0;line-height:1.5" onClick=${() => setP(pa.literature)}>Reset to literature default</button>
      ${name && html`<button class="btn ghost" style="padding:2px 8px;font-size:12px;height:auto;min-height:0;line-height:1.5" onClick=${() => setOpen((o) => !o)}>${open ? "Hide" : "Show"} back-test</button>`}</span>
    ${open && !bt && html`<span class="small faint">Loading…</span>`}
    ${open && bt?.error && html`<span class="small faint">Back-test not available.</span>`}
    ${open && wk && html`<span class="small muted">${bt.label}: reported (black) vs the engine replaying the fitted R(t) (blue, P10–P90). In-sample.
      Cases WAPE ${fmt.pct(bt.metrics.engine.wape_cases, 0)}, deaths WAPE ${fmt.pct(bt.metrics.engine.wape_deaths, 0)}; the engine reproduces ${fmt.pct(bt.metrics.engine.cum_cases_ratio, 0)} of the reported cases.</span>
      <${MultiLine} x=${bt.weeks} height=${150} label="weekly reported cases" minW=${180} series=${[{ name: "Reported", color: "var(--text)", values: bt.observed_cases },
        { name: "Engine", color: "#2f70bb", values: bt.engine_cases[1], band: bt.engine_cases[0].map((lo, i) => [lo, bt.engine_cases[2][i]]) }]} />
      <${MultiLine} x=${bt.weeks} height=${120} label="weekly reported deaths" minW=${180} series=${[{ name: "Reported", color: "var(--text)", values: bt.observed_deaths },
        { name: "Engine", color: "#c2410c", values: bt.engine_deaths[1], band: bt.engine_deaths[0].map((lo, i) => [lo, bt.engine_deaths[2][i]]) }]} />`}
    ${open && bt?.years && html`<span class="small muted">Annual dengue: reported vs the climate model at the WHO level. Reported totals swing ${fmt.compact(Math.min(...bt.observed))}–${fmt.compact(Math.max(...bt.observed))}; the model stays near ${fmt.compact([...bt.glm_x_kappa].sort((a, b) => a - b)[Math.floor(bt.glm_x_kappa.length / 2)])}.${" "}
      ${bt.target_2022 && html`2022 (annual total only): reported ${fmt.compact(bt.target_2022.target)}, model ${fmt.compact(bt.target_2022.glm_x_kappa)}.`}</span>
      <${MultiLine} x=${bt.years} height=${150} xfmt=${(v) => String(v)} label="annual dengue reported vs model" minW=${180} series=${[{ name: "Reported", color: "var(--text)", values: bt.observed },
        { name: "Climate model × WHO level", color: "#2f70bb", values: bt.glm_x_kappa }]} />`}
  </div>`;
}

function colorOf(metric, v) {
  if (v == null || !isFinite(v)) return null;
  if (metric === "load") return v >= 1 ? LEVEL.alert.c : v >= 0.85 ? LEVEL.warning.c : v >= 0.6 ? LEVEL.watch.c : v > 0.02 ? "#b3d6df" : LEVEL.normal.c;
  if (metric === "rt") return RT_COL[v < 0.7 ? 0 : v < 0.95 ? 1 : v < 1.05 ? 2 : v < 1.3 ? 3 : v < 1.8 ? 4 : 5];
  if (metric === "vacc") return SEQ[Math.min(5, Math.floor(v * 6))];
  if (metric === "climate") return RT_COL[v < 0.85 ? 0 : v < 0.97 ? 1 : v < 1.03 ? 2 : v < 1.15 ? 3 : v < 1.3 ? 4 : 5];
  const br = metric === "deaths" ? [0.1, 1, 5, 20, 80] : [1, 10, 50, 200, 1000];
  if (v < br[0] / 10) return LEVEL.normal.c;
  let k = 0; while (k < br.length && v > br[k]) k++;
  return HEAT[k];
}

export default function VnSandbox({ q }) {
  const status = useApi("/vn/status");
  const mobSug = useMobilitySuggestion();   // Meta distance-from-home bounds; labelled, never applied silently
  const catQ = useApi(status.data?.built ? "/vn/sandbox/catalog" : null);
  const cat = catQ.data;
  const [spec, setSpec] = useState(null);
  const [ses, setSes] = useState(null);           // session (static parts + frames)
  const [frames, setFrames] = useState([]);
  const [view, setView] = useState(0);            // frame index shown
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  const [tool, setTool] = useState("inspect");
  const [tp, setTp] = useState({});               // tool params per tool
  const [pick, setPick] = useState(null);         // first province for two-click tools
  const [sel, setSel] = useState(null);
  const [metric, setMetric] = useState("active");
  const [busy, setBusy] = useState(false);
  const [adv, setAdv] = useState(null), [advBusy, setAdvBusy] = useState(false), [advQ, setAdvQ] = useState("");
  const [lang, setLang] = useState(() => { try { return localStorage.getItem("hp_vn_lang") || "en"; } catch (e) { return "en"; } });
  const pending = useRef([]);
  const live = useRef({});
  live.current = { ses, playing, speed, frames };

  useEffect(() => { if (cat && !spec) {
    const d = cat.defaults;
    const key = q.get("pathogen") && cat.pathogens[q.get("pathogen")] ? q.get("pathogen") : "dengue";
    const o = q.get("origin") || scopeOf(q).region;
    setSpec({ ...d, pathogen: pathDefaults(cat, key), seeds: o ? [{ province_id: o, infections: 2000 }] : key === "dengue" ? [] : [{ province_id: "VN79", infections: 300 }] });
  } }, [cat]);
  useEffect(() => { try { localStorage.setItem("hp_vn_lang", lang); } catch (e) {} }, [lang]);

  const set = (k, patch) => setSpec((s) => ({ ...s, [k]: typeof patch === "object" && !Array.isArray(patch) ? { ...s[k], ...patch } : patch }));
  const setP = (patch) => set("pathogen", patch);
  const provs = ses?.provinces || [];
  const pById = useMemo(() => Object.fromEntries(provs.map((p) => [p.province_id, p])), [provs]);
  const geoProvs = useApi(status.data?.built ? "/vn/provinces" : null).data || [];
  const gById = useMemo(() => Object.fromEntries(geoProvs.map((p) => [p.province_id, p])), [geoProvs]);
  const ids = geoProvs.map((p) => p.province_id);
  const nameOf = (id) => (id === "ALL" ? "all provinces" : gById[id]?.name || id);

  // ------------------------------------------------------------------ session lifecycle
  const start = async () => {
    setBusy(true); setPlaying(false); setAdv(null);
    try {
      const r = await api("/vn/sandbox", { method: "POST", body: spec });
      setSes(r.data); setFrames(r.data.frames); setView(r.data.frames.length - 1);
      toast(`${r.data.pathogen.name} sandbox started — press play`, "ok");
    } catch (e) { toast(e.message, "bad"); }
    setBusy(false);
  };
  const absorb = (d, replaceLast) => {
    setSes((s) => ({ ...s, ...d, frames: undefined }));
    setFrames((f) => { const n = replaceLast ? [...f.slice(0, -1), ...d.frames] : [...f, ...d.frames]; setView(n.length - 1); return n; });
    (d.errors || []).forEach((e) => toast(e.error, "bad"));
  };
  const stepping = useRef(false);
  const step = async (days, actions = []) => {
    const s = live.current.ses;
    if (!s || stepping.current) { if (actions.length) pending.current.push(...actions); return; }
    stepping.current = true;
    const acts = [...pending.current, ...actions]; pending.current = [];
    try {
      const r = await api(`/vn/sandbox/${s.sandbox_id}/step`, { method: "POST", body: { days, actions: acts } });
      absorb(r.data, days === 0);
      if (r.data.day >= r.data.max_days) setPlaying(false);
    } catch (e) { setPlaying(false); toast(e.message, "bad"); if (/expired/.test(e.message)) setSes(null); }
    stepping.current = false;
    // actions that arrived while this request was in flight: apply them now if nothing else will
    if (pending.current.length && !live.current.playing) step(0);
  };
  // play loop: one request at a time; larger chunks at high speed
  useEffect(() => {
    if (!playing) return;
    let dead = false;
    const loop = async () => {
      while (!dead && live.current.playing) {
        const sp = live.current.speed, t0 = performance.now();
        const days = sp >= 8 ? Math.round(sp / 4) : 1;
        await step(days);
        const wait = (1000 * days) / sp - (performance.now() - t0);
        await new Promise((res) => setTimeout(res, Math.max(16, wait)));
      }
    };
    loop();
    return () => { dead = true; };
  }, [playing]);
  useEffect(() => {
    const k = (e) => { if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
      if (e.key === " " && live.current.ses) { e.preventDefault(); setPlaying((p) => !p); }
      if (e.key === "Escape") { setTool("inspect"); setPick(null); } };
    window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k);
  }, []);
  const act = (a) => { const list = Array.isArray(a) ? a : [a]; if (playing) { pending.current.push(...list); toast(list.length > 1 ? `Queued ${list.length} actions` : `Queued: ${list[0].type} ${nameOf(list[0].province_id)}`); } else step(0, list); };
  const rewindTo = async (day) => {
    setPlaying(false); setBusy(true);
    try { const r = await api(`/vn/sandbox/${ses.sandbox_id}/rewind`, { method: "POST", body: { day } }); setSes(r.data); setFrames(r.data.frames); setView(r.data.frames.length - 1); toast(`Rewound to day ${day} — new branch`); }
    catch (e) { toast(e.message, "bad"); }
    setBusy(false);
  };
  const advise = async (question) => {
    if (!ses) return;
    setAdvBusy(true);
    try { const r = await api(`/vn/sandbox/${ses.sandbox_id}/advise`, { method: "POST", body: { lang, question: question || null } }); setAdv(r.data); }
    catch (e) { toast(e.message, "bad"); }
    setAdvBusy(false);
  };

  // ------------------------------------------------------------------ map click per tool
  const params = (k) => ({ ...(TOOLS.find((t) => t.k === k)?.p || {}), ...(tp[k] || {}) });
  const onMap = (id) => {
    if (tool === "inspect") { setSel(id === sel ? null : id); return; }
    const p = params(tool);
    if (!ses) {
      // setup phase: seeds and corridors go into the starting spec
      if (tool === "seed") { setSpec((s) => ({ ...s, seeds: [...s.seeds.filter((x) => x.province_id !== id), { province_id: id, infections: Math.round(p.value) }] })); setSel(id); return; }
      if (tool === "corridor") { if (!pick) { setPick(id); return; } if (pick !== id) set("mobility", { corridors: [...spec.mobility.corridors, { from_id: pick, to_id: id, travellers: p.value }] }); setPick(null); return; }
      toast("Start the outbreak first — this tool acts on a running sandbox.");
      return;
    }
    if (tool === "corridor" || tool === "staff") {
      if (!pick) { setPick(id); toast(`${nameOf(id)} selected — now click the ${tool === "staff" ? "destination" : "other end"}`); return; }
      if (pick !== id) act({ type: tool, province_id: pick, to_id: id, value: p.value });
      setPick(null); return;
    }
    act({ type: tool, province_id: id, ...p });
    setSel(id);
  };

  // ------------------------------------------------------------------ derived view
  const F = frames[Math.min(view, frames.length - 1)];
  const atHead = frames.length && view >= frames.length - 1;
  const vals = useMemo(() => {
    if (!F || !ids.length) return {};
    const P = F.prov, out = {};
    provs.forEach((p, i) => {
      const n = Math.max(1, p.population);
      out[p.province_id] = metric === "active" ? (P.active[i] / n) * 1e5 : metric === "load" ? P.hosp[i] / Math.max(1, P.cap[i]) : metric === "rt" ? P.rt[i]
        : metric === "vacc" ? P.vacc[i] : metric === "deaths" ? (P.deaths[i] / n) * 1e5 : F.climate_mult[i];
    });
    return out;
  }, [F, metric, provs]);
  const colors = useMemo(() => Object.fromEntries(Object.entries(vals).map(([k, v]) => [k, colorOf(metric, v)])), [vals, metric]);
  const flows = useMemo(() => { if (!F?.flows?.length || !geoProvs.length) return [];
    const mx = Math.log10(1 + Math.max(...F.flows.map((f) => f.v)));
    return F.flows.filter((f) => gById[f.from] && gById[f.to]).map((f) => ({ from: [gById[f.from].lon, gById[f.from].lat], to: [gById[f.to].lon, gById[f.to].lat], w: Math.max(0.15, Math.log10(1 + f.v) / mx) })); }, [F, gById]);
  const corridors = ses ? ses.corridors : spec?.mobility.corridors || [];
  const routes = [...corridors.filter((c) => gById[c.from_id] && gById[c.to_id]).map((c) => ({ from: [gById[c.from_id].lon, gById[c.from_id].lat], to: [gById[c.to_id].lon, gById[c.to_id].lat] })),
    ...(F?.teams || []).filter((t) => t.state === "travel").map((t) => ({ from: [gById[t.from].lon, gById[t.from].lat], to: [gById[t.to].lon, gById[t.to].lat] }))];
  const movers = (F?.teams || []).map((t, i) => ({ lon: t.lon + (t.state === "travel" ? 0 : ((i % 3) - 1) * 0.08), lat: t.lat + (t.state === "travel" ? 0 : ((Math.floor(i / 3) % 3) - 1) * 0.08), c: t.state === "idle" ? "#9aa19e" : BASE_COL[t.base] }));
  const pulses = F ? provs.map((p, i) => (F.prov.unmet[i] > 0 ? { lon: p.lon, lat: p.lat, level: "alert" } : F.prov.rt[i] > 1.3 && (F.prov.active[i] / p.population) * 1e5 > 30 ? { lon: p.lon, lat: p.lat, level: "warning" } : null)).filter(Boolean)
    : (spec?.seeds || []).filter((s) => gById[s.province_id]).map((s) => ({ lon: gById[s.province_id].lon, lat: gById[s.province_id].lat, level: "alert" }));
  const labels = useMemo(() => {
    if (!F) return (spec?.seeds || []).filter((s) => gById[s.province_id]).map((s) => ({ id: s.province_id, lon: gById[s.province_id].lon, lat: gById[s.province_id].lat, level: "alert", text: `${gById[s.province_id].name} · seed ${fmt.compact(s.infections)}` }));
    const f = { active: (v) => fmt.compact(v), load: (v) => fmt.pct(v), rt: (v) => v.toFixed(2), vacc: (v) => fmt.pct(v), deaths: (v) => v.toFixed(1), climate: (v) => `×${v.toFixed(2)}` }[metric];
    return Object.entries(vals).filter(([id, v]) => id === sel || (metric === "rt" || metric === "climate" ? false : v > 0)).sort((a, b) => (b[0] === sel) - (a[0] === sel) || b[1] - a[1]).slice(0, 5)
      .map(([id, v]) => ({ id, lon: gById[id]?.lon, lat: gById[id]?.lat, level: metric === "load" ? (v >= 1 ? "alert" : v >= 0.85 ? "warning" : null) : null, text: `${gById[id]?.name} ${f(v)}` })).filter((l) => l.lon != null);
  }, [vals, sel, metric, F, spec?.seeds, gById]);

  const series = useMemo(() => {
    const x = frames.map((f) => f.date);
    return { x, active: frames.map((f) => f.nat.active[1]), activeB: frames.map((f) => [f.nat.active[0], f.nat.active[2]]), hosp: frames.map((f) => f.nat.hosp[1]),
      cap: frames.map((f) => f.nat.cap), deaths: frames.map((f) => f.nat.deaths[1]), deathsB: frames.map((f) => [f.nat.deaths[0], f.nat.deaths[2]]), rt: frames.map((f) => f.nat.rt) };
  }, [frames]);
  const selI = sel && ses ? provs.findIndex((p) => p.province_id === sel) : -1;

  if (status.data && !status.data.built) return html`<div class="page"><${TitleRow} title="Viet Nam — outbreak sandbox" /><${Empty}>Run <code>.venv/bin/hp vn-fetch</code> first.</${Empty}></div>`;
  if (!cat || !spec) return html`<div class="page"><${TitleRow} title="Viet Nam — outbreak sandbox" sub="Loading the 34-province model…" /><${SkBlock} h=${600} /></div>`;
  const pa = cat.pathogens[spec.pathogen.key];
  const route = ses?.pathogen.route || pa.route;
  const T = TOOLS.find((t) => t.k === tool);
  const n = F?.nat;

  return html`<div class="page vn sbx">
    <${TitleRow} title="Viet Nam — outbreak sandbox"
      sub=${ses ? html`<b>${ses.pathogen.name}</b> · day ${F?.day ?? 0} of ${ses.max_days} · ${fdate(F?.date)} · ${ses.spec.trials} trials · weather ${ses.spec.weather.mode}. Scenario under the listed assumptions — not a forecast.`
        : "Choose a pathogen, shape the world, seed it on the map — then press play and fight it. Real provinces, populations, climate and capacity; assumed pathogen and intervention effects."}
      right=${html`${ses && html`<label class="pill toggle-pill" title="Let the rule-based advisor act every 7 days"><input type="checkbox" checked=${ses.autopilot} onChange=${(e) => act({ type: "autopilot", value: e.target.checked ? 1 : 0 })} />Autopilot</label>`}
        <button class=${"btn " + (ses ? "" : "pri")} disabled=${busy} onClick=${start}><${Icon} n=${ses ? "refresh" : "play"} s=${13} />${ses ? "Restart with settings" : "Start outbreak"}</button>
        ${ses && html`<button class="btn ghost" onClick=${() => { setPlaying(false); setSes(null); setFrames([]); setAdv(null); }}>Back to setup</button>`}`} />

    <div class="grid">
      <div class="s3 sim-side"><div class="panel controls">
        <${Group} title="Pathogen" open=${!ses} badge=${html`<span class="pill sc">Assumption</span>`}>
          <div class="pgrid">${Object.entries(cat.pathogens).map(([k, v]) => html`<button class=${"pcard" + (spec.pathogen.key === k ? " on" : "")} disabled=${!!ses}
            onClick=${() => setSpec((s) => ({ ...s, pathogen: pathDefaults(cat, k), seeds: s.seeds.length || k === "dengue" ? s.seeds : [{ province_id: "VN79", infections: 300 }] }))}>
            <b>${v.name}</b><span class="small muted">${v.route} · R₀ ${v.r_effective ? "fitted" : v.r0}</span></button>`)}</div>
          <p class="small muted" style="margin:0">${pa.note}</p>
          <${CalNote} pa=${pa} pk=${spec.pathogen.key} setP=${setP} scale=${spec.pathogen.epidemic_scale} />
          <${Slider} label=${pa.r_effective ? "Transmission × fitted R(t)" : "R₀ (basic reproduction number)"} value=${spec.pathogen.r0} set=${(v) => setP({ r0: v })} min=${pa.r_effective ? 0.5 : 0.5} max=${pa.r_effective ? 2 : 20} step=${pa.r_effective ? 0.05 : 0.1} f=${(v) => (pa.r_effective ? `${v.toFixed(2)}×` : v.toFixed(1))} />
          <${Slider} label="Already immune" value=${spec.pathogen.immune} set=${(v) => setP({ immune: v })} min=${0} max=${0.98} step=${0.01} f=${(v) => fmt.pct(v)} />
          <${Slider} label="Incubation (latent) days" value=${spec.pathogen.latent} set=${(v) => setP({ latent: v })} min=${0.5} max=${21} step=${0.5} />
          <${Slider} label="Infectious days" value=${spec.pathogen.infectious} set=${(v) => setP({ infectious: v })} min=${1} max=${21} step=${0.5} />
          <${Slider} label="Hospitalised per infection" value=${spec.pathogen.p_hosp} set=${(v) => setP({ p_hosp: v })} min=${0} max=${0.3} step=${0.001} f=${(v) => fmt.pct(v, 1)} />
          <${Slider} label="Deaths per admission (treated)" value=${spec.pathogen.cfr_hosp} set=${(v) => setP({ cfr_hosp: v })} min=${0} max=${0.4} step=${0.001} f=${(v) => fmt.pct(v, 1)} />
          <${Slider} label="Death risk × without a bed" value=${spec.pathogen.untreated} set=${(v) => setP({ untreated: v })} min=${1} max=${30} step=${0.5} f=${(v) => `×${v}`} />
          <${Slider} label="Length of stay" value=${spec.pathogen.los} set=${(v) => setP({ los: v })} min=${1} max=${30} step=${0.5} f=${(v) => `${v} d`} />
          <${Slider} label="Climate sensitivity" value=${spec.pathogen.climate_sensitivity} set=${(v) => setP({ climate_sensitivity: v })} min=${0} max=${3} step=${0.1} f=${(v) => `×${v.toFixed(1)}`} hint=${route === "vector" ? "scales the fitted GLM climate response" : "scales the assumed weather response"} />
        </${Group}>
        <${Group} title="Vaccine" badge=${html`<span class="pill sc">Assumption</span>`}>
          <${Slider} label="Coverage at start (of non-immune)" value=${spec.vaccine.coverage} set=${(v) => set("vaccine", { coverage: v })} min=${0} max=${1} step=${0.01} f=${(v) => fmt.pct(v)} />
          <${Slider} label="Efficacy vs infection" value=${spec.pathogen.vax_inf} set=${(v) => setP({ vax_inf: v })} min=${0} max=${1} step=${0.01} f=${(v) => fmt.pct(v)} />
          <${Slider} label="Efficacy vs severe disease" value=${spec.pathogen.vax_sev} set=${(v) => setP({ vax_sev: v })} min=${0} max=${1} step=${0.01} f=${(v) => fmt.pct(v)} />
          <span class="small faint">Campaigns during the run: use the Vaccinate tool on the map.</span>
        </${Group}>
        <${Group} title="Weather" badge=${html`<span class="pill">NASA POWER</span>`}>
          <${Seg} label="Weather" value=${spec.weather.mode} onChange=${(v) => set("weather", { mode: v })} options=${[["normal", "Normal year"], ["observed", "Observed now"], ["custom", "Custom"]]} />
          <span class="small faint">${spec.weather.mode === "observed" ? `1994–2023 normals + the anomalies observed to ${cat.weather_observed_until.slice(0, 7)}` : spec.weather.mode === "normal" ? "1994–2023 monthly climate normals per province" : "normals shifted by your settings"}</span>
          ${spec.weather.mode === "custom" && html`
            <${Slider} label="Temperature shift" value=${spec.weather.dt} set=${(v) => set("weather", { dt: v })} min=${-5} max=${5} step=${0.25} f=${(v) => `${v > 0 ? "+" : ""}${v} °C`} />
            <${Slider} label="Rainfall" value=${spec.weather.rain_mult} set=${(v) => set("weather", { rain_mult: v })} min=${0} max=${3} step=${0.05} f=${(v) => `×${v.toFixed(2)}`} />
            <${Slider} label="Humidity shift" value=${spec.weather.drh} set=${(v) => set("weather", { drh: v })} min=${-25} max=${25} step=${1} f=${(v) => `${v > 0 ? "+" : ""}${v} pts`} />`}
        </${Group}>
        <${Group} title="Mobility">
          <${Slider} label="Contacts outside home province" value=${spec.mobility.share} set=${(v) => set("mobility", { share: v })} min=${0} max=${0.2} step=${0.005} f=${(v) => fmt.pct(v, 1)} hint="gravity model: population ÷ distance²" />
          ${mobSug && html`<div class="small faint" style="margin:-4px 0 6px">Meta device pings (${mobSug.window}), population-weighted: <b>${fmt.pct(mobSug.lower, 1)}</b> are 100+ km from home,
            <b>${fmt.pct(mobSug.upper, 1)}</b> are 10+ km. The true share outside the home province lies in between${mobSug.inside ? "; the default is inside that range" : ""}.
            <button class="link" disabled=${!!ses} onClick=${() => set("mobility", { share: +mobSug.lower.toFixed(3) })}>Use ${fmt.pct(mobSug.lower, 1)}</button>
            <button class="link" disabled=${!!ses} onClick=${() => set("mobility", { share: +mobSug.upper.toFixed(3) })}>Use ${fmt.pct(mobSug.upper, 1)}</button></div>`}
          <${Slider} label="Travel reduction" value=${spec.mobility.reduction} set=${(v) => set("mobility", { reduction: v })} min=${0} max=${1} step=${0.05} f=${(v) => fmt.pct(v)} />
          <div class="small">${corridors.length ? corridors.map((c) => html`<div class="row" style="gap:6px"><span style="flex:1">${nameOf(c.from_id)} ↔ ${nameOf(c.to_id)}</span><span class="num muted">${fmt.compact(c.travellers)}/d</span>
            <button class="btn sm ghost" aria-label="Remove corridor" onClick=${() => ses ? act({ type: "corridor_remove", province_id: c.from_id, to_id: c.to_id }) : set("mobility", { corridors: spec.mobility.corridors.filter((x) => x !== c) })}><${Icon} n="x" s=${12} /></button></div>`)
            : html`<span class="faint">No custom corridors — use the Corridor tool on the map.</span>`}</div>
        </${Group}>
        <${Group} title="Hospitals & workforce" badge=${html`<span class="pill sc">Estimate</span>`}>
          <${Seg} label="Distribution" value=${spec.workforce.mode} onChange=${(v) => set("workforce", { mode: v })} options=${[["current", "Current"], ["equal", "Equal per capita"], ["custom", "Custom"]]} />
          <span class="small faint">${spec.workforce.mode === "current" ? "WHO national densities × provincial population" : spec.workforce.mode === "equal" ? "the same national totals spread evenly per person" : "edit the selected province below"}</span>
          ${spec.workforce.mode === "custom" && html`${sel ? html`<div class="fgrid">${["beds", "nurses", "doctors"].map((k) => html`<label class="sl"><span>${k} · ${gById[sel]?.name}</span>
              <input type="number" min="0" step="10" value=${spec.workforce.custom[sel]?.[k] ?? gById[sel]?.[k + "_est"] ?? 0}
                onInput=${(e) => set("workforce", { custom: { ...spec.workforce.custom, [sel]: { ...(spec.workforce.custom[sel] || {}), [k]: +e.target.value || 0 } } })} /></label>`)}</div>`
            : html`<span class="small muted">Select a province on the map (Inspect tool) to edit its beds and staff.</span>`}
            ${Object.keys(spec.workforce.custom).length > 0 && html`<span class="small muted">${Object.keys(spec.workforce.custom).length} province(s) customised · <a class="link" onClick=${() => set("workforce", { custom: {} })}>clear</a></span>`}`}
          <${Slider} label="Beds usable for this outbreak" value=${spec.workforce.bed_share} set=${(v) => set("workforce", { bed_share: v })} min=${0.01} max=${0.5} step=${0.01} f=${(v) => fmt.pct(v)} />
          <${Slider} label="Nurses assigned" value=${spec.workforce.nurse_share} set=${(v) => set("workforce", { nurse_share: v })} min=${0.01} max=${0.5} step=${0.01} f=${(v) => fmt.pct(v)} />
          <${Slider} label="Patients per nurse (shift)" value=${spec.workforce.patients_per_nurse} set=${(v) => set("workforce", { patients_per_nurse: v })} min=${2} max=${12} step=${1} />
          <${Slider} label="Staff surge at start" value=${spec.workforce.surge} set=${(v) => set("workforce", { surge: v })} min=${0} max=${1} step=${0.05} f=${(v) => `+${fmt.pct(v)}`} />
          <${Slider} label="Rapid-response teams per base" value=${spec.workforce.teams_per_base} set=${(v) => set("workforce", { teams_per_base: v })} min=${0} max=${12} step=${1} hint="4 MoH institutes: NIHE, Pasteur Nha Trang, Tây Nguyên, Pasteur HCMC" />
          <${Slider} label="Patients a team can manage" value=${spec.workforce.team_capacity} set=${(v) => set("workforce", { team_capacity: v })} min=${5} max=${150} step=${5} />
        </${Group}>
        <${Group} title="Run">
          <div class="small">${spec.seeds.length ? spec.seeds.map((s) => html`<div class="row" style="gap:6px"><span style="flex:1">Seed · ${nameOf(s.province_id)}</span><span class="num">${fmt.int(s.infections)}</span>
            <button class="btn sm ghost" aria-label="Remove seed" disabled=${!!ses} onClick=${() => setSpec((x) => ({ ...x, seeds: x.seeds.filter((y) => y !== s) }))}><${Icon} n="x" s=${12} /></button></div>`)
            : html`<span class="faint">${pa.start_from_estimate ? "Starts from this month's model estimate in every province. Add seeds for an extra outbreak." : "No seed yet — pick the Seed tool and click a province."}</span>`}</div>
          <${Slider} label="Trials (ensemble)" value=${spec.trials} set=${(v) => setSpec((s) => ({ ...s, trials: v }))} min=${4} max=${48} step=${4} hint="more trials = tighter bands, slower steps" />
          <${Slider} label="Length" value=${spec.max_days} set=${(v) => setSpec((s) => ({ ...s, max_days: v }))} min=${60} max=${730} step=${30} f=${(v) => `${v} d`} />
          <label class="sl"><span>Random seed</span><input type="number" value=${spec.seed} onInput=${(e) => setSpec((s) => ({ ...s, seed: +e.target.value || 0 }))} /></label>
        </${Group}>
      </div></div>

      <div class="s9 stack">
        <div class="kpis sbx-kpis">${[
          ["Active infections", n ? nf(n.active[1]) : "—", n ? `P10–P90 ${nf(n.active[0])}–${nf(n.active[2])}` : "press Start"],
          ["R(t) national", n ? n.rt.toFixed(2) : "—", n ? (n.rt >= 1 ? "growing" : "shrinking") : ""],
          ["In hospital", n ? nf(n.hosp[1]) : "—", n ? `of ${nf(n.cap)} slots · ${n.overloaded} province(s) over` : ""],
          ["Deaths", n ? nf(n.deaths[1]) : "—", n ? `P10–P90 ${nf(n.deaths[0])}–${nf(n.deaths[2])}` : ""],
          ["Provinces reached", n ? `${n.reached} / 34` : "—", n ? `${fmt.compact(n.cum_inf[1])} infected so far` : ""],
          ["Vaccinated", n ? fmt.pct(n.vacc_share, 1) : "—", n ? `${fmt.compact(n.doses)} campaign doses` : ""],
        ].map(([l, v, d], i) => html`<div class="kpi"><div class="l">${l}${i === 0 && html`<span class="pill sc">Scenario</span>`}</div><div class=${"v num" + (i === 0 ? " lead" : "")}>${v}</div><div class="d">${d}</div></div>`)}</div>

        <${Panel} flush title=${html`<span class="row" style="gap:8px">${ses ? html`Day ${F?.day} · ${fdate(F?.date)}` : "Setup map"}${!atHead && ses && html`<span class="pill warn">viewing the past</span>`}</span>`}
          right=${html`<${Seg} label="Map metric" value=${metric} onChange=${setMetric} options=${METRICS} />`}>
          <div class="toolbar" role="toolbar" aria-label="Map tools">${TOOLS.filter((t) => !t.route || t.route === route).map((t) => html`<button class=${"tool" + (tool === t.k ? " on" : "")} onClick=${() => { setTool(t.k); setPick(null); }} title=${t.hint}><${Icon} n=${t.ic} s=${14} />${t.l}</button>`)}</div>
          ${tool !== "inspect" && html`<div class="toolopts small">
            <span class="muted">${pick ? html`<b>${nameOf(pick)}</b> selected — click the ${tool === "staff" ? "destination" : "other end"}.` : T.hint}</span>
            ${T.p?.value != null && html`<label class="row" style="gap:6px">${{ seed: "infections", lockdown: "contact cut", border: "closure", vaccinate: "doses/1k/day", team: "teams", staff: "nurses", vector_control: "R cut", corridor: "trips/day" }[tool]}
              <input type="number" style="width:96px" step=${["lockdown", "border", "vector_control"].includes(tool) ? 0.05 : 1} value=${params(tool).value} onInput=${(e) => setTp((x) => ({ ...x, [tool]: { ...params(tool), value: +e.target.value } }))} /></label>`}
            ${T.p?.days != null && html`<label class="row" style="gap:6px">days<input type="number" style="width:64px" min="1" value=${params(tool).days} onInput=${(e) => setTp((x) => ({ ...x, [tool]: { ...params(tool), days: Math.max(1, +e.target.value || 1) } }))} /></label>`}
            ${tool === "weather" && html`<${Seg} label="Event" value=${params("weather").kind} onChange=${(v) => setTp((x) => ({ ...x, weather: { ...params("weather"), kind: v } }))} options=${[["heatwave", "Heatwave"], ["flood", "Flood"], ["drought", "Drought"], ["cold", "Cold snap"]]} />`}
            ${ses && ["lockdown", "vaccinate", "weather", "border"].includes(tool) && html`<button class="btn sm" onClick=${() => act({ type: tool, province_id: "ALL", ...params(tool) })}>Apply nationwide</button>`}
          </div>`}
          <${VnMap} colors=${ses ? colors : {}} selected=${sel || pick} onSelect=${onMap} height=${640} pulses=${pulses} flows=${flows} movers=${movers} routes=${routes} labels=${labels}
            tooltip=${(id, name) => { const i = provs.findIndex((p) => p.province_id === id); if (i < 0 || !F) return html`<b>${name}</b><br/><span class="muted">${fmt.compact(gById[id]?.population)} people · ${fmt.compact(gById[id]?.nurses_est)} nurses · ${fmt.compact(gById[id]?.beds_est)} beds (est.)</span>`;
              const P = F.prov, pop = provs[i].population;
              return html`<b>${name}</b><br/>${fmt.compact(P.active[i])} active infections (${fmt.int((P.active[i] / pop) * 1e5)}/100k) · R ${P.rt[i].toFixed(2)}
                <br/>${fmt.int(P.hosp[i])} in hospital / ${fmt.int(P.cap[i])} slots${P.unmet[i] > 0 ? html` · <b style=${`color:${LEVEL.alert.c}`}>${fmt.int(P.unmet[i])} without a bed</b>` : ""}
                <br/>${fmt.int(P.deaths[i])} deaths · ${fmt.pct(P.vacc[i])} vaccinated · ${F.weather.t[i]} °C, ${F.weather.rain[i]} mm/d
                ${P.contact[i] < 1 || P.border[i] > 0 ? html`<br/><span class="muted">${P.contact[i] < 1 ? `contacts −${fmt.pct(1 - P.contact[i])} ` : ""}${P.border[i] > 0 ? `border ${fmt.pct(P.border[i])} closed` : ""}</span>` : ""}`; }}>
            <div class="maplegend">${metric === "load" ? [["alert", "≥100%"], ["warning", "85–100%"], ["watch", "60–85%"]].map(([l, t]) => html`<span><i style=${`background:${LEVEL[l].c}`}></i>${t}</span>`)
              : metric === "rt" || metric === "climate" ? html`${RT_COL.map((c) => html`<i style=${`background:${c}`}></i>`)}<span class="muted">${metric === "rt" ? "R < 0.7 … > 1.8" : "weather × transmission  0.85 … 1.3"}</span>`
              : html`${(metric === "vacc" ? SEQ : HEAT).slice(1).map((c) => html`<i style=${`background:${c}`}></i>`)}<span class="muted">${{ active: "active infections per 100k", vacc: "share vaccinated", deaths: "deaths per 100k" }[metric]}</span>`}
              <span><i class="dotflow"></i>infection flow</span><span><i class="sw round" style="background:#2f70bb"></i>team</span></div>
          </${VnMap}>
          ${ses ? html`<div class="playbar" role="group" aria-label="Sandbox playback">
            <button class="btn sm pri play" onClick=${() => { if (!atHead) setView(frames.length - 1); setPlaying(!playing); }} aria-label=${playing ? "Pause" : "Play"} title="Space"><${Icon} n=${playing ? "stop" : "play"} s=${13} /></button>
            <button class="btn sm" disabled=${playing} onClick=${() => step(7)} title="Advance one week">+7 d</button>
            <div class="pb-label"><b class="num">Day ${F?.day}</b><span class="small muted">${fdate(F?.date)}</span></div>
            <input type="range" min="0" max=${Math.max(0, frames.length - 1)} value=${view} onInput=${(e) => { setPlaying(false); setView(+e.target.value); }} aria-label="Timeline" />
            ${!atHead && html`<button class="btn sm" onClick=${() => rewindTo(F.day)} title="Discard everything after this day and continue from here">Rewind here</button>`}
            <div class="seg sm" role="radiogroup" aria-label="Speed">${SPEEDS.map((s) => html`<button class=${s === speed ? "on" : ""} onClick=${() => setSpeed(s)}>${s} d/s</button>`)}</div>
            <span class="small muted num hide-sm">${ses.teams_idle}/${ses.teams_total} teams idle</span>
          </div>` : html`<div class="playbar"><span class="small muted" style="flex:1">${spec.seeds.length ? `${spec.seeds.length} seed(s) placed.` : pa.start_from_estimate ? "Dengue starts from the current provincial estimates." : "Place at least one seed with the Seed tool."}</span>
            <button class="btn pri" disabled=${busy || (!spec.seeds.length && !pa.start_from_estimate)} onClick=${start}><${Icon} n="play" s=${13} />Start outbreak</button></div>`}
        </${Panel}>

        <div class="grid">
          <div class="s8"><${Panel} title=${selI >= 0 ? `${provs[selI].name}: infections and hospital load` : "Nation: active infections, hospital load, deaths"} note="Median of trials, band P10–P90. Click to view a day.">
            ${frames.length > 1 ? (selI >= 0 ? html`<${MultiLine} x=${series.x} cursor=${view} onPick=${(i) => { setPlaying(false); setView(i); }} height=${220} label="Province curves"
                series=${[{ name: "Active infections", color: "var(--chart-1)", values: frames.map((f) => f.prov.active[selI]) },
                  { name: "In hospital", color: LEVEL.warning.c, values: frames.map((f) => f.prov.hosp[selI]) },
                  { name: "Capacity", color: "var(--text)", dash: true, width: 1.4, values: frames.map((f) => f.prov.cap[selI]) }]} />`
              : html`<${MultiLine} x=${series.x} cursor=${view} onPick=${(i) => { setPlaying(false); setView(i); }} height=${220} label="National curves"
                series=${[{ name: "Active infections", color: "var(--chart-1)", values: series.active, band: series.activeB },
                  { name: "In hospital", color: LEVEL.warning.c, values: series.hosp }, { name: "Capacity", color: "var(--text)", dash: true, width: 1.4, values: series.cap },
                  { name: "Deaths (cum.)", color: LEVEL.alert.c, values: series.deaths, band: series.deathsB }]} />`)
              : html`<div class="empty" style="height:220px">${ses ? "Press play — curves draw as the outbreak runs." : "Start the outbreak to see curves."}</div>`}
          </${Panel}></div>
          <div class="s4"><${Panel} title="Live events" flush note=${ses ? `${ses.actions.length} action(s)` : ""}>
            <div class="feed" aria-live="polite" style="max-height:268px">${(ses?.events || []).filter((e) => e.day <= (F?.day ?? 0)).slice(-80).reverse().map((e, i) => html`
              <button class=${"ev k-" + e.kind + (i === 0 && e.day === F?.day ? " fresh" : "")} onClick=${() => { setPlaying(false); const j = frames.findIndex((f) => f.day === e.day); if (j >= 0) setView(j); if (e.province_id) setSel(e.province_id); }}>
                <span class="num small muted">d${e.day}</span><span class="evi"></span><span>${e.text}</span></button>`)}
              ${!(ses?.events || []).length && html`<div style="padding:12px 20px" class="small muted">${ses ? "Nothing yet — press play." : "Events appear here once the outbreak starts."}</div>`}</div>
          </${Panel}></div>

          ${selI >= 0 && F && html`<div class="s12"><${Panel} title=${html`${provs[selI].name} <span class="muted small">${provs[selI].region} · ${fmt.compact(provs[selI].population)} people</span>`}
            right=${html`<button class="btn sm ghost" onClick=${() => setSel(null)} aria-label="Close"><${Icon} n="x" s=${12} /></button>`}>
            <div class="row wrap" style="gap:24px;align-items:flex-start">
              <dl class="kv" style="min-width:260px"><dt>Active / R</dt><dd class="num">${fmt.compact(F.prov.active[selI])} · R ${F.prov.rt[selI].toFixed(2)}</dd>
                <dt>Hospital</dt><dd class="num">${fmt.int(F.prov.hosp[selI])} / ${fmt.int(F.prov.cap[selI])} slots (${F.prov.bind[selI] ? "staff-bound" : "bed-bound"})</dd>
                <dt>Nurses (est.)</dt><dd class="num">${fmt.int(F.prov.nurses[selI])}${F.prov.surge[selI] ? ` · surge +${fmt.pct(F.prov.surge[selI])}` : ""}</dd>
                <dt>Deaths</dt><dd class="num">${fmt.int(F.prov.deaths[selI])}</dd><dt>Vaccinated / susceptible</dt><dd class="num">${fmt.pct(F.prov.vacc[selI])} / ${fmt.pct(F.prov.susc[selI])}</dd>
                <dt>Weather</dt><dd class="num">${F.weather.t[selI]} °C · ${F.weather.rain[selI]} mm/d · transmission ×${F.climate_mult[selI].toFixed(2)}</dd></dl>
              <div class="row wrap" style="gap:6px;flex:1">
                ${[["Seed 1k", { type: "seed", value: 1000 }], ["Distancing 50%", { type: "lockdown", value: 0.5 }], ["Lift distancing", { type: "lockdown", value: 0 }], ["Close border 80%", { type: "border", value: 0.8 }], ["Reopen", { type: "border", value: 0 }],
                  ["Vaccinate 5/1k/d", { type: "vaccinate", value: 5, days: 30 }], ["Send 2 teams", { type: "team", value: 2 }], ["Recall teams", { type: "team", value: 0 }], ["Surge +30%", { type: "surge", value: 0.3 }],
                  ...(route === "vector" ? [["Vector control", { type: "vector_control", value: 0.25, days: 30 }]] : []), ["Heatwave", { type: "weather", kind: "heatwave", days: 14 }], ["Flood", { type: "weather", kind: "flood", days: 10 }]]
                  .map(([l, a]) => html`<button class="btn sm" disabled=${!ses} onClick=${() => act({ ...a, province_id: provs[selI].province_id })}>${l}</button>`)}
              </div></div>
          </${Panel}></div>`}

          <div class="s12"><${Panel} title="AI advisor" note="Reads the live state and proposes moves. Nothing is applied until you press Apply. Falls back to transparent rules when the AI is off or unavailable.">
            <div class="stack" style="gap:12px">
              <form class="row wrap" style="gap:8px" onSubmit=${(e) => { e.preventDefault(); advise(advQ); }}>
                <button type="button" class="btn pri" disabled=${!ses || advBusy} onClick=${() => advise(null)}><${Icon} n="model" s=${14} />${advBusy ? "Thinking…" : "What should we do now?"}</button>
                <input type="text" style="flex:1;min-width:200px" placeholder="Or ask: “How do we protect Hà Nội?”, “Is a lockdown worth it?”" value=${advQ} onInput=${(e) => setAdvQ(e.target.value)} maxlength="1000" aria-label="Question for the advisor" />
                <button class="btn" disabled=${!ses || advBusy || !advQ.trim()}>Ask</button>
                <div class="seg sm" role="radiogroup" aria-label="Language"><button type="button" class=${lang === "en" ? "on" : ""} onClick=${() => setLang("en")}>EN</button><button type="button" class=${lang === "vi" ? "on" : ""} onClick=${() => setLang("vi")}>VI</button></div>
              </form>
              ${adv && html`<div class="advice">
                <div class="small row wrap" style="gap:6px">${adv.source === "llm" ? html`<span class="pill fc">AI · ${adv.model}</span>` : html`<span class="pill">Rule-based</span>`}<span class="muted">for day ${adv.day}${adv.day !== F?.day ? " (state has moved on since)" : ""}</span>
                  ${adv.fallback_reason && html`<span class="muted">· ${adv.fallback_reason}</span>`}</div>
                <div class="well md" dangerouslySetInnerHTML=${{ __html: md(adv.summary || "") }}></div>
                ${adv.actions.length > 0 && html`<div class="stack" style="gap:6px">${adv.actions.map((a, i) => html`<div class="advrow">
                  <span class="pill sc">${a.type.replace("_", " ")}</span><b>${nameOf(a.province_id)}${a.to_id ? ` → ${nameOf(a.to_id)}` : ""}</b>
                  <span class="num muted">${a.value != null ? a.value : ""}${a.days ? ` · ${a.days} d` : ""}${a.kind ? ` · ${a.kind}` : ""}</span><span class="small" style="flex:1">${a.why}</span>
                  <button class="btn sm" disabled=${!ses || a._done} onClick=${() => { const { _done, ...b } = a; act({ ...b, source: "advisor" }); setAdv((x) => ({ ...x, actions: x.actions.map((y, j) => (j === i ? { ...y, _done: true } : y)) })); }}>${a._done ? "Applied" : "Apply"}</button></div>`)}
                  <div class="row"><span class="spacer"></span><button class="btn pri sm" disabled=${!ses || adv.actions.every((a) => a._done)} onClick=${() => { act(adv.actions.filter((a) => !a._done).map(({ _done, ...a }) => ({ ...a, source: "advisor" }))); setAdv((x) => ({ ...x, actions: x.actions.map((y) => ({ ...y, _done: true })) })); }}>Apply all</button></div></div>`}
                ${adv.rejected?.length > 0 && html`<div class="small faint">Dropped ${adv.rejected.length} invalid proposal(s): ${adv.rejected.join("; ")}</div>`}
              </div>`}
              <div class="small faint">${status.data?.llm?.configured ? `AI: ${status.data.llm.provider} · ${status.data.llm.model} (set by ${status.data.llm.source === "admin" ? "an admin" : ".env"})` : "AI not configured — advice is rule-based. An admin can pick the model in Admin → Settings; keys go in .env."}</div>
            </div>
          </${Panel}></div>

          ${ses && html`<div class="s12"><${Panel} title="Scoreboard" note="Totals so far, median of trials (P10–P90)">
            <div class="kpis">${[["Infections", ses.score.infections], ["Reported cases", ses.score.reported], ["Deaths", ses.score.deaths], ["Unmet bed-days", ses.score.unmet_bed_days]].map(([l, q]) => html`<div class="kpi"><div class="l">${l}</div><div class="v num">${fmt.compact(q.p50)}</div><div class="d">${fmt.compact(q.p10)}–${fmt.compact(q.p90)}</div></div>`)}
              <div class="kpi"><div class="l">Costs of your response</div><div class="v num">${fmt.compact(ses.score.doses)} <small>doses</small></div><div class="d">${fmt.compact(ses.score.lockdown_person_days)} person-days restricted · ${fmt.int(ses.score.team_days)} team-days</div></div></div>
          </${Panel}></div>`}
          <div class="s12">${ses && html`<${Prov} items=${Object.entries(ses.basis).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`)} />`}</div>
        </div>
      </div>
    </div>
  </div>`;
}
