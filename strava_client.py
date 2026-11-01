import os
import json
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN_FILE = Path("strava_tokens.json")


def load_tokens():
    """Read saved tokens, or fall back to the refresh token in .env on first run."""
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())
    return {
        "refresh_token": os.getenv("STRAVA_REFRESH_TOKEN"),
        "access_token": None,
        "expires_at": 0,
    }


def save_tokens(tokens):
    """Write tokens to strava_tokens.json (this file is gitignored)."""
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2))


def get_access_token():
    """Return a valid access token, refreshing it if it has expired."""
    tokens = load_tokens()

    # Reuse the current token if it's valid for at least another minute
    if tokens["access_token"] and tokens["expires_at"] > time.time() + 60:
        return tokens["access_token"]

    resp = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": os.getenv("STRAVA_CLIENT_ID"),
            "client_secret": os.getenv("STRAVA_CLIENT_SECRET"),
            "refresh_token": tokens["refresh_token"],
            "grant_type": "refresh_token",
        },
    )
    if not resp.ok:
        print("Strava said:", resp.text)
    resp.raise_for_status()
    data = resp.json()

    save_tokens({
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],  # may be new; always keep the latest
        "expires_at": data["expires_at"],
    })
    return data["access_token"]


def get_activities(n=10):
    """Fetch your n most recent activities."""
    resp = requests.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers={"Authorization": f"Bearer {get_access_token()}"},
        params={"per_page": n},
    )
    if not resp.ok:
        print("Strava said:", resp.text)
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    for ride in get_activities(10):
        miles = ride["distance"] / 1609.34
        print(f'{ride["start_date_local"][:10]}  {ride["name"]}  ({miles:.1f} mi)')