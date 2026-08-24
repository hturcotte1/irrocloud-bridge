"""Reconstructed HELIOS response schemas (``helios/schemas/outputs.py``).

RECONSTRUCTED from Appendix A of the build brief on 2026-08-24, then VERIFIED the same
morning against the live service's own /openapi.json (Helios 0.5.0) — see PROVENANCE.md
for what was corrected. Response models use ``extra="allow"``, and enum-valued fields
stay plain ``str`` on purpose: the live server may return values these models have not
seen, and that must not fail validation. The DEFAULTS mirror the live schema exactly —
notably, Helios is dual-mode and its default measurement is VWC-fraction, NOT tension,
so nothing may assume centibars without checking ``measurement_type``.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# The pre-registered quarantine sentence Helios uses as review_gate_reason.
QUARANTINE_REVIEW_GATE_REASON = (
    "Tension model is quarantined after a failed pilot accuracy evaluation; "
    "operator review is required before irrigation."
)


class MoistureForecast(BaseModel):
    model_config = ConfigDict(extra="allow")

    moisture_24h: float | None = None
    moisture_48h: float | None = None
    moisture_72h: float | None = None
    # Live enum: "vwc_fraction" | "soil_water_tension", defaulting to VWC —
    # readers must check this before treating the numbers as centibars.
    measurement_type: str = "vwc_fraction"
    unit: str = "fraction"


class PersistenceBaseline(BaseModel):
    model_config = ConfigDict(extra="allow")

    moisture_24h: float | None = None
    moisture_48h: float | None = None
    moisture_72h: float | None = None
    method: str = "driest_zone_carry_forward"
    # Same dual-mode caveat as MoistureForecast: VWC is the live default.
    measurement_type: str = "vwc_fraction"
    unit: str = "fraction"


class RecommendationExplanation(BaseModel):
    model_config = ConfigDict(extra="allow")

    stress_probability: float | None = None
    drivers: list[str] = Field(default_factory=list)
    driving_zone: str | None = None
    zone_moisture_summary: dict[str, float] = Field(default_factory=dict)
    high_variability_flag: bool = False
    operator_review_required: bool = False
    # Same dual-mode caveat as MoistureForecast: VWC is the live default.
    measurement_type: str = "vwc_fraction"
    unit: str = "fraction"
    dry_threshold: float | None = None
    wet_threshold: float | None = None
    interpretation: str | None = None
    review_gate_reason: str | None = None
    configuration_blocker: str | None = None
    drip_physics_status: str | None = None


class ReferenceEvapotranspiration(BaseModel):
    model_config = ConfigDict(extra="allow")

    value: float | None = None
    unit: str = "inch_per_day"


class PredictionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    decision: Literal["water", "wait"]
    recommended_amount_in: float | None = None
    # Non-nullable on the wire, defaulting to 0 (the live schema's shape).
    uncapped_need_in: float = 0.0
    caps: Any = None
    binding_constraint: str | None = None
    final_amount_in: float | None = None
    timing_window: str | None = None
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    confidence_caveat: str | None = None
    et_source: str | None = None
    et_is_fallback: bool | None = None
    # An OBJECT on the wire ({value, unit}), never a bare number.
    reference_et: ReferenceEvapotranspiration | None = None
    explanation: RecommendationExplanation
    predicted_moisture: MoistureForecast
    forecast_source: str = "xgboost"
    model_provenance: Any = None
    forecast_drivers: Any = None
    forecast_uncertainty: Any = None
    persistence_baseline: PersistenceBaseline | None = None
    # physics_baseline is VWC-only; the bridge ignores it.
    physics_baseline: Any = None
    regional_insights: Any = None
    recommendation_adjustment: Any = None
    validation_evidence: Any = None
