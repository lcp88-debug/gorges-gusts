"""Fastest time: the fastest anyone has recorded on a segment, behind an app setting."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.sync import read_overall_time, run_sync
from tests.conftest import ATHLETE, NOW
from tests.helpers import saved_settings, segments, set_column

# Pages in this file run with the setting on, unless marked `setting_off`.
setting_off = pytest.mark.setting_off


@pytest.fixture
def app(db, settings, request):
    on = request.node.get_closest_marker("setting_off") is None
    return create_app(replace(settings, fastest_time=on), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app, follow_redirects=False, base_url="http://localhost") as test_client:
        yield test_client


def stored_times(conn):
    return {
        row["id"]: row["fastest_time_s"]
        for row in conn.execute("SELECT id, fastest_time_s FROM segments")
    }


# Sync


def test_the_sync_stores_the_overall_fastest_time_when_the_setting_is_on(conn, fake):
    fake.add_ride(1, [10, 20, 30, 40])
    fake.add_segment(10, "Short", xoms={"overall": "3:49", "kom": "9:99", "qom": "4:10"})
    fake.add_segment(20, "Long", xoms={"overall": "1:02:03"})
    fake.add_segment(30, "Unknown", xoms={})
    fake.add_segment(40, "Sprint", xoms={"overall": "49s"})

    run_sync(conn, fake, ATHLETE, NOW, fastest_time=True)

    assert stored_times(conn) == {10: 229, 20: 3723, 30: None, 40: 49}


@pytest.mark.parametrize("text", ["--", "", "abc", "3:99", "1:2:3:4", "-3:00", "s", "-5s", None, 229])
def test_text_that_cannot_be_read_becomes_blank(conn, fake, text):
    fake.add_ride(1, [10])
    fake.add_segment(10, "Odd", xoms={"overall": text})

    run_sync(conn, fake, ATHLETE, NOW, fastest_time=True)

    assert stored_times(conn) == {10: None}


def test_the_sync_does_not_store_fastest_time_when_the_setting_is_off(conn, fake):
    fake.add_ride(1, [10])
    fake.add_segment(10, "Climb", xoms={"overall": "3:49"})

    run_sync(conn, fake, ATHLETE, NOW)

    assert stored_times(conn) == {10: None}


def test_turning_the_setting_off_clears_the_value_on_the_next_fetch(conn, fake):
    fake.add_ride(1, [10])
    fake.add_segment(10, "Climb", xoms={"overall": "3:49"})
    run_sync(conn, fake, ATHLETE, NOW, fastest_time=True)
    conn.execute("DELETE FROM segments")

    run_sync(conn, fake, ATHLETE, NOW)

    assert stored_times(conn) == {10: None}


def test_nothing_about_who_set_the_time_is_stored(conn, fake):
    fake.add_ride(1, [10])
    fake.add_segment(10, "Climb", xoms={"overall": "3:49", "kom": "3:49"}, leaders="Someone")

    run_sync(conn, fake, ATHLETE, NOW, fastest_time=True)

    columns = {
        row["column_name"]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'segments'"
        )
    }
    assert "fastest_time_s" in columns
    assert not {name for name in columns if "kom" in name or "holder" in name or "leader" in name}


# Starting the app


def start_app(settings, on):
    """Starts and stops the app once, with the fastest-time setting on or off."""
    with TestClient(create_app(replace(settings, fastest_time=on), start_worker=False)):
        pass


def test_starting_with_the_setting_off_blanks_stored_fastest_times(conn, fake, settings):
    fake.add_ride(1, [10])
    fake.add_segment(10, "Climb", xoms={"overall": "3:49"})
    run_sync(conn, fake, ATHLETE, NOW, fastest_time=True)

    start_app(settings, on=False)

    assert stored_times(conn) == {10: None}


def test_turning_the_setting_on_refetches_segment_details_once(conn, fake, settings):
    fake.add_ride(1, [10])
    fake.add_segment(10, "Climb", xoms={"overall": "3:49"})
    start_app(settings, on=False)
    run_sync(conn, fake, ATHLETE, NOW)
    conn.execute("UPDATE athletes SET last_synced_at = %s, wind_stale = FALSE", (NOW,))

    start_app(settings, on=True)

    assert stored_times(conn) == {}
    athlete = conn.execute(
        "SELECT last_synced_at, wind_stale FROM athletes WHERE id = %s", (ATHLETE,)
    ).fetchone()
    assert athlete == {"last_synced_at": None, "wind_stale": True}
    # The next sync fills it in from the rides still cached, without fetching them again.
    fake.calls.clear()
    run_sync(conn, fake, ATHLETE, NOW, fastest_time=True)
    assert stored_times(conn) == {10: 229}
    assert fake.calls == ["list", "segment:10"]

    start_app(settings, on=True)  # Already on: nothing more is cleared.
    assert stored_times(conn) == {10: 229}


def test_overall_time_text_is_read_as_seconds():
    assert read_overall_time(" 3:49 ") == 229
    assert read_overall_time("1:02:03") == 3723


# The app


def test_the_app_gets_fastest_times_while_the_setting_is_on(synced, db):
    set_column(db, "fastest_time_s", {500: 229, 600: 3723, 700: None})

    assert synced.get("/api/state").json()["fastest_time"] is True
    assert {id: s["fastest_time_s"] for id, s in segments(synced).items()} == {500: 229, 600: 3723, 700: None}


def test_it_can_be_sorted_and_filtered_while_the_setting_is_on(synced, db):
    assert synced.put("/api/lists/my_segments/sort", json={"field": "fastest_time", "direction": "asc"}).status_code == 200
    synced.put("/api/lists/my_segments/filters", json={"filters": {"fastest_time": {"max": 240}}})

    data = synced.get("/api/lists/my_segments").json()
    assert data["sort"] == {"field": "fastest_time", "direction": "asc"}
    assert data["filters"] == {"fastest_time": {"max": 240.0}}


@setting_off
def test_with_the_setting_off_the_app_gets_no_fastest_times(synced, db):
    set_column(db, "fastest_time_s", {500: 229, 600: 3723, 700: None})

    assert synced.get("/api/state").json()["fastest_time"] is False
    assert all("fastest_time_s" not in segment for segment in segments(synced).values())


@setting_off
def test_with_the_setting_off_it_cannot_be_sorted_or_filtered(synced, db):
    response = synced.put("/api/lists/my_segments/sort", json={"field": "fastest_time", "direction": "asc"})
    synced.put("/api/lists/my_segments/filters", json={"filters": {"fastest_time": {"max": 180}}})

    assert response.status_code == 422
    with db.connect() as conn:
        saved = conn.execute("SELECT sort_field, filters FROM list_settings").fetchall()
    assert not any(row["sort_field"] == "fastest_time" or row["filters"] for row in saved)


def save_directly(db, sort_field, filters):
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO list_settings (athlete_id, list_kind, sort_field, sort_direction, filters)
            VALUES (1001, 'my_segments', %s, 'asc', %s)
            """,
            (sort_field, filters),
        )


@setting_off
def test_a_saved_fastest_time_filter_and_sort_are_not_sent_while_it_is_off(synced, db):
    save_directly(db, "fastest_time", '{"fastest_time": {"max": 250}}')

    data = synced.get("/api/lists/my_segments").json()

    # The sort falls back to the list's own default.
    assert data["sort"] == {"field": "your_attempts", "direction": "desc"}
    assert data["filters"] == {}


@setting_off
def test_a_saved_fastest_time_filter_is_kept_when_other_choices_are_saved_while_off(synced, db):
    save_directly(db, "distance", '{"fastest_time": {"max": 250}}')

    synced.put("/api/lists/my_segments/sort", json={"field": "average_grade", "direction": "desc"})
    synced.put("/api/lists/my_segments/filters", json={"filters": {"name": "i"}})
    assert saved_settings(db)["filters"] == {"fastest_time": {"max": 250}, "name": "i"}

    synced.put("/api/lists/my_segments/filters", json={"filters": {}})  # Clear all.
    assert saved_settings(db)["filters"] == {"fastest_time": {"max": 250}}


@setting_off
def test_a_saved_fastest_time_sort_is_kept_when_filters_are_saved_while_off(synced, db):
    save_directly(db, "fastest_time", "{}")

    synced.put("/api/lists/my_segments/filters", json={"filters": {"name": "i"}})
    synced.put("/api/lists/my_segments/filters", json={"filters": {}})

    saved = saved_settings(db)
    assert (saved["sort_field"], saved["sort_direction"]) == ("fastest_time", "asc")
