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

echo "Checking ${PI_HOST}..."
ssh "$PI_HOST" bash -s -- "$PI_DIR" <<'REMOTE'
set -Eeuo pipefail

PI_DIR="$1"
cd "$PI_DIR"

compose() {
  docker compose -f compose.yaml -f compose.armv7.yaml "$@"
}

echo
echo "== Containers =="
compose ps

echo
echo "== Host resources =="
uptime
free -h
df -h "$PI_DIR"

echo
echo "== API =="
if health="$(curl -fsS --max-time 5 http://127.0.0.1:8000/healthz)"; then
  echo "healthz: ${health}"
else
  echo "healthz: FAILED (app is not responding on port 8000)"
fi

if status_json="$(curl -fsS --max-time 5 http://127.0.0.1:8000/api/v1/status)"; then
  printf '%s\n' "$status_json" | python3 -c '
import json
import sys

data = json.load(sys.stdin)
stream = data.get("stream", {})
openwaters = data.get("openwaters", {})
ingestion = data.get("ingestion", {})
writer = data.get("writer", {})
database = data.get("database", {})
print("AISStream: %s; messages=%s" % (stream.get("state", "unknown"), stream.get("received_messages", "?")))
print("Open Waters: %s; messages=%s" % (openwaters.get("state", "unknown"), openwaters.get("received_messages", "?")))
print("Ingestion: accepted=%s; sampled=%s; stale=%s" % (ingestion.get("accepted_positions", "?"), ingestion.get("sampled_positions", "?"), ingestion.get("stale_positions", "?")))
print("Writer: queued=%s; errors=%s" % (writer.get("queued", "?"), writer.get("errors", "?")))
print("Database API: stored_positions=%s; last=%s" % (database.get("stored_positions", "?"), database.get("last_stored_position_at", "?")))
'
else
  echo "status: FAILED (app is not responding on port 8000)"
fi

echo
echo "== PostgreSQL =="
compose exec -T db psql -U ais -d ais -X -v ON_ERROR_STOP=1 -P pager=off <<'SQL'
SELECT current_database() AS database,
       pg_size_pretty(pg_database_size(current_database())) AS database_size;

SELECT schemaname || '.' || relname AS table_name,
       pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
       n_live_tup::bigint AS estimated_rows,
       to_char(last_analyze, 'YYYY-MM-DD HH24:MI:SS TZ') AS last_analyze
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC;

\echo ''
\echo 'Position reports by provider/source:'
SELECT data_provider,
       data_source,
       count(*) AS records,
       count(*) FILTER (WHERE speed_over_ground_knots IS NOT NULL) AS with_speed
FROM position_reports
GROUP BY data_provider, data_source
ORDER BY records DESC;
SQL
REMOTE
