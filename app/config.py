import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

RIDE_WINDOW = timedelta(days=90)

# Strava allows 7 days at most (API Policy s.6.2). The margin covers the hourly
# purge and short periods when this machine is off.
CACHE_TTL = timedelta(days=6)


@dataclass(frozen=True)
class Settings:
    database_url: str
    strava_client_id: str
    strava_client_secret: str
    session_secret: str
    token_encryption_key: str
    redirect_uri: str = "http://localhost:8000/auth/callback"
    # Must stay off until webhooks are running.
    pooled_most_popular: bool = False
    map_dir: str = str(Path.home() / ".local/share/gorges-gusts/maps")


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        strava_client_id=os.environ["STRAVA_CLIENT_ID"],
        strava_client_secret=os.environ["STRAVA_CLIENT_SECRET"],
        session_secret=os.environ["SESSION_SECRET"],
        token_encryption_key=os.environ["TOKEN_ENCRYPTION_KEY"],
        pooled_most_popular=os.environ.get("POOLED_MOST_POPULAR", "").lower() == "true",
    )
