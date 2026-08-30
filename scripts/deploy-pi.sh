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

command -v ssh >/dev/null || { echo "ssh is required" >&2; exit 1; }
command -v rsync >/dev/null || { echo "rsync is required" >&2; exit 1; }

echo "Checking ${PI_HOST}..."
ssh "$PI_HOST" "mkdir -p '$PI_DIR' && test -f '$PI_DIR/.env'"

echo "Synchronizing application files..."
rsync -az --human-readable --itemize-changes --delete \
  --exclude='.DS_Store' \
  --exclude='__pycache__/' \
  --exclude='*.py[cod]' \
  "$PROJECT_DIR/backend/" "$PI_HOST:$PI_DIR/backend/"

rsync -az --human-readable --itemize-changes --delete \
  --exclude='.DS_Store' \
  --exclude='node_modules/' \
  --exclude='dist/' \
  "$PROJECT_DIR/frontend/" "$PI_HOST:$PI_DIR/frontend/"

rsync -az --human-readable --itemize-changes --delete \
  --exclude='.DS_Store' \
  "$PROJECT_DIR/config/" "$PI_HOST:$PI_DIR/config/"

rsync -az --human-readable --itemize-changes \
  "$PROJECT_DIR/.dockerignore" \
  "$PROJECT_DIR/Dockerfile.armv7" \
  "$PROJECT_DIR/compose.yaml" \
  "$PROJECT_DIR/compose.armv7.yaml" \
  "$PI_HOST:$PI_DIR/"

rsync -az --human-readable --itemize-changes --delete \
  --exclude='.DS_Store' \
  "$PROJECT_DIR/database/" "$PI_HOST:$PI_DIR/database/"

echo "Building and restarting the app container..."
ssh "$PI_HOST" "cd '$PI_DIR' && \
  export COMPOSE_PROFILES= && \
  docker compose -f compose.yaml -f compose.armv7.yaml config --quiet && \
  docker compose -f compose.yaml -f compose.armv7.yaml up -d --build app && \
  docker compose -f compose.yaml -f compose.armv7.yaml ps"

echo "Deployment complete."
