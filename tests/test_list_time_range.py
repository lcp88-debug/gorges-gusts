"""The time range: which hours best hour is chosen from, kept per list for the visit."""

from datetime import datetime, timedelta, timezone

from app.main import saved_time_range
from tests.conftest import table
from tests.test_list_sort import order, synced  # noqa: F401 (fixtures)
from tests.test_web import add_wind_numbers, app, client  # noqa: F401 (fixtures)

FIVE_HOURS = [4.0, 5.0, -10.0, 3.0, -2.0]


def this_hour():
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


def hour(offset):
    return (this_hour() + timedelta(hours=offset)).isoformat()


def set_range(client, first, last, list="my_segments"):
    client.get("/")  # A range is only set from the page, which records the visit.
    return client.post("/timerange", data={"list": list, "start": hour(first), "end": hour(last)})


def row_500(client, list="my_segments"):
    rows = table(client.get(f"/?list={list}").text)
    return next(row for row in rows if row["Segment"].startswith("Often"))


def add_wind_at(db, segment_id, by_offset):
    """Stores wind effects at chosen hour offsets from now."""
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO segment_wind_models (segment_id, steps, less_certain)"
            " VALUES (%s, '[]', FALSE) ON CONFLICT DO NOTHING",
            (segment_id,),
        )
        for offset, percent in by_offset.items():
            conn.execute(
                """
                INSERT INTO wind_effects (athlete_id, segment_id, hour, percent, speed_ms, from_degrees)
                VALUES (1001, %s, %s, %s, 4.4704, 225)
                """,
                (segment_id, this_hour() + timedelta(hours=offset), percent),
            )


def set_last_seen(db, minutes_ago):
    with db.connect() as conn:
        conn.execute(
            "UPDATE athletes SET last_seen_at = %s",
            (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),),
        )


def test_best_hour_comes_only_from_hours_inside_the_range(synced, db):
    add_wind_numbers(db, 500, FIVE_HOURS)
    assert row_500(synced)["Best hour"].startswith("−10%")

    response = set_range(synced, 3, 4)

    assert response.status_code == 303
    assert row_500(synced)["Best hour"].startswith("−2%")
    set_range(synced, 0, 1)
    assert row_500(synced)["Best hour"].startswith("+4%")


def test_wind_now_and_the_hour_view_ignore_the_range(synced, db):
    add_wind_numbers(db, 500, FIVE_HOURS)
    set_range(synced, 3, 4)

    page = synced.get("/").text

    assert row_500(synced)["Wind now"] == "+4%"
    assert page.count("mph from SW") == 5


def test_a_range_with_no_hours_with_numbers_leaves_best_hour_blank(synced, db):
    add_wind_numbers(db, 500, [4.0, 5.0])
    set_range(synced, 10, 12)

    assert row_500(synced)["Best hour"] == "–"


def test_sorting_and_filtering_by_best_hour_follow_the_range(synced, db):
    # 500's best hour is its third hour, 600's is its first.
    add_wind_at(db, 500, {0: 1.0, 2: -10.0})
    add_wind_at(db, 600, {0: -5.0, 2: 2.0})
    synced.post("/sort", data={"list": "my_segments", "field": "best_hour"})
    assert order(synced.get("/").text) == [500, 600, 700]

    set_range(synced, 0, 1)
    assert order(synced.get("/").text) == [600, 500, 700]

    synced.post("/filters", data={"list": "my_segments", "best_hour_max": "-3"})
    assert order(synced.get("/").text) == [600]


def test_each_list_keeps_its_own_range(synced, db):
    add_wind_numbers(db, 500, FIVE_HOURS)
    set_range(synced, 3, 4, list="explore")

    assert row_500(synced, "explore")["Best hour"].startswith("−2%")
    assert row_500(synced, "my_segments")["Best hour"].startswith("−10%")


def test_bad_ranges_change_nothing_and_say_why(synced, db):
    add_wind_numbers(db, 500, FIVE_HOURS)
    set_range(synced, 3, 4)

    set_range(synced, 4, 3)
    page = synced.get("/").text
    assert "ends before it starts" in page and "Nothing was changed" in page
    assert row_500(synced)["Best hour"].startswith("−2%")
    assert "Nothing was changed" not in synced.get("/").text  # shown once

    set_range(synced, 1, 60)
    assert "within the next 48 hours" in synced.get("/").text
    set_range(synced, -3, 5)
    assert "within the next 48 hours" in synced.get("/").text
    synced.post("/timerange", data={"list": "my_segments", "start": "soon", "end": "later"})
    assert "start of the time range" in synced.get("/").text
    assert row_500(synced)["Best hour"].startswith("−2%")


def test_the_range_is_kept_while_the_rider_keeps_using_the_app(synced, db):
    add_wind_numbers(db, 500, FIVE_HOURS)
    set_range(synced, 3, 4)
    set_last_seen(db, minutes_ago=50)

    assert row_500(synced)["Best hour"].startswith("−2%")


def test_the_range_resets_after_more_than_an_hour_away(synced, db):
    add_wind_numbers(db, 500, FIVE_HOURS)
    set_range(synced, 3, 4)
    set_last_seen(db, minutes_ago=61)

    assert row_500(synced)["Best hour"].startswith("−10%")
    assert row_500(synced)["Best hour"].startswith("−10%")  # and it stays reset


def test_a_range_set_after_a_long_time_away_is_kept(synced, db):
    add_wind_numbers(db, 500, FIVE_HOURS)
    set_last_seen(db, minutes_ago=120)  # The page was left open, then the rider came back.

    synced.post("/timerange", data={"list": "my_segments", "start": hour(3), "end": hour(4)})

    assert row_500(synced)["Best hour"].startswith("−2%")


def test_coming_back_by_setting_a_range_still_resets_the_other_lists_range(synced, db):
    add_wind_numbers(db, 500, FIVE_HOURS)
    set_range(synced, 3, 4, list="explore")
    set_last_seen(db, minutes_ago=120)

    synced.post("/timerange", data={"list": "my_segments", "start": hour(3), "end": hour(4)})

    assert row_500(synced, "explore")["Best hour"].startswith("−10%")
    assert row_500(synced, "my_segments")["Best hour"].startswith("−2%")


def test_the_range_can_be_put_back_to_all_48_hours(synced, db):
    add_wind_numbers(db, 500, FIVE_HOURS)
    set_range(synced, 3, 4)

    synced.post("/timerange/clear", data={"list": "my_segments"})

    assert row_500(synced)["Best hour"].startswith("−10%")


class FakeRequest:
    def __init__(self, ranges):
        self.session = {"time_ranges": ranges}


def test_a_range_that_has_partly_passed_keeps_only_the_hours_to_come():
    now = this_hour() + timedelta(minutes=30)
    request = FakeRequest({"my_segments": [hour(-2), hour(3)]})

    assert saved_time_range(request, "my_segments", now) == (this_hour(), this_hour() + timedelta(hours=3))


def test_a_range_that_has_ended_goes_back_to_all_48_hours():
    request = FakeRequest({"my_segments": [hour(-3), hour(-1)]})

    assert saved_time_range(request, "my_segments", datetime.now(timezone.utc)) is None
