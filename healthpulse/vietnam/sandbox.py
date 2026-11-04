"""Outbreak sandbox for Viet Nam's 34 provinces: pick a pathogen, set weather, mobility, workforce and vaccination, seed
an outbreak on the map, then step day by day and intervene while it runs (lockdowns, border closures, vaccination
campaigns, rapid-response teams, staff redeployment, vector control, weather events, travel corridors).

Engine: a stochastic metapopulation S-E-I-R-V model with a hospital layer, run as a small ensemble of trials so every
number carries a P10-P90 band. It is a SCENARIO tool. What comes from data and what is assumed:

  data   population, province centroids and distances (COD-AB / WorldPop), beds and staff (WHO national densities x
         population - an ESTIMATE), monthly climate normals and the latest observed anomalies (NASA POWER), dengue
         seasonality and climate response (the fitted Poisson GLM in `model.py`), dengue starting level (model estimate).
  assumed pathogen parameters (typical literature ranges, all editable), the climate response of non-dengue
         pathogens, vaccine efficacy and the effect of each intervention.

Determinism: a session is (spec, seed, action log). Replaying the log on a fresh session reproduces it exactly, which
is how rewind works.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
import time
from collections import OrderedDict
from datetime import date, timedelta
from pathlib import Path
from functools import lru_cache
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, field_validator

from ..core.util import new_id
from . import model
from .data import gold_dir
from .crosswalk import BASES, MERGER
from .sim import _haversine, seasonal_shape

PIDS = list(MERGER)
P = len(PIDS)

# ----------------------------------------------------------------------------------------------------------------------
# Pathogen presets. Values are typical literature ranges used as planning defaults, not estimates for Viet Nam.
#   r0           basic reproduction number (dengue: scale on the fitted seasonal R(t), 1 = as fitted, effective R)
#   latent/infectious  mean days; p_hosp = hospitalised per infection; cfr_hosp = deaths per admission when treated;
#   untreated    death multiplier for patients who need a bed and do not get one
#   immune       share already immune at the start (prior infection or routine vaccination)
#   climate      route-specific response to weather anomalies (per raw unit; dengue uses the fitted GLM instead)
PATHOGENS: dict[str, dict] = {
    "dengue": dict(name="Dengue", route="vector", r0=1.0, r_effective=True, latent=5.5, infectious=5.0, report=0.15,
                   p_hosp=0.045, los=4.0, cfr_hosp=0.003, untreated=5.0, immune=0.65, vax_inf=0.61, vax_sev=0.84,
                   admit_delay=4, start_from_estimate=True, epidemic_scale=1.0, shock_sd=0.08,
                   note="Aedes-borne. Seasonality and climate response come from the fitted provincial model."),
    "influenza": dict(name="Seasonal influenza", route="respiratory", r0=1.4, latent=2.0, infectious=3.0, report=0.05,
                      p_hosp=0.004, los=5.0, cfr_hosp=0.05, untreated=2.5, immune=0.35, vax_inf=0.45, vax_sev=0.6,
                      admit_delay=3, climate=dict(ah=-0.03),
                      note="Respiratory. Transmission rises when absolute humidity falls (assumed sensitivity)."),
    "pandemic_flu": dict(name="Novel influenza (pandemic)", route="respiratory", r0=1.9, latent=2.0, infectious=3.5,
                         report=0.08, p_hosp=0.008, los=6.0, cfr_hosp=0.08, untreated=3.0, immune=0.05, vax_inf=0.6,
                         vax_sev=0.75, admit_delay=3, climate=dict(ah=-0.03),
                         note="A new strain: almost no prior immunity and no vaccine at the start."),
    "covid": dict(name="COVID-19 (Delta-like)", route="respiratory", r0=4.0, latent=4.0, infectious=6.0, report=0.3,
                  p_hosp=0.025, los=9.0, cfr_hosp=0.12, untreated=2.5, immune=0.2, vax_inf=0.6, vax_sev=0.9,
                  admit_delay=6, climate=dict(ah=-0.015),
                  note="Respiratory, high R0. Long hospital stays put pressure on staff."),
    "covid_omicron": dict(name="COVID-19 (Omicron-like)", route="respiratory", r0=8.0, latent=3.0, infectious=4.0, report=0.3,
                          p_hosp=0.004, los=5.0, cfr_hosp=0.05, untreated=2.5, immune=0.75, vax_inf=0.35, vax_sev=0.85,
                          admit_delay=4, climate=dict(ah=-0.015),
                          note="Very high R0 but a largely vaccinated population: most infections are mild."),
    "measles": dict(name="Measles", route="contact", r0=15.0, latent=10.0, infectious=8.0, report=0.6, p_hosp=0.15,
                    los=5.0, cfr_hosp=0.01, untreated=4.0, immune=0.92, vax_inf=0.97, vax_sev=0.97, admit_delay=4,
                    climate=dict(t=-0.02),
                    note="Extremely contagious; outbreaks happen where immunity gaps open up."),
    "hfmd": dict(name="Hand, foot & mouth (EV-A71)", route="contact", r0=2.5, latent=4.0, infectious=7.0, report=0.2,
                 p_hosp=0.012, los=4.0, cfr_hosp=0.01, untreated=4.0, immune=0.6, vax_inf=0.9, vax_sev=0.95, admit_delay=3,
                 climate=dict(t=0.04, rh=0.01),
                 note="Mostly young children; warmer, humid weather raises transmission (assumed)."),
    "cholera": dict(name="Cholera", route="water", r0=2.0, latent=1.5, infectious=5.0, report=0.2, p_hosp=0.03,
                    los=3.0, cfr_hosp=0.01, untreated=25.0, immune=0.1, vax_inf=0.65, vax_sev=0.75, admit_delay=1,
                    climate=dict(rain=0.04, t=0.03),
                    note="Waterborne; heavy rain and floods raise transmission. Untreated cases are often fatal."),
    "custom": dict(name="Custom pathogen", route="respiratory", r0=2.5, latent=3.0, infectious=5.0, report=0.2,
                   p_hosp=0.02, los=6.0, cfr_hosp=0.05, untreated=3.0, immune=0.1, vax_inf=0.7, vax_sev=0.85,
                   admit_delay=4, climate=dict(ah=-0.02),
                   note="Start from a respiratory template and set every parameter yourself."),
}
ROUTES = ("vector", "respiratory", "contact", "water")
CALIB_KEYS = ("r0", "latent", "infectious", "report", "p_hosp", "los", "cfr_hosp", "untreated", "immune", "vax_inf", "vax_sev",
              "admit_delay", "epidemic_scale", "shock_sd")

# Literature defaults are kept untouched; `apply_calibration()` overlays the parameters that Viet Nam's own outbreak data
# identified (see vietnam/calibration.py) onto PATHOGENS, and records the provenance in CALIBRATION. Both stay available:
# PathogenSpec(basis="literature") resolves from LITERATURE, and the catalog lists both value sets.
LITERATURE: dict[str, dict] = copy.deepcopy(PATHOGENS)
CALIBRATION: dict[str, dict] = {}


def engine_sha() -> str:
    """Hash of this file: a calibration records it, and an overlay made with a different engine is flagged `stale`."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def read_overlay() -> dict:
    """Overlay from data/gold/vn/calibration.json (written by `hp vn-calibrate`), {} when absent or unreadable. Reads the file
    directly instead of importing `calibration` (which imports this module)."""
    try:
        f = gold_dir() / "calibration.json"
        if not f.exists():
            return {}
        c = json.loads(f.read_text())
        stale = c.get("inputs", {}).get("engine_sha256") != engine_sha()
        return {k: dict(v, stale=stale) for k, v in c.get("overlay", {}).items()}
    except Exception:  # noqa: BLE001 — a missing or unreadable artefact must never stop the sandbox
        return {}


def apply_calibration() -> dict:
    """(Re)load the calibration overlay and apply the parameters marked `applied` onto PATHOGENS. Safe when the file is absent."""
    global CALIBRATION
    for k, v in LITERATURE.items():
        PATHOGENS[k] = copy.deepcopy(v)
    CALIBRATION = read_overlay()
    for key, c in CALIBRATION.items():
        if key in PATHOGENS:
            for prm, info in c.get("params", {}).items():
                if info.get("applied") and prm in PATHOGENS[key]:
                    PATHOGENS[key][prm] = info["value"]
    return CALIBRATION


class PathogenSpec(BaseModel):
    key: str = "dengue"
    name: str | None = None
    route: Literal["vector", "respiratory", "contact", "water"] | None = None
    r0: float | None = Field(None, ge=0.1, le=25)
    latent: float | None = Field(None, ge=0.5, le=30)
    infectious: float | None = Field(None, ge=0.5, le=30)
    report: float | None = Field(None, gt=0, le=1)
    p_hosp: float | None = Field(None, ge=0, le=1)
    los: float | None = Field(None, ge=1, le=40)
    cfr_hosp: float | None = Field(None, ge=0, le=1)
    untreated: float | None = Field(None, ge=1, le=50)
    immune: float | None = Field(None, ge=0, le=0.99)
    vax_inf: float | None = Field(None, ge=0, le=1)
    vax_sev: float | None = Field(None, ge=0, le=1)
    climate_sensitivity: float = Field(1.0, ge=0, le=3)  # scales every climate response
    epidemic_scale: float | None = Field(None, ge=0.05, le=20)  # dengue: multiplies the starting level (1 = model estimate at the WHO-calibrated level)
    shock_sd: float | None = Field(None, ge=0, le=0.3)          # per-trial lognormal transmission shock (spread of the ensemble)
    basis: Literal["calibrated", "literature"] = "calibrated"     # which preset values fill the parameters left empty

    @field_validator("key")
    @classmethod
    def _k(cls, v):
        if v not in PATHOGENS:
            raise ValueError(f"unknown pathogen {v}")
        return v

    def resolved(self) -> dict:
        base = dict((LITERATURE if self.basis == "literature" else PATHOGENS)[self.key])
        for k, v in self.model_dump(exclude={"key", "climate_sensitivity", "basis"}).items():
            if v is not None:
                base[k] = v
        base["key"] = self.key
        base["climate_sensitivity"] = self.climate_sensitivity
        base.setdefault("epidemic_scale", 1.0)
        base.setdefault("shock_sd", 0.08)
        base.setdefault("climate", {})
        if base["route"] != "vector":
            base["r_effective"] = False
            base["start_from_estimate"] = False
        return base


class Corridor(BaseModel):
    from_id: str
    to_id: str
    travellers: float = Field(20000, ge=0, le=2_000_000)  # daily trips each way


class WeatherSpec(BaseModel):
    mode: Literal["normal", "observed", "custom"] = "observed"  # climate normals | repeat latest observed anomalies | normals + shift
    dt: float = Field(0.0, ge=-6, le=6)          # °C added everywhere
    rain_mult: float = Field(1.0, ge=0, le=5)    # x rainfall
    drh: float = Field(0.0, ge=-30, le=30)       # relative humidity points
    replay: bool = False                          # observed mode: replay the anomalies of the real calendar months (for back-tests)


class MobilitySpec(BaseModel):
    share: float = Field(0.04, ge=0, le=0.3)     # share of contacts made outside the home province (gravity model)
    reduction: float = Field(0.0, ge=0, le=1)
    corridors: list[Corridor] = []


class WorkforceSpec(BaseModel):
    mode: Literal["current", "equal", "custom"] = "current"  # current estimate | equal per capita | custom per province
    bed_share: float = Field(0.08, ge=0.005, le=0.8)       # share of beds usable for this outbreak
    nurse_share: float = Field(0.06, ge=0.005, le=0.8)
    patients_per_nurse: float = Field(6, ge=1, le=20)
    surge: float = Field(0.0, ge=0, le=1.5)
    custom: dict[str, dict[str, float]] = {}  # pid -> {"nurses": n, "beds": n, "doctors": n} (absolute)
    team_capacity: int = Field(25, ge=1, le=500)
    team_staff: int = Field(10, ge=1, le=100)
    teams_per_base: int = Field(4, ge=0, le=50)


class VaccineSpec(BaseModel):
    coverage: float = Field(0.0, ge=0, le=1)          # share of the non-immune already vaccinated at the start
    per_province: dict[str, float] = {}


class Seed(BaseModel):
    province_id: str
    infections: int = Field(500, ge=1, le=2_000_000)


class SandboxSpec(BaseModel):
    pathogen: PathogenSpec = PathogenSpec()
    weather: WeatherSpec = WeatherSpec()
    mobility: MobilitySpec = MobilitySpec()
    workforce: WorkforceSpec = WorkforceSpec()
    vaccine: VaccineSpec = VaccineSpec()
    seeds: list[Seed] = []
    start_month: str | None = None
    trials: int = Field(16, ge=4, le=64)
    seed: int = 11
    max_days: int = Field(365, ge=30, le=730)
    autopilot: bool = False  # apply the rule-based advisor's actions every 7 days


ActionType = Literal["seed", "lockdown", "border", "vaccinate", "team", "staff", "surge", "vector_control", "weather",
                     "corridor", "corridor_remove", "autopilot"]


class Action(BaseModel):
    type: ActionType
    province_id: str | None = None     # "ALL" = national where it makes sense
    to_id: str | None = None           # staff move / corridor
    value: float | None = None         # level / count / rate, meaning depends on type (see ACTION_HELP)
    days: int | None = Field(None, ge=1, le=365)
    kind: str | None = None            # weather: heatwave | flood | drought | cold
    why: str | None = Field(None, max_length=400)
    day: int | None = None             # filled by the session when applied
    source: str | None = None          # user | advisor | autopilot

    @field_validator("province_id", "to_id")
    @classmethod
    def _pid(cls, v):
        if v and v != "ALL" and v not in MERGER:
            raise ValueError(f"unknown province {v}")
        return v


ACTION_HELP = {
    "seed": "introduce `value` infections in province_id",
    "lockdown": "cut contacts in province_id (or ALL) by `value` (0-0.9); 0 lifts it",
    "border": "close province_id's travel links by `value` (0-1); 0 reopens",
    "vaccinate": "campaign in province_id (or ALL): `value` doses per 1,000 people per day for `days` days",
    "team": "send `value` rapid-response teams from the nearest base to province_id (adds beds+staff on arrival)",
    "staff": "redeploy `value` nurses from province_id to to_id",
    "surge": "staff surge (overtime, cancelled leave) of `value` (0-1.5) in province_id (or ALL)",
    "vector_control": "dengue only: cut transmission in province_id by `value` (0-0.6) for `days` days",
    "weather": "weather event `kind` (heatwave|flood|drought|cold) in province_id (or ALL) for `days` days",
    "corridor": "add a travel corridor province_id <-> to_id with `value` daily trips",
    "corridor_remove": "remove the corridor province_id <-> to_id",
    "autopilot": "`value` 1 = let the rule-based advisor act every 7 days, 0 = off",
}

WEATHER_EVENTS = {"heatwave": dict(dt=3.0, rain=1.0, rh=-8), "flood": dict(dt=-0.5, rain=3.0, rh=8),
                  "drought": dict(dt=1.5, rain=0.2, rh=-12), "cold": dict(dt=-4.0, rain=0.8, rh=0)}


# ---------------------------------------------------------------------------------------------------------------- data
def growth_to_R(r, sigma: float, gamma: float):
    """Reproduction number that makes this engine's discrete-time chain grow at `r` per day (exact, from the Euler-Lotka
    characteristic equation of the E->I->R daily chain). R is the expected number of infections one infectious person
    causes; the engine's per-day transmission rate is R*(1-exp(-gamma)) (see `beta` in `_advance`)."""
    g = np.exp(np.asarray(r, float))
    es, eg = math.exp(-sigma), math.exp(-gamma)
    R = (g - es) * (g - eg) / ((1 - es) * (1 - eg))
    return np.where(g > max(es, eg), R, 0.0)


def R_to_growth(R: float, sigma: float, gamma: float) -> float:
    """Inverse of `growth_to_R`: daily growth rate of the discrete chain for reproduction number R (R > 0)."""
    es, eg = math.exp(-sigma), math.exp(-gamma)
    # g^2 - (es+eg) g + es*eg - R(1-es)(1-eg) = 0, take the dominant root
    b, c = es + eg, es * eg - R * (1 - es) * (1 - eg)
    return math.log((b + math.sqrt(max(b * b - 4 * c, 0.0))) / 2)


def _abs_humidity(t, rh):
    """g/m³ from °C and %RH (Magnus formula)."""
    return 6.112 * np.exp(17.67 * t / (t + 243.5)) * rh * 2.1674 / (273.15 + t)


@lru_cache(maxsize=1)
def _static() -> dict:
    provs = {p["province_id"]: p for p in model.provinces()}
    N = np.array([provs[p]["population"] or 0 for p in PIDS], float)
    xy = [(provs[p]["lat"], provs[p]["lon"]) for p in PIDS]
    D = np.array([[_haversine(a, b) for b in xy] for a in xy])
    import polars as pl
    w = model.weather_monthly().with_columns(pl.col("month").dt.month().alias("moy"))
    w = w.filter(pl.col("days").is_null() | (pl.col("days") >= 20))
    clim = (w.filter(pl.col("month").dt.year().is_between(1994, 2023)).group_by("province_id", "moy")
            .agg(pl.col("t2m").mean(), pl.col("rain_mm_day").mean(), pl.col("rh").mean()))
    C = np.zeros((3, P, 12))  # t, rain, rh normals
    for r in clim.iter_rows(named=True):
        i, m = PIDS.index(r["province_id"]), r["moy"] - 1
        C[:, i, m] = (r["t2m"], r["rain_mm_day"], r["rh"])
    # latest observed anomaly for each calendar month (the most recent 12 complete months)
    last = w["month"].max()
    recent = w.filter(pl.col("month") > (last - timedelta(days=365)))
    A = np.zeros((3, P, 12))
    for r in recent.iter_rows(named=True):
        i, m = PIDS.index(r["province_id"]), r["moy"] - 1
        if r["t2m"] is not None:
            A[:, i, m] = (r["t2m"] - C[0, i, m], (r["rain_mm_day"] or 0) - C[1, i, m], (r["rh"] or 0) - C[2, i, m])
    anom_ym: dict[tuple[int, int], np.ndarray] = {}
    for r in w.iter_rows(named=True):
        if r["t2m"] is None:
            continue
        i, m = PIDS.index(r["province_id"]), r["moy"] - 1
        a = anom_ym.setdefault((r["month"].year, m), np.zeros((3, P)))
        a[:, i] = (r["t2m"] - C[0, i, m], (r["rain_mm_day"] or 0) - C[1, i, m], (r["rh"] or 0) - C[2, i, m])
    coef = model.model_info()["climate_coefficients"]
    return dict(provs=provs, anom_ym=anom_ym, N=N, D=D, clim=C, anom=A, anom_until=str(last), shape=seasonal_shape(), coef=coef,
                beds=np.array([provs[p]["beds_est"] or 0 for p in PIDS], float),
                nurses=np.array([provs[p]["nurses_est"] or 0 for p in PIDS], float),
                doctors=np.array([provs[p]["doctors_est"] or 0 for p in PIDS], float))


def catalog() -> dict:
    st = _static()
    return {"pathogens": {k: {**v, "literature": {kk: LITERATURE[k][kk] for kk in CALIB_KEYS if kk in LITERATURE[k]},
                              "calibrated": {kk: v[kk] for kk in CALIB_KEYS if kk in v},
                              "calibration": CALIBRATION.get(k)} for k, v in PATHOGENS.items()},
            "actions": ACTION_HELP, "weather_events": WEATHER_EVENTS, "bases": BASES,
            "weather_observed_until": st["anom_until"],
            "defaults": SandboxSpec().model_dump()}


def reset_cache() -> None:
    _static.cache_clear()


# ------------------------------------------------------------------------------------------------------------- session
class Team:
    __slots__ = ("tid", "base", "at", "frm", "depart", "arrive", "deployed")

    def __init__(self, tid, base):
        self.tid, self.base = tid, base
        self.at = base["province_id"]
        self.frm = None
        self.depart = self.arrive = None
        self.deployed = False

    def pos(self, day: float, provs) -> dict:
        if self.frm and day < self.arrive:
            a, b = provs[self.frm], provs[self.at]
            f = max(0.0, min(1.0, (day - self.depart) / max(0.5, self.arrive - self.depart)))
            return {"id": self.tid, "base": self.base["base_id"], "state": "travel", "from": self.frm, "to": self.at,
                    "lon": a["lon"] + (b["lon"] - a["lon"]) * f, "lat": a["lat"] + (b["lat"] - a["lat"]) * f}
        p = provs[self.at]
        return {"id": self.tid, "base": self.base["base_id"], "state": "deployed" if self.deployed else "idle",
                "at": self.at, "lon": p["lon"], "lat": p["lat"]}


class Session:
    def __init__(self, spec: SandboxSpec, sid: str | None = None):
        self.sid = sid or new_id("vnsbx")
        self.spec = spec
        self.created = self.touched = time.time()
        st = _static()
        self.st = st
        self.provs = st["provs"]
        self.path = spec.pathogen.resolved()
        pa = self.path
        K = self.K = spec.trials
        self.rng = np.random.default_rng(spec.seed)
        self.N = st["N"].copy()
        self.day = 0
        mstate = model.state(spec.start_month)
        self.start = date.fromisoformat(mstate["month"])
        self.sigma, self.gamma = 1 / pa["latent"], 1 / pa["infectious"]
        # --- workforce
        wf = spec.workforce
        beds, nurses, doctors = st["beds"].copy(), st["nurses"].copy(), st["doctors"].copy()
        if wf.mode == "equal":
            share = self.N / self.N.sum()
            beds, nurses, doctors = beds.sum() * share, nurses.sum() * share, doctors.sum() * share
        elif wf.mode == "custom":
            for pid, v in wf.custom.items():
                if pid in MERGER:
                    i = PIDS.index(pid)
                    beds[i] = v.get("beds", beds[i])
                    nurses[i] = v.get("nurses", nurses[i])
                    doctors[i] = v.get("doctors", doctors[i])
        self.beds, self.nurses, self.doctors = beds, nurses, doctors
        self.surge = np.full(P, wf.surge)
        # --- immunity and vaccination at the start
        imm = pa["immune"]
        cov = np.array([spec.vaccine.per_province.get(p, spec.vaccine.coverage) for p in PIDS])
        nonimm = self.N * (1 - imm)
        V0 = nonimm * cov
        S0 = nonimm - V0
        E0 = np.zeros(P)
        I0 = np.zeros(P)
        H0 = np.zeros(P)
        if pa.get("start_from_estimate"):
            est = {r["province_id"]: r["estimate"] for r in mstate["provinces"]}
            mc = np.array([est.get(p, 0) or 0 for p in PIDS], float)
            scale = pa.get("epidemic_scale", 1.0)
            # the first month's estimate is spread over its 30 days in proportion to the model's own level curve, and the E/I
            # pools start on the growth eigenvector of the daily chain (E = flow/p_sigma, I = flow/(g - exp(-gamma))) so the
            # first weeks carry no start-up transient
            self.weather_ev = []
            lvl = np.array([self._dengue_level(d) for d in range(30)])
            g0 = np.exp(np.log(self._dengue_level(15) / self._dengue_level(-15)) / 30)
            flow = mc * scale / pa["report"] * lvl[0] / np.maximum(lvl.sum(0), 1e-9)   # new E->I on day 0
            E0 = flow / (1 - math.exp(-self.sigma))
            I0 = flow / np.maximum(g0 - math.exp(-self.gamma), 0.02)
            H0 = mc * scale / 30 * (pa["p_hosp"] / pa["report"]) * pa["los"]
        S0 = np.maximum(S0 - I0 - E0, 0)
        self.s0_share = (S0 + V0 * (1 - pa["vax_inf"])) / np.maximum(self.N, 1)
        tile = lambda a: np.tile(a, (K, 1))  # noqa: E731
        self.S = tile(S0).round()
        self.V = tile(V0).round()
        self.E = self.rng.poisson(tile(E0)).astype(float)
        self.I = self.rng.poisson(tile(I0)).astype(float)
        self.R = tile(self.N - S0 - V0 - E0 - I0).clip(0).round()
        self.H = tile(H0).round()
        self.Dd = np.zeros((K, P))
        self.cum_inf = np.zeros((K, P))
        self.cum_rep = np.zeros((K, P))
        self.cum_unmet = np.zeros((K, P))
        self.doses = np.zeros(P)
        self.delay = max(1, int(round(pa["admit_delay"])))
        self.queue = np.zeros((self.delay, K, P))  # severe infections waiting to be admitted
        self.shock = self.rng.lognormal(0, pa.get("shock_sd", 0.08), (K, 1))
        # --- interventions
        self.contact = np.ones(P)
        self.border = np.zeros(P)
        self.vector = []          # (pid_idx, effect, until)
        self.weather_ev = []      # (idx array, kind, until)
        self.campaigns = []       # (idx array, rate_per_1k, until)
        self.corridors = [c.model_dump() for c in spec.mobility.corridors]
        self.autopilot = spec.autopilot
        self.teams = [Team(i, BASES[i % len(BASES)]) for i in range(wf.teams_per_base * len(BASES))]
        self.lock_person_days = 0.0
        self.actions: list[dict] = []
        self.events: list[dict] = []
        self.reached = (self.E + self.I).mean(0) >= 50  # endemic start (dengue): already present
        self.over_prev = np.zeros(P, bool)
        self.R_prev_above = None
        self.peak_h = 0.0
        self._rebuild_mixing()
        for s in spec.seeds:
            self._seed(PIDS.index(s.province_id), s.infections)
        self.history: list[dict] = [self._frame()]

    # ----------------------------------------------------------------------------------------- mixing and weather
    def _rebuild_mixing(self):
        D, N = self.st["D"], self.N
        m = self.spec.mobility.share * (1 - self.spec.mobility.reduction)
        G = np.where(D > 0, N[None, :] / np.maximum(D, 30) ** 2, 0)
        G = G / G.sum(1, keepdims=True) * m
        for c in self.corridors:
            a, b = PIDS.index(c["from_id"]), PIDS.index(c["to_id"])
            # a trip lasts ~1 day: daily trips / population = share of contact time spent in the other province
            G[a, b] += c["travellers"] / max(N[a], 1)
            G[b, a] += c["travellers"] / max(N[b], 1)
        open_ = 1 - self.border
        G = G * open_[:, None] * open_[None, :]
        off = np.minimum(G.sum(1), 0.6)
        G = G / np.maximum(G.sum(1, keepdims=True), 1e-12) * off[:, None]
        self.M = G + np.diag(1 - off)

    def _weather(self, day: float) -> np.ndarray:
        """(3, P) t, rain, rh on `day` (daily interpolation of monthly values)."""
        st, w = self.st, self.spec.weather
        d = self.start + timedelta(days=int(day))
        mf = d.month - 1 + (d.day - 1) / 30.44 - 0.5
        m0 = int(math.floor(mf)) % 12
        fr = mf - math.floor(mf)
        m1 = (m0 + 1) % 12
        out = st["clim"][:, :, m0] * (1 - fr) + st["clim"][:, :, m1] * fr
        if w.mode == "observed" and w.replay:
            f0 = math.floor(mf)
            ya, ma = divmod(d.year * 12 + f0, 12)
            yb, mb = divmod(d.year * 12 + f0 + 1, 12)
            zero = np.zeros((3, P))
            out = out + st["anom_ym"].get((ya, ma), zero) * (1 - fr) + st["anom_ym"].get((yb, mb), zero) * fr
        elif w.mode == "observed":
            out = out + st["anom"][:, :, m0] * (1 - fr) + st["anom"][:, :, m1] * fr
        elif w.mode == "custom":
            out = out.copy()
            out[0] += w.dt
            out[1] *= w.rain_mult
            out[2] = np.clip(out[2] + w.drh, 5, 100)
        for idx, kind, until in self.weather_ev:
            if day < until:
                e = WEATHER_EVENTS[kind]
                out = out.copy()
                out[0, idx] += e["dt"]
                out[1, idx] *= e["rain"]
                out[2, idx] = np.clip(out[2, idx] + e["rh"], 5, 100)
        return out

    def _climate_mult(self, day: float) -> np.ndarray:
        pa, st = self.path, self.st
        k = pa["climate_sensitivity"]
        clim = st["clim"]
        if pa["route"] == "vector":
            # expected case level = fitted seasonal curve x fitted GLM response to the lag-1 climate anomaly.
            # The level's growth rate r = ln(level(t+15) / level(t-15)) / 30 per day maps to R through the engine's own
            # discrete-time latent/infectious chain (growth_to_R), so that without depletion cases follow the fitted level.
            r = np.log(self._dengue_level(day + 15) / self._dengue_level(day - 15)) / 30
            return np.clip(growth_to_R(r, self.sigma, self.gamma), 0.3, 3.0)
        w = self._weather(day)
        mean = clim.mean(2)
        cf = pa.get("climate", {})
        lin = (cf.get("t", 0) * (w[0] - mean[0]) + cf.get("rain", 0) * (w[1] - mean[1]) + cf.get("rh", 0) * (w[2] - mean[2])
               + cf.get("ah", 0) * (_abs_humidity(w[0], w[2]) - _abs_humidity(mean[0], mean[2])))
        return np.exp(np.clip(k * lin, -0.7, 0.7))

    def _dengue_level(self, day: float) -> np.ndarray:
        st, k = self.st, self.path["climate_sensitivity"]
        d = self.start + timedelta(days=int(day))
        mf = d.month - 1 + (d.day - 1) / 30.44 - 0.5
        m0 = int(math.floor(mf)) % 12
        fr = mf - math.floor(mf)
        season = st["shape"][:, m0] * (1 - fr) + st["shape"][:, (m0 + 1) % 12] * fr
        w = self._weather(day - 30)
        d30 = self.start + timedelta(days=int(day) - 30)
        nm = st["clim"][:, :, d30.month - 1]
        c = st["coef"]
        ta, ra, ha = w[0] - nm[0], w[1] - nm[1], w[2] - nm[2]
        lin = (c["temp anomaly (lag 1)"] * ta + (c["rain anomaly (lag 1)"] + c["rain anomaly (lag 2)"]) * ra
               + c["humidity anomaly (lag 1)"] * ha
               + c["heat above 27 °C² (lag 1)"] * (np.maximum(w[0] - 27, 0) ** 2 - np.maximum(nm[0] - 27, 0) ** 2)
               + c["cold below 20 °C (lag 1)"] * (np.maximum(20 - w[0], 0) - np.maximum(20 - nm[0], 0)))
        return season * np.exp(np.clip(k * lin, -1.5, 1.5))

    # -------------------------------------------------------------------------------------------------- capacity
    def capacity(self) -> tuple[np.ndarray, np.ndarray]:
        wf = self.spec.workforce
        cb = self.beds * wf.bed_share
        cs = self.nurses * wf.nurse_share / 3 * wf.patients_per_nurse
        on = np.zeros(P)
        for t in self.teams:
            if t.deployed and not (t.frm and self.day < t.arrive):
                on[PIDS.index(t.at)] += 1
        cap = np.minimum(cb, cs) * (1 + self.surge) + on * wf.team_capacity
        return cap, np.where(cb <= cs, 0, 1)  # 0 = beds bind, 1 = staff bind

    # -------------------------------------------------------------------------------------------------- actions
    def _ev(self, kind, text, pid=None, **kw):
        if len(self.events) < 2000:
            self.events.append({"day": self.day, "kind": kind, "text": text, "province_id": pid, **kw})

    def _seed(self, i, n):
        self.I[:, i] += n
        self.reached[i] = True

    def _idx(self, pid):
        return np.arange(P) if pid in (None, "ALL") else np.array([PIDS.index(pid)])

    def _name(self, pid):
        return "all provinces" if pid in (None, "ALL") else self.provs[pid]["name"]

    def apply(self, a: Action, source: str = "user") -> dict:
        a = a.model_copy(update={"day": self.day, "source": a.source or source})
        t, pid, v = a.type, a.province_id, a.value
        need_pid = t not in ("autopilot",)
        if need_pid and not pid:
            raise ValueError(f"{t} needs province_id")
        idx = self._idx(pid) if need_pid else None
        nm = self._name(pid) if need_pid else ""
        if t == "seed":
            if pid == "ALL":
                raise ValueError("seed needs one province")
            n = int(max(1, min(2_000_000, v or 500)))
            self._seed(idx[0], n)
            self._ev("seed", f"{n:,} infections introduced in {nm}", pid)
        elif t == "lockdown":
            lv = float(np.clip(v if v is not None else 0.5, 0, 0.9))
            self.contact[idx] = 1 - lv
            self._ev("lockdown", f"Distancing / lockdown in {nm}: contacts −{lv:.0%}" if lv else f"Restrictions lifted in {nm}", pid)
        elif t == "border":
            lv = float(np.clip(v if v is not None else 0.8, 0, 1))
            self.border[idx] = lv
            self._rebuild_mixing()
            self._ev("border", f"Travel links of {nm} closed {lv:.0%}" if lv else f"Travel to/from {nm} reopened", pid)
        elif t == "vaccinate":
            rate = float(np.clip(v if v is not None else 5, 0.1, 50))
            days = a.days or 30
            self.campaigns.append((idx, rate, self.day + days))
            self._ev("vaccinate", f"Vaccination campaign in {nm}: {rate:g} doses/1,000/day for {days} days", pid)
        elif t == "team":
            if pid == "ALL":
                raise ValueError("team needs one province")
            n = int(max(1, min(20, v or 1)))
            sent = 0
            for _ in range(n):
                idle = [tm for tm in self.teams if not tm.deployed]
                if not idle:
                    break
                tm = min(idle, key=lambda x: self.st["D"][PIDS.index(x.at), idx[0]])
                d = self.st["D"][PIDS.index(tm.at), idx[0]] * 1.3
                hours = 6 + d / 45
                tm.frm, tm.depart, tm.arrive = tm.at, self.day, self.day + max(1, math.ceil(hours / 24))
                tm.at, tm.deployed = pid, True
                sent += 1
                self._ev("dispatch", f"Team {tm.tid + 1} dispatched {self.provs[tm.frm]['name']} → {nm} (≈{hours:.0f} h)", pid, team=tm.tid)
            if not sent:
                raise ValueError("no idle teams left — recall one first (team with value 0 recalls all from that province)")
        elif t == "staff":
            if pid == "ALL" or not a.to_id or a.to_id == "ALL":
                raise ValueError("staff needs province_id and to_id")
            i, j = idx[0], PIDS.index(a.to_id)
            n = float(min(max(0, v or 0), self.nurses[i] * 0.5))
            self.nurses[i] -= n
            self.nurses[j] += n
            self.doctors[j] += n * 0.3
            self.doctors[i] = max(0, self.doctors[i] - n * 0.3)
            self._ev("staff", f"{n:,.0f} nurses redeployed {nm} → {self._name(a.to_id)}", a.to_id)
        elif t == "surge":
            lv = float(np.clip(v if v is not None else 0.3, 0, 1.5))
            self.surge[idx] = lv
            self._ev("surge", f"Staff surge +{lv:.0%} in {nm}", pid)
        elif t == "vector_control":
            if self.path["route"] != "vector":
                raise ValueError("vector control only applies to vector-borne pathogens")
            lv = float(np.clip(v if v is not None else 0.25, 0, 0.6))
            days = a.days or 30
            for i in idx:
                self.vector.append((i, lv, self.day + days))
            self._ev("vector", f"Vector control in {nm}: transmission −{lv:.0%} for {days} days", pid)
        elif t == "weather":
            kind = a.kind or "heatwave"
            if kind not in WEATHER_EVENTS:
                raise ValueError(f"unknown weather event {kind}")
            days = a.days or 14
            self.weather_ev.append((idx, kind, self.day + days))
            self._ev("weather", f"{kind.capitalize()} over {nm} for {days} days", pid)
        elif t == "corridor":
            if pid == "ALL" or not a.to_id or a.to_id in ("ALL", pid):
                raise ValueError("corridor needs two different provinces")
            self.corridors = [c for c in self.corridors if {c["from_id"], c["to_id"]} != {pid, a.to_id}]
            self.corridors.append({"from_id": pid, "to_id": a.to_id, "travellers": float(max(0, v or 20000))})
            self._rebuild_mixing()
            self._ev("corridor", f"Travel corridor {nm} ↔ {self._name(a.to_id)}: {v or 20000:,.0f} trips/day", pid)
        elif t == "corridor_remove":
            self.corridors = [c for c in self.corridors if {c["from_id"], c["to_id"]} != {pid, a.to_id}]
            self._rebuild_mixing()
            self._ev("corridor", f"Corridor {nm} ↔ {self._name(a.to_id)} removed", pid)
        elif t == "autopilot":
            self.autopilot = bool(v)
            self._ev("autopilot", "Autopilot on: the rule-based advisor acts every 7 days" if v else "Autopilot off")
        rec = a.model_dump(exclude_none=True)
        self.actions.append(rec)
        return rec

    def recall(self, pid: str):
        for tm in self.teams:
            if tm.deployed and tm.at == pid:
                tm.frm, tm.depart = pid, self.day
                tm.at = tm.base["province_id"]
                d = self.st["D"][PIDS.index(pid), PIDS.index(tm.at)] * 1.3
                tm.arrive = self.day + max(1, math.ceil((6 + d / 45) / 24))
                tm.deployed = False

    # ---------------------------------------------------------------------------------------------------- step
    def step(self, days: int = 1, actions: list[Action] | None = None, source: str = "user") -> tuple[list[dict], list[dict], list[dict]]:
        self.touched = time.time()
        applied, errors = [], []
        for a in actions or []:
            try:
                if a.type == "team" and (a.value or 0) == 0 and a.province_id:
                    self.recall(a.province_id)
                    rec = a.model_dump(exclude_none=True) | {"day": self.day, "source": a.source or source}
                    self.actions.append(rec)
                    self._ev("return", f"Teams recalled from {self._name(a.province_id)}", a.province_id)
                    applied.append(rec)
                else:
                    applied.append(self.apply(a, source))
            except ValueError as e:
                errors.append({"action": a.model_dump(exclude_none=True), "error": str(e)})
        frames = []
        for _ in range(max(0, min(days, self.spec.max_days - self.day))):
            if self.autopilot and self.day % 7 == 0 and self.day > 0:
                from .advisor import rule_actions
                for a in rule_actions(self)[:4]:
                    try:
                        self.apply(a, "autopilot")
                    except ValueError:
                        pass
            self._advance()
            frames.append(self._frame())
        if days == 0:  # actions only: refresh the current frame so the map shows the change at once
            self.history[-1] = self._frame()
            return [self.history[-1]], applied, errors
        self.history.extend(frames)
        return frames, applied, errors

    def _advance(self):
        rng, pa, K = self.rng, self.path, self.K
        t = self.day
        N = np.maximum(self.N, 1)
        # vaccination campaigns (doses spread over the unvaccinated living population; only susceptibles gain protection)
        self.campaigns = [c for c in self.campaigns if t < c[2]]
        if self.campaigns:
            rate = np.zeros(P)
            for idx, r, _ in self.campaigns:
                rate[idx] += r
            unvax = np.maximum(self.S + self.E + self.I + self.R, 1)
            doses = np.minimum(rate * self.N / 1000, unvax.mean(0))
            p = np.clip(doses[None, :] / unvax, 0, 1)
            mv = rng.binomial(self.S.astype(np.int64), p).astype(float)
            self.S -= mv
            self.V += mv
            self.doses += doses
        # transmission
        R = pa["r0"] * self._climate_mult(t) * self.contact
        vec = np.ones(P)
        self.vector = [v for v in self.vector if t < v[2]]
        for i, e, _ in self.vector:
            vec[i] *= 1 - e
        R = R * vec
        # per-day transmission rate: a person stays in I for 1/(1-exp(-gamma)) counted days, so R = beta / (1-exp(-gamma))
        beta = R * (1 - math.exp(-self.gamma)) / (np.maximum(self.s0_share, 1e-6) if pa.get("r_effective") else 1)
        prev = self.I / N
        lam = beta[None, :] * self.shock * (prev @ self.M.T)
        pS = 1 - np.exp(-lam)
        pV = 1 - np.exp(-lam * (1 - pa["vax_inf"]))
        inS = rng.binomial(self.S.astype(np.int64), pS).astype(float)
        inV = rng.binomial(self.V.astype(np.int64), pV).astype(float)
        newI = rng.binomial(self.E.astype(np.int64), 1 - math.exp(-self.sigma)).astype(float)
        newR = rng.binomial(self.I.astype(np.int64), 1 - math.exp(-self.gamma)).astype(float)
        self.S -= inS
        self.V -= inV
        self.E += inS + inV - newI
        self.I += newI - newR
        self.R += newR
        new_inf = inS + inV
        self.cum_inf += new_inf
        rep = rng.binomial(newI.astype(np.int64), pa["report"]).astype(float)
        self.cum_rep += rep
        sev = (rng.binomial(inS.astype(np.int64), pa["p_hosp"]) + rng.binomial(inV.astype(np.int64), pa["p_hosp"] * (1 - pa["vax_sev"]))).astype(float)
        adm = self.queue[t % self.delay].copy()
        self.queue[t % self.delay] = sev
        self.H += adm
        cap, _ = self.capacity()
        un = np.maximum(self.H - cap[None, :], 0)
        treated = self.H - un
        h = 1 / pa["los"]
        pd_t = min(1.0, pa["cfr_hosp"] * h)
        pd_u = min(1.0, pa["cfr_hosp"] * pa["untreated"] * h)
        dead = (rng.binomial(treated.astype(np.int64), pd_t) + rng.binomial(un.astype(np.int64), pd_u)).astype(float)
        dis = rng.binomial(np.maximum(self.H - dead, 0).astype(np.int64), 1 - math.exp(-h)).astype(float)
        self.H = np.maximum(self.H - dead - dis, 0)
        self.Dd += dead
        self.N = np.maximum(self.N - dead.mean(0), 1)
        self.cum_unmet += un
        self.lock_person_days += float(((1 - self.contact) * self.N).sum())
        self._new = dict(inf=new_inf, rep=rep, adm=adm, un=un, cap=cap, R=R, dead=dead)
        self.day += 1
        self._events(cap)

    def _events(self, cap):
        n = self._new
        inf_m = self.cum_inf.mean(0)
        for i in np.where((inf_m >= 50) & ~self.reached)[0]:
            self.reached[i] = True
            self._ev("spread", f"Outbreak reaches {self.provs[PIDS[i]]['name']}", PIDS[i])
        occ = np.median(self.H, 0)
        over = np.median(n["un"], 0) > 0
        for i in np.where(over & ~self.over_prev)[0]:
            self._ev("overload", f"{self.provs[PIDS[i]]['name']} hospitals over capacity ({occ[i]:,.0f} patients, {cap[i]:,.0f} slots)", PIDS[i])
        for i in np.where(~over & self.over_prev)[0]:
            self._ev("recover", f"{self.provs[PIDS[i]]['name']} back under capacity", PIDS[i])
        self.over_prev = over
        rn = self._rt_national()
        above = rn >= 1
        if self.R_prev_above is not None and above != self.R_prev_above:
            self._ev("rt", f"National R crosses {'above' if above else 'below'} 1 (R ≈ {rn:.2f})")
        self.R_prev_above = above
        for tm in self.teams:
            if tm.frm and self.day == tm.arrive:
                self._ev("arrive" if tm.deployed else "return",
                         f"Team {tm.tid + 1} {'on site in' if tm.deployed else 'back at base,'} {self.provs[tm.at]['name']}", tm.at, team=tm.tid)
        d = float(np.median(self.Dd.sum(1)))
        for m in (10, 100, 1000, 10000, 100000):
            if d >= m and not any(e["kind"] == "deaths" and e.get("m") == m for e in self.events):
                self._ev("deaths", f"Deaths pass {m:,} (median of trials)", m=m)

    def _rt(self) -> np.ndarray:
        pa = self.path
        n = getattr(self, "_new", None)
        R = n["R"] if n else pa["r0"] * self._climate_mult(self.day) * self.contact
        if pa.get("r_effective"):
            R = R / np.maximum(self.s0_share, 1e-6)
        susc = (self.S.mean(0) + self.V.mean(0) * (1 - pa["vax_inf"])) / np.maximum(self.N, 1)
        return R * susc

    def _rt_national(self) -> float:
        I = self.I.mean(0)
        r = self._rt()
        return float((r * I).sum() / I.sum()) if I.sum() > 0 else float(np.average(r, weights=self.N))

    # --------------------------------------------------------------------------------------------------- output
    def _frame(self) -> dict:
        cap, bind = self.capacity()
        n = getattr(self, "_new", None)
        med = lambda a: np.median(a, 0)  # noqa: E731
        act = med(self.E + self.I)
        H = med(self.H)
        q = lambda a: [round(float(x), 1) for x in np.percentile(a, [10, 50, 90])]  # noqa: E731
        # importation flows: new infections in p caused by contact with q (ensemble mean), top links
        flows = []
        Sx = self.S.mean(0) + self.V.mean(0) * (1 - self.path["vax_inf"])
        Rn = n["R"] if n else self.path["r0"] * self._climate_mult(self.day) * self.contact
        beta = Rn * (1 - math.exp(-self.gamma)) / (np.maximum(self.s0_share, 1e-6) if self.path.get("r_effective") else 1)
        pr = self.I.mean(0) / np.maximum(self.N, 1)
        F = (Sx * beta)[:, None] * self.M * pr[None, :]
        np.fill_diagonal(F, 0)
        if F.max() > 0:
            top = np.argsort(F, axis=None)[::-1][:28]
            for k in top:
                p_, q_ = divmod(int(k), P)
                if F[p_, q_] < 0.5:
                    break
                flows.append({"from": PIDS[q_], "to": PIDS[p_], "v": round(float(F[p_, q_]), 1)})
        r = lambda a, d=0: [round(float(x), d) for x in a]  # noqa: E731
        prov = {
            "active": r(act), "new_inf": r(med(n["inf"])) if n else [0] * P, "new_rep": r(med(n["rep"])) if n else [0] * P,
            "cum_inf": r(med(self.cum_inf)), "hosp": r(H), "cap": r(cap, 1), "unmet": r(med(n["un"])) if n else [0] * P,
            "deaths": r(med(self.Dd)), "vacc": r(self.V.mean(0) / np.maximum(self.N, 1), 3), "rt": r(self._rt(), 2),
            "susc": r(self.S.mean(0) / np.maximum(self.N, 1), 3), "bind": [int(b) for b in bind],
            "contact": r(self.contact, 2), "border": r(self.border, 2), "nurses": r(self.nurses), "surge": r(self.surge, 2),
        }
        nat = {"active": q((self.E + self.I).sum(1)), "hosp": q(self.H.sum(1)), "deaths": q(self.Dd.sum(1)),
               "cum_inf": q(self.cum_inf.sum(1)), "new_inf": q(n["inf"].sum(1)) if n else [0, 0, 0],
               "unmet": q(n["un"].sum(1)) if n else [0, 0, 0], "cap": round(float(cap.sum())), "rt": round(self._rt_national(), 3),
               "vacc_share": round(float(self.V.mean(0).sum() / self.N.sum()), 4), "doses": round(float(self.doses.sum())),
               "reached": int(self.reached.sum()), "overloaded": int(sum(1 for x in prov["unmet"] if x > 0))}
        return {"day": self.day, "date": str(self.start + timedelta(days=self.day)), "prov": prov, "nat": nat, "flows": flows,
                "teams": [tm.pos(self.day, self.provs) for tm in self.teams],
                "weather": {"t": r(self._weather(self.day)[0], 1), "rain": r(self._weather(self.day)[1], 1)},
                "climate_mult": r(self._climate_mult(self.day), 3)}

    def score(self) -> dict:
        q = lambda a: {k: float(v) for k, v in zip(("p10", "p50", "p90"), np.percentile(a, [10, 50, 90]))}  # noqa: E731
        team_days = sum(1 for e in self.history for t in e["teams"] if t["state"] == "deployed")
        return {"infections": q(self.cum_inf.sum(1)), "reported": q(self.cum_rep.sum(1)), "deaths": q(self.Dd.sum(1)),
                "unmet_bed_days": q(self.cum_unmet.sum(1)), "peak_hosp": max(h["nat"]["hosp"][1] for h in self.history),
                "provinces_reached": int(self.reached.sum()), "doses": float(self.doses.sum()),
                "lockdown_person_days": self.lock_person_days, "team_days": team_days, "actions": len(self.actions)}

    def public(self, frames: list[dict] | None = None) -> dict:
        pa = self.path
        return {"sandbox_id": self.sid, "day": self.day, "max_days": self.spec.max_days, "start_date": str(self.start),
                "semantics": "SCENARIO", "pathogen": pa, "spec": self.spec.model_dump(), "actions": self.actions,
                "events": self.events[-300:], "corridors": self.corridors, "autopilot": self.autopilot,
                "teams_total": len(self.teams), "teams_idle": sum(1 for t in self.teams if not t.deployed),
                "score": self.score(), "frames": frames if frames is not None else self.history,
                "provinces": [{"province_id": p, "name": self.provs[p]["name"], "lat": self.provs[p]["lat"], "lon": self.provs[p]["lon"],
                               "population": self.provs[p]["population"], "region": self.provs[p]["region"],
                               "beds": round(float(self.beds[i])), "doctors": round(float(self.doctors[i]))}
                              for i, p in enumerate(PIDS)],
                "basis": {
                    "population_distances": "WorldPop (HeiGIT/HDX), OCHA COD-AB v02 centroids",
                    "beds_staff": "WHO national densities × provincial population — ESTIMATE" + (
                        "; redistributed equally per capita" if self.spec.workforce.mode == "equal" else
                        "; custom overrides" if self.spec.workforce.mode == "custom" else ""),
                    "weather": {"normal": "NASA POWER 1994–2023 monthly normals",
                                "observed": f"NASA POWER normals + latest observed anomalies (to {self.st['anom_until'][:7]})",
                                "custom": "NASA POWER normals + your shift"}[self.spec.weather.mode],
                    "climate_response": ("fitted provincial Poisson GLM (dengue, 2004–2010) and its seasonal curve" if pa["route"] == "vector"
                                         else "assumed sensitivity for this route (editable)"),
                    "pathogen": "typical literature ranges used as planning defaults (editable) — ASSUMPTION",
                    "interventions": "effects are the levels you set — ASSUMPTION",
                }}


# -------------------------------------------------------------------------------------------------------------- store
_store: OrderedDict[str, Session] = OrderedDict()
_lock = threading.Lock()
TTL = 3 * 3600


def create(spec: SandboxSpec) -> Session:
    s = Session(spec)
    with _lock:
        _store[s.sid] = s
        now = time.time()
        for k in [k for k, v in _store.items() if now - v.touched > TTL]:
            _store.pop(k, None)
        while len(_store) > 40:
            _store.popitem(last=False)
    return s


def get(sid: str) -> Session | None:
    with _lock:
        s = _store.get(sid)
        if s:
            _store.move_to_end(sid)
        return s


def rewind(s: Session, day: int) -> Session:
    """Rebuild the session from its spec and replay the action log up to `day` (deterministic)."""
    day = max(0, min(day, s.day))
    n = Session(s.spec, sid=s.sid)
    acts = [Action(**a) for a in s.actions if a["day"] < day]
    by_day: dict[int, list[Action]] = {}
    for a in acts:
        by_day.setdefault(a.day, []).append(a)
    while n.day < day:
        todo = by_day.pop(n.day, [])
        # autopilot actions are re-generated by the autopilot itself, so only replay explicit ones
        n.step(1, [a for a in todo if a.source != "autopilot"])
    with _lock:
        _store[s.sid] = n
    return n


apply_calibration()
