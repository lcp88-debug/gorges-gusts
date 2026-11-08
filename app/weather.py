"""Fetches hourly forecasts from Open-Meteo for rounded grid points.

Only grid points leave the app: never a route, segment or rider detail.
"""

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone

import requests

from app.region import OutsideRegion, in_region
from app.wind import Wind

API_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_SECONDS = 30
GRID_DEGREES = 0.02  # About 2 km north to south, and a little less east to west.
FORECAST_HOURS = 48
POINTS_PER_REQUEST = 50
VARIABLES = "wind_speed_10m,wind_direction_10m,temperature_2m,surface_pressure"

GridPoint = tuple[float, float]


class WeatherUnavailable(Exception):
    pass


def grid_point(lat: float, lng: float) -> GridPoint:
    """The nearest point on the fixed grid. The only location sent for forecasts."""
    return (
        round(round(lat / GRID_DEGREES) * GRID_DEGREES, 2),
        round(round(lng / GRID_DEGREES) * GRID_DEGREES, 2),
    )


def fetch_forecasts(
    points: Iterable[GridPoint], now: datetime
) -> dict[GridPoint, dict[datetime, Wind]]:
    """The next 48 hours of wind for each grid point, keyed by the start of each UTC hour.

    Hours the service has incomplete data for are left out, so they show as "–".
    """
    unique = sorted(set(points))
    # Rounding to the grid can move a point just past the edge of the region.
    if not all(in_region(lat, lng, padding=GRID_DEGREES) for lat, lng in unique):
        raise OutsideRegion("Forecast requested outside the region")
    forecasts: dict[GridPoint, dict[datetime, Wind]] = {}
    for start in range(0, len(unique), POINTS_PER_REQUEST):
        batch = unique[start : start + POINTS_PER_REQUEST]
        for point, location in zip(batch, _request(batch), strict=True):
            forecasts[point] = _parse_hours(location, now)
    return forecasts


def _request(batch: list[GridPoint]) -> list[Mapping]:
    try:
        response = requests.get(
            API_URL,
            params={
                "latitude": ",".join(f"{lat:.2f}" for lat, _ in batch),
                "longitude": ",".join(f"{lng:.2f}" for _, lng in batch),
                "hourly": VARIABLES,
                "wind_speed_unit": "ms",
                "forecast_days": 3,
                "timezone": "UTC",
            },
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as error:
        raise WeatherUnavailable("The weather service couldn't be reached") from error
    # It returns a single object for one location and a list for several.
    locations = body if isinstance(body, list) else [body]
    if len(locations) != len(batch):
        raise WeatherUnavailable("The weather service returned the wrong number of locations")
    return locations


def _parse_hours(location: Mapping, now: datetime) -> dict[datetime, Wind]:
    this_hour = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    last_hour = this_hour + timedelta(hours=FORECAST_HOURS)
    try:
        hourly = location["hourly"]
        rows = zip(
            hourly["time"],
            hourly["wind_speed_10m"],
            hourly["wind_direction_10m"],
            hourly["temperature_2m"],
            hourly["surface_pressure"],
            strict=True,
        )
        hours = {}
        for time, speed, direction, temperature, pressure in rows:
            hour = datetime.fromisoformat(time).replace(tzinfo=timezone.utc)
            if not this_hour <= hour <= last_hour:
                continue
            if None in (speed, direction, temperature, pressure):
                continue
            hours[hour] = Wind(speed, direction, temperature, pressure)
    except (KeyError, TypeError, ValueError) as error:
        raise WeatherUnavailable("The weather service returned unexpected data") from error
    return hours
