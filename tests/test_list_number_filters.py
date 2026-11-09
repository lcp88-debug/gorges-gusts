"""Minimum and maximum filters on number fields, saved per rider and per list."""

import pytest

from tests.test_list_fields import set_attempt_times
from tests.test_list_sort import order, set_column, set_wind, sort_by, synced  # noqa: F401 (fixtures)
from tests.test_web import app, client, log_in  # noqa: F401 (fixtures)


def filter_by(client, list="my_segments", **entries):
    """Submits the filter form. Keys look like distance_min="1" (other boxes left out)."""
    return client.post("/filters", data={"list": list, **entries})


def test_a_minimum_keeps_only_segments_at_or_above_it(synced, db):
    set_column(db, "distance_m", {500: 1609.34, 600: 3218.68, 700: 800})

    response = filter_by(synced, distance_min="1")

    assert response.status_code == 303
    assert response.headers["location"] == "/?list=my_segments"
    assert order(synced.get("/").text) == [500, 600]


def test_a_maximum_and_a_minimum_together_make_a_range(synced, db):
    set_column(db, "distance_m", {500: 1609.34, 600: 3218.68, 700: 800})

    filter_by(synced, distance_min="0.9", distance_max="1.5")

    assert order(synced.get("/").text) == [500]


def test_filters_on_several_fields_all_apply(synced, db):
    set_column(db, "distance_m", {500: 1609.34, 600: 3218.68, 700: 3218.68})
    set_column(db, "average_grade", {500: 5.0, 600: 1.0, 700: 6.0})

    filter_by(synced, distance_min="1", average_grade_min="4")

    assert order(synced.get("/").text) == [500, 700]


def test_a_blank_value_drops_out_only_when_its_field_is_filtered(synced, db):
    set_column(db, "average_grade", {500: 5.0, 600: None, 700: 6.0})
    assert order(synced.get("/").text) == [500, 600, 700]

    filter_by(synced, average_grade_max="10")

    assert order(synced.get("/").text) == [500, 700]


def test_entries_use_the_units_the_list_shows(synced, db):
    set_column(db, "elevation_gain_m", {500: 100.0, 600: 200.0, 700: 300.0})  # 328, 656, 984 ft
    filter_by(synced, elevation_gain_min="600", elevation_gain_max="700")
    assert order(synced.get("/").text) == [600]

    filter_by(synced)  # empty boxes clear the filters
    set_wind(db, {500: 4.0, 600: -6.0, 700: -2.0})
    filter_by(synced, wind_now_max="-1")
    assert order(synced.get("/").text) == [600, 700]


def test_times_are_typed_as_minutes_and_seconds(synced, db):
    set_attempt_times(db, {500: 150, 600: 61, 700: 3700})

    filter_by(synced, recent_best_min="1:30", recent_best_max="10:00")
    assert order(synced.get("/").text) == [500]

    filter_by(synced, recent_best_min="1:00:00")
    assert order(synced.get("/").text) == [700]


def test_boxes_show_what_is_saved_in_the_units_typed(synced):
    filter_by(synced, distance_min="1.5", recent_best_max="2:05")

    page = synced.get("/?list=my_segments").text

    # No segment is that long, so the empty-list page must still show the boxes.
    assert 'name="distance_min" value="1.5"' in page
    assert 'name="recent_best_max" value="2:05"' in page
    assert 'name="distance_max" value=""' in page


@pytest.mark.parametrize(
    "entries",
    [
        {"distance_min": "abc"},
        {"distance_min": "3", "distance_max": "1"},
        {"distance_min": "nan"},
        {"recent_best_min": "90"},
        {"recent_best_min": "1:75"},
        {"average_grade_min": "1e999"},
    ],
)
def test_invalid_entries_change_nothing_and_show_a_message(synced, db, entries):
    filter_by(synced, average_grade_min="0")

    response = filter_by(synced, **entries)
    page = synced.get(response.headers["location"]).text

    assert 'class="notice error"' in page and "Nothing was changed" in page
    assert 'name="average_grade_min" value="0"' in page
    assert 'name="distance_min" value=""' in page
    # The message is shown once.
    assert "Nothing was changed" not in synced.get("/").text


def test_filters_are_saved_per_list_and_survive_logging_out_and_in(synced, db):
    set_column(db, "distance_m", {500: 1609.34, 600: 3218.68, 700: 800})
    filter_by(synced, distance_min="1")

    synced.post("/logout")
    log_in(synced)

    assert order(synced.get("/?list=my_segments").text) == [500, 600]
    assert order(synced.get("/?list=explore").text) == [600, 700, 500]


def test_clear_all_resets_filters_but_leaves_the_sort(synced, db):
    set_column(db, "distance_m", {500: 1609.34, 600: 3218.68, 700: 800})
    sort_by(synced, "distance")
    filter_by(synced, distance_min="1", average_grade_max="9")
    filter_by(synced, list="explore", distance_min="1")

    response = synced.post("/filters/clear", data={"list": "my_segments"})

    assert response.headers["location"] == "/?list=my_segments"
    assert order(synced.get("/").text) == [600, 500, 700]
    assert order(synced.get("/?list=explore").text) == [600, 500]


def test_unknown_lists_and_fields_change_nothing(synced, db):
    filter_by(synced, list="nonsense", distance_min="1")
    filter_by(synced, nonsense_min="1", name_min="1")

    with db.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM list_settings WHERE filters <> '{}'").fetchone()["n"] == 0
    assert order(synced.get("/").text) == [500, 600, 700]


def test_unknown_saved_filters_are_ignored(synced, db):
    set_column(db, "distance_m", {500: 1609.34, 600: 3218.68, 700: 800})
    filter_by(synced, distance_min="1")
    with db.connect() as conn:
        conn.execute(
            "UPDATE list_settings SET filters = %s::jsonb",
            ['{"retired": {"min": 1}, "distance": {"min": 1}, "average_grade": "junk"}'],
        )

    assert order(synced.get("/").text) == [500, 600]


def test_filtering_needs_a_login(client, db):
    assert filter_by(client, distance_min="1").status_code == 303
    assert client.post("/filters/clear", data={"list": "explore"}).status_code == 303


def test_filters_compare_values_as_the_list_shows_them(synced, db):
    # Shown as 1.0 mi, 0.9 mi and 1.1 mi.
    set_column(db, "distance_m", {500: 1580.0, 600: 1450.0, 700: 1770.0})
    filter_by(synced, distance_min="1", distance_max="1")
    assert order(synced.get("/").text) == [500]

    filter_by(synced)
    set_wind(db, {500: -2.6, 600: -3.4, 700: -2.4})  # Shown as −3%, −3% and −2%.
    filter_by(synced, wind_now_max="-3")
    assert order(synced.get("/").text) == [500, 600]
