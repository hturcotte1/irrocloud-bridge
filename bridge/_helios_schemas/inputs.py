"""Reconstructed HELIOS request schemas (``helios/schemas/inputs.py``).

RECONSTRUCTED from Appendix A of the build brief on 2026-08-24 — the snapshot download
was blocked by the sandbox network policy. Verify against the real file in the morning
(see PROVENANCE.md). Request models use ``extra="forbid"``: the bridge authors these
payloads, so an unknown key is a bug in the bridge, not tolerable drift.
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

    temperature_f: float | None = None
    humidity_pct: float | None = None
    wind_mph: float | None = None
    precipitation_in: float | None = None
    solar_radiation_mj_m2: float | None = None
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
    budget_constraint_enabled: bool = False
    max_irrigation_volume_in: float | None = None


class RecentIrrigationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    applied_in: float


class PredictionRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str
    farm_id: str | None = None
    forecast_horizon_hours: Literal[24, 48, 72]
    weather: WeatherInputPatch | None = None
    irrigation_system: IrrigationSystem
    soil_moisture_readings: list[SoilMoistureReading] = Field(min_length=1)
    soil_properties: SoilProperties
    crop: Crop
    operational: Operational
    location_lat: float
    location_lon: float
    recent_irrigation_events: list[RecentIrrigationEvent] = Field(default_factory=list)
    # Appendix A says only "future_irrigation (default)"; the real shape is unknown
    # tonight, so None (the default) is the only value the bridge ever sends.
    future_irrigation: None = None

    @model_validator(mode="after")
    def _readings_share_field_and_type(self) -> "PredictionRequestPayload":
        field_ids = {r.field_id for r in self.soil_moisture_readings}
        types = {r.measurement_type for r in self.soil_moisture_readings}
        if len(field_ids) > 1:
            raise ValueError("all soil_moisture_readings must share one field_id")
        if len(types) > 1:
            raise ValueError("all soil_moisture_readings must share one measurement_type")
        return self
