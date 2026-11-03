import os
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet
from dotenv import load_dotenv

from app.config import Settings
from app.db import Database

load_dotenv()

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
ATHLETE = 1001
OTHER_ATHLETE = 2002


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
            "map": {"polyline": f"poly{segment_id}"},
            "athlete_count": athletes,
            "effort_count": efforts,
            "athlete_segment_stats": {"effort_count": mine},
            **overrides,
        }


@pytest.fixture
def fake():
    return FakeStrava()


@pytest.fixture
def db():
    database = Database(os.environ["TEST_DATABASE_URL"])
    database.init_schema()
    with database.connect() as conn:
        conn.execute("TRUNCATE athletes, rides, efforts, segments, sync_jobs CASCADE")
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
