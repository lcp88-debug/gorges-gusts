"""Sorting a list by clicking a field's heading, remembered per rider and per list."""

import re

import pytest
import responses
from fastapi.testclient import TestClient

from tests.conftest import LOCAL_ROUTE
from tests.test_web import add_wind_numbers, app, client, log_in, mock_strava  # noqa: F401 (fixtures)

OTHER = 3003
NAMES = {500: "Often &lt;Climb&gt;", 600: "Popular Road", 700: "Third Hill"}


def order(html):
    return [
        {name: id for id, name in NAMES.items()}[name]
        for name in re.findall(r"<td>(Often &lt;Climb&gt;|Popular Road|Third Hill)</td>", html)
    ]


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


@pytest.fixture
def synced(app, client, db):
    """A logged-in rider with three segments: 500 and 600 from Strava, 700 added here."""
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


def sort_by(client, field, list="my_segments"):
    return client.post("/sort", data={"list": list, "field": field})


def set_column(db, column, values):
    with db.connect() as conn:
        for segment_id, value in values.items():
            conn.execute(f"UPDATE segments SET {column} = %s WHERE id = %s", (value, segment_id))


def set_wind(db, values):
    for segment_id, value in values.items():
        if value is not None:
            add_wind_numbers(db, segment_id, [value])


# Each field, the made-up values for segments 500, 600 and 700, and the order of the first click.
NUMBER_FIELDS = [
    ("distance", lambda db, v: set_column(db, "distance_m", v), {500: 900, 600: 2000, 700: None}, [600, 500, 700]),
    ("average_grade", lambda db, v: set_column(db, "average_grade", v), {500: 3.0, 600: None, 700: 7.0}, [700, 500, 600]),
    ("people_on_strava", lambda db, v: set_column(db, "athlete_count", v), {500: None, 600: 5, 700: 50}, [700, 600, 500]),
    ("total_attempts", lambda db, v: set_column(db, "effort_count", v), {500: 10, 600: None, 700: 99}, [700, 500, 600]),
    ("wind_now", set_wind, {500: 4.0, 600: -6.0, 700: None}, [600, 500, 700]),
    ("best_hour", set_wind, {500: None, 600: 2.0, 700: -3.0}, [700, 600, 500]),
]


@pytest.mark.parametrize("field, store, values, first", NUMBER_FIELDS)
def test_first_click_puts_the_most_useful_end_on_top_and_blanks_last(synced, db, field, store, values, first):
    store(db, values)

    response = sort_by(synced, field)

    assert response.status_code == 303
    assert response.headers["location"] == "/?list=my_segments"
    assert order(synced.get("/?list=my_segments").text) == first


@pytest.mark.parametrize("field, store, values, first", NUMBER_FIELDS)
def test_second_click_reverses_but_keeps_blanks_last(synced, db, field, store, values, first):
    store(db, values)

    sort_by(synced, field)
    sort_by(synced, field)

    assert order(synced.get("/").text) == [first[1], first[0], first[2]]


@responses.activate
def test_your_attempts_sorts_most_first_then_reverses(app, client, db):
    log_in(client)
    mock_strava()
    app.state.worker.run_next_job()
    add_third_segment(db, attempts=3)
    sort_by(client, "distance")

    sort_by(client, "your_attempts")
    assert order(client.get("/").text) == [700, 500, 600]
    sort_by(client, "your_attempts")
    assert order(client.get("/").text) == [600, 500, 700]


def test_the_sorted_heading_is_marked_with_its_direction(synced):
    page = synced.get("/").text
    assert 'aria-sort="descending"><form method="post" action="/sort">' in page
    assert re.search(r'aria-sort="descending">.*?value="your_attempts"', page, re.S)

    sort_by(synced, "wind_now")
    assert re.search(r'aria-sort="ascending">.*?value="wind_now"', synced.get("/").text, re.S)


def test_name_cannot_be_sorted(synced):
    assert 'value="name"' not in synced.get("/").text

    sort_by(synced, "name")

    assert order(synced.get("/").text) == [500, 600, 700]


def test_an_unknown_field_or_list_changes_nothing(synced, db):
    sort_by(synced, "nonsense")
    sort_by(synced, "distance", list="nonsense")

    assert order(synced.get("/").text) == [500, 600, 700]
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM list_settings").fetchone()["n"] == 0


def test_each_list_keeps_its_own_sort(synced, db):
    set_column(db, "distance_m", {500: 900, 600: 2000, 700: 100})

    sort_by(synced, "distance", list="explore")

    assert order(synced.get("/?list=explore").text) == [600, 500, 700]
    assert order(synced.get("/?list=my_segments").text) == [500, 600, 700]


def test_with_nothing_saved_each_list_starts_biggest_first_on_its_own_field(synced):
    # Your attempts: 500 twice, 600 and 700 once. People on Strava: 600, 700, 500.
    assert order(synced.get("/?list=my_segments").text) == [500, 600, 700]
    assert order(synced.get("/?list=explore").text) == [600, 700, 500]


def test_sort_survives_logging_out_and_in(synced, db):
    set_column(db, "distance_m", {500: 900, 600: 2000, 700: 100})
    sort_by(synced, "distance")

    synced.post("/logout")
    log_in(synced)

    assert order(synced.get("/").text) == [600, 500, 700]


def test_another_rider_does_not_see_my_sort(synced, app, db):
    set_column(db, "distance_m", {500: 900, 600: 2000, 700: 100})
    sort_by(synced, "distance")

    with TestClient(app, follow_redirects=False, base_url="http://localhost") as other:
        log_in(other, athlete_id=OTHER)
        with db.connect() as conn:
            # Made-up rides for the other rider: 700 three times, 500 twice, 600 once.
            conn.execute("UPDATE athletes SET last_synced_at = now() WHERE id = %s", (OTHER,))
            conn.execute("INSERT INTO rides VALUES (2, %s, now(), now())", (OTHER,))
            for index, segment_id in enumerate([700, 700, 700, 500, 500, 600]):
                conn.execute(
                    """
                    INSERT INTO efforts (id, athlete_id, ride_id, segment_id, start_date, fetched_at)
                    VALUES (%s, %s, 2, %s, now(), now())
                    """,
                    (8000 + index, OTHER, segment_id),
                )
        assert order(other.get("/").text) == [700, 500, 600]
    assert order(synced.get("/").text) == [600, 500, 700]


def test_saved_settings_are_deleted_with_the_rider(synced, db):
    sort_by(synced, "distance")

    with db.connect() as conn:
        conn.execute("DELETE FROM athletes WHERE id = 1001")
        assert conn.execute("SELECT count(*) AS n FROM list_settings").fetchone()["n"] == 0


def test_an_unknown_saved_field_falls_back_to_the_default_sort(synced, db):
    sort_by(synced, "distance")
    with db.connect() as conn:
        conn.execute("UPDATE list_settings SET sort_field = 'retired_field'")

    assert order(synced.get("/").text) == [500, 600, 700]


def test_sorting_needs_a_login(client, db):
    assert sort_by(client, "distance").status_code == 303
