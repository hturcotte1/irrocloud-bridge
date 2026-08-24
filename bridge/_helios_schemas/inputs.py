"""Reconstructed HELIOS request schemas (``helios/schemas/inputs.py``).

RECONSTRUCTED from Appendix A of the build brief on 2026-08-24, then VERIFIED the same
morning against the live service's own /openapi.json (Helios 0.5.0) — see PROVENANCE.md
for what was corrected. Request models use ``extra="forbid"``: the bridge authors these
payloads, so an unknown key is a bug in the bridge, not tolerable drift. Where these
models are STRICTER than the server (a Literal narrower than the server enum, a field
required that the server defaults), that is deliberate: every payload the bridge
validates is a payload the server accepts.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SoilMoistureReading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    field_id: str
    sensor_id: str
    measurement_type: Literal["soil_water_tension"] = "soil_water_tension"
    unit: Literal["centibar"] = "centibar"
    value: float
    volumetric_water_content: None = None
    depth_in: float = Field(gt=0)
    source: str
    quality_flag: str | None = None


class WeatherInputPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The scalar bounds mirror the server's; weather_patch_from_snapshot clamps
    # live Open-Meteo values into these ranges BEFORE building the patch, so a
    # storm gusting past 80 mph degrades to the cap instead of a 422.
    temperature_f: float | None = Field(default=None, ge=-40, le=130)
    humidity_pct: float | None = Field(default=None, ge=0, le=100)
    wind_mph: float | None = Field(default=None, ge=0, le=80)
    precipitation_in: float | None = Field(default=None, ge=0, le=12)
    solar_radiation_mj_m2: float | None = Field(default=None, ge=0, le=35)
    forecast_horizon_hours: int | None = None
    forecast_reference_et_24h_in: float | None = None
    forecast_reference_et_48h_in: float | None = None
    forecast_reference_et_72h_in: float | None = None
    forecast_precipitation_24h_in: float | None = None
    forecast_precipitation_48h_in: float | None = None
    forecast_precipitation_72h_in: float | None = None
    forecast_reference_et_0_24h_in: float | None = None
    forecast_reference_et_24_48h_in: float | None = None
    forecast_reference_et_48_72h_in: float | None = None
    forecast_precipitation_0_24h_in: float | None = None
    forecast_precipitation_24_48h_in: float | None = None
    forecast_precipitation_48_72h_in: float | None = None
    forecast_precipitation_source: (
        Literal["qpf", "pop_fallback", "caller_supplied", "missing"] | None
    ) = None


class FutureIrrigationInput(BaseModel):
    # The server's field is NOT nullable: sending JSON null draws a 422. The
    # default state mirrors the server ("missing_evidence" — the bridge has no
    # knowledge of planned irrigation).
    model_config = ConfigDict(extra="forbid")

    state: Literal[
        "no_action", "known_future_irrigation", "unknown_future_irrigation",
        "missing_evidence",
    ] = "missing_evidence"
    applied_in: float | None = Field(default=None, ge=0)


class IrrigationSystem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    irrigation_type: Literal["pivot", "drip", "flood"]
    pump_capacity_in_per_hour: float = Field(gt=0)
    water_rights_schedule: list[str] = Field(min_length=1)
    energy_price_window: list[str] = Field(default_factory=list)
    drip: None = None


class SoilProperties(BaseModel):
    model_config = ConfigDict(extra="forbid")

    soil_texture: Literal["sand", "loam", "clay"]
    infiltration_rate_in_per_hour: float = Field(gt=0)
    slope_pct: float = Field(ge=0)
    drainage_class: Literal["poor", "moderate", "well"]


class Crop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    crop_type: Literal["corn", "soybean", "alfalfa", "potato", "sugarbeet", "hops"]
    growth_stage: Literal["emergence", "vegetative", "flowering", "grain_fill", "maturity"]
    canopy_status: Literal["active"] = "active"


class Operational(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_area_acres: float = Field(gt=0)
    budget_dollars: float = Field(ge=0)
    # The server's schema defaults this to TRUE when omitted; mirror that so
    # "unspecified" means the same thing on both sides. (Jacob's saved field
    # setups carry an explicit false, which passes through unchanged.)
    budget_constraint_enabled: bool = True
    max_irrigation_volume_in: float | None = None


class RecentIrrigationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    applied_in: float = Field(ge=0)


class PredictionRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str
    # Bridge policy, deliberately narrower than the wire schema (the live
    # OpenAPI types this as a plain integer): the bridge only ever asks for
    # the documented horizons.
    forecast_horizon_hours: Literal[24, 48, 72]
    farm_id: str | None = None
    weather: WeatherInputPatch | None = None
    irrigation_system: IrrigationSystem
    soil_moisture_readings: list[SoilMoistureReading] = Field(min_length=1)
    soil_properties: SoilProperties
    crop: Crop
    operational: Operational
    location_lat: float = Field(ge=-90, le=90)
    location_lon: float = Field(ge=-180, le=180)
    recent_irrigation_events: list[RecentIrrigationEvent] = Field(default_factory=list)
    # Never null on the wire — the server 422s on JSON null here (verified
    # against the live OpenAPI). The default object is what "nothing planned"
    # looks like.
    future_irrigation: FutureIrrigationInput = Field(default_factory=FutureIrrigationInput)

    @model_validator(mode="after")
    def _readings_share_field_and_type(self) -> "PredictionRequestPayload":
        field_ids = {r.field_id for r in self.soil_moisture_readings}
        types = {r.measurement_type for r in self.soil_moisture_readings}
        if len(field_ids) > 1:
            raise ValueError("all soil_moisture_readings must share one field_id")
        if len(types) > 1:
            raise ValueError("all soil_moisture_readings must share one measurement_type")
        return self
