// Viet Nam province map (34 units since 1 July 2025). MapLibre GL with no basemap tiles: fills via feature-state,
// pulsing alert rings, animated "movers" (response teams) with their routes, and flow particles along spread links.
// Animation runs on requestAnimationFrame, pauses when the tab is hidden, and is static under prefers-reduced-motion.
import { html, useEffect, useRef, useState } from "../vendor/preact-htm.js";

let geoP = null;
export const loadVnGeo = () => (geoP ||= fetch("/api/v1/vn/geometry").then((r) => { if (!r.ok) throw new Error("Viet Nam geometry not built — run `hp vn-fetch`"); return r.json(); }));
let mlP = null;
function loadML() {
  if (window.maplibregl) return Promise.resolve(window.maplibregl);
  return (mlP ||= new Promise((res, rej) => {
    const l = document.createElement("link"); l.rel = "stylesheet"; l.href = "/static/vendor/maplibre-gl.css"; document.head.appendChild(l);
    const s = document.createElement("script"); s.src = "/static/vendor/maplibre-gl.js"; s.onload = () => res(window.maplibregl); s.onerror = rej; document.head.appendChild(s);
  }));
}
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
export const calm = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

// alert levels: ordinal, colour-blind-safe (yellow → orange → red), normal stays neutral
export const LEVEL = {
  normal: { c: "#cfe3e7", ink: "var(--muted)", label: "Normal" },
  watch: { c: "#f3d36b", ink: "#6b5200", label: "Watch" },
  warning: { c: "#ee9a3a", ink: "#7a3d00", label: "Warning" },
  alert: { c: "#d6403a", ink: "#ffffff", label: "Alert" },
  no_data: { c: "#e9ecea", ink: "var(--muted)", label: "No data" },
};
export const SEQ = ["#e3f0f3", "#b3d6df", "#7db6c5", "#3f8da3", "#1b6c80", "#0d4553"];
export const HEAT = ["#fdf3d8", "#fbd38d", "#f6a256", "#e8663d", "#c53a33", "#8c1f2c"];

export function classBreaks(vals, n = 5) {
  const v = vals.filter((x) => x != null && x > 0).sort((a, b) => a - b);
  if (v.length < 2) return v.length ? [v[0]] : [];
  return [...new Set(Array.from({ length: n - 1 }, (_, i) => v[Math.floor(((i + 1) / n) * v.length)]))];
}
export const colorFor = (v, breaks, ramp) => { if (v == null) return null; let k = 0; while (k < breaks.length && v > breaks[k]) k++; return ramp[Math.min(ramp.length - 1, k + (breaks.length < ramp.length - 1 ? 1 : 0))]; };

const VN_BOUNDS = [[102.1, 8.4], [109.7, 23.45]];

export function VnMap({ values = {}, colors, selected, onSelect, tooltip, height = 560, pulses = [], movers = [], routes = [], flows = [],
  labels = [], label = "Viet Nam province map", children, onReady, maxZoom = 10 }) {
  const box = useRef(null), map = useRef(null);
  const [err, setErr] = useState(null), [tip, setTip] = useState(null), [ready, setReady] = useState(false);
  const live = useRef({});
  live.current = { values, onSelect, tooltip, pulses, movers, flows };
  const lastIds = useRef(new Set()), lastSel = useRef(null), markers = useRef([]);

  useEffect(() => {
    let dead = false, raf = 0;
    Promise.all([loadML(), loadVnGeo()]).then(([ml, geo]) => {
      if (dead) return;
      const m = new ml.Map({ container: box.current, style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": css("--raised") } }] },
        bounds: VN_BOUNDS, fitBoundsOptions: { padding: 16 }, attributionControl: { compact: true, customAttribution: "OCHA COD-AB v02 · 34 provinces (2025)" },
        dragRotate: false, pitchWithRotate: false, renderWorldCopies: false, maxZoom, minZoom: 3.5 });
      map.current = m;
      m.addControl(new ml.NavigationControl({ showCompass: false }), "top-right");
      m.on("load", () => {
        m.addSource("p", { type: "geojson", data: geo, promoteId: "id" });
        m.addLayer({ id: "fill", type: "fill", source: "p", paint: { "fill-color": ["coalesce", ["feature-state", "c"], css("--nodata") || "#e9ecea"], "fill-opacity": 0.92 } });
        m.addLayer({ id: "line", type: "line", source: "p", paint: { "line-color": css("--surface"), "line-width": 0.8 } });
        m.addLayer({ id: "hover", type: "line", source: "p", paint: { "line-color": css("--text"), "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 1.5, 0] } });
        m.addLayer({ id: "sel", type: "line", source: "p", paint: { "line-color": css("--text"), "line-width": ["case", ["boolean", ["feature-state", "sel"], false], 2.6, 0] } });
        const empty = { type: "FeatureCollection", features: [] };
        m.addSource("routes", { type: "geojson", data: empty });
        m.addLayer({ id: "routes", type: "line", source: "routes", paint: { "line-color": css("--text"), "line-width": 1.4, "line-opacity": 0.55, "line-dasharray": [2, 2] } });
        m.addSource("flow", { type: "geojson", data: empty });
        m.addLayer({ id: "flow", type: "circle", source: "flow", paint: { "circle-radius": ["get", "r"], "circle-color": "#d6403a", "circle-opacity": ["get", "o"], "circle-blur": 0.3 } });
        m.addSource("pulse", { type: "geojson", data: empty });
        m.addLayer({ id: "pulse", type: "circle", source: "pulse", paint: { "circle-radius": ["get", "r"], "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": ["get", "c"], "circle-stroke-width": 2, "circle-stroke-opacity": ["get", "o"] } });
        m.addLayer({ id: "pulse-core", type: "circle", source: "pulse", paint: { "circle-radius": ["get", "core"], "circle-color": ["get", "c"], "circle-stroke-color": "#fff", "circle-stroke-width": 1.2 } });
        m.addSource("movers", { type: "geojson", data: empty });
        m.addLayer({ id: "movers-halo", type: "circle", source: "movers", paint: { "circle-radius": 9, "circle-color": css("--surface"), "circle-opacity": 0.9 } });
        m.addLayer({ id: "movers", type: "circle", source: "movers", paint: { "circle-radius": 6, "circle-color": ["get", "c"], "circle-stroke-color": css("--text"), "circle-stroke-width": 1.2 } });
        let hovered = null;
        m.on("mousemove", "fill", (e) => {
          const f = e.features?.[0]; if (!f) return;
          if (hovered && hovered !== f.id) m.setFeatureState({ source: "p", id: hovered }, { hover: false });
          hovered = f.id; m.setFeatureState({ source: "p", id: hovered }, { hover: true });
          m.getCanvas().style.cursor = "pointer";
          setTip({ x: e.point.x, y: e.point.y, id: f.id, name: f.properties.name });
        });
        m.on("mouseleave", "fill", () => { if (hovered) m.setFeatureState({ source: "p", id: hovered }, { hover: false }); hovered = null; setTip(null); m.getCanvas().style.cursor = ""; });
        m.on("click", "fill", (e) => { const f = e.features?.[0]; if (f) live.current.onSelect?.(f.id); });
        setReady(true);
        onReady?.(m);
        // animation loop (pulses + flow particles); movers are pushed by the parent each frame it changes
        const still = calm();
        let last = 0;
        const tick = (now) => {
          raf = requestAnimationFrame(tick);
          if (document.hidden || now - last < 33) return;
          last = now;
          const ph = (now / 1600) % 1;
          const ps = live.current.pulses;
          m.getSource("pulse")?.setData({ type: "FeatureCollection", features: ps.map((p, i) => {
            const q = still ? 0.5 : (ph + i * 0.13) % 1;
            const s = p.level === "alert" ? 1 : p.level === "warning" ? 0.75 : 0.5;
            return { type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] },
              properties: { r: 5 + q * 22 * s, o: (1 - q) * 0.9, c: LEVEL[p.level]?.c || "#d6403a", core: 3.5 + 1.5 * s } };
          }) });
          const fl = live.current.flows;
          if (fl.length) {
            const feats = [];
            for (let i = 0; i < fl.length; i++) {
              const f = fl[i], n = Math.max(1, Math.min(6, Math.round(f.w * 6)));
              for (let k = 0; k < n; k++) {
                const t = still ? (k + 0.5) / n : ((now / (2600 + i * 37)) + k / n) % 1;
                feats.push({ type: "Feature", geometry: { type: "Point", coordinates: [f.from[0] + (f.to[0] - f.from[0]) * t, f.from[1] + (f.to[1] - f.from[1]) * t] },
                  properties: { r: 2.2 + f.w * 1.8, o: 0.85 * Math.sin(Math.PI * t) } });
              }
            }
            m.getSource("flow")?.setData({ type: "FeatureCollection", features: feats });
          } else m.getSource("flow")?.setData({ type: "FeatureCollection", features: [] });
        };
        raf = requestAnimationFrame(tick);
      });
    }).catch((e) => setErr(e.message));
    return () => { dead = true; cancelAnimationFrame(raf); markers.current.forEach((x) => x.remove()); map.current?.remove(); map.current = null; setReady(false); };
  }, []);

  // fills
  useEffect(() => {
    const m = map.current; if (!m || !ready) return;
    for (const id of lastIds.current) m.setFeatureState({ source: "p", id }, { c: null });
    const ids = new Set();
    for (const [id, c] of Object.entries(colors || {})) { if (c) { m.setFeatureState({ source: "p", id }, { c }); ids.add(id); } }
    lastIds.current = ids;
  }, [colors, ready]);

  useEffect(() => {
    const m = map.current; if (!m || !ready) return;
    if (lastSel.current) m.setFeatureState({ source: "p", id: lastSel.current }, { sel: false });
    if (selected) m.setFeatureState({ source: "p", id: selected }, { sel: true });
    lastSel.current = selected;
  }, [selected, ready]);

  // movers + routes (cheap: a few hundred points at most)
  useEffect(() => {
    const m = map.current; if (!m || !ready) return;
    m.getSource("movers")?.setData({ type: "FeatureCollection", features: movers.map((p) => ({ type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] }, properties: { c: p.c || "#2f70bb" } })) });
  }, [movers, ready]);
  useEffect(() => {
    const m = map.current; if (!m || !ready) return;
    m.getSource("routes")?.setData({ type: "FeatureCollection", features: routes.map((r) => ({ type: "Feature", geometry: { type: "LineString", coordinates: [r.from, r.to] }, properties: {} })) });
  }, [routes, ready]);

  // HTML labels (no glyph server needed)
  useEffect(() => {
    const m = map.current; if (!m || !ready || !window.maplibregl) return;
    markers.current.forEach((x) => x.remove());
    markers.current = labels.map((l) => {
      const el = document.createElement("div");
      el.className = "vnlabel" + (l.level ? " lv-" + l.level : "");
      el.textContent = l.text;
      return new window.maplibregl.Marker({ element: el, anchor: "left", offset: [10, 0] }).setLngLat([l.lon, l.lat]).addTo(m);
    });
  }, [labels.map((l) => l.id + l.text + l.level).join("|"), ready]);

  return html`<div class="mapbox vnmap" style=${`height:${height}px`} role="application" aria-label=${label}>
    <div ref=${box} style="position:absolute;inset:0"></div>
    ${children}
    ${err && html`<div class="empty" style="position:absolute;inset:16px;height:auto;background:var(--surface)">${/webgl/i.test(String(err)) ? "This browser could not start WebGL, so the map cannot be drawn." : String(err)}</div>`}
    ${tip && html`<div class="maptip" style=${`left:${Math.min(tip.x + 14, (box.current?.clientWidth || 400) - 230)}px;top:${tip.y + 14}px`}>${tooltip ? tooltip(tip.id, tip.name) : html`<b>${tip.name}</b>`}</div>`}
  </div>`;
}

// shared helpers for animation of teams along their logged segments
export function teamPosition(team, day, provById) {
  let seg = null;
  for (const s of team.log) { if (day >= s.t0 && (s.t1 == null || day < s.t1)) { seg = s; break; } }
  const base = provById[team.base_province];
  if (!seg) {
    // before the first move, or after the last segment: at the last known place
    const prev = [...team.log].reverse().find((s) => s.t0 <= day);
    const at = prev ? provById[prev.state === "travel" ? prev.to : prev.at] : base;
    return { lon: at.lon, lat: at.lat, state: prev ? (prev.state === "travel" ? "deployed" : prev.state) : "idle", at: prev?.at || team.base_province };
  }
  if (seg.state === "deployed") { const p = provById[seg.at]; return { lon: p.lon, lat: p.lat, state: "deployed", at: seg.at }; }
  const a = provById[seg.from], b = provById[seg.to];
  const f = Math.max(0, Math.min(1, (day - seg.t0) / Math.max(0.5, seg.t1 - seg.t0)));
  return { lon: a.lon + (b.lon - a.lon) * f, lat: a.lat + (b.lat - a.lat) * f, state: "travel", from: seg.from, to: seg.to };
}
