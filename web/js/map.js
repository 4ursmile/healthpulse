// MapLibre choropleth over the IBGE municipal polygons. Values are applied with feature-state (no React re-render per
// polygon, no re-upload of geometry); missing values are hatched, never drawn as zero. No basemap tiles are required.
import { html, useEffect, useRef, useState } from "../vendor/preact-htm.js";
import { fmt } from "./ui.js";

let geoPromise = null;
const loadGeo = () => (geoPromise ||= fetch("/api/v1/map/geometry").then((r) => { if (!r.ok) throw new Error("geometry not ingested"); return r.json(); }));
let mlPromise = null;
function loadMapLibre() {
  if (window.maplibregl) return Promise.resolve(window.maplibregl);
  return (mlPromise ||= new Promise((res, rej) => {
    const css = document.createElement("link");
    css.rel = "stylesheet"; css.href = "/static/vendor/maplibre-gl.css"; document.head.appendChild(css);
    const s = document.createElement("script");
    s.src = "/static/vendor/maplibre-gl.js"; s.onload = () => res(window.maplibregl); s.onerror = rej; document.head.appendChild(s);
  }));
}

// sequential single-hue ramp (teal), 5 classes, colour-blind safe; diverging for differences
export const RAMP = ["#e1eff2", "#b3d6df", "#7db6c5", "#3f8da3", "#1b6c80", "#0d4553"];
export const DIVERGING = ["#2f6db3", "#8fb3dc", "#eef0ef", "#e7a989", "#b5482a"];

function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function hatch() {
  const c = document.createElement("canvas");
  c.width = c.height = 8;
  const g = c.getContext("2d");
  g.fillStyle = css("--nodata") || "#e9ecea"; g.fillRect(0, 0, 8, 8);
  g.strokeStyle = css("--control") || "#c9cdcb"; g.lineWidth = 1;
  g.beginPath(); g.moveTo(0, 8); g.lineTo(8, 0); g.stroke();
  return g.getImageData(0, 0, 8, 8);
}

export function ChoroplethMap({ values = {}, breaks = [], selected, onSelect, focus = [], tooltip, height = 520, label = "Map", ramp = RAMP, points = [] }) {
  const box = useRef(null);
  const map = useRef(null);
  const [err, setErr] = useState(null);
  const [tip, setTip] = useState(null);
  const [isReady, setReady] = useState(false);
  const ready = useRef(false);
  const lastIds = useRef(new Set());
  const lastSel = useRef(null);
  const fitted = useRef(false);
  const props = useRef({ values, onSelect, tooltip });
  props.current = { values, onSelect, tooltip };

  useEffect(() => {
    let dead = false;
    Promise.all([loadMapLibre(), loadGeo()]).then(([ml, geo]) => {
      if (dead) return;
      const m = new ml.Map({ container: box.current, style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": css("--raised") } }] },
        bounds: [[-74, -34], [-34.5, 5.5]], fitBoundsOptions: { padding: 12 }, attributionControl: { compact: true, customAttribution: "IBGE municipal boundaries" },
        dragRotate: false, pitchWithRotate: false, renderWorldCopies: false, maxZoom: 12 });
      map.current = m;
      m.addControl(new ml.NavigationControl({ showCompass: false }), "top-right");
      m.on("load", () => {
        m.addImage("hatch", hatch(), { pixelRatio: 1 });
        m.addSource("mun", { type: "geojson", data: geo, promoteId: "id", tolerance: 0.6 });
        m.addLayer({ id: "nodata", type: "fill", source: "mun", paint: { "fill-pattern": "hatch", "fill-opacity": ["case", ["==", ["feature-state", "has"], true], 0, 0.22] } });
        m.addLayer({ id: "fill", type: "fill", source: "mun", paint: { "fill-color": ["coalesce", ["feature-state", "c"], "rgba(0,0,0,0)"], "fill-opacity": ["case", ["==", ["feature-state", "has"], true], 0.92, 0] } });
        m.addLayer({ id: "line", type: "line", source: "mun", paint: { "line-color": css("--control"), "line-opacity": ["interpolate", ["linear"], ["zoom"], 3, 0.25, 7, 0.7], "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.1, 8, 0.6] } });
        m.addLayer({ id: "hover", type: "line", source: "mun", paint: { "line-color": css("--text"), "line-width": ["case", ["==", ["feature-state", "hover"], true], 1.5, 0] } });
        m.addLayer({ id: "sel", type: "line", source: "mun", paint: { "line-color": css("--text"), "line-width": ["case", ["==", ["feature-state", "sel"], true], 2.5, 0] } });
        m.addSource("pts", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
        m.addLayer({ id: "pts", type: "circle", source: "pts", paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 1.5, 10, 4], "circle-color": css("--text"), "circle-opacity": 0.75 } });
        let hovered = null, raf = 0;
        m.on("mousemove", "fill", (e) => {
          if (raf) return;
          raf = requestAnimationFrame(() => {
            raf = 0;
            const f = e.features?.[0];
            if (!f) return;
            if (hovered !== null && hovered !== f.id) m.setFeatureState({ source: "mun", id: hovered }, { hover: false });
            hovered = f.id;
            m.setFeatureState({ source: "mun", id: hovered }, { hover: true });
            m.getCanvas().style.cursor = "pointer";
            const id = String(f.id).padStart(7, "0");
            setTip({ x: e.point.x, y: e.point.y, id, v: props.current.values[id] });
          });
        });
        m.on("mouseleave", "fill", () => { if (hovered !== null) m.setFeatureState({ source: "mun", id: hovered }, { hover: false }); hovered = null; setTip(null); m.getCanvas().style.cursor = ""; });
        m.on("click", "fill", (e) => { const f = e.features?.[0]; if (f) props.current.onSelect?.(String(f.id).padStart(7, "0")); });
        ready.current = true;
        setReady(true);
      });
    }).catch((e) => setErr(e.message));
    return () => { dead = true; map.current?.remove(); map.current = null; ready.current = false; setReady(false); };
  }, []);

  // apply values via feature-state
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const apply = () => {
      for (const id of lastIds.current) m.setFeatureState({ source: "mun", id: +id }, { has: false, c: null });
      const ids = new Set();
      for (const [id, v] of Object.entries(values)) {
        if (v === null || v === undefined || Number.isNaN(v)) continue;
        let k = 0;
        while (k < breaks.length && v > breaks[k]) k++;
        m.setFeatureState({ source: "mun", id: +id }, { has: true, c: ramp[Math.min(ramp.length - 1, k + (breaks.length < ramp.length - 1 ? 1 : 0))] });
        ids.add(id);
      }
      lastIds.current = ids;
    };
    if (ready.current) apply();
  }, [values, breaks, isReady]);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const apply = () => {
      if (lastSel.current) m.setFeatureState({ source: "mun", id: +lastSel.current }, { sel: false });
      if (selected) m.setFeatureState({ source: "mun", id: +selected }, { sel: true });
      lastSel.current = selected;
    };
    if (ready.current) apply();
  }, [selected, isReady]);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const apply = () => {
      m.getSource("pts")?.setData({ type: "FeatureCollection", features: points.filter((p) => p.lat != null && p.lon != null).map((p) => ({ type: "Feature", properties: { name: p.name }, geometry: { type: "Point", coordinates: [p.lon, p.lat] } })) });
      if (focus.length) {
        loadGeo().then((geo) => {
          const fs = geo.features.filter((f) => focus.includes(String(f.id).padStart(7, "0")));
          if (!fs.length) return;
          let b = [180, 90, -180, -90];
          for (const f of fs) for (const poly of f.geometry.coordinates) for (const [x, y] of poly[0]) b = [Math.min(b[0], x), Math.min(b[1], y), Math.max(b[2], x), Math.max(b[3], y)];
          const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
          m.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, duration: calm || !fitted.current ? 0 : 700, maxZoom: 9 });
          fitted.current = true;
        });
      }
    };
    if (ready.current) apply();
  }, [focus.join(","), points.length, isReady]);

  return html`<div class="mapbox" style=${`height:${height}px`} role="application" aria-label=${label}>
    <div ref=${box} style="position:absolute;inset:0"></div>
    ${err && html`<div class="empty" style="position:absolute;inset:16px;height:auto;background:var(--surface)">${/webgl/i.test(String(err))
      ? "This browser could not start WebGL, so the map cannot be drawn. Enable hardware acceleration or try another browser; the tables on this page are unaffected."
      : html`Map geometry unavailable: ${String(err).slice(0, 160)}. Ingest “IBGE municipal boundaries” in Admin → Data sources.`}</div>`}
    ${tip && html`<div class="maptip" style=${`left:${Math.min(tip.x + 12, (box.current?.clientWidth || 400) - 200)}px;top:${tip.y + 12}px`}>${tooltip ? tooltip(tip.id, tip.v) : html`<b>${tip.id}</b><br/>${tip.v == null ? "No data" : fmt.d1(tip.v)}`}</div>`}
  </div>`;
}

export function Legend({ breaks = [], unit = "", ramp = RAMP, format = fmt.d1, note }) {
  const n = Math.min(ramp.length, breaks.length + 1);
  const cols = ramp.slice(ramp.length - n);
  const labels = breaks.length ? [`≤ ${format(breaks[0])}`, ...breaks.slice(1).map((b, i) => `${format(breaks[i])}–${format(b)}`), `> ${format(breaks[breaks.length - 1])}`] : [];
  return html`<div class="legend" aria-label="Map legend">
    ${breaks.length > 0 && html`<span class="ramp">${cols.map((c, i) => html`<i style=${`background:${c}`} title=${labels[i]}></i>`)}</span><span>${labels[0]}</span><span>…</span><span>${labels[labels.length - 1]} ${unit}</span>`}
    <span class="row" style="gap:6px"><span class="nd"></span>No data</span>${note && html`<span>${note}</span>`}</div>`;
}
