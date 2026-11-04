// One scope for the whole app: Global → country → state/province. Lives in the URL (?scope=global|br|br:SP|vn|vn:VN01),
// is remembered per browser, and decides which screens the side nav offers and what every screen filters to.
import { useApi, navigate, linkWith } from "./api.js";

// Every product screen once, with the path it uses per scope ("global" = no country picked). A screen without a
// path for the current scope has no data there and is not offered.
export const SCREENS = [
  { k: "overview", g: "Situation", l: "Overview", ic: "overview", to: { global: "/overview", br: "/overview", vn: "/vn" }, detail: { br: ["/regions"] } },
  { k: "explore", g: "Situation", l: "Explore map", ic: "map", to: { br: "/explore" } },
  { k: "diseases", g: "Situation", l: "Diseases", ic: "disease", to: { br: "/diseases" }, detail: { br: ["/diseases"] } },
  { k: "workforce", g: "Situation", l: "Workforce", ic: "people", to: { br: "/workforce" } },
  { k: "facilities", g: "Situation", l: "Facilities", ic: "facility", to: { br: "/facilities" } },
  { k: "alerts", g: "Situation", l: "Alert review", ic: "alert", cnt: "alerts", to: { br: "/alerts" } },
  { k: "sim", g: "Plan", l: "Simulation", ic: "sim", to: { br: "/simulation", vn: "/vn/simulate" }, detail: { br: ["/simulation"] } },
  { k: "ops", g: "Situation", l: "Live response", ic: "alert", to: { vn: "/vn/ops" } },
  { k: "sandbox", g: "Plan", l: "Outbreak sandbox", ic: "exp", to: { vn: "/vn/sandbox" } },
  { k: "modellab", g: "Evidence", l: "Model lab", ic: "model", to: { br: "/model-lab" } },
  { k: "datalab", g: "Evidence", l: "Data lab", ic: "data", to: { global: "/data-lab", br: "/data-lab" } },
  // kept last: SCREENS[0] is the fallback screen for every scope, and Story has no Viet Nam path
  { k: "story", g: "Start", l: "Story", ic: "alert", to: { global: "/story", br: "/story" } },
];

export const parseScope = (s) => {
  if (!s || s === "global") return { key: "global", country: null, region: null, ck: "global" };
  const [country, region] = s.split(":");
  return { key: region ? `${country}:${region}` : country, country, region: region || null, ck: country };
};
export const scopeKey = (country, region) => (!country ? "global" : region ? `${country}:${region}` : country);
export const scopeOf = (q) => parseScope(q?.get?.("scope"));

const STORE = "hp_scope";
export const storedScope = () => { try { return localStorage.getItem(STORE); } catch (e) { return null; } };
export const rememberScope = (k) => { try { localStorage.setItem(STORE, k); } catch (e) {} };

// which screen a path belongs to, and whether it is the screen's main path for a scope or one of its detail pages
export function screenFor(path) {
  const p = path.replace(/\/+$/, "") || "/";
  let best = null;
  for (const s of SCREENS) {
    for (const [ck, to] of Object.entries(s.to)) if (p === to && (!best || to.length > best.base.length)) best = { s, ck, base: to, exact: true };
    for (const [ck, list] of Object.entries(s.detail || {})) for (const d of list)
      if (p.startsWith(d + "/") && (!best || d.length > best.base.length)) best = { s, ck, base: d, exact: false };
  }
  return best;
}
const owners = (m) => Object.entries(m.s.to).filter(([, to]) => to === m.base).map(([ck]) => ck)
  .concat(Object.entries(m.s.detail || {}).filter(([, l]) => !m.exact && l.includes(m.base)).map(([ck]) => ck));

// Reconcile the URL with the scope: returns {scope, redirect} where redirect is a full URL to replace with, or null.
export function reconcile(path, q) {
  const m = screenFor(path);
  const explicit = q.get("scope");
  let sc = parseScope(explicit || storedScope() || "global");
  if (!m) return { scope: sc, redirect: null, screen: null };
  const own = owners(m);
  if (!own.includes(sc.ck)) {
    const target = m.exact && m.s.to[sc.ck];
    if (target && target !== m.base) {
      // same screen exists in this scope under another path (e.g. Overview ↔ Viet Nam situation map)
      return { scope: sc, redirect: withScope(target, sc.key, true), screen: m.s };
    }
    // the path decides: pick a scope that owns it, keeping the remembered province when it fits
    const st = parseScope(storedScope());
    sc = own.includes(st.ck) ? st : parseScope(own.includes("global") ? "global" : own[0]);
  }
  if (explicit !== sc.key) {
    const nq = new URLSearchParams(q); nq.set("scope", sc.key);
    return { scope: sc, redirect: path + "?" + nq.toString(), screen: m.s };
  }
  return { scope: sc, redirect: null, screen: m.s };
}

// URL for `path` in scope `key`. `fresh` drops filters that belong to another country (region, p, origin, disease…).
export function withScope(path, key, fresh = false) {
  if (fresh) {
    const q = new URLSearchParams();
    q.set("scope", key);
    const cur = new URLSearchParams(location.search);
    for (const k of ["horizon", "layer"]) if (cur.get(k)) q.set(k, cur.get(k));
    return path + "?" + q.toString();
  }
  return linkWith(path, { scope: key });
}

// Switch scope from anywhere: stays on the same screen when it has data in the new scope, else that scope's Overview.
export function setScope(key, { screen } = {}) {
  const next = parseScope(key);
  rememberScope(next.key);
  const m = screenFor(location.pathname);
  const cur = scopeOf(new URLSearchParams(location.search));
  const s = screen ? SCREENS.find((x) => x.k === screen) : m?.s;
  const target = (s && s.to[next.ck]) || SCREENS[0].to[next.ck];
  const sameCountry = cur.ck === next.ck;
  // detail pages (a municipality, a run) stay put only while the country is unchanged and no province was picked
  const path = m && !m.exact && sameCountry && !screen && !next.region ? location.pathname : target;
  const q = sameCountry ? new URLSearchParams(location.search) : new URLSearchParams(withScope(target, next.key, true).split("?")[1]);
  q.set("scope", next.key);
  for (const k of ["region", "p", "origin"]) q.delete(k);
  navigate(path + "?" + q.toString());
}

// Scope tree (only places with data) + helpers for the current scope.
export function useScopes() {
  const r = useApi("/scopes");
  const countries = r.data?.countries || [];
  const find = (sc) => {
    const c = countries.find((x) => x.id === sc.country) || null;
    const reg = c && sc.region ? c.regions.find((x) => x.id === sc.region) || null : null;
    return { country: c, region: reg };
  };
  return { ...r, countries, find };
}

export function scopeName(sc, tree) {
  if (!sc.country) return "Global";
  const { country, region } = tree.find(sc);
  const cn = country?.name || sc.country.toUpperCase();
  return region ? `${cn} · ${region.name}` : sc.region ? `${cn} · ${sc.region}` : cn;
}

// Brazil state filter for API paths: "&state=SP" (or "?state=SP" with first=true) when a state is in scope.
export const stateParam = (q, first = false) => { const sc = scopeOf(q); return sc.country === "br" && sc.region ? `${first ? "?" : "&"}state=${sc.region}` : ""; };

// Disease to show in this scope: the URL's if that scope has a series for it, else the first available.
export function scopeDisease(q, tree, fallback = "dengue") {
  const want = q.get("disease") || fallback;
  const sc = scopeOf(q);
  const { country, region } = tree.find(sc);
  const avail = region?.diseases || country?.diseases;
  if (!avail || !avail.length || avail.includes(want)) return want;
  return avail.includes(fallback) ? fallback : avail[0];
}
