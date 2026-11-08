import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import psycopg
import requests

from app.db import Database, purge_expired
from app.strava import AuthExpired, RateLimited, StravaClient
from app.sync import run_sync

log = logging.getLogger(__name__)

POLL_SECONDS = 2
PURGE_INTERVAL = timedelta(hours=1)
ClientFactory = Callable[[int], StravaClient]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enqueue_sync(conn: psycopg.Connection, athlete_id: int) -> None:
    """Queue a sync, unless this athlete already has one active."""
    conn.execute(
        """
        INSERT INTO sync_jobs (athlete_id, status) VALUES (%s, 'pending')
        ON CONFLICT (athlete_id) WHERE status IN ('pending', 'running', 'paused') DO NOTHING
        """,
        (athlete_id,),
    )


class Worker:
    """Runs sync jobs one at a time, because all athletes share Strava's limit."""

    def __init__(
        self,
        db: Database,
        client_for: ClientFactory,
        clock: Callable[[], datetime] = _utcnow,
        update_wind: Callable[[psycopg.Connection, datetime], bool] | None = None,
    ):
        self._db = db
        self._client_for = client_for
        self._clock = clock
        self._update_wind = update_wind
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_purge: datetime | None = None

    def start(self) -> None:
        self.recover_interrupted()
        self._thread = threading.Thread(target=self._loop, name="sync-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.purge_if_due()
                if not self.run_next_job() and not self.run_wind_update():
                    self._stop.wait(POLL_SECONDS)
            except Exception:
                log.exception("Sync worker loop error")
                self._stop.wait(POLL_SECONDS)

    def recover_interrupted(self) -> None:
        """Requeue a job that was running when the app last stopped."""
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE sync_jobs SET status = 'pending', updated_at = %s WHERE status = 'running'",
                (self._clock(),),
            )

    def purge_if_due(self) -> None:
        now = self._clock()
        if self._last_purge and now - self._last_purge < PURGE_INTERVAL:
            return
        with self._db.connect() as conn:
            purge_expired(conn, now)
        self._last_purge = now

    def run_wind_update(self) -> bool:
        """Update one rider's wind effects, if any are due. Returns False if none are."""
        if self._update_wind is None:
            return False
        with self._db.connect() as conn:
            return self._update_wind(conn, self._clock())

    def run_next_job(self) -> bool:
        """Run the next job that's ready. Returns False if none is."""
        with self._db.connect() as conn:
            job = conn.execute(
                """
                UPDATE sync_jobs SET status = 'running', resume_at = NULL, updated_at = %(now)s
                WHERE id = (
                    SELECT id FROM sync_jobs
                    WHERE status = 'pending' OR (status = 'paused' AND resume_at <= %(now)s)
                    ORDER BY created_at
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id, athlete_id
                """,
                {"now": self._clock()},
            ).fetchone()
            if job is None:
                return False
            self._run(conn, job["id"], job["athlete_id"])
            return True

    def _run(self, conn: psycopg.Connection, job_id: int, athlete_id: int) -> None:
        def progress(phase: str, done: int, total: int) -> None:
            conn.execute(
                "UPDATE sync_jobs SET phase = %s, done = %s, total = %s, updated_at = %s WHERE id = %s",
                (phase, done, total, self._clock(), job_id),
            )

        try:
            run_sync(conn, self._client_for(athlete_id), athlete_id, self._clock(), progress)
        except RateLimited as exc:
            self._finish(conn, job_id, "paused", resume_at=exc.resume_at)
        except AuthExpired:
            self._finish(conn, job_id, "failed", error="Please log in with Strava again.")
        except requests.RequestException:
            log.exception("Strava request failed for job %s", job_id)
            self._finish(conn, job_id, "failed", error="Couldn't reach Strava. Please try Refresh.")
        except Exception:
            log.exception("Sync failed for job %s", job_id)
            self._finish(conn, job_id, "failed", error="Something went wrong. Please try Refresh.")
        else:
            with conn.transaction():
                self._finish(conn, job_id, "done")
                conn.execute(
                    "UPDATE athletes SET last_synced_at = %s, wind_stale = TRUE WHERE id = %s",
                    (self._clock(), athlete_id),
                )

    def _finish(
        self,
        conn: psycopg.Connection,
        job_id: int,
        status: str,
        resume_at: datetime | None = None,
        error: str | None = None,
    ) -> None:
        conn.execute(
            "UPDATE sync_jobs SET status = %s, resume_at = %s, error = %s, updated_at = %s WHERE id = %s",
            (status, resume_at, error, self._clock(), job_id),
        )
