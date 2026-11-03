from datetime import datetime

import psycopg

from app.config import RIDE_WINDOW

SORT_ORDERS = {
    "attempted": "recent_efforts DESC, lower(s.name), s.id",
    "popular": "s.athlete_count DESC NULLS LAST, lower(s.name), s.id",
}
DEFAULT_SORT = "attempted"


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
    """
    order_by = SORT_ORDERS[sort]  # Keys are fixed above, so this is safe in the query.
    return conn.execute(
        f"""
        SELECT s.id, s.name, s.distance_m, s.average_grade, s.athlete_count, s.effort_count,
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
