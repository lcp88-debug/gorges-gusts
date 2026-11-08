import re
import time
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import pytest
import responses
from fastapi.testclient import TestClient

from app.main import create_app
from app.strava import API_URL, OAUTH_URL
from tests.conftest import LOCAL_ROUTE, OUTSIDE_ROUTE

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


@pytest.fixture
def pooled_app(db, settings):
    return create_app(replace(settings, pooled_most_popular=True), start_worker=False)


@pytest.fixture
def pooled_client(pooled_app):
    with TestClient(pooled_app, follow_redirects=False, base_url="http://localhost") as test_client:
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
            "map": {"polyline": LOCAL_ROUTE},
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
            "map": {"polyline": LOCAL_ROUTE},
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
    assert "all time" not in page


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

    assert "No segments in this area" in client.get("/").text


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
    assert "No segments in this area" not in page


@responses.activate
def test_failed_first_load_shows_the_error_without_an_empty_list_or_retry(app, client, db):
    log_in(client)
    responses.get(f"{API_URL}/athlete/activities", status=401, json={})
    app.state.worker.run_next_job()

    page = client.get("/").text

    assert "log in with Strava again" in page
    assert "No segments in this area" not in page
    assert "Most attempted" not in page
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM sync_jobs").fetchone()["n"] == 1


def test_connect_page_does_not_promise_data_is_only_shown_to_you_when_pooling_is_on(pooled_client):
    page = pooled_client.get("/").text

    assert "only shown to you" not in page
    assert "other riders" in page
    assert "turn this off" in page


def test_connect_page_does_not_mention_contributing_when_pooling_is_off(client):
    assert "other riders" not in client.get("/").text


def seed_contributor_segment(db, contributes=True):
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO athletes (id, access_token_enc, refresh_token_enc, expires_at, scope, contributes)
            VALUES (2002, 'x', 'x', 0, 'read', %s)
            """,
            (contributes,),
        )
        conn.execute("INSERT INTO rides VALUES (77, 2002, now(), now())")
        conn.execute(
            """
            INSERT INTO efforts (id, athlete_id, ride_id, segment_id, start_date, shareable, fetched_at)
            VALUES (771, 2002, 77, 700, now(), TRUE, now())
            """
        )
        conn.execute(
            """
            INSERT INTO segments (id, name, polyline, athlete_count, effort_count, fetched_at)
            VALUES (700, 'Their Climb', %s, 50000, 90000, now())
            """,
            (LOCAL_ROUTE,),
        )


def contributes(db):
    with db.connect() as conn:
        return conn.execute(
            "SELECT contributes FROM athletes WHERE id = %s", (ATHLETE_ID,)
        ).fetchone()["contributes"]


@responses.activate
def test_connecting_with_the_notice_on_screen_makes_a_rider_a_contributor(pooled_client, db):
    log_in(pooled_client)

    assert contributes(db) is True


@responses.activate
def test_connecting_without_the_notice_does_not_make_a_rider_a_contributor(client, db):
    log_in(client)

    assert contributes(db) is None


@responses.activate
def test_logging_in_again_keeps_an_opt_out(pooled_client, db):
    log_in(pooled_client)
    pooled_client.post("/contributing/off")

    log_in(pooled_client)

    assert contributes(db) is False


@responses.activate
def test_rider_sees_contributors_segments_on_most_popular_only(pooled_app, pooled_client, db):
    log_in(pooled_client)
    mock_strava()
    pooled_app.state.worker.run_next_job()
    seed_contributor_segment(db)

    assert "Their Climb" in pooled_client.get("/?sort=popular").text
    assert "Their Climb" not in pooled_client.get("/?sort=attempted").text


@responses.activate
def test_off_switch_keeps_contributors_segments_off_most_popular(app, client, db):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()
    seed_contributor_segment(db)

    page = client.get("/?sort=popular").text

    assert "Their Climb" not in page
    assert "/contributing" not in page


@responses.activate
def test_opting_out_and_back_in_from_the_page(pooled_app, pooled_client, db):
    log_in(pooled_client)
    mock_strava()
    pooled_app.state.worker.run_next_job()
    assert 'action="/contributing/off"' in pooled_client.get("/").text

    pooled_client.post("/contributing/off")

    assert contributes(db) is False
    assert 'action="/contributing/on"' in pooled_client.get("/").text

    pooled_client.post("/contributing/on")

    assert contributes(db) is True


def test_changing_the_setting_when_logged_out_does_nothing(pooled_client):
    response = pooled_client.post("/contributing/off")

    assert response.status_code == 303


@responses.activate
def test_visit_fetches_details_that_expired_before_the_riders_own_rides(app, client, db):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()
    with db.connect() as conn:
        conn.execute("DELETE FROM segments WHERE id = 600")

    client.get("/")
    assert app.state.worker.run_next_job() is True

    assert "Popular Road" in client.get("/").text


@responses.activate
def test_visit_with_nothing_missing_does_not_queue_a_sync(app, client):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()

    client.get("/")

    assert app.state.worker.run_next_job() is False


@responses.activate
def test_rides_expiring_soon_after_a_refresh_trigger_a_new_sync_not_an_empty_list(app, client, db):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()
    client.post("/refresh")
    app.state.worker.run_next_job()
    with db.connect() as conn:
        conn.execute("UPDATE rides SET fetched_at = now() - interval '7 days'")
    app.state.worker.purge_if_due()

    page = client.get("/").text

    assert "No segments in this area" not in page
    assert "Checking Strava" in page
    assert app.state.worker.run_next_job() is True
    assert "Popular Road" in client.get("/").text


@responses.activate
def test_cleanup_with_nothing_expired_does_not_trigger_a_sync(app, client):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()
    app.state.worker.purge_if_due()

    client.get("/")

    assert app.state.worker.run_next_job() is False


def add_wind_numbers(db, segment_id, percents, less_certain=False):
    """Stores made-up wind effects, one per hour starting from this hour."""
    this_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
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
                (ATHLETE_ID, segment_id, this_hour + timedelta(hours=index), percent),
            )


@responses.activate
def test_rows_show_now_best_and_hours(app, client, db):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()
    add_wind_numbers(db, 500, [3.4, -8.6])

    page = client.get("/").text

    assert "+3%" in page and "−9%" in page
    assert "10 mph from SW" in page
    assert "<summary>Hours</summary>" in page
    assert "Less certain" not in page
    assert "estimates from forecast wind" in page
    assert "Open-Meteo" in page and "ESA WorldCover" in page and "Copernicus DEM" in page


@responses.activate
def test_less_certain_numbers_are_marked(app, client, db):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()
    add_wind_numbers(db, 500, [3.0], less_certain=True)

    assert 'title="Less certain' in client.get("/").text


@responses.activate
def test_clicking_a_wind_heading_sorts_the_current_list(app, client, db):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()
    add_wind_numbers(db, 500, [5.0, 4.0])
    add_wind_numbers(db, 600, [-2.0, 9.0])

    assert segment_order(client.get("/?sort=attempted").text) == ["Often &lt;Climb&gt;", "Popular Road"]
    page = client.get("/?sort=attempted&by=now").text
    assert segment_order(page) == ["Popular Road", "Often &lt;Climb&gt;"]
    assert 'href="/?sort=attempted&amp;by=best"' in page
    assert segment_order(client.get("/?sort=attempted&by=nonsense").text) == [
        "Often &lt;Climb&gt;",
        "Popular Road",
    ]


@responses.activate
def test_missing_wind_shows_a_dash_and_no_hours(app, client):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()

    page = client.get("/").text

    assert "<summary>Hours</summary>" not in page
    assert page.count("<td>–</td>") == 4


@responses.activate
def test_profile_is_saved_and_asks_for_new_numbers(app, client, db):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()
    with db.connect() as conn:
        conn.execute("UPDATE athletes SET wind_stale = FALSE")

    response = client.post("/profile", data={"weight_kg": "68.5", "power_20min_w": "275"})

    assert response.status_code == 303
    with db.connect() as conn:
        row = conn.execute("SELECT weight_kg, power_20min_w, wind_stale FROM athletes").fetchone()
    assert row == {"weight_kg": 68.5, "power_20min_w": 275, "wind_stale": True}
    page = client.get("/").text
    assert 'value="68.5"' in page and 'value="275.0"' in page


@responses.activate
def test_blank_profile_returns_to_standard_values(app, client, db):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()
    client.post("/profile", data={"weight_kg": "68", "power_20min_w": "275"})

    client.post("/profile", data={"weight_kg": "", "power_20min_w": ""})

    with db.connect() as conn:
        row = conn.execute("SELECT weight_kg, power_20min_w FROM athletes").fetchone()
    assert row == {"weight_kg": None, "power_20min_w": None}
    page = client.get("/").text
    assert 'placeholder="75"' in page and 'placeholder="200"' in page


@responses.activate
@pytest.mark.parametrize(
    "weight, power", [("5", "200"), ("70", "5000"), ("heavy", "200"), ("nan", "200"), ("70", "inf")]
)
def test_invalid_profile_changes_nothing_and_explains(app, client, db, weight, power):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()

    client.post("/profile", data={"weight_kg": weight, "power_20min_w": power})

    with db.connect() as conn:
        row = conn.execute("SELECT weight_kg, power_20min_w FROM athletes").fetchone()
    assert row == {"weight_kg": None, "power_20min_w": None}
    assert "Nothing was changed" in client.get("/").text
    assert "Nothing was changed" not in client.get("/").text


def test_profile_needs_a_login(client, db):
    assert client.post("/profile", data={"weight_kg": "70"}).status_code == 303


@responses.activate
def test_segments_outside_the_region_do_not_appear(app, client, db):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()
    with db.connect() as conn:
        conn.execute("UPDATE segments SET polyline = %s WHERE id = 600", (OUTSIDE_ROUTE,))

    for sort in ("attempted", "popular"):
        assert segment_order(client.get(f"/?sort={sort}").text) == ["Often &lt;Climb&gt;"]
