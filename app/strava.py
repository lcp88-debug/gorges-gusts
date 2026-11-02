import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests

API_URL = "https://www.strava.com/api/v3"
OAUTH_URL = "https://www.strava.com/oauth"
REQUIRED_SCOPE = "activity:read_all"
TIMEOUT_SECONDS = 30
MAX_PER_PAGE = 200


class RateLimited(Exception):
    def __init__(self, resume_at: datetime):
        super().__init__(f"Strava rate limit reached, resume at {resume_at}")
        self.resume_at = resume_at


class AuthExpired(Exception):
    """The athlete's tokens no longer work, so they need to log in again."""


class NotFound(Exception):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _next_quarter_hour(now: datetime) -> datetime:
    start = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
    return start + timedelta(minutes=15)


def _next_midnight_utc(now: datetime) -> datetime:
    utc = now.astimezone(timezone.utc)
    return utc.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def _pair(value: str | None) -> tuple[int, int] | None:
    """Parse a rate limit header value such as "100,1000"."""
    try:
        short, daily = (value or "").split(",")
        return int(short), int(daily)
    except ValueError:
        return None


class RateLimiter:
    """Tracks Strava's per-application limit, shared by every athlete's requests."""

    HEADERS = (
        ("X-ReadRateLimit-Limit", "X-ReadRateLimit-Usage"),
        ("X-RateLimit-Limit", "X-RateLimit-Usage"),
    )

    def __init__(self):
        self._lock = threading.Lock()
        self._pause_until: datetime | None = None

    def check(self, now: datetime) -> None:
        with self._lock:
            if self._pause_until and now < self._pause_until:
                raise RateLimited(self._pause_until)

    def observe(self, headers, now: datetime) -> None:
        """Pause ahead of the limit, using the usage Strava reports."""
        for limit_header, usage_header in self.HEADERS:
            limit = _pair(headers.get(limit_header))
            usage = _pair(headers.get(usage_header))
            if not limit or not usage:
                continue
            if usage[1] >= limit[1]:
                self._pause(_next_midnight_utc(now))
            elif usage[0] >= limit[0]:
                self._pause(_next_quarter_hour(now))

    def trip(self, headers, now: datetime) -> datetime:
        """Handle a 429, which can arrive even if the reported usage looks fine."""
        self.observe(headers, now)
        self._pause(_next_quarter_hour(now))
        return self._pause_until

    def _pause(self, until: datetime) -> None:
        with self._lock:
            if not self._pause_until or until > self._pause_until:
                self._pause_until = until


class StravaClient:
    """Read-only Strava calls on behalf of one athlete."""

    def __init__(
        self,
        access_token: Callable[[], str],
        limiter: RateLimiter,
        clock: Callable[[], datetime] = _utcnow,
    ):
        self._access_token = access_token
        self._limiter = limiter
        self._clock = clock

    def _get(self, path: str, params: dict | None = None):
        self._limiter.check(self._clock())
        resp = requests.get(
            f"{API_URL}{path}",
            headers={"Authorization": f"Bearer {self._access_token()}"},
            params=params,
            timeout=TIMEOUT_SECONDS,
        )
        now = self._clock()
        if resp.status_code == 429:
            raise RateLimited(self._limiter.trip(resp.headers, now))
        self._limiter.observe(resp.headers, now)
        if resp.status_code == 401:
            raise AuthExpired()
        if resp.status_code == 404:
            raise NotFound(path)
        resp.raise_for_status()
        return resp.json()

    def list_activities(self, after: datetime) -> list[dict]:
        activities = []
        page = 1
        while True:
            batch = self._get(
                "/athlete/activities",
                {"after": int(after.timestamp()), "per_page": MAX_PER_PAGE, "page": page},
            )
            activities.extend(batch)
            if len(batch) < MAX_PER_PAGE:
                return activities
            page += 1

    def get_activity(self, activity_id: int) -> dict:
        return self._get(f"/activities/{activity_id}", {"include_all_efforts": "true"})

    def get_segment(self, segment_id: int) -> dict:
        return self._get(f"/segments/{segment_id}")


class StravaOAuth:
    def __init__(self, client_id: str, client_secret: str):
        self._client_id = client_id
        self._client_secret = client_secret

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "approval_prompt": "auto",
                "scope": f"read,{REQUIRED_SCOPE}",
                "state": state,
            }
        )
        return f"{OAUTH_URL}/authorize?{query}"

    def _token(self, **fields) -> dict:
        resp = requests.post(
            f"{OAUTH_URL}/token",
            data={"client_id": self._client_id, "client_secret": self._client_secret, **fields},
            timeout=TIMEOUT_SECONDS,
        )
        if resp.status_code in (400, 401):
            raise AuthExpired()
        resp.raise_for_status()
        return resp.json()

    def exchange_code(self, code: str) -> dict:
        return self._token(code=code, grant_type="authorization_code")

    def refresh(self, refresh_token: str) -> dict:
        return self._token(refresh_token=refresh_token, grant_type="refresh_token")
