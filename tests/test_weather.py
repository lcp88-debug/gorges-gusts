"""All locations and forecasts here are made up."""

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import responses

from app.weather import API_URL, WeatherUnavailable, fetch_forecasts, grid_point
from app.wind import Wind

NOW = datetime(2026, 11, 3, 12, 30, tzinfo=timezone.utc)
THIS_HOUR = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)


def location(times=("2026-11-03T12:00",), speed=5.0, direction=90, temperature=10.0, pressure=1000.0):
    count = len(times)
    return {
        "hourly": {
            "time": list(times),
            "wind_speed_10m": [speed] * count,
            "wind_direction_10m": [direction] * count,
            "temperature_2m": [temperature] * count,
            "surface_pressure": [pressure] * count,
        }
    }


class TestGridPoint:
    def test_snaps_to_the_nearest_grid_line(self):
        assert grid_point(45.123456, -120.987654) == (45.12, -120.98)

    def test_nearby_locations_share_a_point(self):
        assert grid_point(45.1201, -120.0) == grid_point(45.1249, -120.0)

    def test_precise_location_does_not_survive(self):
        lat, lng = grid_point(45.13579, -120.24681)
        assert (round(lat * 100) % 2, round(lng * 100) % 2) == (0, 0)


class TestFetchForecasts:
    @responses.activate
    def test_returns_each_hour_as_wind(self):
        responses.get(API_URL, json=location())
        assert fetch_forecasts([(45.0, -120.0)], NOW) == {
            (45.0, -120.0): {THIS_HOUR: Wind(5.0, 90, 10.0, 1000.0)}
        }

    @responses.activate
    def test_sends_only_grid_points_and_weather_settings(self):
        responses.get(API_URL, json=[location(), location()])
        fetch_forecasts([(45.02, -120.0), (45.0, -120.02)], NOW)
        query = parse_qs(urlparse(responses.calls[0].request.url).query)
        assert set(query) == {
            "latitude", "longitude", "hourly", "wind_speed_unit", "forecast_days", "timezone",
        }
        assert query["latitude"] == ["45.00,45.02"]
        assert query["longitude"] == ["-120.02,-120.00"]
        assert query["wind_speed_unit"] == ["ms"]

    @responses.activate
    def test_matches_several_locations_to_their_points(self):
        responses.get(API_URL, json=[location(speed=1.0), location(speed=2.0)])
        forecasts = fetch_forecasts([(45.02, -120.0), (45.0, -120.0)], NOW)
        assert forecasts[(45.0, -120.0)][THIS_HOUR].speed_ms == 1.0
        assert forecasts[(45.02, -120.0)][THIS_HOUR].speed_ms == 2.0

    @responses.activate
    def test_repeated_points_are_requested_once(self):
        responses.get(API_URL, json=location())
        fetch_forecasts([(45.0, -120.0), (45.0, -120.0)], NOW)
        assert len(responses.calls) == 1

    @responses.activate
    def test_splits_many_points_across_requests(self):
        responses.get(API_URL, json=[location()] * 50)
        responses.get(API_URL, json=location())
        points = [(round(40 + index * 0.02, 2), -120.0) for index in range(51)]
        assert len(fetch_forecasts(points, NOW)) == 51
        assert len(responses.calls) == 2

    @responses.activate
    def test_no_points_makes_no_request(self):
        assert fetch_forecasts([], NOW) == {}
        assert len(responses.calls) == 0

    @responses.activate
    def test_keeps_only_this_hour_to_48_hours_ahead(self):
        times = (
            "2026-11-03T11:00",
            "2026-11-03T12:00",
            "2026-11-05T12:00",
            "2026-11-05T13:00",
        )
        responses.get(API_URL, json=location(times))
        hours = fetch_forecasts([(45.0, -120.0)], NOW)[(45.0, -120.0)]
        assert [hour.isoformat() for hour in hours] == [
            "2026-11-03T12:00:00+00:00",
            "2026-11-05T12:00:00+00:00",
        ]

    @responses.activate
    def test_leaves_out_hours_with_missing_values(self):
        responses.get(API_URL, json=location(speed=None))
        assert fetch_forecasts([(45.0, -120.0)], NOW) == {(45.0, -120.0): {}}

    @responses.activate
    @pytest.mark.parametrize(
        "reply",
        [
            {"status": 429, "json": {"error": True}},
            {"status": 500, "body": "oops"},
            {"body": requests.ConnectionError()},
            {"body": "not json"},
            {"json": {"hourly": {"time": ["2026-11-03T12:00"]}}},
            {"json": [location(), location()]},
        ],
    )
    def test_service_problems_become_one_error(self, reply):
        responses.get(API_URL, **reply)
        with pytest.raises(WeatherUnavailable):
            fetch_forecasts([(45.0, -120.0)], NOW)
