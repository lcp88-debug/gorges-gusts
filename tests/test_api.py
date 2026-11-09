"""The JSON routes the React app calls: what they send, and what they save."""

from datetime import datetime, timedelta, timezone

import pytest
import responses

from tests.test_list_sort import add_third_segment, set_column, synced  # noqa: F401 (fixtures)
from tests.test_web import (  # noqa: F401 (fixtures)
    add_wind_numbers,
    app,
    client,
    log_in,
    mock_strava,
    pooled_app,
    pooled_client,
)


def this_hour():
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


def hour(offset):
    return (this_hour() + timedelta(hours=offset)).isoformat()


def saved_row(db, kind="my_segments"):
    with db.connect() as conn:
        return conn.execute(
            "SELECT sort_field, sort_direction, filters FROM list_settings"
            " WHERE athlete_id = 1001 AND list_kind = %s",
            (kind,),
        ).fetchone()


# State


def test_logged_out_state_says_so_and_whether_explore_is_pooled(client):
    assert client.get("/api/state").json() == {"logged_in": False, "login_error": None, "pooled": False}


@responses.activate
def test_a_failed_login_is_explained_once(client):
    client.get("/login")
    client.get("/auth/callback", params={"state": "wrong", "code": "abc"})

    assert client.get("/api/state").json()["login_error"] == "That login link expired. Please try again."
    assert client.get("/api/state").json()["login_error"] is None


@responses.activate
def test_first_visit_shows_the_sync_in_progress_then_the_lists(app, client):
    log_in(client)

    state = client.get("/api/state").json()
    assert state["logged_in"] and state["syncing"] and state["busy"]
    assert state["show_lists"] is False
    assert state["job"]["status"] == "pending"

    mock_strava()
    app.state.worker.run_next_job()

    state = client.get("/api/state").json()
    assert not state["syncing"] and state["show_lists"]
    assert state["job"]["status"] == "done"
    assert state["last_synced_at"] is not None


@responses.activate
def test_a_failed_sync_is_reported_and_not_retried(app, client, db):
    from app.strava import API_URL

    log_in(client)
    responses.get(f"{API_URL}/athlete/activities", status=401, json={})
    app.state.worker.run_next_job()

    state = client.get("/api/state").json()

    assert state["job"]["status"] == "failed"
    assert "log in with Strava again" in state["job"]["error"]
    assert state["show_lists"] is False
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM sync_jobs").fetchone()["n"] == 1


def test_state_carries_the_profile_and_its_limits(synced):
    profile = synced.get("/api/state").json()["profile"]

    assert profile["weight_kg"] is None and profile["power_20min_w"] is None
    assert profile["weight_range"] == [30, 200] and profile["power_range"] == [50, 600]


def test_lists_version_changes_when_the_wind_is_updated(synced, db):
    before = synced.get("/api/state").json()["lists_version"]
    with db.connect() as conn:
        conn.execute("UPDATE athletes SET wind_updated_at = now()")

    assert synced.get("/api/state").json()["lists_version"] != before


# Lists


def test_a_list_sends_every_segment_with_its_details(synced):
    data = synced.get("/api/lists/my_segments").json()

    by_id = {segment["id"]: segment for segment in data["segments"]}
    assert set(by_id) == {500, 600, 700}
    climb = by_id[500]
    assert climb["name"] == "Often <Climb>"
    assert climb["town"] == "Ithaca"
    assert climb["distance_m"] == pytest.approx(3218.68)
    assert climb["climb_category"] == 5
    assert climb["recent_efforts"] == 2
    assert climb["recent_best"] == 3723
    assert climb["athlete_count"] == 12 and climb["effort_count"] == 3456
    assert "fastest_time_s" not in climb
    assert "polyline" not in climb
    assert len(data["hours"]) == 49
    assert data["this_hour"] == this_hour().isoformat()


def test_a_list_sends_wind_hours_from_this_hour_on(synced, db):
    add_wind_numbers(db, 500, [3.4, -8.6], less_certain=True)

    climb = next(s for s in synced.get("/api/lists/my_segments").json()["segments"] if s["id"] == 500)

    assert [hour["percent"] for hour in climb["wind_hours"]] == [3.4, -8.6]
    assert climb["wind_hours"][0]["hour"] == this_hour().isoformat()
    assert climb["wind_hours"][0]["speed_ms"] == pytest.approx(4.4704)
    assert climb["wind_hours"][0]["from_degrees"] == 225
    assert climb["less_certain"] is True


def test_with_nothing_saved_each_list_has_its_default_sort(synced):
    assert synced.get("/api/lists/my_segments").json()["sort"] == {"field": "your_attempts", "direction": "desc"}
    assert synced.get("/api/lists/explore").json()["sort"] == {"field": "people_on_strava", "direction": "desc"}


def test_lists_need_a_login_and_a_known_list(client, synced):
    assert synced.get("/api/lists/nonsense").status_code == 404
    synced.post("/api/logout")
    assert synced.get("/api/lists/my_segments").status_code == 401


# Sort


def test_a_sort_is_saved_per_list_and_leaves_filters_alone(synced, db):
    synced.put("/api/lists/my_segments/filters", json={"filters": {"name": "road"}})

    assert synced.put("/api/lists/my_segments/sort", json={"field": "distance", "direction": "asc"}).status_code == 200

    assert synced.get("/api/lists/my_segments").json()["sort"] == {"field": "distance", "direction": "asc"}
    assert synced.get("/api/lists/explore").json()["sort"]["field"] == "people_on_strava"
    assert saved_row(db)["filters"] == {"name": "road"}


@pytest.mark.parametrize("field, direction", [("name", "asc"), ("town", "asc"), ("nonsense", "asc"), ("distance", "up")])
def test_a_sort_that_cannot_be_used_changes_nothing(synced, db, field, direction):
    response = synced.put("/api/lists/my_segments/sort", json={"field": field, "direction": direction})

    assert response.status_code == 422
    assert saved_row(db) is None


# Filters


def test_filters_are_saved_cleaned_and_sent_back(synced, db):
    sent = {
        "distance": {"min": 1, "max": 2.5},
        "town": ["Ithaca", "Elmira"],
        "climb_category": [0, 5],
        "name": "climb",
        "nonsense": {"min": 1},
        "average_grade": "steep",
    }

    response = synced.put("/api/lists/my_segments/filters", json={"filters": sent})

    expected = {
        "distance": {"min": 1.0, "max": 2.5},
        "town": ["Ithaca", "Elmira"],
        "climb_category": [0, 5],
        "name": "climb",
    }
    assert response.json() == {"filters": expected}
    assert saved_row(db)["filters"] == expected
    assert synced.get("/api/lists/my_segments").json()["filters"] == expected
    assert synced.get("/api/lists/explore").json()["filters"] == {}


def test_sending_no_filters_clears_them_and_leaves_the_sort(synced, db):
    synced.put("/api/lists/my_segments/sort", json={"field": "distance", "direction": "asc"})
    synced.put("/api/lists/my_segments/filters", json={"filters": {"name": "road"}})

    synced.put("/api/lists/my_segments/filters", json={"filters": {}})

    assert saved_row(db)["filters"] == {}
    assert saved_row(db)["sort_field"] == "distance"


def test_filters_survive_logging_out_and_in(synced):
    synced.put("/api/lists/my_segments/filters", json={"filters": {"name": "road"}})

    synced.post("/api/logout")
    log_in(synced)

    assert synced.get("/api/lists/my_segments").json()["filters"] == {"name": "road"}


# Time range


def test_a_time_range_is_kept_per_list_for_the_visit(synced):
    synced.get("/api/state")

    response = synced.put("/api/lists/my_segments/time-range", json={"start": hour(2), "end": hour(5)})

    assert response.json() == {"time_range": [hour(2), hour(5)]}
    assert synced.get("/api/lists/my_segments").json()["time_range"] == [hour(2), hour(5)]
    assert synced.get("/api/lists/explore").json()["time_range"] is None


@pytest.mark.parametrize(
    "start, end, message",
    [
        ("soon", hour(2), "The start of the time range isn't a time."),
        (hour(5), hour(2), "The time range ends before it starts."),
        (hour(0), hour(60), "The time range has to be within the next 48 hours."),
    ],
)
def test_a_bad_time_range_changes_nothing_and_says_why(synced, start, end, message):
    synced.put("/api/lists/my_segments/time-range", json={"start": hour(1), "end": hour(3)})

    response = synced.put("/api/lists/my_segments/time-range", json={"start": start, "end": end})

    assert response.status_code == 422
    assert response.json()["error"] == f"{message} Nothing was changed."
    assert synced.get("/api/lists/my_segments").json()["time_range"] == [hour(1), hour(3)]


def test_a_time_range_can_go_back_to_all_48_hours(synced):
    synced.put("/api/lists/my_segments/time-range", json={"start": hour(1), "end": hour(3)})

    synced.delete("/api/lists/my_segments/time-range")

    assert synced.get("/api/lists/my_segments").json()["time_range"] is None


def test_a_time_range_resets_after_more_than_an_hour_away(synced, db):
    synced.put("/api/lists/my_segments/time-range", json={"start": hour(1), "end": hour(3)})
    with db.connect() as conn:
        conn.execute("UPDATE athletes SET last_seen_at = now() - interval '61 minutes'")

    synced.get("/api/state")

    assert synced.get("/api/lists/my_segments").json()["time_range"] is None


# Profile, sharing, refresh, logout


def test_the_profile_is_saved_and_asks_for_new_wind_numbers(synced, db):
    response = synced.put("/api/profile", json={"weight_kg": "68.5", "power_20min_w": 250})

    assert response.json() == {"weight_kg": 68.5, "power_20min_w": 250.0}
    with db.connect() as conn:
        row = conn.execute("SELECT weight_kg, power_20min_w, wind_stale FROM athletes WHERE id = 1001").fetchone()
    assert (row["weight_kg"], row["power_20min_w"], row["wind_stale"]) == (68.5, 250, True)


def test_blank_profile_boxes_go_back_to_standard_values(synced):
    synced.put("/api/profile", json={"weight_kg": 70, "power_20min_w": 200})

    synced.put("/api/profile", json={"weight_kg": "", "power_20min_w": None})

    profile = synced.get("/api/state").json()["profile"]
    assert profile["weight_kg"] is None and profile["power_20min_w"] is None


@pytest.mark.parametrize("weight, power", [("heavy", 200), (10, 200), (70, 5000), (True, 200)])
def test_a_bad_profile_changes_nothing_and_explains(synced, weight, power):
    synced.put("/api/profile", json={"weight_kg": 70, "power_20min_w": 200})

    response = synced.put("/api/profile", json={"weight_kg": weight, "power_20min_w": power})

    assert response.status_code == 422
    assert response.json()["error"] == "Weight must be 30–200 kg and power 50–600 W. Nothing was changed."
    assert synced.get("/api/state").json()["profile"]["weight_kg"] == 70


def test_sharing_can_be_turned_off_and_on(synced):
    synced.put("/api/contributing", json={"on": False})
    assert synced.get("/api/state").json()["contributes"] is False

    synced.put("/api/contributing", json={"on": True})
    assert synced.get("/api/state").json()["contributes"] is True


def test_refresh_queues_one_sync_however_often_it_is_pressed(synced, db):
    synced.post("/api/refresh")
    synced.post("/api/refresh")

    assert synced.get("/api/state").json()["syncing"] is True
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM sync_jobs WHERE status = 'pending'").fetchone()["n"] == 1


def test_logging_out_ends_the_session(synced):
    synced.post("/api/logout")

    assert synced.get("/api/state").json()["logged_in"] is False


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("put", "/api/lists/my_segments/sort", {"field": "distance", "direction": "asc"}),
        ("put", "/api/lists/my_segments/filters", {"filters": {}}),
        ("put", "/api/lists/my_segments/time-range", {"start": "", "end": ""}),
        ("delete", "/api/lists/my_segments/time-range", None),
        ("post", "/api/refresh", None),
        ("put", "/api/contributing", {"on": True}),
        ("put", "/api/profile", {"weight_kg": 70}),
    ],
)
def test_changes_need_a_login(client, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    assert client.request(method.upper(), path, **kwargs).status_code == 401
