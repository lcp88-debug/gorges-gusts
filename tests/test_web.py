import re
import time
from urllib.parse import parse_qs, urlparse

import pytest
import responses
from fastapi.testclient import TestClient

from app.main import create_app
from app.strava import API_URL, OAUTH_URL

ATHLETE_ID = 1001
ACCESS = "made-up-access-token"
REFRESH = "made-up-refresh-token"


@pytest.fixture
def app(db, settings):
    return create_app(settings, start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app, follow_redirects=False, base_url="http://localhost") as test_client:
        yield test_client


def token_response():
    return {
        "access_token": ACCESS,
        "refresh_token": REFRESH,
        "expires_at": int(time.time()) + 3600,
        "athlete": {"id": ATHLETE_ID},
    }


def log_in(client, scope="read,activity:read_all"):
    responses.post(f"{OAUTH_URL}/token", json=token_response())
    login = client.get("/login")
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    return client.get("/auth/callback", params={"state": state, "code": "abc", "scope": scope})


def mock_strava():
    responses.get(
        f"{API_URL}/athlete/activities",
        json=[
            {
                "id": 1,
                "sport_type": "Ride",
                "start_date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "trainer": False,
                "manual": False,
                "private": False,
            }
        ],
    )
    responses.get(
        f"{API_URL}/activities/1",
        json={
            "id": 1,
            "sport_type": "Ride",
            "start_date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "segment_efforts": [
                {
                    "id": 11,
                    "start_date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "segment": {"id": 500, "private": False},
                },
                {
                    "id": 12,
                    "start_date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "segment": {"id": 500, "private": False},
                },
                {
                    "id": 13,
                    "start_date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "segment": {"id": 600, "private": False},
                },
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
            "map": {"polyline": "abc"},
            "athlete_count": 12,
            "effort_count": 3456,
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
            "map": {"polyline": "def"},
            "athlete_count": 9000,
            "effort_count": 50000,
        },
    )


def segment_order(html):
    return re.findall(r"<td>(Often &lt;Climb&gt;|Popular Road)</td>", html)


def test_logged_out_home_offers_strava_login_and_explains_data_use(client):
    page = client.get("/").text

    assert "Connect with Strava" in page
    assert "Private rides aren't added" in page


def test_login_redirects_to_strava_with_a_state(client):
    location = client.get("/login").headers["location"]

    assert location.startswith(f"{OAUTH_URL}/authorize")
    assert parse_qs(urlparse(location).query)["state"][0]


def test_callback_rejects_a_mismatched_state(client):
    client.get("/login")

    response = client.get("/auth/callback", params={"state": "wrong", "code": "abc"})

    assert response.status_code == 303
    assert "expired" in client.get("/").text


@responses.activate
def test_callback_requires_the_needed_scope(client, db):
    log_in(client, scope="read")

    assert "permission" in client.get("/").text
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM athletes").fetchone()["n"] == 0


@responses.activate
def test_login_encrypts_tokens_and_queues_a_sync(client, db):
    log_in(client)

    with db.connect() as conn:
        athlete = conn.execute("SELECT * FROM athletes").fetchone()
        assert athlete["id"] == ATHLETE_ID
        assert ACCESS not in athlete["access_token_enc"]
        assert REFRESH not in athlete["refresh_token_enc"]
        assert conn.execute("SELECT status FROM sync_jobs").fetchone()["status"] == "pending"


@responses.activate
def test_first_load_shows_progress_then_both_sorted_lists(app, client):
    log_in(client)

    loading = client.get("/").text
    assert "Checking Strava" in loading
    assert 'http-equiv="refresh"' in loading
    assert "Most attempted" not in loading

    mock_strava()
    assert app.state.worker.run_next_job() is True

    attempted = client.get("/").text
    assert 'http-equiv="refresh"' not in attempted
    assert segment_order(attempted) == ["Often &lt;Climb&gt;", "Popular Road"]
    assert segment_order(client.get("/?sort=popular").text) == ["Popular Road", "Often &lt;Climb&gt;"]


@responses.activate
def test_rows_show_the_agreed_details(app, client):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()

    page = client.get("/").text

    for expected in ("2.0 mi", "5.2%", "3,456", "View on Strava", "https://www.strava.com/segments/500"):
        assert expected in page
    assert "–" in page  # Popular Road has no all-time count.


@responses.activate
def test_unknown_sort_falls_back_to_most_attempted(app, client):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()

    assert segment_order(client.get("/?sort=nonsense").text)[0] == "Often &lt;Climb&gt;"


@responses.activate
def test_refresh_keeps_lists_visible_and_blocks_a_second_refresh(app, client, db):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()

    client.post("/refresh")
    client.post("/refresh")

    page = client.get("/").text
    assert "Most attempted" in page
    assert "disabled" in page
    with db.connect() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM sync_jobs WHERE status = 'pending'").fetchone()["n"]
            == 1
        )


@responses.activate
def test_no_segments_message(app, client):
    log_in(client)
    responses.get(f"{API_URL}/athlete/activities", json=[])
    app.state.worker.run_next_job()

    assert "No segments found" in client.get("/").text


@responses.activate
def test_logout_returns_to_the_login_page(app, client):
    log_in(client)

    client.post("/logout")

    assert "Connect with Strava" in client.get("/").text


@responses.activate
def test_returning_after_data_expired_reloads_instead_of_showing_an_empty_list(app, client, db):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()
    with db.connect() as conn:
        conn.execute("UPDATE athletes SET last_synced_at = now() - interval '7 days'")
        conn.execute("DELETE FROM rides")
        conn.execute("DELETE FROM segments")

    page = client.get("/").text

    assert "Checking Strava" in page
    assert "No segments found" not in page


@responses.activate
def test_failed_first_load_shows_the_error_without_an_empty_list_or_retry(app, client, db):
    log_in(client)
    responses.get(f"{API_URL}/athlete/activities", status=401, json={})
    app.state.worker.run_next_job()

    page = client.get("/").text

    assert "log in with Strava again" in page
    assert "No segments found" not in page
    assert "Most attempted" not in page
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM sync_jobs").fetchone()["n"] == 1
