from collections.abc import Callable
from datetime import datetime, timezone

import psycopg

from app.config import RIDE_WINDOW
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
            LEFT JOIN segments s ON s.athlete_id = e.athlete_id AND s.id = e.segment_id
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
            _save_segment(conn, athlete_id, segment, now)
    progress("segments", len(missing_segments), len(missing_segments))

    conn.execute(
        """
        DELETE FROM segments s
        WHERE s.athlete_id = %s AND NOT EXISTS (
            SELECT 1 FROM efforts e WHERE e.athlete_id = s.athlete_id AND e.segment_id = s.id
        )
        """,
        (athlete_id,),
    )


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
                INSERT INTO efforts (id, athlete_id, ride_id, segment_id, start_date, fetched_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET fetched_at = EXCLUDED.fetched_at
                """,
                (
                    effort["id"],
                    athlete_id,
                    activity["id"],
                    effort["segment"]["id"],
                    _parse_time(effort["start_date"]),
                    now,
                ),
            )


def _save_segment(conn: psycopg.Connection, athlete_id: int, segment: dict, now: datetime) -> None:
    conn.execute(
        """
        INSERT INTO segments (
            athlete_id, id, name, distance_m, average_grade, polyline,
            athlete_count, effort_count, all_time_efforts, fetched_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (athlete_id, id) DO UPDATE SET
            name = EXCLUDED.name,
            distance_m = EXCLUDED.distance_m,
            average_grade = EXCLUDED.average_grade,
            polyline = EXCLUDED.polyline,
            athlete_count = EXCLUDED.athlete_count,
            effort_count = EXCLUDED.effort_count,
            all_time_efforts = EXCLUDED.all_time_efforts,
            fetched_at = EXCLUDED.fetched_at
        """,
        (
            athlete_id,
            segment["id"],
            segment["name"],
            segment.get("distance"),
            segment.get("average_grade"),
            (segment.get("map") or {}).get("polyline"),
            segment.get("athlete_count"),
            segment.get("effort_count"),
            (segment.get("athlete_segment_stats") or {}).get("effort_count"),
            now,
        ),
    )
