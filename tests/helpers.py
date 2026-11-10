"""Made-up Strava replies and data for the web tests. Never contacts Strava."""

import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import responses

from app.strava import API_URL, OAUTH_URL
from tests.conftest import LOCAL_ROUTE

ATHLETE_ID = 1001
ACCESS = "made-up-access-token"
REFRESH = "made-up-refresh-token"


def token_response(athlete_id=ATHLETE_ID):
    return {
        "access_token": ACCESS,
        "refresh_token": REFRESH,
        "expires_at": int(time.time()) + 3600,
        "athlete": {"id": athlete_id},
    }


def log_in(client, scope="read,activity:read_all", athlete_id=ATHLETE_ID):
    responses.post(f"{OAUTH_URL}/token", json=token_response(athlete_id))
    login = client.get("/login")
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    return client.get("/auth/callback", params={"state": state, "code": "abc", "scope": scope})


def mock_strava():
    """One ride today with segment 500 attempted twice and 600 once."""
    today = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    responses.get(
        f"{API_URL}/athlete/activities",
        json=[{"id": 1, "sport_type": "Ride", "start_date": today, "trainer": False, "manual": False, "private": False}],
    )
    responses.get(
        f"{API_URL}/activities/1",
        json={
            "id": 1,
            "sport_type": "Ride",
            "start_date": today,
            "segment_efforts": [
                {"id": 11, "start_date": today, "segment": {"id": 500, "private": False}},
                {"id": 12, "start_date": today, "elapsed_time": 3723, "segment": {"id": 500, "private": False}},
                {"id": 13, "start_date": today, "segment": {"id": 600, "private": False}},
            ],
        },
    )
    responses.get(
        f"{API_URL}/segments/500",
        json={
            "id": 500,
            "name": "Often <Climb>",
            "distance": 3218.68,
            "average_grade": 5.25,
            "private": False,
            "map": {"polyline": LOCAL_ROUTE},
            "athlete_count": 12,
            "effort_count": 3456,
            "city": "Ithaca",
            "maximum_grade": 11.04,
            "climb_category": 5,
            "total_elevation_gain": 152.4,
            "athlete_segment_stats": {"effort_count": 7},
        },
    )
    responses.get(
        f"{API_URL}/segments/600",
        json={
            "id": 600,
            "name": "Popular Road",
            "distance": 1609.34,
            "average_grade": 1.0,
            "private": False,
            "map": {"polyline": LOCAL_ROUTE},
            "athlete_count": 9000,
            "effort_count": 50000,
        },
    )


def add_third_segment(db, attempts=1):
    """Adds made-up segment 700 to the rider's ride, attempted `attempts` times."""
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO segments (id, name, distance_m, average_grade, polyline, athlete_count,
                                  effort_count, fetched_at)
            VALUES (700, 'Third Hill', 800, 2.0, %s, 40, 100, now())
            """,
            (LOCAL_ROUTE,),
        )
        for index in range(attempts):
            conn.execute(
                """
                INSERT INTO efforts (id, athlete_id, ride_id, segment_id, start_date, fetched_at)
                VALUES (%s, 1001, 1, 700, now(), now())
                """,
                (7000 + index,),
            )


def set_column(db, column, values):
    with db.connect() as conn:
        for segment_id, value in values.items():
            conn.execute(f"UPDATE segments SET {column} = %s WHERE id = %s", (value, segment_id))


def this_hour():
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


def hour(offset):
    """An hour from now as the API writes it."""
    return (this_hour() + timedelta(hours=offset)).isoformat()


def add_wind_numbers(db, segment_id, percents, less_certain=False):
    """Stores made-up wind effects, one per hour starting from this hour."""
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO segment_wind_models (segment_id, steps, less_certain) VALUES (%s, '[]', %s)",
            (segment_id, less_certain),
        )
        for index, percent in enumerate(percents):
            conn.execute(
                """
                INSERT INTO wind_effects (athlete_id, segment_id, hour, percent, speed_ms, from_degrees)
                VALUES (%s, %s, %s, %s, 4.4704, 225)
                """,
                (ATHLETE_ID, segment_id, this_hour() + timedelta(hours=index), percent),
            )


def segments(client, kind="my_segments"):
    """The list's segments by id, as the API sends them."""
    return {segment["id"]: segment for segment in client.get(f"/api/lists/{kind}").json()["segments"]}


def names(client, kind="my_segments"):
    """The names on a list, in the name order the API sends them in."""
    return [segment["name"] for segment in client.get(f"/api/lists/{kind}").json()["segments"]]


def saved_settings(db, kind="my_segments", athlete_id=ATHLETE_ID):
    with db.connect() as conn:
        return conn.execute(
            "SELECT sort_field, sort_direction, filters FROM list_settings"
            " WHERE athlete_id = %s AND list_kind = %s",
            (athlete_id, kind),
        ).fetchone()
