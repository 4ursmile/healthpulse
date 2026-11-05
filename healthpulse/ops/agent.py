"""AI analyst for live response: an LLM that answers controllers' questions by calling tools over the live incident.

Harness:
- Claude (official SDK, manual agentic loop, streamed): the model reads the situation through read-only tools
  (situation, clusters, reports, zones, resources), tests options with `project_scenario` and `optimize_resources`
  (pure what-ifs, nothing saved), and may `propose_zone` / `propose_plan`. Proposals are stored as PROPOSED and drawn
  dashed on the map; only a person can enforce a zone or approve a plan. The model can never set a real zone.
- OpenAI-compatible providers: one grounded call with the situation + an optimizer run in the prompt (no tools).
- No key / disabled / failing: the rule-based brief below, clearly labelled.

Every tool input is validated with pydantic before it runs, and every tool result is the same numbers the screen shows.
Events streamed to the browser: meta, text {t}, tool {name, input}, tool_result {name, ok, summary}, proposal, done, error.
"""
from __future__ import annotations

import json
import os
import time
from collections import deque
from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from ..llm import client as llm
from . import analysis, geo, optimize, project, store

_calls: deque[float] = deque(maxlen=60)
MAX_TURNS = 10
RESULT_CHARS = 12000


# ------------------------------------------------------------------------------------------------------- tool inputs
class NoArgs(BaseModel):
    pass


class ClusterArgs(BaseModel):
    cluster_id: str = Field(..., pattern=r"^C\d+$")


class ReportQuery(BaseModel):
    kind: Literal["case", "suspected", "death", "recovered", "vaccinated", "tested"] | None = None
    province_id: str | None = Field(None, pattern=r"^VN\d{2}$")
    last_days: int = Field(14, ge=1, le=120)


class SimZone(BaseModel):
    kind: Literal["affected", "quarantine", "buffer"]
    cluster_id: str | None = Field(None, pattern=r"^C\d+$", description="use the suggested shape for this cluster")
    lat: float | None = Field(None, ge=5, le=26)
    lon: float | None = Field(None, ge=100, le=120)
    radius_km: float | None = Field(None, gt=0.1, le=80)
    contact_reduction: float = Field(0.6, ge=0, le=0.95)


class ScenarioArgs(BaseModel):
    zones: list[SimZone] = Field(default_factory=list, max_length=12)
    include_latest_plan: bool = True


class OptimizeArgs(BaseModel):
    basis: Literal["simulated", "real"] = "simulated"


class ProposeZone(SimZone):
    kind: Literal["affected", "quarantine", "buffer", "safe", "vaccination"]
    rationale: str = Field(..., min_length=5, max_length=600)


class ProposePlan(BaseModel):
    rationale: str = Field(..., min_length=5, max_length=600)


TOOLS = [
    ("get_situation", NoArgs, "Current situation of the incident: KPIs, R estimate and its basis, data warnings, top clusters, "
     "provinces, active and proposed zones, resource stock, and the 28-day projection under baseline / current / plan. Call this first."),
    ("get_cluster", ClusterArgs, "Full detail of one cluster (C1, C2 …): daily series, growth fit, spread speed, areas, people inside, "
     "zones covering it and the suggested zone shapes."),
    ("query_reports", ReportQuery, "Counts of keyed-in reports by day and by province over the last N days, optionally filtered by kind and province."),
    ("list_zones", NoArgs, "Every zone with kind, basis (real = enforced on the ground, simulated = what-if), status, area, people inside (estimate), "
     "cases inside and contact reduction."),
    ("list_resources", NoArgs, "Resource depots and stock (teams, vaccine doses, test kits, beds, ambulances), plus the approved and latest proposed allocation plans."),
    ("project_scenario", ScenarioArgs, "What-if: project the next 28 days with extra hypothetical zones (cluster_id to reuse the suggested shape, "
     "or lat/lon/radius_km for a circle). Nothing is saved. Returns cases, deaths and peak unmet beds vs baseline and current."),
    ("optimize_resources", OptimizeArgs, "Run the allocation optimizer over current stock: which depot sends what to which cluster/province, "
     "effective delay and projected cases/deaths averted. Nothing is saved."),
    ("propose_zone", ProposeZone, "Store a zone as PROPOSED (drawn dashed on the map) for a controller to accept, simulate or reject. "
     "Use sparingly, only for zones you recommend."),
    ("propose_plan", ProposePlan, "Store the optimizer's current allocation plan as PROPOSED for a controller to approve."),
]


def _inline(sch: dict) -> dict:
    """Resolve pydantic's $defs/$ref so every tool schema is self-contained."""
    defs = sch.pop("$defs", {})

    def walk(x):
        if isinstance(x, dict):
            if "$ref" in x:
                return walk(dict(defs[x["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in x.items() if k != "title"}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x
    return walk(sch)


def tool_defs(can_propose: bool = True) -> list[dict]:
    out = []
    for name, model, desc in TOOLS:
        if name.startswith("propose_") and not can_propose:
            continue
        sch = _inline(model.model_json_schema())
        out.append({"name": name, "description": desc, "input_schema": {"type": "object", **{k: v for k, v in sch.items() if k != "type"}},
                    "eager_input_streaming": True})
    return out


# ------------------------------------------------------------------------------------------------------ tool runtime
def _short_cluster(c: dict) -> dict:
    return {k: c[k] for k in ("id", "label", "lat", "lon", "radius_km", "cases", "confirmed", "last7", "prev7", "trend", "doubling_days",
                              "spread_km_day", "affected_km2", "affected_people_est", "quarantine_km2", "quarantine_people_est", "first", "last")} | {
        "R": c["growth"]["R"], "R_interval": [c["growth"]["R_lo"], c["growth"]["R_hi"]], "R_basis": c["growth"]["basis"], "zones": c.get("zones", [])}


def _zone_view(z: dict) -> dict:
    p = z["params"]
    return {"zone_id": z["zone_id"], "name": z["name"], "kind": z["kind"], "basis": z["basis"], "status": z["status"], "origin": z["origin"],
            "area_km2": p.get("area_km2"), "people_est": p.get("people_est"), "contact_reduction": p.get("contact_reduction"),
            "cases_inside": z.get("stats", {}).get("cases_inside"), "last7_inside": z.get("stats", {}).get("last7_inside"),
            "clusters": z.get("stats", {}).get("clusters"), "activated_at": p.get("activated_at")}


def _proj_view(pr: dict) -> dict:
    out = {}
    for k in ("baseline", "current", "plan"):
        s = pr[k]
        out[k] = {"cases_28d": s["cum_cases"], "interval": [s["cum_lo"], s["cum_hi"]], "next14": s["next14"], "deaths": s["deaths"],
                  "peak_unmet_beds": s["peak_unmet"], **({"averted_vs_baseline": s.get("averted_vs_baseline")} if k != "baseline" else {})}
    return out


class Ctx:
    def __init__(self, iid: str, actor: str):
        self.iid, self.actor = iid, actor
        self.an = analysis.analyze(iid)
        self.proposals: list[dict] = []
        self.last_opt: dict | None = None
        self.can_propose = True

    def _zone_from(self, a: SimZone) -> dict:
        if a.cluster_id:
            c = next((c for c in self.an["clusters"] if c["id"] == a.cluster_id), None)
            if not c:
                raise ValueError(f"no cluster {a.cluster_id}")
            g = c["hull"] if a.kind == "affected" else c["suggest_quarantine"] if a.kind == "quarantine" else geo.buffer_geom(c["suggest_quarantine"], 3)
            if a.kind == "vaccination":
                g = geo.buffer_geom(c["suggest_quarantine"], 2)
            if a.kind == "safe":
                raise ValueError("a safe zone needs lat/lon/radius_km, not a cluster")
            return g
        if a.lat is None or a.lon is None or not a.radius_km:
            raise ValueError("give cluster_id, or lat + lon + radius_km")
        return geo.circle(a.lat, a.lon, a.radius_km)

    def run(self, name: str, raw: dict) -> dict:
        if name.startswith("propose_") and not self.can_propose:
            raise ValueError("this user cannot store proposals")
        model = next(m for n, m, _ in TOOLS if n == name)
        a = model(**(raw or {}))
        an = self.an
        if name == "get_situation":
            res = store.resources(self.iid)
            return {"incident": {k: an["incident"][k] for k in ("name", "mode", "pathogen", "semantics")}, "pathogen": an["incident"]["pathogen_spec"],
                    "settings": an["incident"]["settings"], "as_of": an["as_of"], "kpis": an["kpis"], "warnings": an["warnings"],
                    "clusters": [_short_cluster(c) for c in an["clusters"][:12]],
                    "sporadic": [{k: s[k] for k in ("province_id", "province", "cases", "last7")} for s in an["sporadic"][:10]],
                    "provinces": [{k: p[k] for k in ("province_id", "name", "confirmed", "last7", "per100k_7d", "deaths", "coverage", "hosp_est", "beds_free_est", "bed_load")}
                                  for p in an["provinces"][:12]],
                    "zones": [_zone_view(z) for z in an["zones"] if z["status"] != "lifted"],
                    "suggested_zones": [{k: s[k] for k in ("kind", "cluster", "area_km2", "people_est", "why")} for s in an["suggestions"]],
                    "safe_provinces": [s["name"] for s in an["safe_provinces"]][:20],
                    "stock": [{k: r[k] for k in ("resource_id", "kind", "name", "qty", "province_id")} for r in res],
                    "projection_28d": _proj_view(an["projection"]), "assumptions": project.ASSUMPTIONS}
        if name == "get_cluster":
            c = next((c for c in an["clusters"] if c["id"] == a.cluster_id), None)
            if not c:
                raise ValueError(f"no cluster {a.cluster_id}; clusters are {[x['id'] for x in an['clusters']]}")
            return _short_cluster(c) | {"growth_fit": c["growth"], "daily_last_21": dict(zip(an["days"][-21:], c["series"][-21:])),
                                         "provinces": c["provinces"], "quarantine_margin_km": c["quarantine_margin_km"],
                                         "suggested": [s for s in (x for x in an["suggestions"] if x["cluster"] == c["id"])]}
        if name == "query_reports":
            from datetime import date, timedelta
            since = (date.fromisoformat(an["as_of"]) - timedelta(days=a.last_days - 1)).isoformat()
            by_day, by_prov = {}, {}
            for r in store.reports(self.iid):
                if r["event_date"] < since or (a.kind and r["kind"] != a.kind) or (a.province_id and r["province_id"] != a.province_id):
                    continue
                by_day.setdefault(r["event_date"], {}).setdefault(r["kind"], 0)
                by_day[r["event_date"]][r["kind"]] += r["count"]
                by_prov.setdefault(r["province_id"], {}).setdefault(r["kind"], 0)
                by_prov[r["province_id"]][r["kind"]] += r["count"]
            names = geo.provinces()["attrs"]
            return {"since": since, "by_day": dict(sorted(by_day.items())),
                    "by_province": {f"{k} {names.get(k, {}).get('name', '')}": v for k, v in sorted(by_prov.items(), key=lambda kv: -sum(kv[1].values()))}}
        if name == "list_zones":
            return {"zones": [_zone_view(z) for z in an["zones"]]}
        if name == "list_resources":
            ap = store.approved_plan(self.iid)
            latest = store.plans(self.iid)[:1]
            return {"stock": store.resources(self.iid),
                    "approved_plan": ap and {"plan_id": ap["plan_id"], "moves": [m["why"] for m in ap["plan"]["allocations"]]},
                    "latest_plan": latest and {"plan_id": latest[0]["plan_id"], "status": latest[0]["status"], "moves": [m["why"] for m in latest[0]["plan"]["allocations"]]}}
        if name == "project_scenario":
            extra = []
            for z in a.zones:
                extra.append({"zone_id": "whatif", "kind": z.kind, "basis": "simulated", "status": "active",
                              "geometry": self._zone_from(z), "params": {"contact_reduction": z.contact_reduction}})
            pr = project.scenarios(an, None if a.include_latest_plan else {"allocations": []}, extra)
            return {"what_if_zones": len(extra), "projection_28d": _proj_view(pr), "provinces_short_of_beds": pr["plan"]["provinces"][:5],
                    "note": "plan = real zones + simulated zones + these what-if zones" + (" + the latest proposed allocation plan" if a.include_latest_plan else "")}
        if name == "optimize_resources":
            o = optimize.optimize(an, a.basis)
            self.last_opt = o
            return {"summary": o["summary"], "moves": [m["why"] for m in o["allocations"][:20]], "reserve": o["reserve"], "log": o["log"]}
        if name == "propose_zone":
            g = self._zone_from(a)
            z = store.add_zone(self.iid, store.ZoneIn(kind=a.kind, geometry=g, basis="simulated", status="proposed",
                                                      contact_reduction=a.contact_reduction if a.kind in ("affected", "quarantine", "buffer") else None,
                                                      note=a.rationale), f"ai:{self.actor}", origin="ai")
            self.proposals.append({"type": "zone", "zone_id": z["zone_id"], "name": z["name"]})
            return {"stored": True, "zone_id": z["zone_id"], "name": z["name"], "status": "proposed",
                    "area_km2": z["params"]["area_km2"], "people_est": z["params"]["people_est"]}
        if name == "propose_plan":
            o = self.last_opt or optimize.optimize(an, "simulated")
            if not o["allocations"]:
                return {"stored": False, "reason": "the optimizer found no move that averts cases or deaths"}
            p = store.save_plan(self.iid, o | {"rationale": a.rationale}, "simulated", f"ai:{self.actor}", origin="AI analyst")
            self.proposals.append({"type": "plan", "plan_id": p["plan_id"]})
            return {"stored": True, "plan_id": p["plan_id"], "moves": len(o["allocations"]), "summary": o["summary"]}
        raise ValueError(f"unknown tool {name}")


# --------------------------------------------------------------------------------------------------------- prompting
SYSTEM = """You are the outbreak-response analyst inside HealthPulse's Live response screen (Viet Nam, 34 provinces in force since \
1 July 2025). Controllers key in cases, deaths, vaccinations and tests as they arrive, draw zones (affected, quarantine, buffer, safe, \
vaccination) either for REAL (enforced on the ground) or as SIMULATED what-ifs, and allocate teams, vaccine, test kits, beds and ambulances.

How to work:
- Start with get_situation. Ground every number you state in a tool result; never invent counts, places or capacities.
- Test options before recommending them: project_scenario for zones, optimize_resources for allocation. Compare against baseline and \
current, and say what each option costs (people under restriction, doses, teams pulled from elsewhere).
- Keep the semantics straight. Reported counts are OBSERVED in a live incident and SYNTHETIC in a drill; projections are SCENARIOS \
under listed assumptions, not forecasts. Say when R comes from the prior rather than a fit, and when data are thin or delayed.
- Quarantine is a heavy measure: prefer the smallest zone that covers the cluster's spread, and mention proportionality, \
essential services and support for people inside (food, income, care). Note equity when a zone covers many people for few cases.
- You may propose_zone or propose_plan when you clearly recommend it. Proposals wait for a human: never claim a zone is enforced \
or a dispatch is approved.
- Answer in the language of the question (Vietnamese or English), keep province names in Vietnamese, use short sections and \
bullets, and end with 2-4 concrete next decisions for the controller."""


def available() -> tuple[bool, str | None]:
    cfg = llm.config()
    if not cfg["available"]:
        return False, ("AI turned off by an admin" if not cfg["enabled"] else f"AI not configured ({cfg['missing']})" if not cfg["configured"]
                       else "AI cooling down after an error")
    now = time.time()
    if len([x for x in _calls if now - x < 60]) >= 15:
        return False, "LLM rate limit for this server (15/min)"
    return True, None


def ask(iid: str, actor: str, question: str, lang: str = "en", history: list[dict] | None = None,
        can_propose: bool = True) -> Iterator[tuple[str, dict]]:
    ok, why = available()
    cfg = llm.config()
    try:
        ctx = Ctx(iid, actor)
        ctx.can_propose = can_propose
    except KeyError as e:
        yield "error", {"error": str(e)}
        return
    if not ok:
        yield "meta", {"provider": "rule_based", "reason": why}
        yield "text", {"t": rule_brief(ctx.an, lang)}
        yield "done", {"proposals": []}
        return
    _calls.append(time.time())
    yield "meta", {"provider": cfg["provider"], "model": cfg["model"], "tools": [t[0] for t in TOOLS] if cfg["provider"] == "anthropic" else []}
    try:
        if cfg["provider"] == "anthropic":
            yield from _claude_loop(ctx, question, lang, history or [], cfg)
        else:
            yield from _openai_single(ctx, question, lang)
        llm._mark(True)
    except llm.LLMError as e:
        llm._mark(False, str(e))
        yield "error", {"error": f"{e} — rule-based brief instead"}
        yield "text", {"t": "\n\n" + rule_brief(ctx.an, lang)}
    yield "done", {"proposals": ctx.proposals}


def _claude_loop(ctx: Ctx, question: str, lang: str, history: list[dict], cfg: dict) -> Iterator[tuple[str, dict]]:
    import anthropic
    client = llm.claude_client(cfg)
    q = question.strip() or ("Tình hình hiện tại thế nào và nên làm gì tiếp?" if lang == "vi" else "What is the situation and what should we do next?")
    msgs: list = [m for m in history[-6:] if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"].strip()]
    msgs.append({"role": "user", "content": f"[incident {ctx.an['incident']['name']} · {ctx.an['incident']['mode']} · as of {ctx.an['as_of']} · answer in {'Vietnamese' if lang == 'vi' else 'English'}]\n\n{q}"})
    kw = dict(model=cfg["model"], max_tokens=32000, system=SYSTEM if ctx.can_propose else SYSTEM + "\n\nThis user is read-only: you cannot store proposals.",
              tools=tool_defs(ctx.can_propose))
    if cfg["effort"] in llm.EFFORTS:
        kw["output_config"] = {"effort": cfg["effort"]}
    use_fallback = llm.use_fallback(cfg)
    for turn in range(MAX_TURNS):
        try:
            ctxm = (client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default", messages=msgs, **kw)
                    if use_fallback else client.messages.stream(messages=msgs, **kw))
            with ctxm as s:
                for text in s.text_stream:
                    yield "text", {"t": text}
                final = s.get_final_message()
        except ValueError as e:  # the SDK could not parse a streamed tool input at all
            yield "error", {"error": f"tool input could not be parsed ({e}); retrying"}
            continue
        except anthropic.AuthenticationError:
            raise llm.LLMError("Claude rejected the API key (check ANTHROPIC_API_KEY)")
        except anthropic.NotFoundError:
            raise llm.LLMError(f"Claude: model {cfg['model']} not found")
        except anthropic.RateLimitError:
            raise llm.LLMError("Claude: rate limited, try again shortly")
        except anthropic.BadRequestError as e:
            raise llm.LLMError(f"Claude: bad request ({e.message})")
        except anthropic.APIStatusError as e:
            raise llm.LLMError(f"Claude API error {e.status_code}")
        except anthropic.APIConnectionError:
            raise llm.LLMError(f"Claude: network error (is {cfg['base_url']} reachable?)")
        if final.stop_reason == "refusal":
            yield "text", {"t": "\n\n_The model declined this request._"}
            return
        if final.stop_reason == "pause_turn":
            msgs.append({"role": "assistant", "content": final.content})
            continue
        uses = [b for b in final.content if b.type == "tool_use"]
        if final.stop_reason == "max_tokens":
            yield "text", {"t": "\n\n_(answer truncated at the token limit)_"}
            return
        if final.stop_reason != "tool_use" or not uses:
            return
        msgs.append({"role": "assistant", "content": final.content})
        results = []
        for b in uses:
            yield "tool", {"name": b.name, "input": b.input}
            try:
                out = ctx.run(b.name, b.input if isinstance(b.input, dict) else {})
                txt = json.dumps(out, ensure_ascii=False, default=str)
                if len(txt) > RESULT_CHARS:
                    txt = txt[:RESULT_CHARS] + ' …"(truncated)"'
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": txt})
                yield "tool_result", {"name": b.name, "ok": True, "summary": _summ(b.name, out)}
                if b.name.startswith("propose_") and out.get("stored"):
                    yield "proposal", out
            except (ValidationError, ValueError, KeyError, StopIteration) as e:
                err = str(e)[:600]
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True, "content": err})
                yield "tool_result", {"name": b.name, "ok": False, "summary": err[:200]}
        msgs.append({"role": "user", "content": results})
    yield "text", {"t": "\n\n_(stopped after the tool-call limit)_"}


def _summ(name: str, out: dict) -> str:
    if name == "get_situation":
        k = out["kpis"]
        return f"{k['confirmed']:,.0f} confirmed · R≈{k['R']} ({k['r_basis']}) · {k['clusters']} clusters"
    if name == "project_scenario":
        p = out["projection_28d"]["plan"]
        return f"plan: {p['cases_28d']:,} cases/28d, averts {p.get('averted_vs_baseline', 0):,} vs baseline"
    if name == "optimize_resources":
        s = out["summary"]
        return f"{s['moves']} moves · ~{s['cases_averted']:,} cases, {s['deaths_averted']} deaths averted"
    if name == "propose_zone":
        return f"proposed {out.get('name')}"
    if name == "propose_plan":
        return f"proposed plan {out.get('plan_id')}" if out.get("stored") else out.get("reason", "")
    if name == "get_cluster":
        return f"{out.get('label')}: R≈{out.get('R'):.2f}, {out.get('trend')}"
    return "ok"


def _openai_single(ctx: Ctx, question: str, lang: str) -> Iterator[tuple[str, dict]]:
    sit = ctx.run("get_situation", {})
    yield "tool", {"name": "get_situation", "input": {}}
    yield "tool_result", {"name": "get_situation", "ok": True, "summary": _summ("get_situation", sit)}
    opt = ctx.run("optimize_resources", {"basis": "simulated"})
    yield "tool", {"name": "optimize_resources", "input": {"basis": "simulated"}}
    yield "tool_result", {"name": "optimize_resources", "ok": True, "summary": _summ("optimize_resources", opt)}
    user = (f"SITUATION:\n```json\n{json.dumps(sit, ensure_ascii=False, default=str)[:30000]}\n```\nOPTIMIZER (not saved):\n```json\n"
            f"{json.dumps(opt, ensure_ascii=False, default=str)[:8000]}\n```\n\nAnswer in {'Vietnamese' if lang == 'vi' else 'English'}.\n\nQUESTION: "
            f"{question or 'What is the situation and what should we do next?'}")
    sys = SYSTEM.replace("Start with get_situation.", "The situation and an optimizer run are given below (you cannot call tools).")
    for t in llm.stream(sys, user, max_tokens=6000):
        yield "text", {"t": t}


# --------------------------------------------------------------------------------------------------- rule-based brief
def rule_brief(an: dict, lang: str = "en") -> str:
    vi = lang == "vi"
    k = an["kpis"]
    pr = an["projection"]
    L = []
    if vi:
        L.append(f"**Tóm tắt (quy tắc, không dùng AI)** — {k['confirmed']:,.0f} ca xác nhận, {k['last7']:,.0f} ca trong 7 ngày"
                 + (f" ({k['change_7d']:+.0%} so với tuần trước)" if k['change_7d'] is not None else "")
                 + f", R ≈ {k['R']} [{k['R_lo']}–{k['R_hi']}] ({'ước tính' if k['r_basis'] == 'fitted' else 'giả định ban đầu'}), {k['clusters']} ổ dịch.")
    else:
        L.append(f"**Rule-based brief (no AI)** — {k['confirmed']:,.0f} confirmed, {k['last7']:,.0f} in the last 7 days"
                 + (f" ({k['change_7d']:+.0%} vs the week before)" if k['change_7d'] is not None else "")
                 + f", R ≈ {k['R']} [{k['R_lo']}–{k['R_hi']}] ({k['r_basis']}), {k['clusters']} cluster(s).")
    for c in an["clusters"][:4]:
        z = "no zone" if not c.get("zones") else f"{len(c['zones'])} zone(s)"
        L.append(f"- **{c['label']}**: {c['cases']:.0f} cases, {c['last7']:.0f} this week, {c['trend']}"
                 + (f", doubling {c['doubling_days']} d" if c["doubling_days"] else "") + f", ~{c['affected_km2']} km² affected, {z}.")
    sug = an["suggestions"]
    if sug:
        L.append("")
        L.append("**Đề xuất vùng**" if vi else "**Suggested zones** (accept on the map to test or enforce):")
        for s in sug[:5]:
            L.append(f"- {s['kind']} · {s['cluster']}: {s['area_km2']:,.1f} km², ~{s['people_est']:,} people — {s['why']}")
    b, c, p = pr["baseline"], pr["current"], pr["plan"]
    L.append("")
    L.append(f"**28-day scenario**: baseline {b['cum_cases']:,} cases (range {b['cum_lo']:,}–{b['cum_hi']:,}), current measures {c['cum_cases']:,}, "
             f"plan {p['cum_cases']:,} (averts {p['averted_vs_baseline']:,}). Peak unmet beds: {b['peak_unmet']:,} → {p['peak_unmet']:,}.")
    short = [x for x in p["provinces"] if x["peak_unmet"] > 0][:3]
    if short:
        L.append("- Beds run short in " + ", ".join(f"{x['name']} (peak {x['peak_unmet']:,} without a bed)" for x in short) + ": run the optimizer for beds/ambulances.")
    if an["safe_provinces"]:
        L.append(f"- {len(an['safe_provinces'])} province(s) have no reports and are far from every cluster (safe): "
                 + ", ".join(s["name"] for s in an["safe_provinces"][:6]) + ("…" if len(an["safe_provinces"]) > 6 else "") + ".")
    for w in an["warnings"]:
        L.append(f"- _{w}_")
    return "\n".join(L)
