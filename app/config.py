import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger(__name__)

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
    pooled_explore: bool = False
    # Shows the fastest time anyone has recorded on each segment. Off until chosen.
    fastest_time: bool = False
    map_dir: str = str(Path.home() / ".local/share/gorges-gusts/maps")


def load_settings() -> Settings:
    load_dotenv()
    if "POOLED_MOST_POPULAR" in os.environ:
        # Renamed in ADR 0001. The old name's value is not read.
        log.warning("POOLED_MOST_POPULAR is no longer used and is ignored. Use POOLED_EXPLORE instead.")
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        strava_client_id=os.environ["STRAVA_CLIENT_ID"],
        strava_client_secret=os.environ["STRAVA_CLIENT_SECRET"],
        session_secret=os.environ["SESSION_SECRET"],
        token_encryption_key=os.environ["TOKEN_ENCRYPTION_KEY"],
        pooled_explore=os.environ.get("POOLED_EXPLORE", "").lower() == "true",
        fastest_time=os.environ.get("FASTEST_TIME", "").lower() == "true",
    )
