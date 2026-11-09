"""Logging in with Strava, the app's page, and what each rider's lists hold."""

from urllib.parse import parse_qs, urlparse

import pytest
import responses
from fastapi.testclient import TestClient

from app.main import create_app
from app.strava import API_URL, OAUTH_URL
from tests.conftest import LOCAL_ROUTE, OUTSIDE_ROUTE
from tests.helpers import ACCESS, ATHLETE_ID, REFRESH, log_in, mock_strava, names


# The page


def test_the_page_is_the_built_react_app(db, settings, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root></div>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    with TestClient(create_app(settings, start_worker=False, web_build=tmp_path)) as client:
        page = client.get("/")
        assert page.status_code == 200 and "<div id=root>" in page.text
        assert page.headers["cache-control"] == "no-cache"
        assert client.get("/assets/app.js").text == "console.log(1)"


def test_without_a_build_the_page_says_how_to_make_one(db, settings, tmp_path):
    with TestClient(create_app(settings, start_worker=False, web_build=tmp_path)) as client:
        page = client.get("/")
        assert page.status_code == 503 and "start.sh" in page.text


# Logging in


def test_login_redirects_to_strava_with_a_state(client):
    location = client.get("/login").headers["location"]

    assert location.startswith(f"{OAUTH_URL}/authorize")
    assert parse_qs(urlparse(location).query)["state"][0]


def test_callback_rejects_a_mismatched_state(client):
    client.get("/login")

    response = client.get("/auth/callback", params={"state": "wrong", "code": "abc"})

    assert response.status_code == 303
    assert "expired" in client.get("/api/state").json()["login_error"]


def test_a_cancelled_login_says_so(client):
    login = client.get("/login")
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

    client.get("/auth/callback", params={"state": state, "error": "access_denied"})

    assert client.get("/api/state").json()["login_error"] == "Strava login was cancelled."


@responses.activate
def test_callback_requires_the_needed_scope(client, db):
    log_in(client, scope="read")

    assert "permission" in client.get("/api/state").json()["login_error"]
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


# Lists


@responses.activate
def test_both_lists_hold_the_riders_segments_once_synced(app, client):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()

    assert names(client, "my_segments") == ["Often <Climb>", "Popular Road"]
    assert names(client, "explore") == ["Often <Climb>", "Popular Road"]


@responses.activate
def test_a_rider_with_no_rides_has_empty_lists(app, client):
    log_in(client)
    responses.get(f"{API_URL}/athlete/activities", json=[])
    app.state.worker.run_next_job()

    assert client.get("/api/state").json()["show_lists"] is True
    assert names(client) == []


@responses.activate
def test_segments_outside_the_region_do_not_appear(app, client, db):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()
    with db.connect() as conn:
        conn.execute("UPDATE segments SET polyline = %s WHERE id = 600", (OUTSIDE_ROUTE,))

    for kind in ("my_segments", "explore"):
        assert names(client, kind) == ["Often <Climb>"]


# Coming back after data has expired


@responses.activate
def test_returning_after_data_expired_syncs_again_instead_of_showing_an_empty_list(app, client, db):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()
    with db.connect() as conn:
        conn.execute("UPDATE athletes SET last_synced_at = now() - interval '7 days'")
        conn.execute("DELETE FROM rides")
        conn.execute("DELETE FROM segments")

    state = client.get("/api/state").json()

    assert state["syncing"] is True and state["show_lists"] is False


@responses.activate
def test_visit_fetches_details_that_expired_before_the_riders_own_rides(app, client, db):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()
    with db.connect() as conn:
        conn.execute("DELETE FROM segments WHERE id = 600")

    client.get("/api/state")
    assert app.state.worker.run_next_job() is True

    assert "Popular Road" in names(client)


@responses.activate
def test_visit_with_nothing_missing_does_not_queue_a_sync(app, client):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()

    client.get("/api/state")

    assert app.state.worker.run_next_job() is False


@responses.activate
def test_rides_expiring_soon_after_a_refresh_trigger_a_new_sync_not_an_empty_list(app, client, db):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()
    client.post("/api/refresh")
    app.state.worker.run_next_job()
    with db.connect() as conn:
        conn.execute("UPDATE rides SET fetched_at = now() - interval '7 days'")
    app.state.worker.purge_if_due()

    assert client.get("/api/state").json()["syncing"] is True
    assert app.state.worker.run_next_job() is True
    assert "Popular Road" in names(client)


@responses.activate
def test_cleanup_with_nothing_expired_does_not_trigger_a_sync(app, client):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()
    app.state.worker.purge_if_due()

    client.get("/api/state")

    assert app.state.worker.run_next_job() is False


# Pooled Explore and contributors


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


def test_the_app_is_told_whether_explore_is_pooled(client, pooled_client):
    assert client.get("/api/state").json()["pooled"] is False
    assert pooled_client.get("/api/state").json()["pooled"] is True


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
    pooled_client.put("/api/contributing", json={"on": False})

    log_in(pooled_client)

    assert contributes(db) is False


@responses.activate
def test_rider_sees_contributors_segments_on_explore_only(pooled_app, pooled_client, db):
    log_in(pooled_client)
    mock_strava()
    pooled_app.state.worker.run_next_job()
    seed_contributor_segment(db)

    assert "Their Climb" in names(pooled_client, "explore")
    assert "Their Climb" not in names(pooled_client, "my_segments")


@responses.activate
def test_off_switch_keeps_contributors_segments_off_explore(app, client, db):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()
    seed_contributor_segment(db)

    assert "Their Climb" not in names(client, "explore")


@responses.activate
@pytest.mark.parametrize("on", [False, True])
def test_opting_out_and_back_in(pooled_app, pooled_client, db, on):
    log_in(pooled_client)
    pooled_client.put("/api/contributing", json={"on": not on})

    pooled_client.put("/api/contributing", json={"on": on})

    assert contributes(db) is on
    assert pooled_client.get("/api/state").json()["contributes"] is on
