"""Fastest time: the fastest anyone has recorded on a segment, behind an app setting."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.sync import read_overall_time, run_sync
from tests.conftest import ATHLETE, NOW, table
from tests.test_list_fields import column
from tests.test_list_number_filters import filter_by
from tests.test_list_sort import order, set_column, sort_by, synced  # noqa: F401 (fixtures)
from tests.test_web import log_in  # noqa: F401 (fixtures)

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


# Page


def test_the_page_shows_fastest_time_as_m_ss_and_h_mm_ss(synced, db):
    set_column(db, "fastest_time_s", {500: 229, 600: 3723, 700: None})

    headings = list(table(synced.get("/").text)[0])

    assert "Fastest time" in headings
    assert column(synced, "Fastest time") == ["3:49", "1:02:03", "–"]


def test_it_sorts_fastest_first_with_blanks_last_and_reverses(synced, db):
    set_column(db, "fastest_time_s", {500: 300, 600: 200, 700: None})

    sort_by(synced, "fastest_time")
    assert order(synced.get("/").text) == [600, 500, 700]

    sort_by(synced, "fastest_time")
    assert order(synced.get("/").text) == [500, 600, 700]


def test_it_filters_by_minimum_and_maximum_in_minutes_and_seconds(synced, db):
    set_column(db, "fastest_time_s", {500: 150, 600: 300, 700: None})

    filter_by(synced, fastest_time_min="2:00", fastest_time_max="4:00")
    assert order(synced.get("/").text) == [500]

    filter_by(synced, fastest_time_min="3:00")
    assert order(synced.get("/").text) == [600]


def test_the_filter_has_boxes_while_the_setting_is_on(synced):
    page = synced.get("/").text

    assert 'name="fastest_time_min"' in page and 'name="fastest_time_max"' in page


@setting_off
def test_with_the_setting_off_the_page_has_no_fastest_time_field(synced, db):
    set_column(db, "fastest_time_s", {500: 229, 600: 3723, 700: None})

    page = synced.get("/").text

    assert "Fastest time" not in page and "fastest_time" not in page
    assert "3:49" not in page


@setting_off
def test_with_the_setting_off_it_cannot_be_sorted_or_filtered(synced, db):
    set_column(db, "fastest_time_s", {500: 300, 600: 200, 700: None})
    before = order(synced.get("/").text)

    sort_by(synced, "fastest_time")
    filter_by(synced, fastest_time_max="3:00")

    assert order(synced.get("/").text) == before
    with db.connect() as conn:
        saved = conn.execute("SELECT sort_field, filters FROM list_settings").fetchall()
    assert not any(row["sort_field"] == "fastest_time" or row["filters"] for row in saved)


@setting_off
def test_a_saved_fastest_time_filter_and_sort_are_ignored_while_it_is_off(synced, db):
    set_column(db, "fastest_time_s", {500: 300, 600: 200, 700: None})
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO list_settings (athlete_id, list_kind, sort_field, sort_direction, filters)
            VALUES (1001, 'my_segments', 'fastest_time', 'asc', '{"fastest_time": {"max": 250}}')
            """
        )

    # All three segments stay, and the sort falls back to the list's own default.
    assert sorted(order(synced.get("/").text)) == [500, 600, 700]
    assert "aria-sort" in synced.get("/").text
    assert 'name="fastest_time_max"' not in synced.get("/").text


@setting_off
def test_a_saved_fastest_time_filter_is_kept_when_other_settings_are_saved_while_off(synced, db):
    set_column(db, "fastest_time_s", {500: 300, 600: 200, 700: None})
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO list_settings (athlete_id, list_kind, sort_field, sort_direction, filters)
            VALUES (1001, 'my_segments', 'distance', 'desc', '{"fastest_time": {"max": 250}}')
            """
        )

    sort_by(synced, "average_grade")
    filter_by(synced, name_search="i")
    page = synced.get("/").text
    synced.post("/filters/clear", data={"list": "my_segments"})

    assert sorted(order(page)) == [500, 700]  # Only the name search applies.
    assert "Filters (1 on)" in page
    with db.connect() as conn:
        saved = conn.execute("SELECT filters FROM list_settings").fetchone()["filters"]
    assert saved == {"fastest_time": {"max": 250}}


@setting_off
def test_a_saved_fastest_time_sort_is_kept_when_filters_are_saved_while_off(synced, db):
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO list_settings (athlete_id, list_kind, sort_field, sort_direction, filters)
            VALUES (1001, 'my_segments', 'fastest_time', 'asc', '{}')
            """
        )

    filter_by(synced, name_search="i")
    synced.post("/filters/clear", data={"list": "my_segments"})

    with db.connect() as conn:
        saved = conn.execute("SELECT sort_field, sort_direction FROM list_settings").fetchone()
    assert (saved["sort_field"], saved["sort_direction"]) == ("fastest_time", "asc")
