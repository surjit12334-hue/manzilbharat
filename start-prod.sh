#!/usr/bin/env bash
# Production start: gunicorn with multiple workers behind the host's $PORT.
#
# WHY THE DB STATE MATTERS HERE: with `python3 backend/app.py` (one process,
# dev only) the in-memory rate limiter and reset-token store work. Under
# gunicorn -w N each worker had its OWN dict, so N x the rate limit and a
# reset token issued by worker A could not be seen by worker B. Since
# commit "feat(prod): move rate limiter + reset tokens into Postgres" both
# live in the database, so any number of workers is safe.
set -euo pipefail

cd "$(dirname "$0")"

# Load .env if present. NOT `source .env`: values may contain spaces
# (Gmail app passwords are grouped like "abcd efgh ijkl mnop") and
# sourcing would try to EXECUTE the second word as a command. Parse
# KEY=VALUE lines instead; the host's own env (Render etc.) still wins
# because we only export keys that exist here... except real deployments
# should set secrets in the dashboard and ship no .env at all.
if [ -f .env ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ''|'#'*) continue ;;
        esac
        case "$line" in
            [A-Za-z_]*=*)
                key="${line%%=*}"
                val="${line#*=}"
                # strip one layer of surrounding quotes if present
                case "$val" in
                    \"*\") val="${val#\"}"; val="${val%\"}" ;;
                    \'*\') val="${val#\'}"; val="${val%\'}" ;;
                esac
                # host env (Render's $PORT, dashboard secrets) wins
                if [ -z "${!key+x}" ]; then
                    export "$key=$val"
                fi
                ;;
        esac
    done < .env
fi

if [ -d venv ]; then
    GUNICORN=venv/bin/gunicorn
else
    GUNICORN=gunicorn
fi

PORT="${PORT:-5000}"
WORKERS="${WEB_CONCURRENCY:-2}"

echo "Starting (production) on 0.0.0.0:${PORT} with ${WORKERS} workers"
exec "$GUNICORN" \
    --workers "$WORKERS" \
    --bind "0.0.0.0:${PORT}" \
    --timeout 60 \
    --access-logfile - \
    --error-logfile - \
    backend.app:app
