"""Reconstructed HELIOS response schemas (``helios/schemas/outputs.py``).

RECONSTRUCTED from Appendix A of the build brief on 2026-08-24 — the snapshot download
was blocked by the sandbox network policy. Verify against the real file in the morning
(see PROVENANCE.md). Response models use ``extra="allow"``: the live server may return
fields Appendix A did not list, and that must not fail validation.
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
    measurement_type: str = "soil_water_tension"
    unit: str = "centibar"


class PersistenceBaseline(BaseModel):
    model_config = ConfigDict(extra="allow")

    moisture_24h: float | None = None
    moisture_48h: float | None = None
    moisture_72h: float | None = None
    method: str = "driest_zone_carry_forward"
    measurement_type: str = "soil_water_tension"
    unit: str = "centibar"


class RecommendationExplanation(BaseModel):
    model_config = ConfigDict(extra="allow")

    stress_probability: float | None = None
    drivers: list[str] = Field(default_factory=list)
    driving_zone: str | None = None
    zone_moisture_summary: dict[str, float] = Field(default_factory=dict)
    high_variability_flag: bool = False
    operator_review_required: bool = False
    measurement_type: str = "soil_water_tension"
    unit: str = "centibar"
    dry_threshold: float | None = None
    wet_threshold: float | None = None
    interpretation: str | None = None
    review_gate_reason: str | None = None
    configuration_blocker: str | None = None
    drip_physics_status: str | None = None


class PredictionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    decision: Literal["water", "wait"]
    recommended_amount_in: float | None = None
    uncapped_need_in: float | None = None
    caps: Any = None
    binding_constraint: str | None = None
    final_amount_in: float | None = None
    timing_window: str | None = None
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    confidence_caveat: str | None = None
    et_source: str | None = None
    et_is_fallback: bool | None = None
    reference_et: float | None = None
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
