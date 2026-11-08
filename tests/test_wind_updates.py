"""All routes, riders, maps and forecasts here are made up."""

import re
from datetime import timedelta

import pytest

from app.db import purge_expired
from app.lists import add_wind, sort_by_wind
from app.region import in_region
from app.main import _hour, _wind_percent, templates
from app.weather import WeatherUnavailable, grid_point
from app.wind import Wind
from app.wind_updates import update_next_rider, wind_pending
from tests.conftest import ATHLETE, NOW, OTHER_ATHLETE, encode_route

THIS_HOUR = NOW.replace(minute=0, second=0, microsecond=0)
GRASS = 30


NORTHBOUND = encode_route([(42.4, -76.5), (42.41, -76.5)])
SOUTHBOUND = encode_route([(42.41, -76.5), (42.4, -76.5)])


class FakeMaps:
    """Flat grassland everywhere."""

    def __init__(self):
        self.routes = []

    def for_route(self, points):
        self.routes.append(points)
        return (lambda lat, lng: GRASS), (lambda lat, lng: 100.0)


class Forecast:
    """A steady north wind that picks up after the first hour."""

    def __init__(self):
        self.requests = []
        self.fail = False

    def __call__(self, points, now):
        points = set(points)
        self.requests.append(points)
        if self.fail:
            raise WeatherUnavailable("down")
        return {
            point: {
                THIS_HOUR: Wind(2.0, 0.0),
                THIS_HOUR + timedelta(hours=1): Wind(8.0, 0.0),
            }
            for point in points
        }


def add_segment(conn, segment_id, polyline=NORTHBOUND, grade=0.0, athlete=ATHLETE):
    conn.execute(
        """
        INSERT INTO segments (id, name, distance_m, average_grade, polyline, fetched_at)
        VALUES (%s, %s, 1000, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (segment_id, f"Segment {segment_id}", grade, polyline, NOW),
    )
    ride = segment_id * 10 + athlete
    conn.execute(
        "INSERT INTO rides (id, athlete_id, start_date, fetched_at) VALUES (%s, %s, %s, %s)",
        (ride, athlete, NOW - timedelta(days=1), NOW),
    )
    conn.execute(
        """
        INSERT INTO efforts (id, athlete_id, ride_id, segment_id, start_date, fetched_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (ride, athlete, ride, segment_id, NOW - timedelta(days=1), NOW),
    )


def mark_seen(conn, athlete=ATHLETE, when=NOW):
    conn.execute("UPDATE athletes SET last_seen_at = %s WHERE id = %s", (when, athlete))


def effects(conn, segment_id, athlete=ATHLETE):
    return conn.execute(
        """
        SELECT * FROM wind_effects WHERE athlete_id = %s AND segment_id = %s ORDER BY hour
        """,
        (athlete, segment_id),
    ).fetchall()


def run(conn, forecast=None, when=NOW, maps=None):
    return update_next_rider(conn, maps or FakeMaps(), forecast or Forecast(), when, False)


class TestUpdate:
    def test_works_out_each_hour_with_the_right_direction(self, conn):
        add_segment(conn, 1, NORTHBOUND)
        add_segment(conn, 2, SOUTHBOUND)
        mark_seen(conn)
        assert run(conn)
        head, tail = effects(conn, 1), effects(conn, 2)
        assert [row["hour"] for row in head] == [THIS_HOUR, THIS_HOUR + timedelta(hours=1)]
        assert 0 < head[0]["percent"] < head[1]["percent"]
        assert tail[1]["percent"] < tail[0]["percent"] < 0

    def test_leaves_riders_who_are_not_using_the_app(self, conn):
        add_segment(conn, 1)
        mark_seen(conn, when=NOW - timedelta(hours=2))
        forecast = Forecast()
        assert not run(conn, forecast)
        assert forecast.requests == []

    def test_does_nothing_more_until_the_next_hour(self, conn):
        add_segment(conn, 1)
        mark_seen(conn)
        assert run(conn)
        assert not run(conn)
        mark_seen(conn, when=NOW + timedelta(minutes=61))
        assert run(conn, when=NOW + timedelta(minutes=61))

    def test_a_new_profile_changes_the_numbers_straight_away(self, conn):
        add_segment(conn, 1, grade=7.0)
        mark_seen(conn)
        run(conn)
        before = effects(conn, 1)[1]["percent"]
        conn.execute(
            "UPDATE athletes SET power_20min_w = 400, weight_kg = 60, wind_stale = TRUE WHERE id = %s",
            (ATHLETE,),
        )
        assert run(conn)
        assert effects(conn, 1)[1]["percent"] != pytest.approx(before)

    def test_only_rounded_points_go_to_the_weather_service(self, conn):
        add_segment(conn, 1, encode_route([(42.41234, -76.54321), (42.42345, -76.54321)]))
        mark_seen(conn)
        forecast = Forecast()
        run(conn, forecast)
        assert forecast.requests[0]
        assert all(grid_point(*point) == point for point in forecast.requests[0])

    def test_shares_fresh_forecasts_between_riders(self, conn):
        add_segment(conn, 1)
        add_segment(conn, 1, athlete=OTHER_ATHLETE)
        mark_seen(conn)
        mark_seen(conn, OTHER_ATHLETE)
        forecast = Forecast()
        run(conn, forecast)
        run(conn, forecast)
        assert len(forecast.requests) == 1
        assert effects(conn, 1, OTHER_ATHLETE)

    def test_builds_each_segment_model_once(self, conn):
        add_segment(conn, 1)
        mark_seen(conn)
        maps = FakeMaps()
        run(conn, maps=maps)
        conn.execute("UPDATE athletes SET wind_stale = TRUE WHERE id = %s", (ATHLETE,))
        run(conn, maps=maps)
        assert len(maps.routes) == 1

    def test_weather_trouble_leaves_no_numbers_and_stops_retrying(self, conn):
        add_segment(conn, 1)
        mark_seen(conn)
        forecast = Forecast()
        forecast.fail = True
        assert run(conn, forecast)
        assert effects(conn, 1) == []
        assert not run(conn, forecast)

    @pytest.mark.parametrize(
        "polyline, grade", [(None, 0.0), (NORTHBOUND, None), ("_p~iF", 0.0), ("~~~~~~~~~~~~", 0.0)]
    )
    def test_unusable_segment_has_no_numbers_but_others_still_work(self, conn, polyline, grade):
        add_segment(conn, 1, polyline, grade)
        add_segment(conn, 2)
        mark_seen(conn)
        assert run(conn)
        assert effects(conn, 1) == []
        assert effects(conn, 2)

    def test_unexpected_failure_does_not_retry_non_stop(self, conn):
        add_segment(conn, 1)
        mark_seen(conn)

        class BrokenMaps:
            def for_route(self, points):
                raise RuntimeError("boom")

        assert run(conn, maps=BrokenMaps())
        assert not run(conn)

    def test_steep_descent_is_marked_less_certain(self, conn):
        add_segment(conn, 1, grade=-8.0)
        add_segment(conn, 2, grade=1.0)
        mark_seen(conn)
        run(conn)
        rows = [{"id": 1}, {"id": 2}]
        add_wind(conn, ATHLETE, rows, NOW)
        assert [row["less_certain"] for row in rows] == [True, False]


class TestRegion:
    OUTSIDE = encode_route([(44.0, -120.0), (44.01, -120.0)])
    CROSSES_THE_EDGE = encode_route([(42.94, -76.5), (42.96, -76.5)])

    @pytest.mark.parametrize("polyline", [OUTSIDE, CROSSES_THE_EDGE])
    def test_routes_outside_use_no_maps_or_weather(self, conn, polyline):
        add_segment(conn, 1, polyline)
        mark_seen(conn)
        maps, forecast = FakeMaps(), Forecast()
        assert run(conn, forecast, maps=maps)
        assert maps.routes == []
        assert forecast.requests == []
        assert effects(conn, 1) == []

    def test_outside_routes_do_not_stop_local_ones(self, conn):
        add_segment(conn, 1, self.OUTSIDE)
        add_segment(conn, 2)
        mark_seen(conn)
        maps, forecast = FakeMaps(), Forecast()
        run(conn, forecast, maps=maps)
        assert len(maps.routes) == 1
        assert all(in_region(*point, padding=0.02) for point in forecast.requests[0])
        assert effects(conn, 2)

    def test_outside_routes_are_not_logged_as_problems(self, conn, caplog):
        add_segment(conn, 1, self.OUTSIDE)
        mark_seen(conn)
        run(conn)
        assert caplog.records == []


class TestRetention:
    def test_route_based_results_are_deleted_with_the_segment(self, conn):
        add_segment(conn, 1)
        mark_seen(conn)
        run(conn)
        conn.execute("DELETE FROM segments WHERE id = 1")
        assert conn.execute("SELECT count(*) AS n FROM segment_wind_models").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM wind_effects").fetchone()["n"] == 0

    def test_everything_is_deleted_with_the_rider(self, conn):
        add_segment(conn, 1)
        mark_seen(conn)
        run(conn)
        conn.execute("DELETE FROM athletes WHERE id = %s", (ATHLETE,))
        assert conn.execute("SELECT count(*) AS n FROM wind_effects").fetchone()["n"] == 0

    def test_purge_removes_expired_segments_results_and_forecasts(self, conn):
        add_segment(conn, 1)
        mark_seen(conn)
        run(conn)
        purge_expired(conn, NOW + timedelta(days=7))
        for table in ("segment_wind_models", "wind_effects", "forecasts"):
            assert conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0

    def test_purge_removes_hours_that_have_passed(self, conn):
        add_segment(conn, 1)
        mark_seen(conn)
        run(conn)
        purge_expired(conn, NOW + timedelta(hours=2))
        assert [row["hour"] for row in effects(conn, 1)] == [THIS_HOUR + timedelta(hours=1)]


class TestListValues:
    def setup_rows(self, conn):
        add_segment(conn, 1, NORTHBOUND)
        add_segment(conn, 2, SOUTHBOUND)
        add_segment(conn, 3, None)
        mark_seen(conn)
        run(conn)
        rows = [{"id": 1}, {"id": 2}, {"id": 3}]
        add_wind(conn, ATHLETE, rows, NOW)
        return rows

    def test_now_is_this_hour_and_best_is_the_lowest(self, conn):
        first, second, missing = self.setup_rows(conn)
        assert first["wind_now"]["hour"] == THIS_HOUR
        assert first["wind_best"]["hour"] == THIS_HOUR
        assert second["wind_best"]["hour"] == THIS_HOUR + timedelta(hours=1)
        assert missing["wind_now"] is None and missing["wind_best"] is None

    def test_sorts_biggest_saving_first_with_missing_last(self, conn):
        rows = self.setup_rows(conn)
        assert [row["id"] for row in sort_by_wind(rows, "best")] == [2, 1, 3]
        assert [row["id"] for row in sort_by_wind(rows, "now")] == [2, 1, 3]

    def test_other_riders_numbers_are_not_mixed_in(self, conn):
        self.setup_rows(conn)
        rows = [{"id": 1}]
        add_wind(conn, OTHER_ATHLETE, rows, NOW)
        assert rows[0]["wind_hours"] == []


class TestWindPending:
    def row(self, stale=False, started=NOW, updated=NOW):
        return {"wind_stale": stale, "wind_started_at": started, "wind_updated_at": updated}

    def test_settled(self):
        assert not wind_pending(self.row(), NOW)

    def test_stale_or_never_updated(self):
        assert wind_pending(self.row(stale=True), NOW)
        assert wind_pending(self.row(started=None, updated=None), NOW)

    def test_overdue_after_time_away(self):
        assert not wind_pending(self.row(), NOW + timedelta(minutes=59))
        assert wind_pending(self.row(), NOW + timedelta(days=2))

    def test_running(self):
        started = NOW + timedelta(minutes=1)
        assert wind_pending(self.row(started=started), started)

    def test_a_run_that_died_stops_counting(self):
        started = NOW + timedelta(minutes=1)
        assert not wind_pending(self.row(started=started), started + timedelta(minutes=11))


class TestFilters:
    def test_percentages_and_labels(self):
        assert _wind_percent(-5.6) == "\u22126%"
        assert _wind_percent(5.6) == "+6%"
        assert _wind_percent(-0.4) == "0%"
        assert _wind_percent(0.4) == "0%"
        assert re.fullmatch(r"\w{3} \d{1,2}(am|pm)", _hour(NOW))
        assert templates.env.filters["compass"](0) == "N"
        assert templates.env.filters["compass"](355) == "N"
        assert templates.env.filters["compass"](225) == "SW"
        assert templates.env.filters["mph"](4.4704) == "10 mph"
