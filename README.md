# gorges-gusts
Gives daily notifications on best strava segments to put in a KOM attempt for, using live wind data.

## Running locally

Shows the segments from your last 90 days of road rides, sorted by your efforts and by popularity.

### One-time setup

1. In your [Strava API settings](https://www.strava.com/settings/api), set **Authorization Callback Domain** to `localhost`, and upgrade the app to 10 athletes.
2. Install Postgres and the Python packages:
   ```sh
   conda create -n gorges-pg -c conda-forge postgresql
   python -m venv .venv
   .venv/bin/pip install --only-binary cryptography -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill it in. `SESSION_SECRET` can be any long random string. Generate `TOKEN_ENCRYPTION_KEY` with:
   ```sh
   .venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

### Start and stop

Postgres doesn't restart by itself after a reboot:

```sh
conda run -n gorges-pg pg_ctl -D ~/.local/share/gorges-gusts/pgdata -l ~/.local/share/gorges-gusts/pgdata/server.log start
.venv/bin/python -m uvicorn app.asgi:app --port 8000
```

Then open <http://localhost:8000>. Open it using `localhost`, not `127.0.0.1`, so the login cookie survives the trip to Strava.

To stop Postgres:

```sh
conda run -n gorges-pg pg_ctl -D ~/.local/share/gorges-gusts/pgdata stop
```

### Tests

Tests use made-up data and the `TEST_DATABASE_URL` database, and never contact Strava:

```sh
.venv/bin/python -m pytest
```
