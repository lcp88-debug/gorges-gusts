# gorges-gusts
Gives daily notifications on best strava segments to put in a KOM attempt for, using live wind data.

## Running locally

Shows the segments from your last 90 days of road rides, on two lists, My Segments and Explore. Setting `POOLED_EXPLORE=true` also adds other riders' segments to Explore. Leave it off until webhooks are running.

### One-time setup

1. In your [Strava API settings](https://www.strava.com/settings/api), set **Authorization Callback Domain** to `localhost`, and upgrade the app to 10 athletes.
2. Install Postgres, Node (for the app's screens) and the Python packages:
   ```sh
   conda create -n gorges-pg -c conda-forge postgresql
   conda create -n gorges-node -c conda-forge nodejs=22
   python -m venv .venv
   .venv/bin/pip install --only-binary cryptography -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill it in. `SESSION_SECRET` can be any long random string. Generate `TOKEN_ENCRYPTION_KEY` with:
   ```sh
   .venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

### Start and stop

```sh
./start.sh
```

This starts Postgres if needed, builds the app's screens if their code has changed, starts the app, and opens <http://localhost:8000> in your browser. Press Ctrl+C to stop the app.

Postgres keeps running in the background. To stop it too:

```sh
conda run -n gorges-pg pg_ctl -D ~/.local/share/gorges-gusts/pgdata stop
```

### Tests

Tests use made-up data and never contact Strava. The server's tests use the `TEST_DATABASE_URL` database:

```sh
.venv/bin/python -m pytest
```

The screens' tests, including sorting and filtering, which happen in the browser:

```sh
cd web
PATH="$(conda info --base)/envs/gorges-node/bin:$PATH" npm test
```

### Working on the screens

The screens are a React app in `web/` (see `docs/adr/0003-react-app-with-list-rules-in-the-browser.md`). To see changes as you save, run `./start.sh` in one terminal and this in another, then open <http://localhost:5173>:

```sh
cd web
PATH="$(conda info --base)/envs/gorges-node/bin:$PATH" npm run dev
```

### Wind maps

The lists only cover the region set in `app/region.py`. Segments with any part of their route outside it don't appear and use no maps or weather calls.

The first wind update downloads land cover and elevation maps for the region to `~/.local/share/gorges-gusts/maps`, about 220 MB in total. They're kept for next time, and it's safe to delete them because they download again when needed.
