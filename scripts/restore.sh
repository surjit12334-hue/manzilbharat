#!/usr/bin/env bash
# =============================================================================
# Restore a database backup (Phase 5 step 4).
#
#   ./scripts/restore.sh                          list the available backups
#   ./scripts/restore.sh backups/manzil-...dump   restore that one
#   ./scripts/restore.sh backups/manzil-...dump --yes   restore without asking
#
# The restore REPLACES the contents of the database with the dump: every
# table is emptied and filled again from the file (pg_restore --clean drops
# the existing objects first, so a restore over a database that already has
# the tables works). Take a fresh backup first (./scripts/backup.sh) if
# there is anything in the database you still need.
#
# The connection comes from $DATABASE_URL, from .env, or from DB_HOST/... -
# the same order as scripts/backup.sh.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-backups}"

# --- read .env without sourcing it ------------------------------------------
# Line by line, not `source`: a value with a space in it would break it.
if [ -f .env ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|'#'*) continue ;;
    esac
    case "$line" in
      [A-Za-z_]*=*)
        key="${line%%=*}"
        val="${line#*=}"
        case "$val" in
          \"*\") val="${val#\"}"; val="${val%\"}" ;;
          \'*\') val="${val#\'}"; val="${val%\'}" ;;
        esac
        if [ -z "${!key:-}" ]; then
          export "$key=$val"
        fi
        ;;
    esac
  done < .env
fi

# --- no file given: show what is there --------------------------------------
if [ $# -lt 1 ]; then
  echo "Available backups in $BACKUP_DIR:"
  if ! ls -1t "$BACKUP_DIR"/manzil-*.dump >/dev/null 2>&1; then
    echo "  (none yet - run ./scripts/backup.sh)"
    exit 1
  fi
  ls -1t "$BACKUP_DIR"/manzil-*.dump
  echo
  echo "Usage: $0 <file> [--yes]"
  exit 0
fi

file="$1"
shift || true
assume_yes="${1:-}"

if [ ! -f "$file" ]; then
  echo "ERROR: $file does not exist." >&2
  exit 1
fi

if ! command -v pg_restore >/dev/null 2>&1; then
  echo "ERROR: pg_restore is not installed (postgresql-client)." >&2
  exit 1
fi

if [ -z "$assume_yes" ]; then
  echo "This will ERASE the current contents of the database and replace"
  echo "them with $file."
  read -r -p "Type 'restore' to continue: " answer
  if [ "$answer" != "restore" ]; then
    echo "Cancelled - nothing was changed."
    exit 1
  fi
fi

restore_args=(--clean --if-exists --no-owner --no-privileges --exit-on-error)
if [ -n "${DATABASE_URL:-}" ]; then
  db_target="$DATABASE_URL"
else
  # The DB_* variables the app uses, handed to pg_restore the way it expects
  # them.
  if [ -n "${DB_HOST:-}" ] && [ -z "${PGHOST:-}" ]; then export PGHOST="$DB_HOST"; fi
  if [ -n "${DB_PORT:-}" ] && [ -z "${PGPORT:-}" ]; then export PGPORT="$DB_PORT"; fi
  if [ -n "${DB_NAME:-}" ] && [ -z "${PGDATABASE:-}" ]; then export PGDATABASE="$DB_NAME"; fi
  if [ -n "${DB_USER:-}" ] && [ -z "${PGUSER:-}" ]; then export PGUSER="$DB_USER"; fi
  if [ -n "${DB_PASSWORD:-}" ] && [ -z "${PGPASSWORD:-}" ]; then export PGPASSWORD="$DB_PASSWORD"; fi
  if [ -z "${PGDATABASE:-}" ] && [ -z "${PGHOST:-}" ]; then
    echo "ERROR: no database to restore into. Set DATABASE_URL (or DB_HOST/DB_NAME)." >&2
    exit 1
  fi
  db_target="${PGDATABASE:-}"
fi

echo "Restoring $file ..."
pg_restore "${restore_args[@]}" --dbname "$db_target" "$file"
echo "Done."
