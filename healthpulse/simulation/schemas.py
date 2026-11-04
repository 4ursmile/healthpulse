"""Scenario specification (spec §5.6) with unit-checked, bounded fields."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Window(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_week: str | None = Field(None, description="First target week (Sunday, ISO date). Default: first forecast target week")
    weeks: int = Field(4, ge=1, le=4)


class Stressors(BaseModel):
    model_config = ConfigDict(extra="forbid")
    holiday_surge_pct: float = Field(0.0, ge=-50, le=200, description="Scenario assumption: % change of reported cases (holiday contact/travel)")
    weather_stress_pct: float = Field(0.0, ge=-50, le=200, description="Scenario assumption: % change of reported cases (weather)")
    weather_use: Literal["none", "scenario_override"] = "none"
    trial_correlation: float = Field(0.5, ge=0.0, le=1.0, description="Share of forecast uncertainty shared by all regions in a trial")


class Resources(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mobile_teams_available: int = Field(0, ge=0, le=5000)
    visits_per_team_per_week: float = Field(35.0, gt=0, le=2000)
    cost_per_team_week_brl: float = Field(2500.0, ge=0)
    cost_per_km_brl: float = Field(1.2, ge=0)
    max_travel_minutes: float = Field(90.0, gt=0, le=2000)
    speed_kmh: float = Field(50.0, gt=1, le=200)
    total_budget_brl: float | None = Field(None, ge=0)
    team_bases: list[str] = Field(default_factory=list, description="IBGE codes where mobile teams start; default: largest region in scope")
    teams_removed: int = Field(0, ge=0, le=5000, description="Resource-shortage stressor: existing teams withdrawn")
    effective_visit_capacity_source: Literal["planner_input", "verified_operational"] = "planner_input"
    max_teams_per_region: int = Field(10, ge=1, le=200)


class Capacity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    basis: Literal["per_10k_residents", "explicit"] = "per_10k_residents"
    visits_per_10k_residents_per_week: float = Field(0.1, ge=0, le=10000)
    explicit_visits_per_week: dict[str, float] = Field(default_factory=dict)
    existing_teams: dict[str, int] = Field(default_factory=dict)
    site_closure_share: dict[str, float] = Field(default_factory=dict, description="0..1 of capacity removed per region")

    @field_validator("explicit_visits_per_week")
    @classmethod
    def _nonneg(cls, v):
        if any(x < 0 for x in v.values()):
            raise ValueError("capacity cannot be negative")
        return v

    @field_validator("site_closure_share")
    @classmethod
    def _share(cls, v):
        if any(not 0 <= x <= 1 for x in v.values()):
            raise ValueError("closure share must be between 0 and 1")
        return v


class Assumptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visit_rate: float = Field(0.30, ge=0, le=1, description="Health-service visits per reported case (scenario assumption)")
    visit_rate_basis: Literal["scenario_assumption", "published_estimate"] = "scenario_assumption"
    equity_alpha: float = Field(0.5, ge=0, le=5, description="Weight on access gap in the objective")
    equity_weight_basis: str = "facilities_per_capita_gap"
    equity_floor_regions: int = Field(0, ge=0, le=500, description="The N regions with the largest access gap get at least one team if they have expected unmet demand")
    travel_cost_weight: float = Field(0.01, ge=0, le=10, description="λ: unmet visits traded per BRL of travel cost")


class SimulationCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draws: int = Field(200, ge=10, le=100_000)
    random_seed: int = Field(42, ge=0, le=2**31 - 1)
    solver: Literal["auto", "milp", "greedy"] = "auto"


class Transmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    r0_low: float = Field(1.1, gt=0, le=10)
    r0_high: float = Field(1.6, gt=0, le=10)
    latent_days: float = Field(4.0, gt=0, le=30)
    infectious_days: float = Field(5.0, gt=0, le=30)
    reporting_fraction: float = Field(0.1, gt=0, le=1)
    coupling: float = Field(0.02, ge=0, le=1, description="Gravity-model mixing strength between regions (assumption, no OD data)")
    contact_reduction_pct: float = Field(0.0, ge=0, le=90)
    intervention_start_week: int = Field(1, ge=1, le=26)
    weeks: int = Field(8, ge=1, le=26)
    immune_share: float = Field(0.0, ge=0, le=0.95)


class ScenarioSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=3, max_length=160)
    mode: Literal["operational", "transmission_research"] = "operational"
    forecast_id: str | None = None
    disease_id: str = "dengue"
    region_ids: list[str] = Field(default_factory=list)
    window: Window = Field(default_factory=Window)
    stressors: Stressors = Field(default_factory=Stressors)
    resources: Resources = Field(default_factory=Resources)
    capacity: Capacity = Field(default_factory=Capacity)
    assumptions: Assumptions = Field(default_factory=Assumptions)
    simulation: SimulationCfg = Field(default_factory=SimulationCfg)
    transmission: Transmission | None = None
    required_approval: Literal["human_planner"] = "human_planner"
    notes: str | None = Field(None, max_length=4000)

    @field_validator("name", "notes")
    @classmethod
    def _no_outcome_claims(cls, v):
        if v and any(w in v.lower() for w in ("lives saved", "deaths prevented", "infections prevented", "cases prevented")):
            raise ValueError("scenario text cannot claim prevented outcomes; operational scenarios estimate service shortfall only")
        return v
