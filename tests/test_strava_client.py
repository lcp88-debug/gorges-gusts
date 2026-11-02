from datetime import datetime, timedelta, timezone

import pytest
import responses

from app.strava import (
    API_URL,
    OAUTH_URL,
    AuthExpired,
    NotFound,
    RateLimited,
    RateLimiter,
    StravaClient,
    StravaOAuth,
)

NOW = datetime(2026, 11, 1, 12, 7, tzinfo=timezone.utc)
NEXT_QUARTER = datetime(2026, 11, 1, 12, 15, tzinfo=timezone.utc)
NEXT_MIDNIGHT = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)


def usage(short, daily):
    return {"X-ReadRateLimit-Limit": "100,1000", "X-ReadRateLimit-Usage": f"{short},{daily}"}


def make_client(limiter=None):
    return StravaClient(lambda: "token", limiter or RateLimiter(), clock=lambda: NOW)


@responses.activate
def test_sends_the_token_and_asks_for_all_efforts():
    responses.get(f"{API_URL}/activities/1", json={"id": 1})

    make_client().get_activity(1)

    request = responses.calls[0].request
    assert request.headers["Authorization"] == "Bearer token"
    assert "include_all_efforts=true" in request.url


@responses.activate
def test_list_activities_pages_only_while_pages_are_full():
    responses.get(f"{API_URL}/athlete/activities", json=[{"id": i} for i in range(200)])
    responses.get(f"{API_URL}/athlete/activities", json=[{"id": 999}])

    activities = make_client().list_activities(after=NOW - timedelta(days=90))

    assert len(activities) == 201
    assert len(responses.calls) == 2


@responses.activate
def test_pauses_at_the_15_minute_limit_before_getting_a_429():
    responses.get(f"{API_URL}/segments/1", json={"id": 1}, headers=usage(100, 200))
    client = make_client()

    client.get_segment(1)
    with pytest.raises(RateLimited) as exc:
        client.get_segment(1)

    assert exc.value.resume_at == NEXT_QUARTER
    assert len(responses.calls) == 1


@responses.activate
def test_pauses_until_midnight_utc_at_the_daily_limit():
    responses.get(f"{API_URL}/segments/1", json={"id": 1}, headers=usage(5, 1000))
    client = make_client()

    client.get_segment(1)
    with pytest.raises(RateLimited) as exc:
        client.get_segment(1)

    assert exc.value.resume_at == NEXT_MIDNIGHT


@responses.activate
def test_429_pauses_and_the_limit_is_shared_between_athletes():
    responses.get(f"{API_URL}/segments/1", status=429, json={})
    limiter = RateLimiter()

    with pytest.raises(RateLimited) as exc:
        make_client(limiter).get_segment(1)
    assert exc.value.resume_at == NEXT_QUARTER

    with pytest.raises(RateLimited):
        make_client(limiter).get_segment(1)
    assert len(responses.calls) == 1


def test_requests_continue_after_the_pause_ends():
    limiter = RateLimiter()
    limiter.trip({}, NOW)

    with pytest.raises(RateLimited):
        limiter.check(NOW)
    limiter.check(NEXT_QUARTER)


@responses.activate
@pytest.mark.parametrize("status,error", [(401, AuthExpired), (404, NotFound)])
def test_maps_error_statuses(status, error):
    responses.get(f"{API_URL}/segments/1", status=status, json={})

    with pytest.raises(error):
        make_client().get_segment(1)


def test_authorize_url_asks_for_the_needed_scope():
    url = StravaOAuth("12345", "secret").authorize_url("http://localhost:8000/cb", "abc")

    assert "client_id=12345" in url
    assert "scope=read%2Cactivity%3Aread_all" in url
    assert "state=abc" in url
    assert "secret" not in url


@responses.activate
def test_rejected_refresh_means_logging_in_again():
    responses.post(f"{OAUTH_URL}/token", status=400, json={})

    with pytest.raises(AuthExpired):
        StravaOAuth("12345", "secret").refresh("old")
