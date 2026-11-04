// Simple / Pro view mode. Simple (default) shows the answer and the next action; Pro adds every table, fold,
// diagnostic and admin tool. Remembered per browser; a missing or blocked localStorage just means Simple.
import { html, useState, useEffect } from "../vendor/preact-htm.js";

const KEY = "hp_mode";
// a shared link can carry ?mode=pro|simple; it is remembered like a click on the switch
try { const u = new URLSearchParams(location.search).get("mode"); if (u === "pro" || u === "simple") localStorage.setItem("hp_mode", u); } catch (e) {}
const read = () => { try { return localStorage.getItem(KEY) === "pro" ? "pro" : "simple"; } catch (e) { return "simple"; } };
export const getMode = read;
export function setMode(m) {
  try { localStorage.setItem(KEY, m); } catch (e) {}
  window.dispatchEvent(new Event("hp-mode"));
}
export function useMode() {
  const [m, set] = useState(read);
  useEffect(() => { const f = () => set(read()); window.addEventListener("hp-mode", f); return () => window.removeEventListener("hp-mode", f); }, []);
  return m;
}
export const usePro = () => useMode() === "pro";

// renders children only in Pro mode
export const Pro = ({ children }) => (usePro() ? children : null);

export function ModeSwitch() {
  const m = useMode();
  return html`<div class="seg sm modesw" role="radiogroup" aria-label="View mode" title="Simple shows the answer; Pro shows every diagnostic">
    ${[["simple", "Simple"], ["pro", "Pro"]].map(([v, l]) => html`<button role="radio" aria-checked=${m === v} class=${m === v ? "on" : ""}
      onClick=${() => setMode(v)}>${l}</button>`)}</div>`;
}

// "Show the details" link for Simple mode: one click jumps to Pro
export const MoreInPro = ({ children = "Show all diagnostics" }) => (usePro() ? null
  : html`<button class="link small" onClick=${() => setMode("pro")}>${children} →</button>`);
