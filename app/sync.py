from collections.abc import Callable
from datetime import datetime, timezone

import psycopg
from psycopg import sql

from app.config import RIDE_WINDOW
from app.db import delete_unused_segments
from app.lists import parse_time
from app.strava import NotFound, StravaClient

Progress = Callable[[str, int, int], None]


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def is_counted_ride(activity: dict) -> bool:
    """Outdoor, GPS-recorded, non-private road rides."""
    return (
        activity.get("sport_type") == "Ride"
        and not activity.get("trainer")
        and not activity.get("manual")
        and not activity.get("private")
    )


def run_sync(
    conn: psycopg.Connection,
    client: StravaClient,
    athlete_id: int,
    now: datetime,
    progress: Progress = lambda phase, done, total: None,
    fastest_time: bool = False,
) -> None:
    """Bring one athlete's cached rides, efforts and segments up to date.

    Each ride and segment is saved as soon as it's fetched, so running this again
    after a pause or restart continues from where it stopped.
    """
    cutoff = now - RIDE_WINDOW
    progress("rides", 0, 0)
    rides = {
        activity["id"]: activity
        for activity in client.list_activities(after=cutoff)
        if is_counted_ride(activity) and _parse_time(activity["start_date"]) >= cutoff
    }

    # Anything no longer listed was deleted, made private, retyped or aged out.
    conn.execute(
        "DELETE FROM rides WHERE athlete_id = %s AND NOT (id = ANY(%s))",
        (athlete_id, list(rides)),
    )

    cached = {
        row["id"]
        for row in conn.execute("SELECT id FROM rides WHERE athlete_id = %s", (athlete_id,))
    }
    missing_rides = [ride_id for ride_id in rides if ride_id not in cached]
    for done, ride_id in enumerate(missing_rides):
        progress("rides", done, len(missing_rides))
        try:
            activity = client.get_activity(ride_id)
        except NotFound:
            continue
        if is_counted_ride(activity):
            _save_ride(conn, athlete_id, activity, now)
    progress("rides", len(missing_rides), len(missing_rides))

    missing_segments = [
        row["segment_id"]
        for row in conn.execute(
            """
            SELECT DISTINCT e.segment_id
            FROM efforts e
            LEFT JOIN segments s ON s.id = e.segment_id
            WHERE e.athlete_id = %s AND s.id IS NULL
            ORDER BY e.segment_id
            """,
            (athlete_id,),
        )
    ]
    for done, segment_id in enumerate(missing_segments):
        progress("segments", done, len(missing_segments))
        try:
            segment = client.get_segment(segment_id)
        except NotFound:
            segment = None
        if segment is None or segment.get("private"):
            conn.execute(
                "DELETE FROM efforts WHERE athlete_id = %s AND segment_id = %s",
                (athlete_id, segment_id),
            )
        else:
            _save_segment(conn, segment, now, fastest_time)
    progress("segments", len(missing_segments), len(missing_segments))

    delete_unused_segments(conn)


def _save_ride(conn: psycopg.Connection, athlete_id: int, activity: dict, now: datetime) -> None:
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO rides (id, athlete_id, start_date, fetched_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET fetched_at = EXCLUDED.fetched_at
            """,
            (activity["id"], athlete_id, _parse_time(activity["start_date"]), now),
        )
        for effort in activity.get("segment_efforts", []):
            if effort["segment"].get("private"):
                continue
            conn.execute(
                """
                INSERT INTO efforts (
                    id, athlete_id, ride_id, segment_id, start_date, elapsed_s, shareable,
                    fetched_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    elapsed_s = EXCLUDED.elapsed_s,
                    shareable = EXCLUDED.shareable,
                    fetched_at = EXCLUDED.fetched_at
                """,
                (
                    effort["id"],
                    athlete_id,
                    activity["id"],
                    effort["segment"]["id"],
                    _parse_time(effort["start_date"]),
                    effort.get("elapsed_time"),
                    effort.get("visibility") == "everyone",
                    now,
                ),
            )


def read_overall_time(text: object) -> int | None:
    """Strava's overall time text, such as "3:49" or "1:02:03", as seconds. Blank if unreadable."""
    if not isinstance(text, str):
        return None
    try:
        return int(parse_time(text.strip()))
    except ValueError:
        return None


def segment_details(segment: dict, fastest_time: bool = False) -> dict:
    """The segment details we keep from Strava's segment reply, keyed by column name.

    A missing value is stored as blank. Fastest time is only read with the setting on,
    and Strava's reply says who set it, which is never kept.
    """
    return {
        "id": segment["id"],
        "name": segment["name"],
        "distance_m": segment.get("distance"),
        "average_grade": segment.get("average_grade"),
        "polyline": (segment.get("map") or {}).get("polyline"),
        "athlete_count": segment.get("athlete_count"),
        "effort_count": segment.get("effort_count"),
        "town": segment.get("city") or None,
        "maximum_grade": segment.get("maximum_grade"),
        "climb_category": segment.get("climb_category"),
        "elevation_gain_m": segment.get("total_elevation_gain"),
        "fastest_time_s": (
            read_overall_time((segment.get("xoms") or {}).get("overall")) if fastest_time else None
        ),
    }


def _save_segment(
    conn: psycopg.Connection, segment: dict, now: datetime, fastest_time: bool = False
) -> None:
    details = {**segment_details(segment, fastest_time), "fetched_at": now}
    columns = sql.SQL(", ").join(map(sql.Identifier, details))
    updates = sql.SQL(", ").join(
        sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(column))
        for column in details
        if column != "id"
    )
    conn.execute(
        sql.SQL(
            "INSERT INTO segments ({columns}) VALUES ({values}) "
            "ON CONFLICT (id) DO UPDATE SET {updates}"
        ).format(
            columns=columns,
            values=sql.SQL(", ").join(sql.Placeholder() * len(details)),
            updates=updates,
        ),
        list(details.values()),
    )
