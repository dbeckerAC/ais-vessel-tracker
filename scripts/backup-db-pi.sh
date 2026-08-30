#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

load_local_setting() {
  local name="$1"
  local value
  [ -n "${!name:-}" ] && return
  [ -f "$PROJECT_DIR/.env" ] || return
  value="$(sed -n -E "s/^[[:space:]]*${name}[[:space:]]*=[[:space:]]*(.*)[[:space:]]*$/\1/p" "$PROJECT_DIR/.env" | head -n 1)"
  [ -n "$value" ] && printf -v "$name" '%s' "$value"
}

load_local_setting PI_HOST
load_local_setting PI_DIR

: "${PI_HOST:?PI_HOST must be set in .env or the environment (for example, user@192.168.1.20)}"
: "${PI_DIR:?PI_DIR must be set in .env or the environment (for example, /home/user/ais-tools)}"

BACKUP_DIR="$PROJECT_DIR/backups"

command -v ssh >/dev/null || { echo "ssh is required" >&2; exit 1; }
mkdir -p "$BACKUP_DIR"

backup_file="$BACKUP_DIR/ais-$(date -u +%Y%m%d-%H%M%S).dump"
temporary_file="${backup_file}.partial"
trap 'rm -f "$temporary_file"' EXIT

echo "Creating PostgreSQL backup from ${PI_HOST}..."
ssh "$PI_HOST" "cd '$PI_DIR' && docker compose -f compose.yaml -f compose.armv7.yaml exec -T db pg_dump -U ais -d ais -Fc" > "$temporary_file"

if [ ! -s "$temporary_file" ]; then
  echo "Backup failed: the dump is empty" >&2
  exit 1
fi

mv "$temporary_file" "$backup_file"
echo "Backup written to ${backup_file} ($(du -h "$backup_file" | awk '{print $1}'))"
