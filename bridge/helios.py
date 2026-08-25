"""Talk to Helios: login, predict, and save runs into the grower's history.

Two implementations behind one surface:

- ``LiveHelios`` — the real service (``HELIOS_MODE=live``). Cookie session from
  ``POST /web/auth/login``, calls spaced ≥ 2 s apart (the server rate-limits at
  60/minute), three retries with backoff on 5xx/timeouts, then ONE last retry
  with caller-supplied Open-Meteo weather (the server's own NOAA enrichment is
  the usual soft failure), then gives up with status "unavailable".
- ``OfflineHelios`` — a stub built from the copied schemas, with
  ``operator_review_required=True`` and the real quarantine sentence, so the
  message code and tests exercise the honest path (build brief §10).

Saving runs: ``mapApiRun``'s full shape was not available tonight, so before
POSTing the bridge fetches ``GET /web/runs`` and compares its run object
against the newest history entry. If the shapes do not match exactly, it SKIPS
the save and logs why — never write garbage into Jacob's history (§10).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
import pandas as pd

from bridge._helios_schemas import (
    PredictionRequestPayload,
    PredictionResponse,
    WeatherInputPatch,
)
from bridge._helios_schemas.outputs import QUARANTINE_REVIEW_GATE_REASON
from bridge.config import FieldConfig
from bridge.errors import AlertError
from bridge.weather import WeatherSnapshot

READINGS_WINDOW = timedelta(hours=72)
# MAX_PHYSICAL_SENSOR_COUNT's numeric value was not in Appendix A; cap request
# size conservatively and keep the newest readings (§10). Morning task: read
# the real constant from the snapshot.
MAX_READINGS_PER_REQUEST = 500
MIN_SECONDS_BETWEEN_CALLS = 2.0
RETRY_BACKOFF_S = (2.0, 4.0)
PRIMARY_HORIZON = 24


def default_field_setup(field_cfg: FieldConfig) -> dict:
    """The saved-field defaults from Appendix A, for offline mode and tests.
    Live mode uses the real setup returned by Helios at login instead."""
    return {
        "field_key": field_cfg.key,
        "field_name": field_cfg.name,
        "field_area_acres": field_cfg.acres,
        "crop_type": field_cfg.crop,
        "growth_stage": "flowering",
        "soil_texture": "loam",
        "drainage_class": "moderate",
        "infiltration_rate": 0.47,
        "slope_pct": 1.0,
        "location_lat": field_cfg.lat,
        "location_lon": field_cfg.lon,
        "irrigation_type": "pivot",
        "pump_capacity": 0.24,
        "budget_dollars": 0,
        "budget_constraint_enabled": False,
    }


def build_prediction_payload(
    field_setup: dict,
    readings: pd.DataFrame,
    now_utc: datetime,
    horizon: int = PRIMARY_HORIZON,
    weather: WeatherInputPatch | None = None,
) -> dict:
    """Map the saved field setup + cleaned readings into a
    ``PredictionRequestPayload`` the way the Helios browser client does,
    validate it against the copied schema, and return it as a JSON-ready dict.
    Raises AlertError when there are no readings in the 72 h window."""
    field_key = field_setup["field_key"]
    window = readings[readings["ts_utc"] >= pd.Timestamp(now_utc) - READINGS_WINDOW]
    if window.empty:
        raise AlertError(
            f"No readings in the last 72 hours for {field_key}; not asking "
            "Helios for a forecast on stale air."
        )
    window = window.sort_values("ts_utc").tail(MAX_READINGS_PER_REQUEST)

    reading_dicts = [
        {
            "timestamp": row.ts_utc.isoformat(),
            "field_id": field_key,
            "sensor_id": row.sensor_id,
            "measurement_type": "soil_water_tension",
            "unit": "centibar",
            "value": float(row.tension_cb),
            "volumetric_water_content": None,
            "depth_in": float(row.depth_in),
            "source": "irrocloud_bridge",
            # pandas renders a missing flag as NaN; the schema wants str | None.
            "quality_flag": None if pd.isna(row.quality_flag) else str(row.quality_flag),
        }
        for row in window.itertuples(index=False)
    ]

    payload = PredictionRequestPayload.model_validate(
        {
            "field_id": field_key,
            "farm_id": None,
            "forecast_horizon_hours": horizon,
            "weather": None if weather is None else weather.model_dump(),
            "irrigation_system": {
                "irrigation_type": field_setup.get("irrigation_type", "pivot"),
                "pump_capacity_in_per_hour": field_setup.get("pump_capacity", 0.24),
                # The browser's value for water rights was not visible in
                # Appendix A; "unrestricted" is the bridge's stand-in. Morning
                # task: confirm against buildPredictionRequest() in the snapshot.
                "water_rights_schedule": ["unrestricted"],
                "energy_price_window": [],
                "drip": None,
            },
            "soil_moisture_readings": reading_dicts,
            "soil_properties": {
                "soil_texture": field_setup.get("soil_texture", "loam"),
                "infiltration_rate_in_per_hour": field_setup.get("infiltration_rate", 0.47),
                "slope_pct": field_setup.get("slope_pct", 1.0),
                "drainage_class": field_setup.get("drainage_class", "moderate"),
            },
            "crop": {
                "crop_type": field_setup["crop_type"],
                "growth_stage": field_setup.get("growth_stage", "flowering"),
                "canopy_status": "active",
            },
            "operational": {
                "field_area_acres": field_setup["field_area_acres"],
                "budget_dollars": field_setup.get("budget_dollars", 0),
                "budget_constraint_enabled": field_setup.get(
                    "budget_constraint_enabled", False
                ),
                "max_irrigation_volume_in": None,
            },
            "location_lat": field_setup["location_lat"],
            "location_lon": field_setup["location_lon"],
            "recent_irrigation_events": [],
            # {} — never null: the server 422s on JSON null here. The model
            # fills in the "missing_evidence" default.
            "future_irrigation": {},
        }
    )
    return payload.model_dump(mode="json")


def _clamp(value: float | None, low: float, high: float) -> float | None:
    """Keep a live weather value inside the server's accepted range — a gust
    past 80 mph should degrade to the cap, not fail the retry with a 422."""
    if value is None:
        return None
    return min(max(value, low), high)


def weather_patch_from_snapshot(
    snapshot: WeatherSnapshot, horizon: int = PRIMARY_HORIZON
) -> WeatherInputPatch:
    """Open-Meteo → the caller-supplied weather patch for the last-resort retry."""
    p24, p48, p72 = snapshot.precip_24_in, snapshot.precip_48_in, snapshot.precip_72_in
    return WeatherInputPatch(
        temperature_f=_clamp(snapshot.temperature_f, -40, 130),
        humidity_pct=_clamp(snapshot.humidity_pct, 0, 100),
        wind_mph=_clamp(snapshot.wind_mph, 0, 80),
        precipitation_in=_clamp(snapshot.precipitation_in, 0, 12),
        solar_radiation_mj_m2=_clamp(snapshot.solar_radiation_mj_m2, 0, 35),
        forecast_horizon_hours=horizon,
        forecast_precipitation_24h_in=p24,
        forecast_precipitation_48h_in=p48,
        forecast_precipitation_72h_in=p72,
        forecast_precipitation_0_24h_in=p24,
        forecast_precipitation_24_48h_in=round(p48 - p24, 2),
        forecast_precipitation_48_72h_in=round(p72 - p48, 2),
        forecast_precipitation_source="caller_supplied",
    )


def _camel(key: str) -> str:
    parts = key.split("_")
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def _camelize(value):
    """Recursively rename snake_case dict keys to camelCase, the way the
    Helios frontend's mapApiRun does with API sub-objects. Keys without
    underscores (zone labels like '12 inch Average') pass through unchanged."""
    if isinstance(value, dict):
        return {_camel(k): _camelize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_camelize(v) for v in value]
    return value


def build_run_object(
    field_setup: dict,
    response: dict,
    run_date_local: str,
    timestamp_iso: str,
    payload: dict | None = None,
) -> dict:
    """Mirror of ``mapApiRun``'s run object, extended on 2026-08-24 to the
    full shape read from the newest entry in Jacob's real run history
    (``GET /web/runs``; the live save path still refuses to POST unless the
    keys match that history exactly — see save_run). Values come from the
    validated prediction response; the frontend-composed extras (copyText,
    summary, inputSnapshot, sourceLabel) are honest bridge equivalents."""
    predicted = response.get("predicted_moisture") or {}
    explanation = response.get("explanation") or {}
    evidence = response.get("validation_evidence") or {}
    drivers_doc = response.get("forecast_drivers") or {}
    reference_et = response.get("reference_et") or {}
    field_name = field_setup.get("field_name", field_setup["field_key"])
    prompt = (
        f"Automated morning check of {field_name} "
        "from IrroCloud sensor readings (temporary bridge)."
    )
    readings = (payload or {}).get("soil_moisture_readings") or []
    sensor_ids = sorted({r.get("sensor_id") for r in readings if r.get("sensor_id")})

    review_reason = explanation.get("review_gate_reason")
    decision = response.get("decision")
    amount = response.get("final_amount_in")
    timing = response.get("timing_window")
    summary_bits = [f"{field_name}: {str(decision or 'no decision').upper()}."]
    if decision == "water" and amount:
        summary_bits.append(f"Apply {amount} in during {timing} only after review.")
    if review_reason:
        summary_bits.append(review_reason)
    summary = " ".join(summary_bits)

    copy_lines = [
        f"Run: {field_name} • Morning bridge",
        f"Timestamp: {timestamp_iso}",
        f"Decision: {str(decision or '—').upper()}",
        f"Recommended amount: {response.get('recommended_amount_in')} in",
        f"Timing window: {timing}",
        f"Heuristic confidence: {response.get('confidence_score')}",
        f"Stress probability: {explanation.get('stress_probability')}",
        f"Forecast source: {response.get('forecast_source')}",
        f"Forecast 24h: {predicted.get('moisture_24h')} cb",
        f"Forecast 48h: {predicted.get('moisture_48h')} cb",
        f"Forecast 72h: {predicted.get('moisture_72h')} cb",
        "Measurement: soil water tension (centibars). Higher tension means drier soil.",
        f"Driving zone: {explanation.get('driving_zone')}",
        f"Operator review required: {explanation.get('operator_review_required')}",
        f"Review gate: {review_reason}",
        "Saved by the IrroCloud → Helios morning bridge (automated).",
    ]

    return {
        # Deterministic id: a same-day re-run overwrites instead of duplicating.
        "id": f"bridge-{field_setup['field_key']}-{run_date_local}",
        "title": f"{field_name} • Morning bridge",
        "timestamp": timestamp_iso,
        "prompt": prompt,
        "decision": decision,
        # The fallback mirrors the live schema's default (VWC), but in practice
        # the field is always present: runs are only saved for responses that
        # passed the tension gate in _result_from_response.
        "measurementType": predicted.get("measurement_type", "vwc_fraction"),
        "recommendedAmountIn": response.get("recommended_amount_in"),
        "uncappedNeedIn": response.get("uncapped_need_in"),
        "caps": response.get("caps"),
        "bindingConstraint": response.get("binding_constraint"),
        "finalAmountIn": amount,
        "dripRuntime": _camelize(response.get("drip_runtime")),
        "timingWindow": timing,
        # ---- the rest of mapApiRun's shape (from Jacob's history) ----------
        "sourceLabel": "Morning bridge (automated)",
        "summary": summary,
        "copyText": "\n".join(copy_lines),
        "confidenceScore": response.get("confidence_score"),
        "stressProbability": explanation.get("stress_probability"),
        "drivers": explanation.get("drivers") or [],
        "drivingZone": explanation.get("driving_zone"),
        "zoneMoistureSummary": explanation.get("zone_moisture_summary") or {},
        "highVariabilityFlag": explanation.get("high_variability_flag", False),
        "operatorReviewRequired": explanation.get("operator_review_required", True),
        "reviewGateReason": review_reason,
        "configurationBlocker": explanation.get("configuration_blocker"),
        "estimatedEtIn": reference_et.get("value") if isinstance(reference_et, dict) else None,
        "etSource": response.get("et_source"),
        "forecastSource": response.get("forecast_source"),
        "forecastDrivers48h": _camelize(drivers_doc.get("drivers_48h") or []),
        "forecastUncertainty": _camelize(response.get("forecast_uncertainty")),
        "predicted": {
            "moisture24h": predicted.get("moisture_24h"),
            "moisture48h": predicted.get("moisture_48h"),
            "moisture72h": predicted.get("moisture_72h"),
        },
        "physicsBaseline": _camelize(response.get("physics_baseline")),
        "recommendationAdjustment": _camelize(response.get("recommendation_adjustment")),
        "regionalInsights": _camelize(response.get("regional_insights")),
        "validationEvidence": _camelize(evidence),
        "backendSnapshot": {
            "modelHash": evidence.get("model_artifact_hash"),
            "apiVersion": None,
            "trainingDate": evidence.get("model_training_date"),
            "validationMode": evidence.get("validation_mode"),
        },
        "inputSnapshot": {
            "model": "Helios Core",
            "farmId": field_setup.get("field_key"),
            "fieldName": field_name,
            "cropType": field_setup.get("crop_type"),
            "growthStage": field_setup.get("growth_stage"),
            "canopyStatus": "active",
            "soilTexture": field_setup.get("soil_texture"),
            "drainageClass": field_setup.get("drainage_class"),
            "infiltrationRate": field_setup.get("infiltration_rate"),
            "slopePct": field_setup.get("slope_pct"),
            "irrigationType": field_setup.get("irrigation_type"),
            "pumpCapacity": field_setup.get("pump_capacity"),
            "fieldAreaAcres": field_setup.get("field_area_acres"),
            "budgetDollars": field_setup.get("budget_dollars"),
            "budgetConstraintEnabled": field_setup.get("budget_constraint_enabled"),
            "locationLat": field_setup.get("location_lat"),
            "locationLon": field_setup.get("location_lon"),
            "measurementType": "soil_water_tension",
            "sensorCount": len(sensor_ids),
            "sensorIds": sensor_ids,
            "readingsInRequest": len(readings),
            "analysisPrompt": prompt,
            "autoSave": True,
        },
    }


@dataclass
class HeliosResult:
    """What one field got out of Helios this run."""

    field_key: str
    status: str  # "ok" | "unavailable" | "skipped"
    forecast_24: float | None = None
    persistence_24: float | None = None
    decision: str | None = None
    review_required: bool = True
    review_reason: str | None = None
    confidence: float | None = None
    forecast_source: str | None = None
    weather_was_caller_supplied: bool = False
    save_status: str = "not_attempted"  # saved | skipped | failed | offline | not_attempted
    save_detail: str | None = None
    run_id: str | None = None
    error: str | None = None
    response: dict = field(default_factory=dict)


def _result_from_response(field_key: str, doc: dict) -> HeliosResult:
    parsed = PredictionResponse.model_validate(doc)
    # Helios is dual-mode and its schema DEFAULTS to VWC-fraction. The bridge
    # only understands tension; anything else must not be passed off as
    # centibars.
    if parsed.predicted_moisture.measurement_type != "soil_water_tension":
        return HeliosResult(
            field_key=field_key,
            status="unavailable",
            error=(
                "Helios answered in "
                f"'{parsed.predicted_moisture.measurement_type}', not "
                "soil_water_tension — refusing to read those numbers as "
                "centibars."
            ),
        )
    persistence = parsed.persistence_baseline
    if persistence is not None and persistence.measurement_type != "soil_water_tension":
        persistence = None
    return HeliosResult(
        field_key=field_key,
        status="ok",
        forecast_24=parsed.predicted_moisture.moisture_24h,
        persistence_24=persistence.moisture_24h if persistence else None,
        decision=parsed.decision,
        review_required=parsed.explanation.operator_review_required,
        review_reason=parsed.explanation.review_gate_reason,
        confidence=parsed.confidence_score,
        forecast_source=parsed.forecast_source,
        response=parsed.model_dump(mode="json"),
    )


class OfflineHelios:
    """A faithful stand-in: realistic numbers, honest review gate."""

    mode = "offline"

    def __init__(self) -> None:
        self.saved_runs: list[dict] = []

    def field_setups(self, fields: list[FieldConfig]) -> dict[str, dict]:
        return {f.key: default_field_setup(f) for f in fields}

    def predict(self, payload: dict, weather_snapshot: WeatherSnapshot | None = None) -> dict:
        # Validate what the bridge WOULD have sent — offline mode still proves
        # the payload path.
        request = PredictionRequestPayload.model_validate(payload)
        newest_ts = max(r.timestamp for r in request.soil_moisture_readings)
        at_newest = [
            r.value for r in request.soil_moisture_readings if r.timestamp == newest_ts
        ]
        driest = max(at_newest) if at_newest else 30.0
        zone_summary = {
            f'{int(r.depth_in)}"': r.value
            for r in request.soil_moisture_readings
            if r.timestamp == newest_ts
        }
        stub = PredictionResponse.model_validate(
            {
                "decision": "wait",
                "recommended_amount_in": 0.0,
                "uncapped_need_in": 0.0,
                "caps": None,
                "binding_constraint": None,
                "final_amount_in": 0.0,
                "timing_window": "re-check tomorrow morning",
                "confidence_score": 0.55,
                "confidence_caveat": "offline stub response",
                "et_source": "offline_stub",
                "et_is_fallback": True,
                "reference_et": None,
                "explanation": {
                    "stress_probability": 0.2,
                    "drivers": ["offline stub"],
                    "driving_zone": '12"',
                    "zone_moisture_summary": zone_summary,
                    "high_variability_flag": False,
                    "operator_review_required": True,
                    "measurement_type": "soil_water_tension",
                    "unit": "centibar",
                    "interpretation": "Offline stub: tomorrow looks slightly drier than today.",
                    "review_gate_reason": QUARANTINE_REVIEW_GATE_REASON,
                },
                "predicted_moisture": {
                    "moisture_24h": round(driest + 2.8, 1),
                    "moisture_48h": round(driest, 1),
                    "moisture_72h": round(driest, 1),
                    "measurement_type": "soil_water_tension",
                    "unit": "centibar",
                },
                "forecast_source": "xgboost",
                "persistence_baseline": {
                    "moisture_24h": round(driest, 1),
                    "moisture_48h": round(driest, 1),
                    "moisture_72h": round(driest, 1),
                    "method": "driest_zone_carry_forward",
                    "measurement_type": "soil_water_tension",
                    "unit": "centibar",
                },
            }
        )
        return stub.model_dump(mode="json")

    def save_run(self, run_obj: dict) -> tuple[str, str]:
        self.saved_runs.append(run_obj)
        return "offline", "offline mode: run recorded locally, nothing sent"


class LiveHelios:
    """The real service. All HTTP goes through _request for rate spacing."""

    mode = "live"

    def __init__(
        self,
        base_url: str,
        email: str,
        password: str,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.password = password
        self.sleep = sleep
        self.client = client or httpx.Client(
            base_url=self.base_url, timeout=30.0, follow_redirects=True
        )
        self._last_call_at: float | None = None
        self._session_doc: dict | None = None

    # ---- plumbing -------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if self._last_call_at is not None:
            elapsed = time.monotonic() - self._last_call_at
            if elapsed < MIN_SECONDS_BETWEEN_CALLS:
                self.sleep(MIN_SECONDS_BETWEEN_CALLS - elapsed)
        response = self.client.request(method, path, **kwargs)
        self._last_call_at = time.monotonic()
        return response

    # ---- API ------------------------------------------------------------
    def login(self) -> dict:
        response = self._request(
            "POST", "/web/auth/login", json={"email": self.email, "password": self.password}
        )
        if response.status_code == 401:
            raise AlertError(
                "Helios rejected the login (wrong email or password). Check "
                "HELIOS_EMAIL and HELIOS_PASSWORD in the .env file."
            )
        if response.status_code == 503:
            raise AlertError(
                "Helios says its web login is not configured (HTTP 503). "
                "Marco needs to look at the server."
            )
        response.raise_for_status()
        self._session_doc = response.json()
        return self._session_doc

    def field_setups(self, fields: list[FieldConfig]) -> dict[str, dict]:
        """Match fields.json to the account's saved fields by field_key.
        A missing field is a stop-and-alert — never create fields (§10)."""
        if self._session_doc is None:
            self.login()
        doc = self._session_doc
        field_docs = doc.get("fields") or []
        if not field_docs:
            # The login response's field list is optional; /web/fields returns
            # the same shape and is the documented confirmation call.
            response = self._request("GET", "/web/fields")
            if response.status_code < 400:
                doc = response.json()
                field_docs = doc.get("fields") or []
        by_key = {f.get("field_key", "").lower(): f for f in field_docs}
        if not by_key:
            onboarding = " (the account still needs its field setup/onboarding)" if (
                doc.get("onboarding_required")
            ) else ""
            raise AlertError(
                "Jacob's Helios account returned no saved fields at all"
                + onboarding
                + ", so there is nothing to match fields.json against. The "
                "fields must exist in Helios first — not creating them on my "
                "own; Henry decides."
            )
        # Jacob's account names its fields field-1..field-4; fields.json maps
        # each bridge key to that via helios_field_key (falling back to the
        # bridge key for accounts that use the same names).
        wanted = {f.key: (f.helios_field_key or f.key).lower() for f in fields}
        missing = [
            f"{key} (helios key '{hk}')" for key, hk in wanted.items() if hk not in by_key
        ]
        if missing:
            raise AlertError(
                "These fields from fields.json are not in the Helios account: "
                + ", ".join(missing)
                + ". Not creating fields on my own — Henry decides. Known Helios "
                "fields: "
                + (", ".join(sorted(by_key)) or "(none)")
            )
        return {key: by_key[hk] for key, hk in wanted.items()}

    def predict(
        self, payload: dict, weather_snapshot: WeatherSnapshot | None = None
    ) -> dict:
        """Three tries plain, then one with caller-supplied weather. Raises
        AlertError on a 4xx (our payload is wrong — stop, don't guess);
        raises httpx errors only after every retry is spent."""
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self._request("POST", "/web/predict", json=payload)
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    raise AlertError(
                        f"Helios rejected the forecast request (HTTP "
                        f"{response.status_code}). The bridge's payload no longer "
                        f"matches what Helios expects: {response.text[:500]}"
                    )
                response.raise_for_status()
                return response.json()
            except AlertError:
                raise
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt < 2:
                    self.sleep(RETRY_BACKOFF_S[attempt])

        if weather_snapshot is not None:
            patched = dict(payload)
            patched["weather"] = weather_patch_from_snapshot(
                weather_snapshot, payload.get("forecast_horizon_hours", PRIMARY_HORIZON)
            ).model_dump(mode="json")
            try:
                response = self._request("POST", "/web/predict", json=patched)
                response.raise_for_status()
                doc = response.json()
                doc["_bridge_weather_was_caller_supplied"] = True
                return doc
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_error = exc

        raise last_error if last_error else RuntimeError("predict failed with no error")

    def get_runs(self) -> dict:
        response = self._request("GET", "/web/runs")
        response.raise_for_status()
        return response.json()

    def save_run(self, run_obj: dict) -> tuple[str, str]:
        """Template-confirmed save (§10): POST only when our run object's shape
        exactly matches the newest run_history entry; otherwise skip and say
        why. Returns (status, plain-English detail)."""
        try:
            history = self.get_runs().get("run_history") or []
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            return "failed", f"could not read run history to confirm the shape: {exc}"
        if not history:
            return (
                "skipped",
                "Jacob's run history is empty, so there is no template to confirm "
                "the run shape against. Morning task: read mapApiRun from the "
                "HELIOS snapshot and extend build_run_object.",
            )
        template_keys = set(history[0].keys())
        our_keys = set(run_obj.keys())
        if template_keys != our_keys:
            missing = sorted(template_keys - our_keys)
            extra = sorted(our_keys - template_keys)
            return (
                "skipped",
                "run shape not confirmed — refusing to write a guessed shape into "
                f"Jacob's history. Missing keys: {missing or 'none'}; unexpected "
                f"keys: {extra or 'none'}. Morning task: extend build_run_object "
                "from mapApiRun (or template it from GET /web/runs).",
            )
        try:
            response = self._request(
                "POST", "/web/runs", json={"run": run_obj, "saved": True}
            )
            response.raise_for_status()
            return "saved", f"saved as {run_obj['id']}"
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            return "failed", f"Helios refused the save: {exc}"


def run_helios_for_field(
    session: LiveHelios | OfflineHelios,
    field_setup: dict,
    readings: pd.DataFrame,
    weather_snapshot: WeatherSnapshot | None,
    now_utc: datetime,
    run_date_local: str,
) -> HeliosResult:
    """The whole per-field Helios stage: build → validate → predict → save.
    Never raises for availability problems (returns status="unavailable");
    raises AlertError only for stop-and-alert conditions."""
    field_key = field_setup["field_key"]
    try:
        payload = build_prediction_payload(field_setup, readings, now_utc)
    except AlertError as exc:
        return HeliosResult(field_key=field_key, status="skipped", error=exc.reason)

    try:
        doc = session.predict(payload, weather_snapshot)
    except AlertError:
        raise
    except Exception as exc:
        return HeliosResult(
            field_key=field_key,
            status="unavailable",
            error=f"Helios unreachable after retries: {exc}",
        )

    caller_weather = bool(doc.pop("_bridge_weather_was_caller_supplied", False))
    result = _result_from_response(field_key, doc)
    result.weather_was_caller_supplied = caller_weather

    run_obj = build_run_object(
        field_setup,
        result.response,
        run_date_local,
        pd.Timestamp(now_utc).isoformat(),
        payload=payload,
    )
    result.run_id = run_obj["id"]
    result.save_status, result.save_detail = session.save_run(run_obj)
    return result
