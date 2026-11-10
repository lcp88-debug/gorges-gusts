"""Reading settings from the environment."""

import logging

import pytest

from app.config import load_settings


@pytest.fixture
def env(monkeypatch):
    for name in ("DATABASE_URL", "STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "SESSION_SECRET",
                 "TOKEN_ENCRYPTION_KEY"):
        monkeypatch.setenv(name, "made-up")
    monkeypatch.delenv("POOLED_EXPLORE", raising=False)
    monkeypatch.delenv("POOLED_MOST_POPULAR", raising=False)
    monkeypatch.setattr("app.config.load_dotenv", lambda: None)  # Not the developer's .env.
    return monkeypatch


def test_the_old_pooling_name_warns_and_is_not_read(env, caplog):
    env.setenv("POOLED_MOST_POPULAR", "true")

    with caplog.at_level(logging.WARNING):
        settings = load_settings()

    assert settings.pooled_explore is False
    assert "Use POOLED_EXPLORE instead" in caplog.text


def test_no_warning_without_the_old_name(env, caplog):
    env.setenv("POOLED_EXPLORE", "true")

    with caplog.at_level(logging.WARNING):
        settings = load_settings()

    assert settings.pooled_explore is True
    assert caplog.text == ""
