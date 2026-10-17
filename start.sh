#!/usr/bin/env bash
# Start the Manzil Bharat Flask server.
# Serves both the API and the whole frontend, so one port covers everything.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "Error: .env not found. Create it first:" >&2
    echo "  cp .env.example .env && \$EDITOR .env" >&2
    exit 1
fi

if [ -d venv ]; then
    source venv/bin/activate
fi

PORT="$(grep -E '^PORT=' .env | cut -d= -f2- | tr -d '[:space:]')"
PORT="${PORT:-${FLASK_RUN_PORT:-${PORT:-5000}}}"
if echo "$PORT" | grep -qv '^[0-9]\+$'; then PORT=5000; fi

echo "Starting on http://localhost:${PORT}"
echo "  Admin panel : http://localhost:${PORT}/admin.html"
echo "  Home        : http://localhost:${PORT}/"
echo

exec python3 backend/app.py
