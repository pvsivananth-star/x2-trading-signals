#!/bin/sh
set -eu
: "${BACKUP_DIR:=./backups}"
: "${RETENTION_DAYS:=30}"
find "$BACKUP_DIR" -type f -name '*.dump' -mtime +"$RETENTION_DAYS" -delete
