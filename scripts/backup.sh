#!/usr/bin/env bash
# =============================================================================
# Nightly database backup (Phase 5 step 4).
#
#   ./scripts/backup.sh                    take a backup now
#   BACKUP_KEEP=30 ./scripts/backup.sh    keep 30 files instead of 14
#
# WHAT IT DOES
#   pg_dump --format=custom --compress=9  ->  backups/manzil-YYYYmmdd-HHMMSS.dump
#   then it deletes the oldest files until only BACKUP_KEEP are left.
#
# WHY THE CUSTOM FORMAT
#   "custom" (-Fc) is pg_dump's own compressed archive, and it is the only
#   format pg_restore can clean up: restoring it over a database that
#   already has the tables works (--clean --if-exists drops them first),
#   while a plain SQL dump would stop at "relation already exists".
#   To read it as SQL:  pg_restore -f - backups/manzil-....dump | less
#
# WHERE THE CONNECTION COMES FROM
#   1. $DATABASE_URL (the environment wins)
#   2. .env in the project root (parsed, NOT sourced - a value with a space
#      in it would break `source`)
#   3. DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD, the same variables the
#      app itself uses, handed to pg_dump as PGHOST/PGPORT/...
#
# A failed dump NEVER overwrites a good backup: the new file is written to a
# temporary name, checked (not empty), and only then moved into place.
#
# HOW TO RUN IT EVERY NIGHT
#   crontab -e
#   17 3 * * * cd /path/to/manzil-bharat && ./scripts/backup.sh >> logs/backup.log 2>&1
#
#   On a server that is always on, that is the whole job. On a free-tier
#   host the disk is wiped on every deploy, so use the GitHub Actions
#   workflow in .github/workflows/backup.yml instead - it runs on a schedule
#   and keeps the dump as an artifact.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-backups}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"

# --- read .env without sourcing it ------------------------------------------
# Parsing the file line by line is not a style choice: the .env of this
# project contains a value with a space in it (an SMTP app password), and
# `source .env` would try to RUN the second word as a command.
if [ -f .env ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|'#'*) continue ;;
    esac
    case "$line" in
      [A-Za-z_]*=*)
        key="${line%%=*}"
        val="${line#*=}"
        # Strip one layer of matching quotes.
        case "$val" in
          \"*\") val="${val#\"}"; val="${val%\"}" ;;
          \'*\') val="${val#\'}"; val="${val%\'}" ;;
        esac
        # The environment (Render, cron, CI) always wins over the file.
        if [ -z "${!key:-}" ]; then
          export "$key=$val"
        fi
        ;;
    esac
  done < .env
fi

if ! command -v pg_dump >/dev/null 2>&1; then
  echo "ERROR: pg_dump is not installed (postgresql-client)." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

stamp="$(date -u +%Y%m%d-%H%M%S)"
final="$BACKUP_DIR/manzil-$stamp.dump"
tmp="$final.partial"

cleanup() {
  rm -f "$tmp"
}
trap cleanup EXIT

dump_args=(--no-owner --no-privileges --format=custom --compress=9)
if [ -n "${DATABASE_URL:-}" ]; then
  dump_args+=("$DATABASE_URL")
else
  # The DB_* variables the app uses, handed to pg_dump the way it expects
  # them. Doing it this way avoids having to percent-encode a password
  # into a connection URL.
  if [ -n "${DB_HOST:-}" ] && [ -z "${PGHOST:-}" ]; then export PGHOST="$DB_HOST"; fi
  if [ -n "${DB_PORT:-}" ] && [ -z "${PGPORT:-}" ]; then export PGPORT="$DB_PORT"; fi
  if [ -n "${DB_NAME:-}" ] && [ -z "${PGDATABASE:-}" ]; then export PGDATABASE="$DB_NAME"; fi
  if [ -n "${DB_USER:-}" ] && [ -z "${PGUSER:-}" ]; then export PGUSER="$DB_USER"; fi
  if [ -n "${DB_PASSWORD:-}" ] && [ -z "${PGPASSWORD:-}" ]; then export PGPASSWORD="$DB_PASSWORD"; fi
  if [ -z "${PGDATABASE:-}" ] && [ -z "${PGHOST:-}" ]; then
    echo "ERROR: no database to back up. Set DATABASE_URL (or DB_HOST/DB_NAME)." >&2
    exit 1
  fi
fi

echo "Backing up the database -> $final"
if ! pg_dump "${dump_args[@]}" > "$tmp"; then
  echo "ERROR: pg_dump failed - the previous backups are untouched." >&2
  exit 1
fi

# An empty file is a broken backup, not a backup. Refuse to move it into
# place.
if [ ! -s "$tmp" ]; then
  echo "ERROR: the dump is empty - it was discarded." >&2
  exit 1
fi

mv "$tmp" "$final"
trap - EXIT

size="$(du -h "$final" | cut -f1)"
echo "Done: $final ($size)"

# --- keep only the newest BACKUP_KEEP files ---------------------------------
# ls -1t sorts by modification time, newest first.
count=$(ls -1 "$BACKUP_DIR"/manzil-*.dump 2>/dev/null | wc -l | tr -d ' ')
if [ "$count" -gt "$BACKUP_KEEP" ]; then
  echo "Deleting the $((count - BACKUP_KEEP)) oldest backup(s)"
  ls -1t "$BACKUP_DIR"/manzil-*.dump | tail -n +$((BACKUP_KEEP + 1)) | while IFS= read -r old; do
    echo "  - $(basename "$old")"
    rm -f "$old"
  done
fi

echo "Backups kept: $(ls -1 "$BACKUP_DIR"/manzil-*.dump 2>/dev/null | wc -l | tr -d ' ')"
