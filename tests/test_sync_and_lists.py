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
    assert [(r["name"], r["recent_efforts"]) for r in rows] == [("Climb", 3), ("Flat", 1)]
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


def test_second_rider_reuses_segment_details_without_a_request(conn, fake):
    fake.add_ride(1, [10])
    fake.add_segment(10, "Shared")
    run_sync(conn, fake, ATHLETE, NOW)

    fake.activities.clear()
    fake.calls.clear()
    fake.add_ride(2, [10, 10])
    run_sync(conn, fake, OTHER_ATHLETE, NOW)

    assert "segment:10" not in fake.calls
    mine = segment_list(conn, ATHLETE, "attempted", NOW)
    theirs = segment_list(conn, OTHER_ATHLETE, "attempted", NOW)
    assert [(r["name"], r["recent_efforts"]) for r in mine] == [("Shared", 1)]
    assert [(r["name"], r["recent_efforts"]) for r in theirs] == [("Shared", 2)]


def test_all_time_counts_are_gone_from_the_list(conn, fake):
    fake.add_ride(1, [10])
    fake.add_segment(10, "Climb", mine=40)
    run_sync(conn, fake, ATHLETE, NOW)

    assert "all_time_efforts" not in segment_list(conn, ATHLETE, "attempted", NOW)[0]


def test_old_per_rider_segment_table_is_replaced_on_startup(db):
    with db.connect() as conn:
        conn.execute("DROP TABLE segments")
        conn.execute(
            """
            CREATE TABLE segments (
                athlete_id BIGINT NOT NULL,
                id BIGINT NOT NULL,
                name TEXT NOT NULL,
                fetched_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (athlete_id, id)
            )
            """
        )

    db.init_schema()
    db.init_schema()

    with db.connect() as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'segments'"
            )
        }
    assert "athlete_id" not in columns
    assert "all_time_efforts" not in columns


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


def sync_two_riders_on_one_segment(conn, fake, second_sync_at):
    fake.add_ride(1, [10])
    fake.add_segment(10, "Shared")
    run_sync(conn, fake, ATHLETE, NOW)
    fake.activities.clear()
    fake.add_ride(2, [10])
    run_sync(conn, fake, OTHER_ATHLETE, second_sync_at)


def segment_ids_stored(conn):
    return [row["id"] for row in conn.execute("SELECT id FROM segments ORDER BY id")]


def test_reusing_details_does_not_extend_how_long_they_are_kept(conn, fake):
    sync_two_riders_on_one_segment(conn, fake, NOW + timedelta(days=2))

    purge_expired(conn, NOW + timedelta(days=6, minutes=1))

    assert segment_ids_stored(conn) == []


def test_expired_details_are_fetched_again_with_a_new_retention_period(conn, fake):
    sync_two_riders_on_one_segment(conn, fake, NOW + timedelta(days=2))
    purge_expired(conn, NOW + timedelta(days=6, minutes=1))

    fake.calls.clear()
    run_sync(conn, fake, OTHER_ATHLETE, NOW + timedelta(days=6, minutes=2))
    purge_expired(conn, NOW + timedelta(days=8))

    assert "segment:10" in fake.calls
    assert names(conn, athlete=OTHER_ATHLETE, now=NOW + timedelta(days=8)) == ["Shared"]


def test_details_stay_while_another_rider_still_needs_them(conn, fake):
    sync_two_riders_on_one_segment(conn, fake, NOW)

    fake.activities.clear()
    run_sync(conn, fake, ATHLETE, NOW)

    assert segment_ids_stored(conn) == [10]


def test_details_are_deleted_once_no_cached_ride_includes_the_segment(conn, fake):
    sync_two_riders_on_one_segment(conn, fake, NOW)

    fake.activities.clear()
    run_sync(conn, fake, ATHLETE, NOW)
    run_sync(conn, fake, OTHER_ATHLETE, NOW)

    assert segment_ids_stored(conn) == []


def test_purging_a_riders_rides_also_deletes_details_only_they_needed(conn, fake):
    fake.add_ride(1, [10])
    fake.add_segment(10, "Mine")
    run_sync(conn, fake, ATHLETE, NOW)
    conn.execute("UPDATE segments SET fetched_at = %s", (NOW + timedelta(days=5),))

    purge_expired(conn, NOW + timedelta(days=6, minutes=1))

    assert segment_ids_stored(conn) == []


def test_details_table_does_not_record_who_fetched_them(conn, fake):
    fake.add_ride(1, [10])
    fake.add_segment(10, "Mine")
    run_sync(conn, fake, ATHLETE, NOW)

    row = conn.execute("SELECT * FROM segments").fetchone()

    assert not {"athlete_id", "all_time_efforts"} & set(row)


# Most Popular with other riders' segments


def contribute(conn, athlete=OTHER_ATHLETE, contributes=True):
    conn.execute("UPDATE athletes SET contributes = %s WHERE id = %s", (contributes, athlete))


def sync_contributor_ride(conn, fake, segment_id=20, name="Their climb", **ride):
    fake.activities.clear()
    fake.add_ride(9, [segment_id], **ride)
    fake.add_segment(segment_id, name, athletes=900)
    run_sync(conn, fake, OTHER_ATHLETE, NOW)


def pooled_rows(conn, athlete=ATHLETE, sort="popular", now=NOW):
    return segment_list(conn, athlete, sort, now, pooled=True)


def test_most_popular_mixes_in_contributors_segments_with_zero_attempts(conn, fake):
    fake.add_ride(1, [10])
    fake.add_segment(10, "My road", athletes=5)
    run_sync(conn, fake, ATHLETE, NOW)
    contribute(conn)
    sync_contributor_ride(conn, fake)

    rows = pooled_rows(conn)

    assert [(r["name"], r["recent_efforts"]) for r in rows] == [("Their climb", 0), ("My road", 1)]


def test_contributors_attempts_are_not_counted_as_mine(conn, fake):
    fake.add_ride(1, [20])
    fake.add_segment(20, "Both ride this")
    run_sync(conn, fake, ATHLETE, NOW)
    contribute(conn)
    sync_contributor_ride(conn, fake, name="Both ride this")

    assert [r["recent_efforts"] for r in pooled_rows(conn)] == [1]


def test_most_attempted_stays_limited_to_my_own_segments(conn, fake):
    contribute(conn)
    sync_contributor_ride(conn, fake)

    assert pooled_rows(conn, sort="attempted") == []


def test_list_rows_name_no_other_rider(conn, fake):
    contribute(conn)
    sync_contributor_ride(conn, fake)

    row = pooled_rows(conn)[0]

    assert not [key for key in row if "athlete_id" in key]
    assert OTHER_ATHLETE not in row.values()


def test_most_popular_is_my_own_segments_when_pooling_is_off(conn, fake):
    contribute(conn)
    sync_contributor_ride(conn, fake)

    assert segment_list(conn, ATHLETE, "popular", NOW) == []


def test_opting_out_removes_segments_from_other_riders_lists(conn, fake):
    contribute(conn)
    sync_contributor_ride(conn, fake)
    assert len(pooled_rows(conn)) == 1

    contribute(conn, contributes=False)

    assert pooled_rows(conn) == []


def test_rider_who_has_not_seen_the_notice_does_not_contribute(conn, fake):
    sync_contributor_ride(conn, fake)

    assert pooled_rows(conn) == []


def test_segment_stays_if_another_contributor_also_rode_it(conn, fake):
    contribute(conn, ATHLETE)
    contribute(conn)
    third = 3003
    conn.execute(
        """
        INSERT INTO athletes (id, access_token_enc, refresh_token_enc, expires_at, scope)
        VALUES (%s, 'x', 'x', 0, 'read,activity:read_all')
        """,
        (third,),
    )
    fake.add_ride(1, [20])
    fake.add_segment(20, "Both ride this")
    run_sync(conn, fake, ATHLETE, NOW)
    sync_contributor_ride(conn, fake, name="Both ride this")

    contribute(conn, ATHLETE, contributes=False)

    assert [r["name"] for r in pooled_rows(conn, athlete=third)] == ["Both ride this"]


def test_opted_out_rider_still_sees_contributors_segments(conn, fake):
    contribute(conn, ATHLETE, contributes=False)
    contribute(conn)
    sync_contributor_ride(conn, fake)

    assert [r["name"] for r in pooled_rows(conn)] == ["Their climb"]


@pytest.mark.parametrize("visibility", ["followers_only", "only_me", None])
def test_only_rides_visible_to_everyone_feed_other_riders_lists(conn, fake, visibility):
    contribute(conn)
    sync_contributor_ride(conn, fake, visibility=visibility)

    assert pooled_rows(conn) == []
    assert names(conn, athlete=OTHER_ATHLETE) == ["Their climb"]


def test_contributors_rides_older_than_90_days_do_not_count(conn, fake):
    contribute(conn)
    sync_contributor_ride(conn, fake, when="2026-10-01T10:00:00Z")

    assert pooled_rows(conn, now=NOW + timedelta(days=60)) == []


def test_contributor_drops_out_when_their_cached_rides_expire(conn, fake):
    contribute(conn)
    sync_contributor_ride(conn, fake)

    purge_expired(conn, NOW + timedelta(days=6, minutes=1))

    assert pooled_rows(conn, now=NOW + timedelta(days=6, minutes=1)) == []
