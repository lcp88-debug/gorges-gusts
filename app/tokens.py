import time

from cryptography.fernet import Fernet, InvalidToken

from app.db import Database
from app.strava import AuthExpired, StravaOAuth

EXPIRY_MARGIN_SECONDS = 60


class TokenStore:
    """Keeps each athlete's Strava tokens encrypted in the database."""

    def __init__(self, db: Database, oauth: StravaOAuth, encryption_key: str):
        self._db = db
        self._oauth = oauth
        self._fernet = Fernet(encryption_key)

    def _encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def _decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise AuthExpired() from exc

    def save_new_athlete(self, athlete_id: int, tokens: dict, scope: str) -> None:
        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT INTO athletes (id, access_token_enc, refresh_token_enc, expires_at, scope)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    access_token_enc = EXCLUDED.access_token_enc,
                    refresh_token_enc = EXCLUDED.refresh_token_enc,
                    expires_at = EXCLUDED.expires_at,
                    scope = EXCLUDED.scope
                """,
                (
                    athlete_id,
                    self._encrypt(tokens["access_token"]),
                    self._encrypt(tokens["refresh_token"]),
                    tokens["expires_at"],
                    scope,
                ),
            )

    def access_token(self, athlete_id: int) -> str:
        """Return a working access token, refreshing and saving it if needed."""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT access_token_enc, refresh_token_enc, expires_at FROM athletes WHERE id = %s",
                (athlete_id,),
            ).fetchone()
            if row is None:
                raise AuthExpired()
            if row["expires_at"] > time.time() + EXPIRY_MARGIN_SECONDS:
                return self._decrypt(row["access_token_enc"])

            tokens = self._oauth.refresh(self._decrypt(row["refresh_token_enc"]))
            conn.execute(
                """
                UPDATE athletes
                SET access_token_enc = %s, refresh_token_enc = %s, expires_at = %s
                WHERE id = %s
                """,
                (
                    self._encrypt(tokens["access_token"]),
                    self._encrypt(tokens["refresh_token"]),
                    tokens["expires_at"],
                    athlete_id,
                ),
            )
            return tokens["access_token"]
