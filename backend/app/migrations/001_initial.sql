CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS vessels (
    mmsi varchar(9) PRIMARY KEY CHECK (mmsi ~ '^[0-9]{9}$'),
    display_name text,
    call_sign text,
    imo bigint,
    ship_type integer,
    destination text,
    latitude double precision,
    longitude double precision,
    speed_over_ground_knots double precision,
    course_over_ground_degrees double precision,
    true_heading_degrees integer,
    navigational_status integer,
    position geometry(Point, 4326),
    position_received_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tracked_vessels (
    mmsi varchar(9) PRIMARY KEY REFERENCES vessels(mmsi) ON DELETE RESTRICT,
    active boolean NOT NULL DEFAULT true,
    personal_label text,
    added_at timestamptz NOT NULL DEFAULT now(),
    deactivated_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vessel_names (
    mmsi varchar(9) NOT NULL REFERENCES vessels(mmsi) ON DELETE RESTRICT,
    normalized_name text NOT NULL,
    display_name text NOT NULL,
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    PRIMARY KEY (mmsi, normalized_name)
);

CREATE TABLE IF NOT EXISTS position_reports (
    mmsi varchar(9) NOT NULL REFERENCES vessels(mmsi) ON DELETE RESTRICT,
    received_at timestamptz NOT NULL,
    latitude double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    position geometry(Point, 4326) NOT NULL,
    speed_over_ground_knots double precision,
    course_over_ground_degrees double precision,
    true_heading_degrees integer,
    navigational_status integer,
    source_message_type text NOT NULL,
    source_hash char(64) NOT NULL,
    PRIMARY KEY (mmsi, received_at, source_hash)
) PARTITION BY RANGE (received_at);

CREATE INDEX IF NOT EXISTS position_reports_mmsi_time_idx
    ON position_reports (mmsi, received_at);
CREATE INDEX IF NOT EXISTS position_reports_time_brin_idx
    ON position_reports USING brin (received_at);
CREATE INDEX IF NOT EXISTS position_reports_position_gist_idx
    ON position_reports USING gist (position);

CREATE TABLE IF NOT EXISTS static_reports (
    id bigserial PRIMARY KEY,
    mmsi varchar(9) NOT NULL REFERENCES vessels(mmsi) ON DELETE RESTRICT,
    received_at timestamptz NOT NULL,
    source_message_type text NOT NULL,
    payload_hash char(64) NOT NULL,
    normalized_payload jsonb NOT NULL,
    UNIQUE (mmsi, payload_hash)
);
CREATE INDEX IF NOT EXISTS static_reports_mmsi_time_idx
    ON static_reports (mmsi, received_at DESC);

CREATE TABLE IF NOT EXISTS port_visits (
    id bigserial PRIMARY KEY,
    mmsi varchar(9) NOT NULL REFERENCES vessels(mmsi) ON DELETE RESTRICT,
    port_id text NOT NULL,
    arrived_at timestamptz,
    departed_at timestamptz,
    algorithm_version text NOT NULL,
    confidence double precision,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trips (
    id bigserial PRIMARY KEY,
    mmsi varchar(9) NOT NULL REFERENCES vessels(mmsi) ON DELETE RESTRICT,
    started_at timestamptz NOT NULL,
    ended_at timestamptz,
    origin_port_id text,
    destination_port_id text,
    algorithm_version text NOT NULL,
    confidence double precision,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS processing_runs (
    processor_name text NOT NULL,
    algorithm_version text NOT NULL,
    watermark timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (processor_name, algorithm_version)
);

