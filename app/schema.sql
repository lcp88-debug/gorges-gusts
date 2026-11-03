-- Permanent: only what's needed to keep an athlete connected.
CREATE TABLE IF NOT EXISTS athletes (
    id                BIGINT PRIMARY KEY,
    access_token_enc  TEXT NOT NULL,
    refresh_token_enc TEXT NOT NULL,
    expires_at        BIGINT NOT NULL,
    scope             TEXT NOT NULL,
    last_synced_at    TIMESTAMPTZ
);
-- NULL until the athlete has connected with the contributing notice on screen.
ALTER TABLE athletes ADD COLUMN IF NOT EXISTS contributes BOOLEAN;

-- Strava data: every row carries fetched_at and is purged after CACHE_TTL.
CREATE TABLE IF NOT EXISTS rides (
    id         BIGINT PRIMARY KEY,
    athlete_id BIGINT NOT NULL REFERENCES athletes (id) ON DELETE CASCADE,
    start_date TIMESTAMPTZ NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS rides_athlete_idx ON rides (athlete_id);

CREATE TABLE IF NOT EXISTS efforts (
    id         BIGINT PRIMARY KEY,
    athlete_id BIGINT NOT NULL REFERENCES athletes (id) ON DELETE CASCADE,
    ride_id    BIGINT NOT NULL REFERENCES rides (id) ON DELETE CASCADE,
    segment_id BIGINT NOT NULL,
    start_date TIMESTAMPTZ NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL
);
-- True only for rides visible to everyone. Older rows stay false until refetched.
ALTER TABLE efforts ADD COLUMN IF NOT EXISTS shareable BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS efforts_athlete_segment_idx ON efforts (athlete_id, segment_id);
CREATE INDEX IF NOT EXISTS efforts_ride_idx ON efforts (ride_id);
CREATE INDEX IF NOT EXISTS efforts_segment_idx ON efforts (segment_id);

-- Older versions stored segments per athlete. It's only a cache, so start it again.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'segments' AND column_name = 'athlete_id'
    ) THEN
        DROP TABLE segments;
    END IF;
END $$;

-- Shared between athletes, and deliberately doesn't record who fetched each one.
CREATE TABLE IF NOT EXISTS segments (
    id            BIGINT PRIMARY KEY,
    name          TEXT NOT NULL,
    distance_m    DOUBLE PRECISION,
    average_grade DOUBLE PRECISION,
    polyline      TEXT,
    athlete_count BIGINT,
    effort_count  BIGINT,
    fetched_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_jobs (
    id         BIGSERIAL PRIMARY KEY,
    athlete_id BIGINT NOT NULL REFERENCES athletes (id) ON DELETE CASCADE,
    status     TEXT NOT NULL CHECK (status IN ('pending', 'running', 'paused', 'done', 'failed')),
    phase      TEXT,
    done       INTEGER NOT NULL DEFAULT 0,
    total      INTEGER NOT NULL DEFAULT 0,
    resume_at  TIMESTAMPTZ,
    error      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_job_per_athlete
    ON sync_jobs (athlete_id) WHERE status IN ('pending', 'running', 'paused');
