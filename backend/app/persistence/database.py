from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import asyncpg

from app.domain.models import PositionObservation, StaticObservation


MMSI_PATTERN = re.compile(r"^[0-9]{9}$")


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.pool: asyncpg.Pool | None = None
        self._known_partitions: set[str] = set()

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self.url, min_size=1, max_size=5)

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()

    def _pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("database pool is not connected")
        return self.pool

    async def migrate(self) -> None:
        pool = self._pool()
        migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
        async with pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version text PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            async with connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock(927461)")
                applied = {
                    row["version"]
                    for row in await connection.fetch("SELECT version FROM schema_migrations")
                }
                for path in sorted(migrations_dir.glob("*.sql")):
                    if path.name in applied:
                        continue
                    await connection.execute(path.read_text(encoding="utf-8"))
                    await connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES($1)", path.name
                    )

        now = datetime.now(timezone.utc)
        await self.ensure_partition(now)
        await self.ensure_partition(_add_month(now))

    async def ensure_partition(self, value: datetime) -> None:
        month_start = value.astimezone(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        next_month = _add_month(month_start)
        suffix = month_start.strftime("%Y_%m")
        if suffix in self._known_partitions:
            return
        table_name = f"position_reports_{suffix}"
        statement = (
            f"CREATE TABLE IF NOT EXISTS {table_name} "
            "PARTITION OF position_reports "
            f"FOR VALUES FROM ('{month_start.isoformat()}') TO ('{next_month.isoformat()}')"
        )
        async with self._pool().acquire() as connection:
            await connection.execute(statement)
        self._known_partitions.add(suffix)

    async def seed_tracked_vessels(self, config_path: Path) -> int:
        document = json.loads(config_path.read_text(encoding="utf-8"))
        raw_vessels = document.get("vessels")
        if not isinstance(raw_vessels, list):
            raise ValueError("vessels config must contain a 'vessels' list")

        seen: set[str] = set()
        values: list[tuple[str, str | None]] = []
        for item in raw_vessels:
            if not isinstance(item, dict):
                raise ValueError("each configured vessel must be an object")
            mmsi = str(item.get("mmsi", "")).strip()
            if not MMSI_PATTERN.fullmatch(mmsi):
                raise ValueError(f"invalid configured MMSI: {mmsi!r}")
            if mmsi in seen:
                raise ValueError(f"duplicate configured MMSI: {mmsi}")
            seen.add(mmsi)
            label = item.get("personal_label")
            values.append((mmsi, str(label).strip() if label else None))

        inserted = 0
        async with self._pool().acquire() as connection:
            async with connection.transaction():
                for mmsi, label in values:
                    await connection.execute(
                        "INSERT INTO vessels(mmsi) VALUES($1) ON CONFLICT DO NOTHING", mmsi
                    )
                    result = await connection.execute(
                        """
                        INSERT INTO tracked_vessels(mmsi, personal_label)
                        VALUES($1, $2)
                        ON CONFLICT DO NOTHING
                        """,
                        mmsi,
                        label,
                    )
                    inserted += int(result.endswith("1"))
        return inserted

    async def active_mmsis(self) -> list[str]:
        rows = await self._pool().fetch(
            "SELECT mmsi FROM tracked_vessels WHERE active ORDER BY added_at, mmsi"
        )
        return [row["mmsi"] for row in rows]

    async def list_tracked_vessels(self, include_inactive: bool = True) -> list[dict[str, Any]]:
        where = "" if include_inactive else "WHERE tv.active"
        rows = await self._pool().fetch(
            f"""
            SELECT tv.mmsi, tv.active, tv.personal_label, tv.added_at,
                   tv.deactivated_at, v.display_name, v.call_sign, v.imo,
                   v.ship_type, v.latitude, v.longitude,
                   v.position_received_at
            FROM tracked_vessels tv
            JOIN vessels v USING (mmsi)
            {where}
            ORDER BY tv.active DESC, COALESCE(tv.personal_label, v.display_name, tv.mmsi)
            """
        )
        return [_record_dict(row) for row in rows]

    async def add_tracked_vessel(
        self, mmsi: str, personal_label: str | None, capacity_limit: int
    ) -> dict[str, Any]:
        if not MMSI_PATTERN.fullmatch(mmsi):
            raise ValueError("MMSI must contain exactly nine digits")
        label = personal_label.strip() if personal_label else None
        async with self._pool().acquire() as connection:
            async with connection.transaction():
                count = await connection.fetchval(
                    "SELECT count(*) FROM tracked_vessels WHERE active AND mmsi <> $1", mmsi
                )
                if count >= capacity_limit - 1:
                    raise OverflowError(
                        f"AISStream subscription limit of {capacity_limit} MMSIs reached"
                    )
                await connection.execute(
                    "INSERT INTO vessels(mmsi) VALUES($1) ON CONFLICT DO NOTHING", mmsi
                )
                await connection.execute(
                    """
                    INSERT INTO tracked_vessels(mmsi, active, personal_label)
                    VALUES($1, true, $2)
                    ON CONFLICT (mmsi) DO UPDATE SET
                        active = true,
                        personal_label = COALESCE(EXCLUDED.personal_label, tracked_vessels.personal_label),
                        deactivated_at = NULL,
                        updated_at = now()
                    """,
                    mmsi,
                    label,
                )
        rows = await self.list_tracked_vessels()
        return next(row for row in rows if row["mmsi"] == mmsi)

    async def deactivate_tracked_vessel(self, mmsi: str) -> bool:
        result = await self._pool().execute(
            """
            UPDATE tracked_vessels
            SET active = false, deactivated_at = now(), updated_at = now()
            WHERE mmsi = $1 AND active
            """,
            mmsi,
        )
        return result.endswith("1")

    async def latest_position_times(self) -> dict[str, datetime]:
        rows = await self._pool().fetch(
            "SELECT mmsi, max(received_at) AS received_at FROM position_reports GROUP BY mmsi"
        )
        return {row["mmsi"]: row["received_at"] for row in rows}

    async def persist_positions(self, observations: list[PositionObservation]) -> None:
        if not observations:
            return
        for month in {item.received_at.strftime("%Y-%m") for item in observations}:
            await self.ensure_partition(datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc))

        latest: dict[str, PositionObservation] = {}
        for item in observations:
            previous = latest.get(item.mmsi)
            if previous is None or item.received_at > previous.received_at:
                latest[item.mmsi] = item

        async with self._pool().acquire() as connection:
            async with connection.transaction():
                await connection.executemany(
                    """
                    INSERT INTO position_reports(
                        mmsi, received_at, latitude, longitude, position,
                        speed_over_ground_knots, course_over_ground_degrees,
                        true_heading_degrees, navigational_status,
                        source_message_type, source_hash,
                        data_provider, data_source, source_station
                    ) VALUES(
                        $1, $2, $3, $4, ST_SetSRID(ST_MakePoint($4, $3), 4326),
                        $5, $6, $7, $8, $9, $10, $11, $12, $13
                    ) ON CONFLICT DO NOTHING
                    """,
                    [
                        (
                            item.mmsi,
                            item.received_at,
                            item.latitude,
                            item.longitude,
                            item.speed_over_ground_knots,
                            item.course_over_ground_degrees,
                            item.true_heading_degrees,
                            item.navigational_status,
                            item.source_message_type,
                            item.source_hash,
                            item.data_provider,
                            item.data_source,
                            item.source_station,
                        )
                        for item in observations
                    ],
                )
                await connection.executemany(
                    """
                    UPDATE vessels SET
                        display_name = COALESCE($2, display_name),
                        latitude = $3,
                        longitude = $4,
                        position = ST_SetSRID(ST_MakePoint($4, $3), 4326),
                        speed_over_ground_knots = $5,
                        course_over_ground_degrees = $6,
                        true_heading_degrees = $7,
                        navigational_status = $8,
                        position_received_at = $9,
                        position_data_provider = $10,
                        position_data_source = $11,
                        position_source_station = $12,
                        updated_at = now()
                    WHERE mmsi = $1
                      AND (position_received_at IS NULL OR position_received_at < $9)
                    """,
                    [
                        (
                            item.mmsi,
                            item.display_name,
                            item.latitude,
                            item.longitude,
                            item.speed_over_ground_knots,
                            item.course_over_ground_degrees,
                            item.true_heading_degrees,
                            item.navigational_status,
                            item.received_at,
                            item.data_provider,
                            item.data_source,
                            item.source_station,
                        )
                        for item in latest.values()
                    ],
                )
                await self._upsert_names(connection, observations)

    async def persist_static(self, observation: StaticObservation) -> None:
        payload = observation.normalized_payload
        async with self._pool().acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "INSERT INTO vessels(mmsi) VALUES($1) ON CONFLICT DO NOTHING",
                    observation.mmsi,
                )
                await connection.execute(
                    """
                    INSERT INTO static_reports(
                        mmsi, received_at, source_message_type, payload_hash, normalized_payload
                    ) VALUES($1, $2, $3, $4, $5::jsonb)
                    ON CONFLICT (mmsi, payload_hash) DO NOTHING
                    """,
                    observation.mmsi,
                    observation.received_at,
                    observation.source_message_type,
                    observation.payload_hash,
                    json.dumps(payload),
                )
                await connection.execute(
                    """
                    UPDATE vessels SET
                        display_name = COALESCE($2, display_name),
                        call_sign = COALESCE($3, call_sign),
                        imo = COALESCE($4, imo),
                        ship_type = COALESCE($5, ship_type),
                        destination = COALESCE($6, destination),
                        updated_at = now()
                    WHERE mmsi = $1
                    """,
                    observation.mmsi,
                    payload.get("display_name"),
                    payload.get("call_sign"),
                    payload.get("imo"),
                    payload.get("ship_type"),
                    payload.get("destination"),
                )
                name = payload.get("display_name")
                if name:
                    await _upsert_name(connection, observation.mmsi, name, observation.received_at)

    async def _upsert_names(
        self, connection: asyncpg.Connection, observations: list[PositionObservation]
    ) -> None:
        latest: dict[tuple[str, str], PositionObservation] = {}
        for item in observations:
            if item.display_name:
                latest[(item.mmsi, item.display_name.casefold())] = item
        for item in latest.values():
            await _upsert_name(connection, item.mmsi, item.display_name or "", item.received_at)

    async def current_vessels(self) -> list[dict[str, Any]]:
        rows = await self._pool().fetch(
            """
            SELECT v.mmsi, tv.personal_label, v.display_name, v.call_sign, v.imo,
                   v.ship_type, v.destination, v.latitude, v.longitude,
                   v.speed_over_ground_knots, v.course_over_ground_degrees,
                   v.true_heading_degrees, v.navigational_status,
                   v.position_received_at,
                   v.position_data_provider AS data_provider,
                   v.position_data_source AS data_source,
                   v.position_source_station AS source_station
            FROM tracked_vessels tv
            JOIN vessels v USING(mmsi)
            WHERE tv.active AND v.position IS NOT NULL
            ORDER BY COALESCE(tv.personal_label, v.display_name, v.mmsi)
            """
        )
        return [_record_dict(row) for row in rows]

    async def historical_track(
        self,
        mmsi: str,
        start: datetime,
        end: datetime,
        tolerance_m: float,
        gap_minutes: int,
    ) -> dict[str, Any] | None:
        row = await self._pool().fetchrow(
            """
            WITH source AS (
                SELECT received_at, position,
                       speed_over_ground_knots, course_over_ground_degrees,
                       true_heading_degrees, navigational_status
                FROM position_reports
                WHERE mmsi = $1 AND received_at >= $2 AND received_at <= $3
                ORDER BY received_at
            ), ordered AS (
                SELECT *,
                       lag(received_at) OVER (ORDER BY received_at) AS previous_at,
                       lag(position) OVER (ORDER BY received_at) AS previous_position,
                       lag(speed_over_ground_knots) OVER (ORDER BY received_at) AS previous_speed,
                       lag(course_over_ground_degrees) OVER (ORDER BY received_at) AS previous_course,
                       lag(true_heading_degrees) OVER (ORDER BY received_at) AS previous_heading,
                       lag(navigational_status) OVER (ORDER BY received_at) AS previous_navigation_status
                FROM source
            ), line AS (
                SELECT count(*)::integer AS source_point_count,
                       min(received_at) AS started_at,
                       max(received_at) AS ended_at,
                       CASE WHEN count(*) >= 2 THEN ST_MakeLine(position ORDER BY received_at) END AS geom,
                       COALESCE(
                           jsonb_agg(
                               jsonb_build_object(
                                   'type', 'Feature',
                                   'geometry', ST_AsGeoJSON(ST_MakeLine(previous_position, position))::jsonb,
                                   'properties', jsonb_build_object(
                                       'mmsi', $1,
                                       'started_at', previous_at,
                                       'ended_at', received_at,
                                       'gap_seconds', EXTRACT(EPOCH FROM (received_at - previous_at)),
                                       'is_gap', received_at - previous_at > ($5 * interval '1 minute'),
                                       'speed_knots', CASE
                                           WHEN previous_speed IS NOT NULL AND speed_over_ground_knots IS NOT NULL
                                           THEN (previous_speed + speed_over_ground_knots) / 2.0
                                           ELSE COALESCE(speed_over_ground_knots, previous_speed)
                                       END,
                                       'start', jsonb_build_object(
                                           'received_at', previous_at,
                                           'speed_over_ground_knots', previous_speed,
                                           'course_over_ground_degrees', previous_course,
                                           'true_heading_degrees', previous_heading,
                                           'navigational_status', previous_navigation_status
                                       ),
                                       'end', jsonb_build_object(
                                           'received_at', received_at,
                                           'speed_over_ground_knots', speed_over_ground_knots,
                                           'course_over_ground_degrees', course_over_ground_degrees,
                                           'true_heading_degrees', true_heading_degrees,
                                           'navigational_status', navigational_status
                                       )
                                   )
                               ) ORDER BY received_at
                           ) FILTER (WHERE previous_position IS NOT NULL),
                           '[]'::jsonb
                       ) AS segments
                FROM ordered
            ), simplified AS (
                SELECT *,
                    CASE WHEN geom IS NULL THEN NULL
                         WHEN $4 > 0 THEN ST_Transform(
                             ST_Simplify(ST_Transform(geom, 3857), $4, true), 4326
                         )
                         ELSE geom END AS result_geom
                FROM line
            )
            SELECT source_point_count, started_at, ended_at,
                   CASE WHEN result_geom IS NULL THEN source_point_count
                        ELSE ST_NPoints(result_geom) END AS returned_point_count,
                   CASE WHEN result_geom IS NULL THEN NULL ELSE ST_AsGeoJSON(result_geom)::jsonb END AS geometry,
                   segments
            FROM simplified
            """,
            mmsi,
            start,
            end,
            tolerance_m,
            gap_minutes,
        )
        if row is None or row["source_point_count"] == 0:
            return None
        result = _record_dict(row)
        geometry = result.get("geometry")
        if isinstance(geometry, str):
            result["geometry"] = json.loads(geometry)
        segments = result.get("segments")
        if isinstance(segments, str):
            result["segments"] = json.loads(segments)
        result["mmsi"] = mmsi
        result["tolerance_m"] = tolerance_m
        result["gap_threshold_minutes"] = gap_minutes
        result["simplified"] = result["returned_point_count"] < result["source_point_count"]
        return result

    async def provisional_trips(
        self, mmsi: str, start: datetime, end: datetime, gap_hours: int
    ) -> list[dict[str, Any]]:
        rows = await self._pool().fetch(
            """
            WITH ordered AS (
                SELECT received_at, position,
                       lag(received_at) OVER (ORDER BY received_at) AS previous_at
                FROM position_reports
                WHERE mmsi = $1 AND received_at >= $2 AND received_at <= $3
            ), marked AS (
                SELECT *, CASE
                    WHEN previous_at IS NULL OR received_at - previous_at > ($4 * interval '1 hour')
                    THEN 1 ELSE 0 END AS starts_segment
                FROM ordered
            ), grouped AS (
                SELECT *, sum(starts_segment) OVER (ORDER BY received_at) AS segment_id
                FROM marked
            )
            SELECT segment_id::integer,
                   min(received_at) AS started_at,
                   max(received_at) AS ended_at,
                   count(*)::integer AS point_count,
                   CASE WHEN count(*) >= 2
                        THEN ST_Length(ST_MakeLine(position ORDER BY received_at)::geography) / 1852.0
                        ELSE 0 END AS distance_nm
            FROM grouped
            GROUP BY segment_id
            ORDER BY started_at DESC
            """,
            mmsi,
            start,
            end,
            gap_hours,
        )
        return [
            {
                **_record_dict(row),
                "mmsi": mmsi,
                "method": f"observation_gap_{gap_hours}h_v1",
                "provisional": True,
            }
            for row in rows
        ]

    async def stats(self) -> dict[str, Any]:
        row = await self._pool().fetchrow(
            """
            SELECT
                (SELECT count(*) FROM tracked_vessels WHERE active)::integer AS active_vessels,
                (SELECT count(*) FROM position_reports)::bigint AS stored_positions,
                (SELECT max(received_at) FROM position_reports) AS last_stored_position_at
            """
        )
        return _record_dict(row)


async def _upsert_name(
    connection: asyncpg.Connection, mmsi: str, display_name: str, received_at: datetime
) -> None:
    normalized = " ".join(display_name.casefold().split())
    if not normalized:
        return
    await connection.execute(
        """
        INSERT INTO vessel_names(mmsi, normalized_name, display_name, first_seen_at, last_seen_at)
        VALUES($1, $2, $3, $4, $4)
        ON CONFLICT (mmsi, normalized_name) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            last_seen_at = GREATEST(vessel_names.last_seen_at, EXCLUDED.last_seen_at)
        """,
        mmsi,
        normalized,
        display_name,
        received_at,
    )


def _add_month(value: datetime) -> datetime:
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1, day=1)
    return value.replace(month=value.month + 1, day=1)


def _record_dict(record: asyncpg.Record) -> dict[str, Any]:
    result = dict(record)
    for key, value in list(result.items()):
        if isinstance(value, datetime):
            result[key] = value.isoformat().replace("+00:00", "Z")
    return result
