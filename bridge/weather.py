"""Open-Meteo weather: free, no key, and allowed to fail without failing the run.

Two uses:

- the daily email's "rain in the forecast" line (next 24/48/72 h precipitation);
- the last-resort caller-supplied weather patch for Helios when its own NOAA
  enrichment is down (build brief §10).

Plus historical daily precipitation for the season replay's event labeling.
Every function returns ``None`` on any network or parsing problem — the email
then says "rain forecast unavailable" instead of the run failing (§9).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
TIMEOUT_S = 20.0


@dataclass
class WeatherSnapshot:
    """Forecast totals plus current conditions (for the Helios weather patch)."""

    precip_24_in: float
    precip_48_in: float
    precip_72_in: float
    temperature_f: float | None = None
    humidity_pct: float | None = None
    wind_mph: float | None = None
    precipitation_in: float | None = None  # current-hour precipitation
    solar_radiation_mj_m2: float | None = None  # today's shortwave sum

    def mentionable_precip(self) -> bool:
        """The email mentions rain at 0.1 inch or more over the 72 h window."""
        return self.precip_72_in >= 0.1


def fetch_forecast(
    lat: float,
    lon: float,
    now_utc: datetime,
    client: httpx.Client | None = None,
) -> WeatherSnapshot | None:
    """Next-72 h precipitation totals and current conditions for one field."""
    params = {
        "latitude": round(lat, 5),
        "longitude": round(lon, 5),
        "hourly": "precipitation",
        "current": (
            "temperature_2m,relative_humidity_2m,wind_speed_10m,"
            "precipitation,shortwave_radiation"
        ),
        "daily": "shortwave_radiation_sum",
        "forecast_days": 4,
        "precipitation_unit": "inch",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "UTC",
    }
    try:
        own_client = client is None
        client = client or httpx.Client(timeout=TIMEOUT_S)
        try:
            response = client.get(FORECAST_URL, params=params)
            response.raise_for_status()
            doc = response.json()
        finally:
            if own_client:
                client.close()

        times = [
            datetime.fromisoformat(t).replace(tzinfo=now_utc.tzinfo)
            for t in doc["hourly"]["time"]
        ]
        precip = doc["hourly"]["precipitation"]

        def total(hours: int) -> float:
            end = now_utc + timedelta(hours=hours)
            return round(
                sum(
                    p or 0.0
                    for t, p in zip(times, precip, strict=False)
                    if now_utc <= t < end
                ),
                2,
            )

        current = doc.get("current", {})
        daily = doc.get("daily", {})
        solar = None
        if daily.get("shortwave_radiation_sum"):
            solar = daily["shortwave_radiation_sum"][0]
        return WeatherSnapshot(
            precip_24_in=total(24),
            precip_48_in=total(48),
            precip_72_in=total(72),
            temperature_f=current.get("temperature_2m"),
            humidity_pct=current.get("relative_humidity_2m"),
            wind_mph=current.get("wind_speed_10m"),
            precipitation_in=current.get("precipitation"),
            solar_radiation_mj_m2=solar,
        )
    except Exception:
        # Any failure — DNS, timeout, schema change — degrades to "rain
        # forecast unavailable" rather than failing the morning run.
        return None


def fetch_historical_precip(
    lat: float,
    lon: float,
    start: date,
    end: date,
    tz_name: str = "America/Boise",
    client: httpx.Client | None = None,
) -> dict[date, float] | None:
    """Daily precipitation (inches) per field-local date, for replay labeling."""
    params = {
        "latitude": round(lat, 5),
        "longitude": round(lon, 5),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "precipitation_sum",
        "precipitation_unit": "inch",
        "timezone": tz_name,
    }
    try:
        own_client = client is None
        client = client or httpx.Client(timeout=TIMEOUT_S)
        try:
            response = client.get(ARCHIVE_URL, params=params)
            response.raise_for_status()
            doc = response.json()
        finally:
            if own_client:
                client.close()
        days = doc["daily"]["time"]
        sums = doc["daily"]["precipitation_sum"]
        return {
            date.fromisoformat(d): (s if s is not None else 0.0)
            for d, s in zip(days, sums, strict=False)
        }
    except Exception:
        return None
