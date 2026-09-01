#!/usr/bin/env sh
set -eu
umask 077

: "${NOVEL_MYSQL_DATABASE:=novel_auto_write}"
: "${NOVEL_MYSQL_HOST:=127.0.0.1}"
: "${NOVEL_MYSQL_USER:=novelapp}"
: "${NOVEL_BACKUP_DIR:=/var/backups/novel-auto-write}"

mkdir -p "$NOVEL_BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$NOVEL_BACKUP_DIR/$stamp-before-migrate.sql"
mysqldump \
  --single-transaction \
  --set-gtid-purged=OFF \
  --host="$NOVEL_MYSQL_HOST" \
  --user="$NOVEL_MYSQL_USER" \
  --password \
  "$NOVEL_MYSQL_DATABASE" \
  > "$target"
chmod 600 "$target"
