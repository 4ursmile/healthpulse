// API client: envelope-aware fetch, auth token, URL state, data hooks and real job tracking (SSE with polling fallback).
import { useState, useEffect, useRef, useCallback } from "../vendor/preact-htm.js";

const BASE = "/api/v1";
let token = null;
try { token = localStorage.getItem("hp_token"); } catch (e) {}

export function setToken(t) {
  token = t;
  try { t ? localStorage.setItem("hp_token", t) : localStorage.removeItem("hp_token"); } catch (e) {}
  window.dispatchEvent(new Event("hp-auth"));
}
export const getToken = () => token;

export class ApiError extends Error {
  constructor(status, detail, requestId) {
    super(typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => `${d.loc}: ${d.msg}`).join("; ") : JSON.stringify(detail));
    this.status = status; this.detail = detail; this.requestId = requestId;
  }
}

export async function api(path, { method = "GET", body, idem, form, signal } = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (idem) headers["Idempotency-Key"] = idem;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  let res;
  try {
    res = await fetch(BASE + path, { method, headers, body: payload, signal });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError(0, "The server could not be reached. Check that `hp serve` is running.");
  }
  let j = null;
  try { j = await res.json(); } catch (e) {}
  if (!res.ok) throw new ApiError(res.status, j?.error?.detail || res.statusText, j?.request_id);
  return j;
}

export const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random());

// GET hook: keeps the previous data while refreshing so layouts never jump; exposes loading/error separately.
const cache = new Map();
export function useApi(path, deps = []) {
  const [state, set] = useState(() => (path && cache.has(path) ? { ...cache.get(path), loading: true } : { loading: !!path }));
  const [n, bump] = useState(0);
  useEffect(() => {
    if (!path) { set({ loading: false }); return; }
    const ctl = new AbortController();
    set((s) => ({ ...s, loading: true, error: null }));
    api(path, { signal: ctl.signal }).then((r) => {
      const v = { data: r.data, meta: r.meta || {}, warnings: r.warnings || [], loading: false, error: null, at: new Date() };
      cache.set(path, v);
      if (cache.size > 120) cache.delete(cache.keys().next().value);
      set(v);
    }).catch((e) => { if (e.name !== "AbortError") set((s) => ({ ...s, loading: false, error: e })); });
    return () => ctl.abort();
  }, [path, n, ...deps]);
  const reload = useCallback(() => bump((x) => x + 1), []);
  return { ...state, reload };
}

export function invalidate(prefix) { for (const k of [...cache.keys()]) if (k.startsWith(prefix)) cache.delete(k); }

// Real job state from the queue: SSE stream of job + log events; falls back to polling every 1.5 s.
export function useJob(jobId, onDone) {
  const [job, setJob] = useState(null);
  const [logs, setLogs] = useState([]);
  const done = useRef(onDone);
  done.current = onDone;
  useEffect(() => {
    if (!jobId) { setJob(null); setLogs([]); return; }
    let closed = false, es = null, timer = null, after = 0;
    setLogs([]);
    const terminal = (s) => ["succeeded", "succeeded_with_warnings", "quarantined", "failed", "canceled", "timed_out"].includes(s);
    const finish = (j) => { if (done.current) done.current(j); };
    const poll = async () => {
      try {
        const [j, l] = await Promise.all([api(`/jobs/${jobId}`), api(`/jobs/${jobId}/logs?after=${after}`)]);
        if (closed) return;
        setJob(j.data);
        if (l.data.length) { after = l.data[l.data.length - 1].seq; setLogs((x) => [...x, ...l.data].slice(-400)); }
        if (terminal(j.data.status)) return finish(j.data);
      } catch (e) {}
      timer = setTimeout(poll, 1500);
    };
    if (window.EventSource) {
      es = new EventSource(`${BASE}/jobs/${jobId}/events`);
      es.addEventListener("job", (e) => { const j = JSON.parse(e.data); setJob(j); if (terminal(j.status)) { es.close(); finish(j); } });
      es.addEventListener("log", (e) => { const l = JSON.parse(e.data); after = l.seq; setLogs((x) => [...x, l].slice(-400)); });
      es.onerror = () => { es.close(); if (!closed) poll(); };
    } else poll();
    return () => { closed = true; es && es.close(); clearTimeout(timer); };
  }, [jobId]);
  return { job, logs };
}

// ---------------------------------------------------------------- URL state (global filters live in the query string)
export function useLocation() {
  const [loc, set] = useState({ path: location.pathname, q: new URLSearchParams(location.search) });
  useEffect(() => {
    const f = () => set({ path: location.pathname, q: new URLSearchParams(location.search) });
    window.addEventListener("popstate", f);
    window.addEventListener("hp-nav", f);
    return () => { window.removeEventListener("popstate", f); window.removeEventListener("hp-nav", f); };
  }, []);
  return loc;
}

export function navigate(to, { replace = false } = {}) {
  if (to === location.pathname + location.search) return;
  history[replace ? "replaceState" : "pushState"](null, "", to);
  window.dispatchEvent(new Event("hp-nav"));
  if (!replace) document.querySelector(".sheet")?.scrollTo(0, 0);
}

export function setQuery(patch, { replace = true } = {}) {
  const q = new URLSearchParams(location.search);
  for (const [k, v] of Object.entries(patch)) (v === null || v === undefined || v === "") ? q.delete(k) : q.set(k, v);
  const s = q.toString();
  navigate(location.pathname + (s ? `?${s}` : ""), { replace });
}

// carries the global filters (disease, horizon, region, week, layer, forecast_id, scenario_id) across screens
const GLOBAL = ["scope", "disease", "horizon", "region", "week", "layer"];
export function linkWith(path, extra = {}) {
  const q = new URLSearchParams();
  const cur = new URLSearchParams(location.search);
  for (const k of GLOBAL) if (cur.get(k)) q.set(k, cur.get(k));
  for (const [k, v] of Object.entries(extra)) (v === null || v === undefined) ? q.delete(k) : q.set(k, v);
  const s = q.toString();
  return path + (s ? `?${s}` : "");
}
