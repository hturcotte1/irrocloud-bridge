"""Helios client: payload validation, retries, weather fallback, safe saves."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from bridge._helios_schemas import PredictionRequestPayload, PredictionResponse
from bridge._helios_schemas.outputs import QUARANTINE_REVIEW_GATE_REASON
from bridge.errors import AlertError
from bridge.helios import (
    MAX_READINGS_PER_REQUEST,
    LiveHelios,
    OfflineHelios,
    build_prediction_payload,
    build_run_object,
    default_field_setup,
    run_helios_for_field,
    weather_patch_from_snapshot,
)
from bridge.parse import load_irrocloud_csv
from bridge.weather import WeatherSnapshot
from tests.test_analyze import make_field

NOW = datetime(2026, 8, 24, 12, 30, tzinfo=UTC)


def rv80_readings(synthetic_dir):
    return load_irrocloud_csv(synthetic_dir / "rv80.csv", "rv80").readings


def rv80_setup():
    return default_field_setup(make_field(key="rv80"))


# ---- payload -----------------------------------------------------------------


def test_payload_validates_and_windows(synthetic_dir):
    payload = build_prediction_payload(rv80_setup(), rv80_readings(synthetic_dir), NOW)
    parsed = PredictionRequestPayload.model_validate(payload)  # would raise on drift
    assert parsed.forecast_horizon_hours == 24
    assert parsed.weather is None  # omitted so the server enriches
    assert len(parsed.soil_moisture_readings) <= MAX_READINGS_PER_REQUEST
    assert {r.field_id for r in parsed.soil_moisture_readings} == {"rv80"}
    assert {r.unit for r in parsed.soil_moisture_readings} == {"centibar"}
    assert {r.source for r in parsed.soil_moisture_readings} == {"irrocloud_bridge"}
    oldest = min(r.timestamp for r in parsed.soil_moisture_readings)
    assert (NOW - oldest).total_seconds() <= 72 * 3600
    # The live server rejects JSON null here (422, verified against its
    # OpenAPI on 2026-08-24); the wire value must be the default object.
    assert payload["future_irrigation"] == {
        "state": "missing_evidence",
        "applied_in": None,
    }


def test_payload_caps_keep_newest(synthetic_dir):
    readings = rv80_readings(synthetic_dir)  # ~500+ readings in 72 h? force the cap:
    payload = build_prediction_payload(rv80_setup(), readings, NOW)
    n = len(payload["soil_moisture_readings"])
    if n == MAX_READINGS_PER_REQUEST:
        newest_kept = max(r["timestamp"] for r in payload["soil_moisture_readings"])
        newest_all = readings["ts_utc"].max().isoformat()
        assert newest_kept == newest_all
    else:
        assert n <= MAX_READINGS_PER_REQUEST


def test_payload_refuses_stale_window(synthetic_dir):
    later = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    with pytest.raises(AlertError):
        build_prediction_payload(rv80_setup(), rv80_readings(synthetic_dir), later)


def test_weather_patch_mapping():
    snap = WeatherSnapshot(
        precip_24_in=0.1, precip_48_in=0.4, precip_72_in=0.5,
        temperature_f=88.0, humidity_pct=30.0, wind_mph=8.0,
        precipitation_in=0.0, solar_radiation_mj_m2=24.0,
    )
    patch = weather_patch_from_snapshot(snap)
    assert patch.forecast_precipitation_source == "caller_supplied"
    assert patch.forecast_precipitation_24h_in == 0.1
    assert patch.forecast_precipitation_24_48h_in == pytest.approx(0.3)
    assert patch.forecast_precipitation_48_72h_in == pytest.approx(0.1)
    assert patch.temperature_f == 88.0


# ---- offline stub ------------------------------------------------------------


def test_offline_stub_is_honest(synthetic_dir):
    offline = OfflineHelios()
    payload = build_prediction_payload(rv80_setup(), rv80_readings(synthetic_dir), NOW)
    doc = offline.predict(payload)
    parsed = PredictionResponse.model_validate(doc)
    assert parsed.explanation.operator_review_required is True
    assert parsed.explanation.review_gate_reason == QUARANTINE_REVIEW_GATE_REASON
    assert parsed.forecast_source == "xgboost"
    assert parsed.persistence_baseline.method == "driest_zone_carry_forward"
    # Persistence carries today's driest zone forward.
    assert parsed.persistence_baseline.moisture_24h <= parsed.predicted_moisture.moisture_24h


def test_offline_full_field_stage(synthetic_dir, frozen_now):
    offline = OfflineHelios()
    result = run_helios_for_field(
        offline, rv80_setup(), rv80_readings(synthetic_dir), None, frozen_now, "2026-08-24"
    )
    assert result.status == "ok"
    assert result.review_required is True
    assert result.review_reason == QUARANTINE_REVIEW_GATE_REASON
    assert result.save_status == "offline"
    assert result.run_id == "bridge-rv80-2026-08-24"
    assert offline.saved_runs and offline.saved_runs[0]["id"] == result.run_id


# ---- live client -------------------------------------------------------------


def _ok_response_doc():
    offline = OfflineHelios()
    return {
        **offline.predict(
            {
                "field_id": "rv80",
                "farm_id": None,
                "forecast_horizon_hours": 24,
                "weather": None,
                "irrigation_system": {
                    "irrigation_type": "pivot",
                    "pump_capacity_in_per_hour": 0.24,
                    "water_rights_schedule": ["unrestricted"],
                    "energy_price_window": [],
                    "drip": None,
                },
                "soil_moisture_readings": [
                    {
                        "timestamp": NOW.isoformat(),
                        "field_id": "rv80",
                        "sensor_id": "rv80_a_12",
                        "measurement_type": "soil_water_tension",
                        "unit": "centibar",
                        "value": 35.0,
                        "volumetric_water_content": None,
                        "depth_in": 12.0,
                        "source": "irrocloud_bridge",
                        "quality_flag": None,
                    }
                ],
                "soil_properties": {
                    "soil_texture": "loam",
                    "infiltration_rate_in_per_hour": 0.47,
                    "slope_pct": 1.0,
                    "drainage_class": "moderate",
                },
                "crop": {
                    "crop_type": "sugarbeet",
                    "growth_stage": "flowering",
                    "canopy_status": "active",
                },
                "operational": {
                    "field_area_acres": 73.0,
                    "budget_dollars": 0,
                    "budget_constraint_enabled": False,
                    "max_irrigation_volume_in": None,
                },
                "location_lat": 43.0,
                "location_lon": -116.1,
                "recent_irrigation_events": [],
                # {} — never null; the live server 422s on JSON null here.
                "future_irrigation": {},
            }
        )
    }


def make_live(handler, sleeps=None) -> LiveHelios:
    recorded = sleeps if sleeps is not None else []
    return LiveHelios(
        "https://helios.example",
        "user@example.com",
        "secret",
        client=httpx.Client(
            base_url="https://helios.example", transport=httpx.MockTransport(handler)
        ),
        sleep=recorded.append,
    )


def test_login_and_field_setups():
    def handler(request):
        assert request.url.path == "/web/auth/login"
        body = json.loads(request.content)
        assert body == {"email": "user@example.com", "password": "secret"}
        return httpx.Response(
            200,
            json={
                "email": "user@example.com",
                "fields": [
                    {"field_key": "rv80", "field_name": "RV80", "crop_type": "sugarbeet"},
                    {"field_key": "whitted", "field_name": "Whitted", "crop_type": "corn"},
                ],
            },
        )

    live = make_live(handler)
    setups = live.field_setups([make_field(key="rv80")])
    assert setups["rv80"]["field_name"] == "RV80"


def test_login_401_is_plain_english_alert():
    live = make_live(lambda req: httpx.Response(401, json={"detail": "bad"}))
    with pytest.raises(AlertError) as excinfo:
        live.login()
    assert "wrong email or password" in excinfo.value.reason


def test_missing_field_stops_never_creates():
    def handler(request):
        return httpx.Response(200, json={"fields": [{"field_key": "rv80"}]})

    live = make_live(handler)
    with pytest.raises(AlertError) as excinfo:
        live.field_setups([make_field(key="rv80"), make_field(key="whitted")])
    assert "whitted" in excinfo.value.reason
    assert "Not creating fields" in excinfo.value.reason


def test_predict_retries_then_caller_weather_fallback():
    calls = []

    def handler(request):
        if request.url.path != "/web/predict":
            return httpx.Response(404)
        body = json.loads(request.content)
        calls.append(body.get("weather"))
        if body.get("weather") is None:
            return httpx.Response(503, text="NOAA enrichment failed")
        return httpx.Response(200, json=_ok_response_doc())

    sleeps: list[float] = []
    live = make_live(handler, sleeps)
    snap = WeatherSnapshot(precip_24_in=0.0, precip_48_in=0.0, precip_72_in=0.0)
    doc = live.predict({"forecast_horizon_hours": 24, "weather": None}, snap)
    assert doc.get("_bridge_weather_was_caller_supplied") is True
    assert len(calls) == 4  # three plain tries + one with weather
    assert calls[3]["forecast_precipitation_source"] == "caller_supplied"
    assert 2.0 in sleeps and 4.0 in sleeps  # backoff between the plain tries


def test_predict_unavailable_after_all_retries(synthetic_dir, frozen_now):
    def handler(request):
        return httpx.Response(503, text="down")

    live = make_live(handler)
    snap = WeatherSnapshot(precip_24_in=0.0, precip_48_in=0.0, precip_72_in=0.0)
    result = run_helios_for_field(
        live, rv80_setup(), rv80_readings(synthetic_dir), snap, frozen_now, "2026-08-24"
    )
    assert result.status == "unavailable"
    assert "unreachable" in result.error


def test_predict_4xx_is_an_alert_not_a_retry():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(422, text="validation error")

    live = make_live(handler)
    with pytest.raises(AlertError):
        live.predict({"forecast_horizon_hours": 24, "weather": None}, None)
    assert len(calls) == 1  # no retry on our-payload-is-wrong


def test_save_run_skipped_without_template():
    def handler(request):
        if request.url.path == "/web/runs" and request.method == "GET":
            return httpx.Response(200, json={"run_history": [], "saved_runs": []})
        raise AssertionError("must not POST without a confirmed shape")

    live = make_live(handler)
    status, detail = live.save_run({"id": "bridge-rv80-2026-08-24"})
    assert status == "skipped"
    assert "template" in detail


def test_save_run_skipped_on_shape_mismatch():
    template = {"id": "x", "title": "t", "timestamp": "now", "extraServerField": 1}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"run_history": [template]})
        raise AssertionError("must not POST on shape mismatch")

    live = make_live(handler)
    status, detail = live.save_run({"id": "y", "title": "t2", "timestamp": "now"})
    assert status == "skipped"
    assert "extraServerField" in detail


def test_save_run_posts_when_shape_confirmed():
    run_obj = build_run_object(rv80_setup(), _ok_response_doc(), "2026-08-24", NOW.isoformat())
    posted = {}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"run_history": [dict.fromkeys(run_obj, 0)]})
        posted.update(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    live = make_live(handler)
    status, detail = live.save_run(run_obj)
    assert status == "saved"
    assert posted["saved"] is True
    assert posted["run"]["id"] == "bridge-rv80-2026-08-24"


def test_calls_are_spaced_two_seconds():
    def handler(request):
        return httpx.Response(200, json={"run_history": [], "saved_runs": []})

    sleeps: list[float] = []
    live = make_live(handler, sleeps)
    live.get_runs()
    live.get_runs()
    # Second call must have waited out the 2-second spacing window.
    assert sleeps and 0 < sleeps[0] <= 2.0
