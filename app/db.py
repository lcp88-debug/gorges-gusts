from datetime import datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.config import CACHE_TTL

SCHEMA = Path(__file__).with_name("schema.sql")


class Database:
    def __init__(self, url: str):
        self.url = url

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self.url, row_factory=dict_row, autocommit=True)

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(SCHEMA.read_text())


def delete_unused_segments(conn: psycopg.Connection) -> None:
    """Delete segment details that no athlete's cached rides include."""
    conn.execute(
        """
        DELETE FROM segments s
        WHERE NOT EXISTS (SELECT 1 FROM efforts e WHERE e.segment_id = s.id)
        """
    )


def purge_expired(conn: psycopg.Connection, now: datetime) -> None:
    """Delete Strava data past its retention limit, for every athlete."""
    cutoff = now - CACHE_TTL
    with conn.transaction():
        # Refresh doesn't renew cached rides, so an athlete can lose rides soon after a
        # sync. Clearing this makes their next visit sync again.
        conn.execute(
            """
            UPDATE athletes SET last_synced_at = NULL
            WHERE id IN (SELECT athlete_id FROM rides WHERE fetched_at < %s)
            """,
            (cutoff,),
        )
        conn.execute("DELETE FROM rides WHERE fetched_at < %s", (cutoff,))
        conn.execute("DELETE FROM segments WHERE fetched_at < %s", (cutoff,))
        last_hour = now - timedelta(hours=1)
        conn.execute("DELETE FROM wind_effects WHERE hour < %s", (last_hour,))
        conn.execute(
            "DELETE FROM forecasts WHERE hour < %s OR fetched_at < %s", (last_hour, cutoff)
        )
        delete_unused_segments(conn)
