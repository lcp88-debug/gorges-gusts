from datetime import timedelta

import pytest

from app.db import purge_expired
from app.lists import segment_list
from app.strava import NotFound, RateLimited
from app.sync import run_sync
from tests.conftest import ATHLETE, NOW, OTHER_ATHLETE


def names(conn, sort="attempted", athlete=ATHLETE, now=NOW):
    return [row["name"] for row in segment_list(conn, athlete, sort, now)]


def test_builds_unique_segments_with_90_day_counts(conn, fake):
    fake.add_ride(1, [10, 20])
    fake.add_ride(2, [10, 10])
    fake.add_segment(10, "Climb", mine=40)
    fake.add_segment(20, "Flat", mine=3)

    run_sync(conn, fake, ATHLETE, NOW)

    rows = segment_list(conn, ATHLETE, "attempted", NOW)
    assert [(r["name"], r["recent_efforts"], r["all_time_efforts"]) for r in rows] == [
        ("Climb", 3, 40),
        ("Flat", 1, 3),
    ]
    assert fake.calls.count("segment:10") == 1


def test_two_lists_use_their_own_sort_order(conn, fake):
    fake.add_ride(1, [10, 10, 20])
    fake.add_segment(10, "Often ridden", athletes=5)
    fake.add_segment(20, "Popular", athletes=900)

    run_sync(conn, fake, ATHLETE, NOW)

    assert names(conn, "attempted") == ["Often ridden", "Popular"]
    assert names(conn, "popular") == ["Popular", "Often ridden"]


def test_ties_sort_by_name_and_missing_counts_sort_last(conn, fake):
    fake.add_ride(1, [10, 20, 30])
    fake.add_segment(10, "bravo", athletes=5)
    fake.add_segment(20, "Alpha", athletes=5)
    fake.add_segment(30, "Charlie", athletes=None)

    run_sync(conn, fake, ATHLETE, NOW)

    assert names(conn, "attempted") == ["Alpha", "bravo", "Charlie"]
    assert names(conn, "popular") == ["Alpha", "bravo", "Charlie"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"sport_type": "GravelRide"},
        {"sport_type": "MountainBikeRide"},
        {"sport_type": "VirtualRide"},
        {"sport_type": "EBikeRide"},
        {"sport_type": "Run"},
        {"trainer": True},
        {"manual": True},
        {"private": True},
    ],
)
def test_only_outdoor_public_road_rides_count(conn, fake, overrides):
    fake.add_ride(1, [10], **overrides)
    fake.add_segment(10, "Excluded")

    run_sync(conn, fake, ATHLETE, NOW)

    assert names(conn) == []
    assert "activity:1" not in fake.calls


def test_ride_older_than_90_days_is_ignored(conn, fake):
    fake.add_ride(1, [10], when="2026-07-01T10:00:00Z")
    fake.add_segment(10, "Old")

    run_sync(conn, fake, ATHLETE, NOW)

    assert names(conn) == []


def test_private_segment_on_public_ride_is_skipped(conn, fake):
    fake.add_ride(1, [10, 20])
    fake.details[1]["segment_efforts"][0]["segment"]["private"] = True
    fake.add_segment(10, "Secret")
    fake.add_segment(20, "Public")

    run_sync(conn, fake, ATHLETE, NOW)

    assert names(conn) == ["Public"]
    assert "segment:10" not in fake.calls


def test_segment_with_no_details_is_dropped(conn, fake):
    fake.add_ride(1, [10, 20])
    fake.add_segment(20, "Still here")
    fake.fail_on["segment:10"] = NotFound("gone")

    run_sync(conn, fake, ATHLETE, NOW)

    assert names(conn) == ["Still here"]


def test_second_sync_fetches_only_new_rides_and_segments(conn, fake):
    fake.add_ride(1, [10])
    fake.add_segment(10, "Known")
    run_sync(conn, fake, ATHLETE, NOW)

    fake.calls.clear()
    fake.add_ride(2, [10, 20])
    fake.add_segment(20, "New")
    run_sync(conn, fake, ATHLETE, NOW)

    assert fake.calls == ["list", "activity:2", "segment:20"]
    assert names(conn) == ["Known", "New"]


def test_ride_gone_from_strava_is_removed_with_its_segments(conn, fake):
    fake.add_ride(1, [10])
    fake.add_ride(2, [20])
    fake.add_segment(10, "Keep")
    fake.add_segment(20, "Remove")
    run_sync(conn, fake, ATHLETE, NOW)

    fake.activities = [a for a in fake.activities if a["id"] != 2]
    run_sync(conn, fake, ATHLETE, NOW)

    assert names(conn) == ["Keep"]
    assert conn.execute("SELECT count(*) AS n FROM segments").fetchone()["n"] == 1


def test_efforts_past_90_days_stop_counting_before_the_next_sync(conn, fake):
    fake.add_ride(1, [10], when="2026-10-01T10:00:00Z")
    fake.add_segment(10, "Ageing")
    run_sync(conn, fake, ATHLETE, NOW)

    assert names(conn, now=NOW + timedelta(days=60)) == []


def test_rate_limit_keeps_progress_and_resume_skips_finished_work(conn, fake):
    fake.add_ride(1, [10])
    fake.add_ride(2, [20])
    fake.add_segment(10, "First")
    fake.add_segment(20, "Second")
    fake.fail_on["activity:2"] = RateLimited(NOW + timedelta(minutes=15))

    with pytest.raises(RateLimited):
        run_sync(conn, fake, ATHLETE, NOW)

    fake.calls.clear()
    run_sync(conn, fake, ATHLETE, NOW)

    assert "activity:1" not in fake.calls
    assert "activity:2" in fake.calls
    assert names(conn) == ["First", "Second"]


def test_athletes_only_see_their_own_segments(conn, fake):
    fake.add_ride(1, [10])
    fake.add_segment(10, "Mine")
    run_sync(conn, fake, ATHLETE, NOW)

    assert names(conn, athlete=OTHER_ATHLETE) == []


def test_two_athletes_on_one_segment_keep_separate_stats(conn, fake):
    fake.add_ride(1, [10])
    fake.add_segment(10, "Shared", mine=7)
    run_sync(conn, fake, ATHLETE, NOW)

    fake.activities.clear()
    fake.add_ride(2, [10])
    fake.add_segment(10, "Shared", mine=99)
    run_sync(conn, fake, OTHER_ATHLETE, NOW)

    mine = segment_list(conn, ATHLETE, "attempted", NOW)
    theirs = segment_list(conn, OTHER_ATHLETE, "attempted", NOW)
    assert mine[0]["all_time_efforts"] == 7
    assert theirs[0]["all_time_efforts"] == 99


def test_polyline_is_stored(conn, fake):
    fake.add_ride(1, [10])
    fake.add_segment(10, "Route")
    run_sync(conn, fake, ATHLETE, NOW)

    row = conn.execute("SELECT polyline FROM segments WHERE id = 10").fetchone()
    assert row["polyline"] == "poly10"


def test_purge_removes_only_data_past_the_cache_limit(conn, fake):
    fake.add_ride(1, [10])
    fake.add_segment(10, "Old")
    run_sync(conn, fake, ATHLETE, NOW)

    purge_expired(conn, NOW + timedelta(days=5))
    assert conn.execute("SELECT count(*) AS n FROM rides").fetchone()["n"] == 1

    purge_expired(conn, NOW + timedelta(days=6, minutes=1))
    for table in ("rides", "efforts", "segments"):
        assert conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM athletes").fetchone()["n"] == 2
