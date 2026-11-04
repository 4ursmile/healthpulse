// Shell (tinted L-frame + white sheet), router with lazy page modules, session/role switcher and region search.
import { html, render, useState, useEffect, useMemo, useRef, createContext, useContext } from "../vendor/preact-htm.js";
import { api, setToken, getToken, useLocation, navigate, linkWith, useApi } from "./api.js";
import { Icon, Link, Toasts, Avatar, label, ErrorBox, fmt } from "./ui.js";
import { SCREENS, reconcile, rememberScope, setScope, useScopes, scopeName, scopeKey, screenFor } from "./scope.js";
import { LEVEL } from "./vnmap.js";
import { ModeSwitch, useMode } from "./mode.js";

export const Session = createContext({ me: null, can: () => false, reload: () => {} });
export const useSession = () => useContext(Session);

const ROUTES = [
  ["/overview", "overview"], ["/explore", "explore"], ["/regions/:id", "region"], ["/diseases", "disease"], ["/diseases/:id", "disease"],
  ["/workforce", "workforce"], ["/facilities", "facilities"], ["/simulation", "simulation"], ["/simulation/runs/:run", "simrun"],
  ["/simulation/:id", "simulation"], ["/model-lab", "modellab"], ["/data-lab", "datalab"], ["/alerts", "alerts"], ["/login", "login"],
  ["/vn", "vn/dashboard"], ["/vn/simulate", "vn/simulate"], ["/vn/sandbox", "vn/sandbox"], ["/vn/ops", "vn/ops"],
  ["/admin", "admin/control"], ["/admin/sources", "admin/sources"], ["/admin/sources/:id", "admin/source"],
  ["/admin/ingestions", "admin/ingestions"], ["/admin/ingestions/:id", "admin/ingestion"], ["/admin/catalog", "admin/catalog"],
  ["/admin/quality", "admin/catalog"], ["/admin/features", "admin/features"], ["/admin/features/new", "admin/featurebuilder"],
  ["/admin/features/:id", "admin/feature"], ["/admin/feature-sets", "admin/featuresets"], ["/admin/datasets", "admin/datasets"],
  ["/admin/datasets/new", "admin/datasetbuilder"], ["/admin/datasets/:id", "admin/dataset"], ["/admin/experiments", "admin/experiments"],
  ["/admin/experiments/new", "admin/wizard"], ["/admin/experiments/compare", "admin/compare"], ["/admin/training-runs/:id", "admin/run"],
  ["/admin/registry", "admin/registry"], ["/admin/registry/:model/:version", "admin/model"], ["/admin/deployments", "admin/deployments"],
  ["/admin/model-monitoring", "admin/monitoring"], ["/admin/simulation-audit", "admin/simaudit"], ["/admin/security", "admin/security"],
  ["/admin/settings", "admin/settings"],
];

function match(path) {
  for (const [pat, mod] of ROUTES) {
    const a = pat.split("/"), b = path.replace(/\/+$/, "").split("/");
    if (a.length !== b.length) continue;
    const params = {};
    let ok = true;
    for (let i = 0; i < a.length; i++) {
      if (a[i].startsWith(":")) params[a[i].slice(1)] = decodeURIComponent(b[i]);
      else if (a[i] !== b[i]) { ok = false; break; }
    }
    if (ok) return { mod, params };
  }
  return null;
}

// product groups come from SCREENS (scope.js); only screens with data in the current scope are listed
const GROUPS = ["Situation", "Plan", "Evidence"];
const ADMIN = { h: "Admin", admin: true, items: [["/admin", "Control center", "control"], ["/admin/sources", "Data sources", "source"],
    ["/admin/ingestions", "Ingestion runs", "runs"], ["/admin/catalog", "Catalog & quality", "catalog"], ["/admin/features", "Feature store", "feature"],
    ["/admin/datasets", "Training datasets", "dataset"], ["/admin/experiments", "Experiments", "exp"], ["/admin/registry", "Model registry", "registry", "reviews"],
    ["/admin/deployments", "Deployments", "deploy"], ["/admin/model-monitoring", "Monitoring", "monitor"], ["/admin/simulation-audit", "Simulation audit", "lineage"],
    ["/admin/security", "Access & audit", "audit"], ["/admin/settings", "Settings", "settings"]] };

// Simple mode keeps the admin nav to the everyday path: check data, train, review, deploy. Pro lists every tool.
const SIMPLE_ADMIN = new Set(["/admin", "/admin/sources", "/admin/experiments", "/admin/registry", "/admin/deployments", "/admin/settings"]);
const SIMPLE_HIDE = new Set(["datalab"]);

const Mark = () => html`<svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="6" fill="var(--accent)"/>
  <path d="M4 13h3.2l1.8-5 3 9 2.2-6 1.3 2H20" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

const norm = (t) => (t || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/đ/g, "d").toLowerCase();
// One search for every place with data: countries, Brazilian states, Vietnamese provinces and Brazilian municipalities.
function Search({ tree }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState(0);
  const inp = useRef(null);
  const r = useApi(q.length >= 2 && tree.countries.some((c) => c.id === "br") ? `/metadata/regions?q=${encodeURIComponent(q)}&scope=pilot&limit=6` : null);
  useEffect(() => {
    const f = (e) => { if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) { e.preventDefault(); inp.current?.focus(); } };
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, []);
  const n = norm(q);
  const places = n.length >= 2 ? tree.countries.flatMap((c) => [
    ...(norm(c.name).includes(n) || c.id === n ? [{ kind: "place", key: c.id, name: c.name, sub: `${c.units} ${c.unit_name}` }] : []),
    ...c.regions.filter((x) => norm(x.name).includes(n) || norm(x.id) === n).map((x) => ({ kind: "place", key: scopeKey(c.id, x.id), name: x.name, sub: `${c.level_name} · ${c.name}`, level: x.level })),
  ]).slice(0, 6) : [];
  const rows = [...places, ...(r.data || []).map((m) => ({ kind: "muni", ...m }))];
  const go = (m) => {
    setOpen(false); setQ("");
    if (m.kind === "place") return setScope(m.key);
    rememberScope(scopeKey("br", m.state_abbr));
    navigate(linkWith(`/regions/${m.municipality_id}`, { region: m.municipality_id, scope: scopeKey("br", m.state_abbr) }));
  };
  return html`<div class="search" role="search">
    <${Icon} n="search" s=${14} />
    <input ref=${inp} type="search" placeholder="Search country, state, province or municipality" aria-label="Search places" value=${q}
      onInput=${(e) => { setQ(e.target.value); setOpen(true); setSel(0); }} onFocus=${() => setOpen(true)} onBlur=${() => setTimeout(() => setOpen(false), 150)}
      onKeyDown=${(e) => { if (e.key === "ArrowDown") setSel((s) => Math.min(rows.length - 1, s + 1)); if (e.key === "ArrowUp") setSel((s) => Math.max(0, s - 1)); if (e.key === "Enter" && rows[sel]) go(rows[sel]); if (e.key === "Escape") setOpen(false); }} />
    <kbd>/</kbd>
    ${open && q.length >= 2 && html`<div class="results" role="listbox">${rows.length ? rows.map((m, i) => m.kind === "place" ? html`
      <button role="option" aria-selected=${i === sel} onMouseDown=${() => go(m)}><${Icon} n="map" s=${14} /><span style="flex:1">${m.name} <span class="muted">· ${m.sub}</span></span>
        ${m.level && html`<i class="sdot" style=${`background:${LEVEL[m.level]?.c}`}></i>`}<span class="pill">Scope</span></button>` : html`
      <button role="option" aria-selected=${i === sel} onMouseDown=${() => go(m)}><${Icon} n="pin" s=${14} /><span style="flex:1">${m.name} <span class="muted">· ${m.state_abbr}</span></span>
        <span class="idc muted">${m.municipality_id}</span><span class="pill ok">Data</span></button>`)
      : html`<div class="small muted" style="padding:8px 10px">${r.loading ? "Searching…" : "No place with data matches."}</div>`}</div>`}
  </div>`;
}

const fweek = (d) => (d ? new Date(d + "T00:00:00Z").toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" }) : "—");
const fmonth = (d) => (d ? new Date(d + "T00:00:00Z").toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" }) : "—");
const SARI = { covid19_sari: "COVID SARI", influenza_sari: "Flu SARI", dengue: "Dengue", chikungunya: "Chikungunya" };
// one-line headline per scope node, so the picker itself says where attention is needed
function scopeSub(sc, tree) {
  if (!tree.countries.length) return tree.loading ? "Loading places…" : "No data ingested yet";
  if (!sc.country) return `${tree.countries.length} countries · ${tree.countries.reduce((a, c) => a + c.regions.length, 0)} places with data`;
  const { country: c, region: x } = tree.find(sc);
  if (!c) return "No data for this scope";
  if (!x) return c.id === "vn" ? `${c.regions.length} provinces · estimate ${fmonth(c.latest)}` : `${c.regions.length} states · week of ${fweek(c.latest)}`;
  return regionLine(c, x);
}
const regionLine = (c, x) => c.id === "vn" ? `${LEVEL[x.level]?.label || "—"} · ${fmt.d1(x.incidence_per_100k)} per 100k`
  : x.reported_last_week != null ? `${fmt.int(x.reported_last_week)} dengue cases last week${x.open_alerts ? ` · ${x.open_alerts} alerts` : ""}`
  : `${x.diseases.map((d) => SARI[d] || d).join(", ")} only · ${fmt.int(x.units)} municipalities`;

// The scope picker (replaces the old fixed "Brazil pilot" workspace button). Lists only places with data.
function ScopePicker({ sc, tree }) {
  const [open, set] = useState(false);
  const [f, setF] = useState("");
  const [more, setMore] = useState({});
  const box = useRef(null);
  const inp = useRef(null);
  useEffect(() => {
    if (!open) return;
    setTimeout(() => inp.current?.focus(), 0);
    const h = (e) => { if (!box.current?.contains(e.target)) set(false); };
    const k = (e) => e.key === "Escape" && set(false);
    document.addEventListener("mousedown", h); window.addEventListener("keydown", k);
    return () => { document.removeEventListener("mousedown", h); window.removeEventListener("keydown", k); };
  }, [open]);
  const pick = (key) => { set(false); setF(""); setScope(key); };
  const n = norm(f);
  const lists = tree.countries.map((c) => {
    const regs = n ? c.regions.filter((x) => norm(x.name).includes(n) || norm(x.id) === n) : c.regions;
    const lim = n || more[c.id] || sc.country === c.id ? regs.length : 6;
    return { c, regs: regs.slice(0, lim), hidden: regs.length - lim, show: !n || regs.length || norm(c.name).includes(n) };
  });
  const first = () => { for (const l of lists) { if (n && norm(l.c.name).includes(n)) return l.c.id; if (l.regs[0]) return scopeKey(l.c.id, l.regs[0].id); } return null; };
  const on = (key) => key === sc.key;
  return html`<div class="scopebox" ref=${box}>
    <button class="ws" onClick=${() => set(!open)} aria-haspopup="listbox" aria-expanded=${open} title="Change scope">
      <${Icon} n="map" s=${16} /><span style="flex:1;min-width:0"><div class="t ell">${scopeName(sc, tree)}</div><div class="s ell">${scopeSub(sc, tree)}</div></span><${Icon} n="chevron" s=${14} /></button>
    ${open && html`<div class="scopepop" role="listbox" aria-label="Scope">
      <input type="search" ref=${inp} placeholder="Filter places" aria-label="Filter places" value=${f} onInput=${(e) => setF(e.target.value)}
        onKeyDown=${(e) => { if (e.key === "Enter") { const k = first(); if (k) pick(k); } }} />
      <div class="scroll">
        ${!n && html`<button role="option" aria-selected=${on("global")} class=${"srow top" + (on("global") ? " on" : "")} onClick=${() => pick("global")}>
          <span class="nm">Global</span><span class="meta">${tree.countries.length} countries with data</span></button>`}
        ${lists.filter((l) => l.show).map(({ c, regs, hidden }) => html`
          <button role="option" aria-selected=${on(c.id)} class=${"srow top" + (on(c.id) ? " on" : "")} onClick=${() => pick(c.id)}>
            <span class="nm">${c.name}</span><span class="meta">${c.regions.length} ${c.level_name.toLowerCase()}s · ${c.id === "vn" ? fmonth(c.latest) : `wk ${fweek(c.latest)}`}</span></button>
          ${regs.map((x) => html`<button role="option" aria-selected=${on(scopeKey(c.id, x.id))} class=${"srow" + (on(scopeKey(c.id, x.id)) ? " on" : "")} onClick=${() => pick(scopeKey(c.id, x.id))}>
            <i class="sdot" style=${`background:${c.id === "vn" ? LEVEL[x.level]?.c : x.open_alerts ? "var(--warning)" : x.reported_last_week != null ? "var(--accent)" : "var(--control)"}`}></i>
            <span class="nm">${x.name}</span><span class="meta">${c.id === "vn" ? LEVEL[x.level]?.label : x.reported_last_week != null ? `${fmt.int(x.reported_last_week)}/wk` : "SARI only"}</span></button>`)}
          ${hidden > 0 && html`<button class="srow more" onClick=${() => setMore((m) => ({ ...m, [c.id]: true }))}>Show ${hidden} more ${c.level_name.toLowerCase()}s</button>`}`)}
        ${n && !lists.some((l) => l.show) && html`<div class="small muted" style="padding:8px 10px">No place with data matches.</div>`}
      </div>
      <div class="small faint" style="padding:6px 10px 2px">Only places with ingested data are listed.</div>
    </div>`}
  </div>`;
}

const isDark = () => { const t = document.documentElement.getAttribute("data-theme"); return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches; };
const theme = () => { const n = isDark() ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", n); try { localStorage.setItem("hp_theme", n); } catch (e) {} window.dispatchEvent(new Event("hp-theme")); };

function ThemeBtn() {
  const [dark, set] = useState(isDark());
  useEffect(() => { const f = () => set(isDark()); window.addEventListener("hp-theme", f); return () => window.removeEventListener("hp-theme", f); }, []);
  return html`<button class="btn ghost theme" onClick=${theme} aria-label=${dark ? "Switch to light theme" : "Switch to dark theme"} title=${dark ? "Light theme" : "Dark theme"}>
    <${Icon} n=${dark ? "sun" : "moon"} /></button>`;
}

function Account({ me }) {
  const [open, set] = useState(false);
  const box = useRef(null);
  useEffect(() => {
    if (!open) return;
    const f = (e) => { if (!box.current?.contains(e.target)) set(false); };
    const k = (e) => e.key === "Escape" && set(false);
    document.addEventListener("mousedown", f); window.addEventListener("keydown", k);
    return () => { document.removeEventListener("mousedown", f); window.removeEventListener("keydown", k); };
  }, [open]);
  return html`<div style="position:relative" ref=${box}>
    <button class="btn ghost" onClick=${() => set(!open)} aria-haspopup="menu" aria-expanded=${open}>
      <${Avatar} role=${me?.role} name=${me?.display_name} /><span class="hide-sm">${me?.user_id === "anonymous" ? "Public viewer" : me?.display_name}</span></button>
    ${open && html`<div class="menu" role="menu">
      <div class="small muted" style="padding:8px 10px">Signed in as ${label(me?.role)}</div>
      <button class="btn ghost" role="menuitem" onClick=${() => { set(false); navigate("/login"); }}><${Icon} n="user" />Switch role</button>
      <button class="btn ghost" role="menuitem" onClick=${() => { set(false); theme(); }}><${Icon} n="sun" />Toggle theme</button>
      ${getToken() && html`<button class="btn ghost" role="menuitem" onClick=${() => { set(false); setToken(null); navigate("/overview"); }}><${Icon} n="back" />Sign out</button>`}
    </div>`}
  </div>`;
}

const readShut = () => { try { return JSON.parse(localStorage.getItem("hp_nav_shut") || "{}"); } catch (e) { return {}; } };
function Side({ path, me, open, close, counts, sc, tree }) {
  const isAdmin = me && me.role !== "viewer";
  const [shut, setShut] = useState(readShut);
  const toggle = (h) => setShut((x) => { const n = { ...x, [h]: !x[h] }; try { localStorage.setItem("hp_nav_shut", JSON.stringify(n)); } catch (e) {} return n; });
  const mode = useMode();
  const cur = screenFor(path)?.s.k;
  const groups = GROUPS.map((h) => ({ h, items: SCREENS.filter((s) => s.g === h && s.to[sc.ck] && (mode === "pro" || !SIMPLE_HIDE.has(s.k) || s.k === cur))
    .map((s) => [s.to[sc.ck], s.l, s.ic, s.cnt, s.k]) })).filter((g) => g.items.length);
  if (isAdmin) groups.push(mode === "pro" ? ADMIN : { ...ADMIN, items: ADMIN.items.filter(([to]) => SIMPLE_ADMIN.has(to) || path === to || path.startsWith(to + "/")) });
  return html`<nav class=${"side" + (open ? " open" : "")} aria-label="Main">
    <div class="row" style="justify-content:space-between">
      <div class="brand"><${Mark} />HealthPulse</div>
      ${open && html`<button class="btn ghost sm" aria-label="Close menu" onClick=${close}><${Icon} n="close" /></button>`}
    </div>
    <${ScopePicker} sc=${sc} tree=${tree} />
    <div class="nav">${groups.map((g) => {
      const isOn = ([to, , , , k]) => (k ? k === cur : to === "/admin" ? path === to : path === to || path.startsWith(to + "/"));
      const closed = !!shut[g.h] && !g.items.some(isOn);
      return html`<button class="grp" aria-expanded=${!closed} onClick=${() => toggle(g.h)}>${g.h}<${Icon} n="chevron" s=${12} /></button>
      <div class=${"items" + (closed ? " shut" : "")}><div>${g.items.map((it) => {
      const [to, l, ic, cnt] = it;
      const on = isOn(it);
      const c = cnt && counts[cnt];
      return html`<${Link} to=${to.startsWith("/admin") ? to : linkWith(to, { scope: sc.key })} cls=${on ? "on" : ""} tabIndex=${closed ? -1 : 0}><${Icon} n=${ic} />${l}${c ? html`<span class="count" title=${cnt === "alerts" ? "draft alerts awaiting review" : "models awaiting review"}>${c}</span>` : null}</${Link}>`; })}</div></div>`; })}
    </div>
    <div class="foot small muted" style="padding:0 8px"><${Icon} n="info" s=${14} /><span>Decision support only. Every number says if it is observed, forecast, scenario or synthetic.</span></div>
  </nav>`;
}

const loaded = {};
// first paint of a lazily loaded screen: the final layout in flat grey bars (title, KPI strip, two panels)
const PageSkeleton = () => html`<div class="page" aria-busy="true" aria-label="Loading">
  <span class="sk sk-title"></span><span class="sk sk-sub"></span>
  <div class="grid" style="margin-top:28px"><span class="sk sk-panel s12" style="height:104px"></span>
    <span class="sk sk-panel s8" style="height:380px"></span><span class="sk sk-panel s4" style="height:380px"></span></div></div>`;
function Page({ route, q, me }) {
  const [Comp, setComp] = useState(() => loaded[route.mod] || null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    if (loaded[route.mod]) { setComp(() => loaded[route.mod]); return; }
    setComp(null);
    import(`./pages/${route.mod}.js`).then((m) => { loaded[route.mod] = m.default; setComp(() => m.default); }).catch((e) => setErr(e));
  }, [route.mod]);
  if (err) return html`<div class="page"><${ErrorBox} error=${err} /></div>`;
  if (!Comp) return html`<${PageSkeleton} />`;
  return html`<${Comp} params=${route.params} q=${q} me=${me} />`;
}

function App() {
  const loc = useLocation();
  const [me, setMe] = useState(null);
  const [menu, setMenu] = useState(false);
  const [counts, setCounts] = useState({});
  const loadMe = () => api("/me").then((r) => setMe(r.data)).catch(() => setMe({ user_id: "anonymous", role: "viewer", display_name: "Public viewer", permissions: ["read"] }));
  useEffect(() => { loadMe(); const f = () => loadMe(); window.addEventListener("hp-auth", f); return () => window.removeEventListener("hp-auth", f); }, []);
  useEffect(() => {
    api("/alerts?state=draft").then((r) => setCounts((c) => ({ ...c, alerts: r.data.length }))).catch(() => {});
    if (me && me.role !== "viewer") api("/admin/registry/models").then((r) => setCounts((c) => ({ ...c, reviews: r.data.filter((m) => ["CANDIDATE", "REVIEWED"].includes(m.approval_state)).length }))).catch(() => {});
  }, [me?.role, loc.path]);
  useEffect(() => { setMenu(false); const m = document.getElementById("main"); if (m) m.scrollTo({ top: 0, behavior: "instant" }); }, [loc.path]);
  const session = useMemo(() => ({ me, can: (p) => !!me?.permissions?.includes(p), reload: loadMe }), [me]);
  useEffect(() => { if (loc.path === "/" || loc.path === "") navigate("/overview", { replace: true }); }, [loc.path]);
  const route = match(loc.path);
  const crumbs = loc.path.split("/").filter(Boolean);
  const tree = useScopes();
  const rec = useMemo(() => reconcile(loc.path, loc.q), [loc.path, loc.q.toString()]);
  const sc = rec.scope;
  useEffect(() => { if (rec.redirect) navigate(rec.redirect, { replace: true }); else if (rec.screen) rememberScope(sc.key); }, [rec.redirect, sc.key]);
  // an unknown province/state in the URL (or one that lost its data) falls back to its country
  useEffect(() => { if (tree.data && sc.region && !tree.find(sc).region) setScope(sc.country); }, [tree.data, sc.key]);
  const screenLabel = rec.screen?.l;
  return html`<${Session.Provider} value=${session}>
    <div class="shell">
      <${Side} path=${loc.path} me=${me} open=${menu} close=${() => setMenu(false)} counts=${counts} sc=${sc} tree=${tree} />
      <header class="top">
        <button class="btn ghost sm menu-btn" aria-label="Open menu" onClick=${() => setMenu(true)}><${Icon} n="menu" /></button>
        <div class="crumbs">${crumbs[0] === "admin" ? html`<span>Admin</span><span>/</span><b>${label((crumbs[1] || "control center").replace(/-/g, " "))}</b>`
          : rec.screen ? html`<span>${scopeName(sc, tree)}</span><span>/</span><b>${screenLabel}</b>`
          : html`<b>${label((crumbs[0] || "").replace(/-/g, " "))}</b>`}</div>
        <${Search} tree=${tree} />
        <div class="right"><${ModeSwitch} /><span class="pill hide-sm" title="Environment">${me?.environment || "local"}</span><${ThemeBtn} /><${Account} me=${me} /></div>
      </header>
      <main class="sheet" id="main">${rec.redirect ? html`<${PageSkeleton} />` : route ? html`<${Page} key=${route.mod + "|" + sc.ck} route=${route} q=${loc.q} me=${me} />` : html`<div class="page"><h1>Not found</h1><p class="subline">No screen at ${loc.path}.</p></div>`}</main>
    </div>
    <${Toasts} />
  </${Session.Provider}>`;
}

try { const t = localStorage.getItem("hp_theme"); if (t) document.documentElement.setAttribute("data-theme", t); } catch (e) {}
// local/demo only: `#as=<role>` signs in with that seeded demo account (used by screenshot checks); the API refuses it elsewhere
async function boot() {
  const m = location.hash.match(/^#as=([a-z_]+)/);
  if (m) {
    try {
      const r = await api("/auth/demo-accounts");
      const u = r.data.find((x) => x.role === m[1]);
      if (u) setToken(u.token);
    } catch (e) {}
    history.replaceState(null, "", location.pathname + location.search);
  }
  render(html`<${App} />`, document.getElementById("app"));
}
boot();
