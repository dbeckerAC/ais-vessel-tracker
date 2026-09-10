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

: "${PI_HOST:?PI_HOST must be set in .env or the environment}"
: "${PI_DIR:?PI_DIR must be set in .env or the environment}"

MIN_SPEED_KNOTS="${OUTLIER_MIN_SPEED_KNOTS:-100}"
MAX_BRIDGE_SPEED_KNOTS="${OUTLIER_MAX_BRIDGE_SPEED_KNOTS:-60}"
MIN_DISTANCE_KM="${OUTLIER_MIN_DISTANCE_KM:-5}"

for value in "$MIN_SPEED_KNOTS" "$MAX_BRIDGE_SPEED_KNOTS" "$MIN_DISTANCE_KM"; do
  [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] || {
    echo "Outlier thresholds must be non-negative numbers" >&2
    exit 2
  }
done

command -v ssh >/dev/null || { echo "ssh is required" >&2; exit 1; }

candidate_file="$(mktemp "${TMPDIR:-/tmp}/ais-outliers.XXXXXX")"
trap 'rm -f "$candidate_file"' EXIT

echo "Scanning ${PI_HOST} for isolated track spikes..."
ssh "$PI_HOST" bash -s -- \
  "$PI_DIR" "$MIN_SPEED_KNOTS" "$MAX_BRIDGE_SPEED_KNOTS" "$MIN_DISTANCE_KM" \
  > "$candidate_file" <<'REMOTE'
set -Eeuo pipefail

cd "$1"
docker compose -f compose.yaml -f compose.armv7.yaml exec -T db \
  psql -U ais -d ais -X -v ON_ERROR_STOP=1 -At -F $'\x1f' \
  -v min_speed_knots="$2" -v max_bridge_speed_knots="$3" -v min_distance_km="$4" <<'SQL'
WITH neighbors AS (
    SELECT p.*,
           lag(received_at) OVER w AS previous_at,
           lead(received_at) OVER w AS next_at,
           lag(position) OVER w AS previous_position,
           lead(position) OVER w AS next_position,
           lag(latitude) OVER w AS previous_latitude,
           lag(longitude) OVER w AS previous_longitude,
           lead(latitude) OVER w AS next_latitude,
           lead(longitude) OVER w AS next_longitude
    FROM position_reports p
    WINDOW w AS (PARTITION BY mmsi ORDER BY received_at, source_hash)
), scored AS (
    SELECT n.*,
           extract(epoch FROM received_at - previous_at) AS previous_seconds,
           extract(epoch FROM next_at - received_at) AS next_seconds,
           extract(epoch FROM next_at - previous_at) AS bridge_seconds,
           ST_DistanceSphere(previous_position, position) AS previous_metres,
           ST_DistanceSphere(position, next_position) AS next_metres,
           ST_DistanceSphere(previous_position, next_position) AS bridge_metres
    FROM neighbors n
    WHERE previous_position IS NOT NULL
      AND next_position IS NOT NULL
)
SELECT s.mmsi,
       to_char(s.received_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') || 'Z',
       s.source_hash,
       replace(COALESCE(tv.personal_label, v.display_name, s.mmsi), E'\x1f', ' '),
       round(s.latitude::numeric, 5),
       round(s.longitude::numeric, 5),
       COALESCE(s.speed_over_ground_knots::text, 'unknown'),
       s.data_provider,
       s.data_source,
       COALESCE(s.source_station, '-'),
       round(s.previous_latitude::numeric, 5),
       round(s.previous_longitude::numeric, 5),
       round(s.next_latitude::numeric, 5),
       round(s.next_longitude::numeric, 5),
       round((s.previous_metres / 1000)::numeric, 1),
       round((s.next_metres / 1000)::numeric, 1),
       round((s.previous_metres / s.previous_seconds * 1.943844)::numeric, 1),
       round((s.next_metres / s.next_seconds * 1.943844)::numeric, 1),
       round((s.bridge_metres / s.bridge_seconds * 1.943844)::numeric, 1)
FROM scored s
JOIN vessels v USING (mmsi)
LEFT JOIN tracked_vessels tv USING (mmsi)
WHERE s.previous_seconds > 0
  AND s.next_seconds > 0
  AND s.bridge_seconds > 0
  AND s.previous_metres >= :'min_distance_km'::double precision * 1000
  AND s.next_metres >= :'min_distance_km'::double precision * 1000
  AND s.previous_metres / s.previous_seconds * 1.943844 > :'min_speed_knots'::double precision
  AND s.next_metres / s.next_seconds * 1.943844 > :'min_speed_knots'::double precision
  AND s.bridge_metres / s.bridge_seconds * 1.943844 <= :'max_bridge_speed_knots'::double precision
ORDER BY s.received_at, s.mmsi, s.source_hash;
SQL
REMOTE

candidate_count="$(wc -l < "$candidate_file" | tr -d ' ')"
if [ "$candidate_count" -eq 0 ]; then
  echo "No outliers found. No rows were changed."
  exit 0
fi

echo "Found ${candidate_count} candidate(s)."
echo "A candidate must be at least ${MIN_DISTANCE_KM} km from both neighbors,"
echo "require over ${MIN_SPEED_KNOTS} kn on both legs, and leave a plausible"
echo "neighbor-to-neighbor track of at most ${MAX_BRIDGE_SPEED_KNOTS} kn."
echo "Confirmed deletions are immediate; run ./scripts/backup-db-pi.sh first if needed."

deleted=0
candidate_number=0
exec 3<&0
while IFS=$'\x1f' read -r \
  mmsi received_at source_hash vessel latitude longitude sog provider source station \
  previous_latitude previous_longitude next_latitude next_longitude \
  previous_km next_km previous_knots next_knots bridge_knots; do
  candidate_number=$((candidate_number + 1))
  [[ "$mmsi" =~ ^[0-9]{9}$ ]] || { echo "Invalid MMSI in query result" >&2; exit 1; }
  [[ "$received_at" =~ ^[0-9T:.+-]+Z$ ]] || { echo "Invalid timestamp in query result" >&2; exit 1; }
  [[ "$source_hash" =~ ^[0-9a-f]{64}$ ]] || { echo "Invalid source hash in query result" >&2; exit 1; }
  echo
  echo "[${candidate_number}/${candidate_count}] ${vessel} (MMSI ${mmsi})"
  echo "  Time:      ${received_at}"
  echo "  Position:  ${latitude}, ${longitude}"
  echo "  Feed:      ${provider}/${source} (station ${station})"
  echo "  SOG:       ${sog} kn"
  echo "  Previous:  ${previous_latitude}, ${previous_longitude} (${previous_km} km; ${previous_knots} kn implied)"
  echo "  Next:      ${next_latitude}, ${next_longitude} (${next_km} km; ${next_knots} kn implied)"
  echo "  Without it, the neighboring points imply ${bridge_knots} kn."

  while true; do
    if ! read -r -p "Delete this exact position report? [y/N/q] " answer <&3; then
      echo
      echo "Input closed. Stopped after deleting ${deleted} row(s)."
      exit 0
    fi
    case "$answer" in
      y|Y)
        delete_count="$(ssh "$PI_HOST" bash -s -- "$PI_DIR" "$mmsi" "$received_at" "$source_hash" <<'REMOTE'
set -Eeuo pipefail

cd "$1"
docker compose -f compose.yaml -f compose.armv7.yaml exec -T db \
  psql -U ais -d ais -X -v ON_ERROR_STOP=1 -qAt \
  -v mmsi="$2" -v received_at="$3" -v source_hash="$4" <<'SQL'
WITH deleted AS (
    DELETE FROM position_reports
    WHERE mmsi = :'mmsi'
      AND received_at = :'received_at'::timestamptz
      AND source_hash = :'source_hash'
    RETURNING 1
)
SELECT count(*) FROM deleted;
SQL
REMOTE
)"
        if [ "$delete_count" = "1" ]; then
          echo "  Deleted."
          deleted=$((deleted + 1))
        else
          echo "  Not deleted: the exact row no longer exists." >&2
        fi
        break
        ;;
      q|Q)
        echo
        echo "Stopped. Deleted ${deleted} row(s)."
        exit 0
        ;;
      n|N|'')
        echo "  Kept."
        break
        ;;
      *) echo "Please enter y, n, or q." ;;
    esac
  done
done < "$candidate_file"

echo
echo "Finished. Deleted ${deleted} of ${candidate_count} candidate row(s)."
