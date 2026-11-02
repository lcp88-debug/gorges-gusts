#!/bin/bash
# Starts Postgres if needed, then the app, and opens it in the browser.
set -euo pipefail
cd "$(dirname "$0")"

URL="http://localhost:8000"
PORT=8000
PGDATA_DIR="$HOME/.local/share/gorges-gusts/pgdata"
CONDA_BASE="$(conda info --base 2>/dev/null || echo /Applications/anaconda3)"
PG_CTL="$CONDA_BASE/envs/gorges-pg/bin/pg_ctl"

if [ ! -x "$PG_CTL" ] || [ ! -x .venv/bin/python ]; then
  echo "Setup isn't finished. See 'One-time setup' in README.md." >&2
  exit 1
fi

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Something is already running on port $PORT, probably Gorges Gusts."
  echo "Opening $URL"
  open "$URL"
  exit 0
fi

if ! "$PG_CTL" -D "$PGDATA_DIR" status >/dev/null 2>&1; then
  echo "Starting Postgres..."
  "$PG_CTL" -D "$PGDATA_DIR" -l "$PGDATA_DIR/server.log" -w start >/dev/null
fi

# Open the browser once the app is answering. Gives up after 30 seconds.
(
  for _ in {1..60}; do
    if curl -s -o /dev/null "$URL"; then
      open "$URL"
      exit 0
    fi
    sleep 0.5
  done
) &

echo "Gorges Gusts is starting at $URL"
echo "Press Ctrl+C to stop it."
echo
exec .venv/bin/python -m uvicorn app.asgi:app --host localhost --port "$PORT"
