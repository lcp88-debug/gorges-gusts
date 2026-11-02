from datetime import datetime
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


def purge_expired(conn: psycopg.Connection, now: datetime) -> None:
    """Delete Strava data past its retention limit, for every athlete."""
    cutoff = now - CACHE_TTL
    with conn.transaction():
        conn.execute("DELETE FROM rides WHERE fetched_at < %s", (cutoff,))
        conn.execute("DELETE FROM segments WHERE fetched_at < %s", (cutoff,))
