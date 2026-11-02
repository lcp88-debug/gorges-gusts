from datetime import timedelta

import requests

from app.strava import AuthExpired, RateLimited
from app.worker import Worker, enqueue_sync
from tests.conftest import ATHLETE, NOW


def make_worker(db, fake, clock=lambda: NOW):
    return Worker(db, lambda athlete_id: fake, clock=clock)


def job(conn):
    return conn.execute("SELECT * FROM sync_jobs ORDER BY id DESC LIMIT 1").fetchone()


def test_a_successful_job_finishes_and_records_the_sync_time(conn, db, fake):
    fake.add_ride(1, [10])
    fake.add_segment(10, "Climb")
    enqueue_sync(conn, ATHLETE)

    assert make_worker(db, fake).run_next_job() is True

    finished = job(conn)
    assert finished["status"] == "done"
    assert (finished["phase"], finished["done"], finished["total"]) == ("segments", 1, 1)
    athlete = conn.execute("SELECT last_synced_at FROM athletes WHERE id = %s", (ATHLETE,)).fetchone()
    assert athlete["last_synced_at"] == NOW


def test_no_job_means_nothing_runs(conn, db, fake):
    assert make_worker(db, fake).run_next_job() is False


def test_only_one_active_job_per_athlete(conn):
    enqueue_sync(conn, ATHLETE)
    enqueue_sync(conn, ATHLETE)

    assert conn.execute("SELECT count(*) AS n FROM sync_jobs").fetchone()["n"] == 1


def test_rate_limited_job_pauses_then_resumes_when_due(conn, db, fake):
    resume_at = NOW + timedelta(minutes=8)
    fake.add_ride(1, [10])
    fake.add_segment(10, "Climb")
    fake.fail_on["segment:10"] = RateLimited(resume_at)
    enqueue_sync(conn, ATHLETE)
    worker = make_worker(db, fake)

    worker.run_next_job()
    assert job(conn)["status"] == "paused"
    assert job(conn)["resume_at"] == resume_at

    assert worker.run_next_job() is False

    assert make_worker(db, fake, clock=lambda: resume_at).run_next_job() is True
    assert job(conn)["status"] == "done"


def test_job_interrupted_by_a_restart_runs_again(conn, db, fake):
    conn.execute("INSERT INTO sync_jobs (athlete_id, status) VALUES (%s, 'running')", (ATHLETE,))
    worker = make_worker(db, fake)

    assert worker.run_next_job() is False
    worker.recover_interrupted()

    assert worker.run_next_job() is True
    assert job(conn)["status"] == "done"


def test_expired_login_fails_with_a_clear_message(conn, db, fake):
    fake.fail_on["list"] = AuthExpired()
    enqueue_sync(conn, ATHLETE)

    make_worker(db, fake).run_next_job()

    assert job(conn)["status"] == "failed"
    assert "log in" in job(conn)["error"]


def test_network_failure_can_be_retried(conn, db, fake):
    fake.fail_on["list"] = requests.ConnectionError()
    enqueue_sync(conn, ATHLETE)
    worker = make_worker(db, fake)

    worker.run_next_job()
    assert job(conn)["status"] == "failed"

    enqueue_sync(conn, ATHLETE)
    worker.run_next_job()
    assert job(conn)["status"] == "done"


def test_purge_runs_at_start_and_then_hourly(conn, db, fake):
    fake.add_ride(1, [10])
    fake.add_segment(10, "Climb")
    enqueue_sync(conn, ATHLETE)
    current = {"now": NOW}
    worker = make_worker(db, fake, clock=lambda: current["now"])
    worker.run_next_job()

    current["now"] = NOW + timedelta(days=5)
    worker.purge_if_due()
    current["now"] = NOW + timedelta(days=6, minutes=30)
    worker.purge_if_due()
    assert conn.execute("SELECT count(*) AS n FROM rides").fetchone()["n"] == 0
