"""The segment details and recent best shown on each list, and how they sort."""

import pytest

from tests.conftest import table
from tests.test_list_sort import order, set_column, sort_by, synced  # noqa: F401 (fixtures)
from tests.test_web import app, client  # noqa: F401 (fixtures)


def column(client, heading, list="my_segments"):
    return [row[heading] for row in table(client.get(f"/?list={list}").text)]


def set_attempt_times(db, values):
    """Gives every attempt of the rider on each made-up segment this elapsed time."""
    with db.connect() as conn:
        for segment_id, seconds in values.items():
            conn.execute(
                "UPDATE efforts SET elapsed_s = %s WHERE segment_id = %s", (seconds, segment_id)
            )


def test_headings_appear_in_the_agreed_order(synced):
    headings = list(table(synced.get("/").text)[0])

    assert headings[:12] == [
        "Segment",
        "Town",
        "Distance",
        "Avg grade",
        "Steepest grade",
        "Elevation gain",
        "Climb category",
        "Your attempts, 90 days",
        "Recent best",
        "Wind now",
        "Best hour",
        "Next 48 hours",
    ]


def test_rows_show_town_steepest_grade_and_elevation_gain_in_page_units(synced, db):
    set_column(db, "town", {500: "Ithaca", 600: None, 700: None})
    set_column(db, "maximum_grade", {500: 12.46, 600: None, 700: None})
    set_column(db, "elevation_gain_m", {500: 95.4, 600: 1000.0, 700: None})

    assert column(synced, "Town") == ["Ithaca", "–", "–"]
    assert column(synced, "Steepest grade") == ["12.5%", "–", "–"]
    assert column(synced, "Elevation gain") == ["313 ft", "3,281 ft", "–"]


@pytest.mark.parametrize(
    "stored, shown", [(0, "none"), (1, "4"), (2, "3"), (3, "2"), (4, "1"), (5, "HC"), (None, "–")]
)
def test_climb_category_is_shown_as_strava_names_it(synced, db, stored, shown):
    set_column(db, "climb_category", {500: stored})

    assert column(synced, "Climb category")[0] == shown


@pytest.mark.parametrize(
    "seconds, shown",
    [(59, "0:59"), (229, "3:49"), (3599, "59:59"), (3600, "1:00:00"), (3723, "1:02:03"), (None, "–")],
)
def test_recent_best_is_shown_as_a_time(synced, db, seconds, shown):
    set_attempt_times(db, {500: seconds})

    assert column(synced, "Recent best")[0] == shown


# Each field, made-up values for segments 500, 600 and 700, and the order after one click and two.
NEW_FIELDS = [
    ("steepest_grade", lambda db, v: set_column(db, "maximum_grade", v),
     {500: 8.0, 600: None, 700: 15.0}, [700, 500, 600], [500, 700, 600]),
    ("elevation_gain", lambda db, v: set_column(db, "elevation_gain_m", v),
     {500: None, 600: 30.0, 700: 10.0}, [600, 700, 500], [700, 600, 500]),
    # HC first and "none" after every real category, but still above a blank.
    ("climb_category", lambda db, v: set_column(db, "climb_category", v),
     {500: 0, 600: 5, 700: None}, [600, 500, 700], [500, 600, 700]),
    # Category 4, category 1 and category 3.
    ("climb_category", lambda db, v: set_column(db, "climb_category", v),
     {500: 1, 600: 4, 700: 2}, [600, 700, 500], [500, 700, 600]),
    ("recent_best", set_attempt_times,
     {500: 300, 600: None, 700: 95}, [700, 500, 600], [500, 700, 600]),
]


@pytest.mark.parametrize("field, store, values, first, second", NEW_FIELDS)
def test_new_fields_sort_most_useful_end_first_with_blanks_last(synced, db, field, store, values, first, second):
    store(db, values)

    sort_by(synced, field)
    assert order(synced.get("/").text) == first

    sort_by(synced, field)
    assert order(synced.get("/").text) == second


def test_town_cannot_be_sorted(synced, db):
    set_column(db, "town", {500: "Ulysses", 600: "Dryden", 700: "Ithaca"})

    assert 'value="town"' not in synced.get("/").text
    sort_by(synced, "town")
    assert order(synced.get("/").text) == [500, 600, 700]
