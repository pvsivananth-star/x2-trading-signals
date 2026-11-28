#!/bin/sh
set -eu
: "${BACKUP_DIR:=./backups}"
: "${DATABASE_URL:?DATABASE_URL must be supplied externally}"
mkdir -p "$BACKUP_DIR"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
pg_dump "$DATABASE_URL" --format=custom --file="$BACKUP_DIR/x2-$stamp.dump"
printf '%s\n' "$BACKUP_DIR/x2-$stamp.dump"
