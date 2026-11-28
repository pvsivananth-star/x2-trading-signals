#!/bin/sh
set -eu
: "${DATABASE_URL:?DATABASE_URL must be supplied externally}"
: "${BACKUP_FILE:?BACKUP_FILE is required}"
pg_restore --clean --if-exists --no-owner --dbname="$DATABASE_URL" "$BACKUP_FILE"
