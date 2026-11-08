"""Tick-box filters (climb category, town) and the name search, saved per rider and list."""

from tests.test_list_number_filters import filter_by
from tests.test_list_sort import order, set_column, sort_by, synced  # noqa: F401 (fixtures)
from tests.test_web import app, client, log_in  # noqa: F401 (fixtures)


def test_ticked_climb_categories_keep_only_those_segments(synced, db):
    set_column(db, "climb_category", {500: 0, 600: 3, 700: 5})

    filter_by(synced, climb_category_3="on", climb_category_5="on")

    assert order(synced.get("/").text) == [600, 700]


def test_none_is_a_real_climb_category(synced, db):
    set_column(db, "climb_category", {500: 0, 600: 3, 700: 5})

    filter_by(synced, climb_category_0="on")

    assert order(synced.get("/").text) == [500]


def test_climb_tick_boxes_run_none_then_4_to_hc(synced):
    page = synced.get("/").text

    positions = [page.index(f'name="climb_category_{n}"') for n in range(6)]
    assert positions == sorted(positions)
    assert "> none" in page and "> HC" in page


def test_town_tick_boxes_list_only_towns_on_the_list(synced, db):
    set_column(db, "town", {500: "Ithaca", 600: "Dryden", 700: None})
    with db.connect() as conn:  # A town that no segment on this list is in.
        conn.execute("UPDATE segments SET town = 'Elmira' WHERE id = 700")
        conn.execute("DELETE FROM efforts WHERE segment_id = 700")

    page = synced.get("/").text

    assert 'name="town_Ithaca"' in page
    assert 'name="town_Dryden"' in page
    assert 'name="town_Elmira"' not in page


def test_ticking_towns_keeps_only_those_towns_and_drops_blanks(synced, db):
    set_column(db, "town", {500: "Ithaca", 600: "Dryden", 700: None})

    filter_by(synced, town_Ithaca="on")
    assert order(synced.get("/").text) == [500]

    filter_by(synced, town_Ithaca="on", town_Dryden="on")
    assert order(synced.get("/").text) == [500, 600]


def test_name_search_matches_any_part_ignoring_capitals(synced):
    # Names are "Often <Climb>", "Popular Road" and "Third Hill".
    filter_by(synced, name_search="OPULAR")
    assert order(synced.get("/").text) == [600]

    filter_by(synced, name_search="i")
    assert order(synced.get("/").text) == [500, 700]  # Often, Third Hill; not Popular Road


def test_these_filters_combine_with_number_filters(synced, db):
    set_column(db, "climb_category", {500: 3, 600: 3, 700: 0})
    set_column(db, "distance_m", {500: 3000, 600: 500, 700: 3000})

    filter_by(synced, climb_category_3="on", distance_min="1")

    assert order(synced.get("/").text) == [500]


def test_saved_boxes_are_shown_again(synced, db):
    set_column(db, "town", {500: "Ithaca", 600: "Dryden", 700: "Ithaca"})
    filter_by(synced, town_Dryden="on", climb_category_2="on", name_search="hill")

    page = synced.get("/").text

    assert 'name="town_Dryden" checked' in page
    assert 'name="town_Ithaca" checked' not in page
    assert 'name="climb_category_2" checked' in page
    assert 'name="name_search" value="hill"' in page


def test_filters_are_saved_per_list_and_clear_all_removes_them(synced, db):
    set_column(db, "town", {500: "Ithaca", 600: "Dryden", 700: "Ithaca"})
    everything = order(synced.get("/").text)
    filter_by(synced, town_Ithaca="on", name_search="i")
    assert sorted(order(synced.get("/").text)) == [500, 700]

    assert sorted(order(synced.get("/?list=explore").text)) == [500, 600, 700]

    synced.post("/filters/clear", data={"list": "my_segments"})
    assert order(synced.get("/").text) == everything


def test_a_bad_category_changes_nothing(synced):
    filter_by(synced, name_search="x")

    response = filter_by(synced, climb_category_9="on")

    assert response.status_code == 303
    assert 'name="name_search" value="x"' in synced.get("/").text
