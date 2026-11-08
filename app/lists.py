from datetime import datetime, timedelta, timezone

import psycopg

from app.config import RIDE_WINDOW
from app.region import route_in_region

SORT_ORDERS = {
    "attempted": "recent_efforts DESC, lower(s.name), s.id",
    "popular": "s.athlete_count DESC NULLS LAST, lower(s.name), s.id",
}
DEFAULT_SORT = "attempted"
WIND_SORTS = ("now", "best")
FORECAST_HOURS = 48


def has_missing_details(conn: psycopg.Connection, athlete_id: int) -> bool:
    """Whether any of the athlete's cached rides include a segment with no details."""
    return conn.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM efforts e
            LEFT JOIN segments s ON s.id = e.segment_id
            WHERE e.athlete_id = %s AND s.id IS NULL
        ) AS missing
        """,
        (athlete_id,),
    ).fetchone()["missing"]


def segment_list(
    conn: psycopg.Connection, athlete_id: int, sort: str, now: datetime, pooled: bool = False
) -> list[dict]:
    """An athlete's segments from their last 90 days of rides, sorted by `sort`.

    With `pooled`, Most Popular also includes segments from contributors' rides.
    Most Attempted always stays limited to the athlete's own segments.
    Segments with any part of their route outside the region are left out of both.
    """
    order_by = SORT_ORDERS[sort]  # Keys are fixed above, so this is safe in the query.
    rows = conn.execute(
        f"""
        SELECT s.polyline, s.id, s.name, s.distance_m, s.average_grade, s.athlete_count, s.effort_count,
               COUNT(e.id) FILTER (WHERE e.athlete_id = %(athlete_id)s) AS recent_efforts
        FROM segments s
        JOIN efforts e ON e.segment_id = s.id
        JOIN athletes a ON a.id = e.athlete_id
        WHERE e.start_date >= %(cutoff)s
          AND (e.athlete_id = %(athlete_id)s OR (%(pooled)s AND a.contributes IS TRUE AND e.shareable))
        GROUP BY s.id
        ORDER BY {order_by}
        """,
        {
            "athlete_id": athlete_id,
            "cutoff": now - RIDE_WINDOW,
            "pooled": pooled and sort == "popular",
        },
    ).fetchall()
    return [
        {key: value for key, value in row.items() if key != "polyline"}
        for row in rows
        if route_in_region(row["polyline"])
    ]


def add_wind(
    conn: psycopg.Connection, athlete_id: int, segments: list[dict], now: datetime
) -> None:
    """Adds each segment's `wind_now`, `wind_best`, `wind_hours` and `less_certain`."""
    this_hour = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    ids = [segment["id"] for segment in segments]
    hours: dict[int, list[dict]] = {}
    for row in conn.execute(
        """
        SELECT segment_id, hour, percent, speed_ms, from_degrees FROM wind_effects
        WHERE athlete_id = %s AND segment_id = ANY(%s) AND hour >= %s AND hour <= %s
        ORDER BY hour
        """,
        (athlete_id, ids, this_hour, this_hour + timedelta(hours=FORECAST_HOURS)),
    ):
        hours.setdefault(row["segment_id"], []).append(row)
    less_certain = {
        row["segment_id"]
        for row in conn.execute(
            "SELECT segment_id FROM segment_wind_models WHERE segment_id = ANY(%s) AND less_certain",
            (ids,),
        )
    }
    for segment in segments:
        rows = hours.get(segment["id"], [])
        segment["wind_hours"] = rows
        segment["wind_now"] = next((row for row in rows if row["hour"] == this_hour), None)
        # min() keeps the earliest of equal hours.
        segment["wind_best"] = min(rows, key=lambda row: row["percent"], default=None)
        segment["less_certain"] = segment["id"] in less_certain


def sort_by_wind(segments: list[dict], by: str) -> list[dict]:
    """Biggest saving first, with segments that have no number last. Keeps the order of ties."""
    key = f"wind_{by}"
    return sorted(
        segments,
        key=lambda segment: (
            segment[key] is None,
            segment[key]["percent"] if segment[key] is not None else 0,
        ),
    )
