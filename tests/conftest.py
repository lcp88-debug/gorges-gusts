import os
from dataclasses import replace
from datetime import datetime, timezone

import pytest
import responses
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from dotenv import load_dotenv

from app.config import Settings
from app.db import Database
from app.main import create_app

load_dotenv()

def pytest_configure(config):
    config.addinivalue_line(
        "markers", "setting_off: run the page with the fastest-time setting off (test_fastest_time.py)"
    )


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


@pytest.fixture
def fake():
    return FakeStrava()


@pytest.fixture
def db():
    database = Database(os.environ["TEST_DATABASE_URL"])
    database.init_schema()
    with database.connect() as conn:
        conn.execute("TRUNCATE athletes, rides, efforts, segments, sync_jobs, forecasts, app_settings_seen CASCADE")
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


# The web app, logged-out and logged-in. Strava is never contacted: logins and syncs use
# made-up replies from tests/helpers.py.


@pytest.fixture
def app(db, settings):
    return create_app(settings, start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app, follow_redirects=False, base_url="http://localhost") as test_client:
        yield test_client


@pytest.fixture
def pooled_app(db, settings):
    return create_app(replace(settings, pooled_explore=True), start_worker=False)


@pytest.fixture
def pooled_client(pooled_app):
    with TestClient(pooled_app, follow_redirects=False, base_url="http://localhost") as test_client:
        yield test_client


@pytest.fixture
def synced(app, client, db):
    """A logged-in rider with three segments: 500 and 600 from Strava, 700 added here."""
    from tests.helpers import add_third_segment, log_in, mock_strava  # helpers imports conftest

    # Started here rather than with @responses.activate, so the made-up Strava
    # replies also cover this setup and any later logins in the test.
    responses.start()
    try:
        log_in(client)
        mock_strava()
        app.state.worker.run_next_job()
        add_third_segment(db)
        yield client
    finally:
        responses.stop()
        responses.reset()
