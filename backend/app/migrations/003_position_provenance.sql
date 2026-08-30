ALTER TABLE position_reports
    ADD COLUMN data_provider text NOT NULL DEFAULT 'aisstream',
    ADD COLUMN data_source text NOT NULL DEFAULT 'aisstream',
    ADD COLUMN source_station text;

ALTER TABLE vessels
    ADD COLUMN position_data_provider text,
    ADD COLUMN position_data_source text,
    ADD COLUMN position_source_station text;

UPDATE vessels
SET position_data_provider = 'aisstream',
    position_data_source = 'aisstream'
WHERE position_received_at IS NOT NULL;
