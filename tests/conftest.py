import os
from datetime import datetime, timezone
from html.parser import HTMLParser

import pytest
from cryptography.fernet import Fernet
from dotenv import load_dotenv

from app.config import Settings
from app.db import Database

load_dotenv()

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
ATHLETE = 1001
OTHER_ATHLETE = 2002


def encode_route(points):
    """Encodes made-up (latitude, longitude) points as a polyline."""

    def encode_value(value):
        number = round(value * 1e5)
        number = ~(number << 1) if number < 0 else number << 1
        text = ""
        while number >= 0x20:
            text += chr((0x20 | (number & 0x1F)) + 63)
            number >>= 5
        return text + chr(number + 63)

    text, last_lat, last_lng = "", 0.0, 0.0
    for lat, lng in points:
        text += encode_value(lat - last_lat) + encode_value(lng - last_lng)
        last_lat, last_lng = lat, lng
    return text


LOCAL_ROUTE = encode_route([(42.4, -76.5), (42.41, -76.5)])
OUTSIDE_ROUTE = encode_route([(44.0, -120.0), (44.01, -120.0)])


class FakeStrava:
    """Stands in for StravaClient, serving made-up data and recording its calls."""

    def __init__(self):
        self.activities: list[dict] = []
        self.details: dict[int, dict] = {}
        self.segments: dict[int, dict] = {}
        self.calls: list[str] = []
        self.fail_on: dict[str, Exception] = {}

    def _record(self, call: str) -> None:
        self.calls.append(call)
        if call in self.fail_on:
            raise self.fail_on.pop(call)

    def list_activities(self, after):
        self._record("list")
        return list(self.activities)

    def get_activity(self, activity_id):
        self._record(f"activity:{activity_id}")
        return self.details[activity_id]

    def get_segment(self, segment_id):
        self._record(f"segment:{segment_id}")
        return self.segments[segment_id]

    def add_ride(
        self, ride_id, segment_ids, when="2026-10-20T10:00:00Z", visibility="everyone", **overrides
    ):
        activity = {
            "id": ride_id,
            "sport_type": "Ride",
            "start_date": when,
            "trainer": False,
            "manual": False,
            "private": False,
            **overrides,
        }
        self.activities.append(activity)
        self.details[ride_id] = {
            **activity,
            "segment_efforts": [
                {
                    "id": ride_id * 1000 + index,
                    "start_date": when,
                    "visibility": visibility,
                    "segment": {"id": segment_id, "private": False},
                }
                for index, segment_id in enumerate(segment_ids)
            ],
        }

    def add_segment(self, segment_id, name, athletes=10, efforts=20, mine=5, **overrides):
        self.segments[segment_id] = {
            "id": segment_id,
            "name": name,
            "distance": 1609.34,
            "average_grade": 4.5,
            "private": False,
            "map": {"polyline": LOCAL_ROUTE},
            "athlete_count": athletes,
            "effort_count": efforts,
            "athlete_segment_stats": {"effort_count": mine},
            **overrides,
        }


class _Table(HTMLParser):
    """Reads the list's headings and the text of each row's own cells, skipping hour views."""

    def __init__(self):
        super().__init__()
        self.headings: list[str] = []
        self.rows: list[list[str]] = []
        self.row: list[str] = []
        self.depth = 0
        self.cell: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.depth += 1
        elif self.depth == 1 and tag == "tr":
            self.row = []
        elif self.depth == 1 and tag in ("th", "td"):
            self.cell = ""

    def handle_endtag(self, tag):
        if tag == "table":
            self.depth -= 1
        elif self.depth == 1 and tag in ("th", "td"):
            self.row.append(" ".join(self.cell.split()))
            self.cell = None
        elif self.depth == 1 and tag == "tr":
            if self.headings:
                self.rows.append(self.row)
            else:
                self.headings = self.row

    def handle_data(self, data):
        if self.depth == 1 and self.cell is not None:
            self.cell += data


def table(html):
    """Each row as {heading: cell text}, in page order. Sort arrows are left out."""
    parser = _Table()
    parser.feed(html)
    headings = [heading.rstrip(" ↑↓") for heading in parser.headings]
    return [dict(zip(headings, row)) for row in parser.rows]


@pytest.fixture
def fake():
    return FakeStrava()


@pytest.fixture
def db():
    database = Database(os.environ["TEST_DATABASE_URL"])
    database.init_schema()
    with database.connect() as conn:
        conn.execute("TRUNCATE athletes, rides, efforts, segments, sync_jobs, forecasts CASCADE")
    return database


@pytest.fixture
def conn(db):
    with db.connect() as connection:
        for athlete_id in (ATHLETE, OTHER_ATHLETE):
            connection.execute(
                """
                INSERT INTO athletes (id, access_token_enc, refresh_token_enc, expires_at, scope)
                VALUES (%s, 'x', 'x', 0, 'read,activity:read_all')
                """,
                (athlete_id,),
            )
        yield connection


@pytest.fixture
def settings():
    return Settings(
        database_url=os.environ["TEST_DATABASE_URL"],
        strava_client_id="12345",
        strava_client_secret="test-secret",
        session_secret="test-session-secret",
        token_encryption_key=Fernet.generate_key().decode(),
    )
