"""Keeps each rider's wind effects up to date, using stored routes, maps and forecasts."""

import logging
from collections.abc import Callable, Iterable
from dataclasses import astuple
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.types.json import Jsonb

from app.maps import Maps, MapsUnavailable
from app.lists import segment_list
from app.region import OutsideRegion, in_region
from app.shelter import ExposureTable, build_exposure, shelter_factor, steep_terrain
from app.weather import FORECAST_HOURS, GridPoint, WeatherUnavailable, grid_point
from app.wind import (
    DEFAULT_POWER_20MIN_W,
    DEFAULT_WEIGHT_KG,
    RiderProfile,
    Step,
    Wind,
    build_steps,
    decode_polyline,
    has_steep_descent,
    segment_power,
    wind_effects,
)

log = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(hours=1)
SEEN_RECENTLY = timedelta(hours=1)
STUCK_AFTER = timedelta(minutes=10)

FetchForecasts = Callable[[Iterable[GridPoint], datetime], dict[GridPoint, dict[datetime, Wind]]]


def build_model(polyline: str, average_grade: float, maps: Maps) -> tuple[ExposureTable, bool]:
    """Works out one segment's steps and shelter. `average_grade` is rise over run."""
    points = decode_polyline(polyline)
    # The whole route has to be inside, so no part of it can reach for an outside map tile.
    if not all(in_region(lat, lng) for lat, lng in points):
        raise OutsideRegion("Route is outside the region")
    land_cover, elevation = maps.for_route(points)
    steps = build_steps(points, average_grade, elevation)
    exposure = build_exposure(steps, land_cover, elevation)
    return exposure, steep_terrain(steps, elevation) or has_steep_descent(steps)


def _ensure_models(conn: psycopg.Connection, segment_ids: list[int], maps: Maps) -> None:
    rows = conn.execute(
        """
        SELECT s.id, s.polyline, s.average_grade
        FROM segments s
        LEFT JOIN segment_wind_models m ON m.segment_id = s.id
        WHERE s.id = ANY(%s) AND m.segment_id IS NULL
          AND s.polyline IS NOT NULL AND s.average_grade IS NOT NULL
        """,
        (segment_ids,),
    ).fetchall()
    for row in rows:
        try:
            # Strava gives the average grade as a percentage.
            exposure, less_certain = build_model(row["polyline"], row["average_grade"] / 100, maps)
        except OutsideRegion:
            continue  # Expected for rides elsewhere: these simply have no wind numbers.
        except (ValueError, MapsUnavailable) as error:
            log.warning("No wind model for segment %s: %s", row["id"], error)
            continue
        steps = [[*astuple(step), list(values)] for step, values in exposure.items()]
        conn.execute(
            "INSERT INTO segment_wind_models (segment_id, steps, less_certain) VALUES (%s, %s, %s)",
            (row["id"], Jsonb(steps), less_certain),
        )


def _load_models(conn: psycopg.Connection, segment_ids: list[int]) -> dict[int, ExposureTable]:
    rows = conn.execute(
        "SELECT segment_id, steps FROM segment_wind_models WHERE segment_id = ANY(%s)",
        (segment_ids,),
    ).fetchall()
    return {
        row["segment_id"]: {Step(*step[:5]): tuple(step[5]) for step in row["steps"]}
        for row in rows
    }


def _refresh_forecasts(
    conn: psycopg.Connection, points: set[GridPoint], fetch: FetchForecasts, now: datetime
) -> None:
    fresh = {
        (row["lat"], row["lng"])
        for row in conn.execute(
            "SELECT DISTINCT lat, lng FROM forecasts WHERE fetched_at >= %s",
            (now - UPDATE_INTERVAL,),
        )
    }
    needed = points - fresh
    if not needed:
        return
    try:
        forecasts = fetch(needed, now)
    except WeatherUnavailable as error:
        log.warning("Couldn't refresh forecasts: %s", error)
        return
    with conn.transaction():
        for (lat, lng), hours in forecasts.items():
            conn.execute("DELETE FROM forecasts WHERE lat = %s AND lng = %s", (lat, lng))
            for hour, wind in hours.items():
                conn.execute(
                    """
                    INSERT INTO forecasts (
                        lat, lng, hour, speed_ms, from_degrees, temperature_c, pressure_hpa, fetched_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (lat, lng, hour, wind.speed_ms, wind.from_degrees, wind.temperature_c,
                     wind.pressure_hpa, now),
                )


def _load_forecasts(
    conn: psycopg.Connection, points: set[GridPoint], now: datetime
) -> dict[GridPoint, dict[datetime, Wind]]:
    this_hour = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    forecasts: dict[GridPoint, dict[datetime, Wind]] = {point: {} for point in points}
    rows = conn.execute(
        "SELECT * FROM forecasts WHERE hour >= %s AND hour <= %s",
        (this_hour, this_hour + timedelta(hours=FORECAST_HOURS)),
    )
    for row in rows:
        point = (row["lat"], row["lng"])
        if point in forecasts:
            forecasts[point][row["hour"]] = Wind(
                row["speed_ms"], row["from_degrees"], row["temperature_c"], row["pressure_hpa"]
            )
    return forecasts


def _save_effects(
    conn: psycopg.Connection,
    athlete_id: int,
    segment_id: int,
    table: ExposureTable,
    profile: RiderProfile,
    forecasts: dict[GridPoint, dict[datetime, Wind]],
) -> None:
    steps = list(table)
    points = {step: grid_point(step.lat, step.lng) for step in steps}
    hours = set.intersection(*(set(forecasts[point]) for point in points.values()))
    shelter = shelter_factor(table)
    power = segment_power(steps, profile)
    middle = steps[len(steps) // 2]
    hours = sorted(hours)
    effects = wind_effects(
        steps,
        profile,
        [lambda step, hour=hour: forecasts[points[step]][hour] for hour in hours],
        shelter,
        power,
    )
    with conn.transaction():
        conn.execute(
            "DELETE FROM wind_effects WHERE athlete_id = %s AND segment_id = %s",
            (athlete_id, segment_id),
        )
        for hour, effect in zip(hours, effects):
            shown = forecasts[points[middle]][hour]
            conn.execute(
                """
                INSERT INTO wind_effects (athlete_id, segment_id, hour, percent, speed_ms, from_degrees)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (athlete_id, segment_id, hour, effect.percent, shown.speed_ms, shown.from_degrees),
            )


def rider_profile(row: dict) -> RiderProfile:
    return RiderProfile(
        row["weight_kg"] if row["weight_kg"] is not None else DEFAULT_WEIGHT_KG,
        row["power_20min_w"] if row["power_20min_w"] is not None else DEFAULT_POWER_20MIN_W,
    )


def wind_pending(row: dict, now: datetime) -> bool:
    """Whether a rider's wind effects are about to change, from their `athletes` row."""
    started, updated = row["wind_started_at"], row["wind_updated_at"]
    if row["wind_stale"] or updated is None:
        return True
    # After time away, the stored hours have all passed, and an update is due as soon as
    # they're seen again.
    if now - updated >= UPDATE_INTERVAL:
        return True
    return started is not None and started > updated and now - started < STUCK_AFTER


def update_next_rider(
    conn: psycopg.Connection, maps: Maps, fetch: FetchForecasts, now: datetime, pooled: bool
) -> bool:
    """Updates one rider who's using the app and is due. Returns False if none are."""
    rider = conn.execute(
        """
        SELECT id, weight_kg, power_20min_w FROM athletes
        WHERE last_seen_at >= %(seen)s
          AND (wind_stale OR wind_updated_at IS NULL OR wind_updated_at < %(due)s)
        ORDER BY wind_updated_at NULLS FIRST
        LIMIT 1
        """,
        {"seen": now - SEEN_RECENTLY, "due": now - UPDATE_INTERVAL},
    ).fetchone()
    if rider is None:
        return False

    # Cleared first, so a profile saved during this update triggers another one.
    conn.execute(
        "UPDATE athletes SET wind_stale = FALSE, wind_started_at = %s WHERE id = %s",
        (now, rider["id"]),
    )
    try:
        segment_ids = [row["id"] for row in segment_list(conn, rider["id"], "popular", now, pooled)]
        _ensure_models(conn, segment_ids, maps)
        models = _load_models(conn, segment_ids)
        points = {grid_point(step.lat, step.lng) for table in models.values() for step in table}
        _refresh_forecasts(conn, points, fetch, now)
        forecasts = _load_forecasts(conn, points, now)
        profile = rider_profile(rider)
        for segment_id, table in models.items():
            _save_effects(conn, rider["id"], segment_id, table, profile, forecasts)
    except Exception:
        # Waits for the next hourly update, rather than retrying non-stop.
        log.exception("Wind update failed for rider %s", rider["id"])
    finally:
        conn.execute(
            "UPDATE athletes SET wind_updated_at = %s WHERE id = %s", (now, rider["id"])
        )
    return True
