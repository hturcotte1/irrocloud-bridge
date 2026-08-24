"""Open-Meteo client: mapping and graceful failure. No real network."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx

from bridge.weather import fetch_forecast, fetch_historical_precip

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def _forecast_doc():
    times, precip = [], []
    start = NOW.replace(minute=0)
    for h in range(96):
        t = start + timedelta(hours=h)
        times.append(t.strftime("%Y-%m-%dT%H:%M"))
        # 0.05" during hours 0..9 (first 24 h), 0.1" during hours 30..33 (48 h window)
        precip.append(0.05 if h < 10 else (0.1 if 30 <= h < 34 else 0.0))
    return {
        "hourly": {"time": times, "precipitation": precip},
        "current": {
            "temperature_2m": 88.1,
            "relative_humidity_2m": 31,
            "wind_speed_10m": 7.3,
            "precipitation": 0.0,
            "shortwave_radiation": 700,
        },
        "daily": {"time": [times[0][:10]], "shortwave_radiation_sum": [24.5]},
    }


def make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_forecast_totals_and_current_conditions():
    client = make_client(lambda req: httpx.Response(200, json=_forecast_doc()))
    snap = fetch_forecast(43.0, -116.1, NOW, client=client)
    assert snap is not None
    assert snap.precip_24_in == 0.5  # 10 × 0.05
    assert snap.precip_48_in == 0.9  # + 4 × 0.1
    assert snap.precip_72_in == 0.9
    assert snap.temperature_f == 88.1
    assert snap.humidity_pct == 31
    assert snap.wind_mph == 7.3
    assert snap.solar_radiation_mj_m2 == 24.5
    assert snap.mentionable_precip()


def test_no_rain_is_not_mentionable():
    doc = _forecast_doc()
    doc["hourly"]["precipitation"] = [0.0] * 96
    client = make_client(lambda req: httpx.Response(200, json=doc))
    snap = fetch_forecast(43.0, -116.1, NOW, client=client)
    assert snap.precip_72_in == 0.0
    assert not snap.mentionable_precip()


def test_server_error_returns_none():
    client = make_client(lambda req: httpx.Response(500, text="boom"))
    assert fetch_forecast(43.0, -116.1, NOW, client=client) is None


def test_network_error_returns_none():
    def handler(request):
        raise httpx.ConnectError("no route")

    client = make_client(handler)
    assert fetch_forecast(43.0, -116.1, NOW, client=client) is None


def test_historical_precip_maps_dates():
    doc = {
        "daily": {
            "time": ["2026-08-20", "2026-08-21", "2026-08-22"],
            "precipitation_sum": [0.0, 0.25, None],
        }
    }
    client = make_client(lambda req: httpx.Response(200, json=doc))
    result = fetch_historical_precip(
        43.0, -116.1, date(2026, 8, 20), date(2026, 8, 22), client=client
    )
    assert result == {
        date(2026, 8, 20): 0.0,
        date(2026, 8, 21): 0.25,
        date(2026, 8, 22): 0.0,  # None → 0.0
    }


def test_historical_failure_returns_none():
    client = make_client(lambda req: httpx.Response(404, text="nope"))
    assert fetch_historical_precip(
        43.0, -116.1, date(2026, 8, 20), date(2026, 8, 22), client=client
    ) is None
