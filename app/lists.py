from datetime import datetime

import psycopg

from app.config import RIDE_WINDOW

SORT_ORDERS = {
    "attempted": "recent_efforts DESC, lower(s.name), s.id",
    "popular": "s.athlete_count DESC NULLS LAST, lower(s.name), s.id",
}
DEFAULT_SORT = "attempted"


def segment_list(
    conn: psycopg.Connection, athlete_id: int, sort: str, now: datetime
) -> list[dict]:
    """One athlete's segments from their last 90 days of rides, sorted by `sort`."""
    order_by = SORT_ORDERS[sort]  # Keys are fixed above, so this is safe in the query.
    return conn.execute(
        f"""
        SELECT s.id, s.name, s.distance_m, s.average_grade, s.athlete_count,
               s.effort_count, s.all_time_efforts, COUNT(e.id) AS recent_efforts
        FROM segments s
        JOIN efforts e ON e.athlete_id = s.athlete_id AND e.segment_id = s.id
        WHERE s.athlete_id = %s AND e.start_date >= %s
        GROUP BY s.athlete_id, s.id
        ORDER BY {order_by}
        """,
        (athlete_id, now - RIDE_WINDOW),
    ).fetchall()
