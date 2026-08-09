-- Setup script for weather_documents table
-- Run this manually in your Lakebase Postgres database - 2nd SCRIPT.
--
-- Raw document store for NWS alerts and forecast periods. References locations(id) instead of duplicating lat/lon/grid info on every row.

--- Set search path to custome schema as Lakebase SQL EDITOR defaults to public schema
SET search_path TO weather;

CREATE TABLE IF NOT EXISTS weather_documents (
    id TEXT PRIMARY KEY,
    location_id TEXT NOT NULL REFERENCES locations (id),
    source_type TEXT NOT NULL,
    headline TEXT,
    narrative_text TEXT NOT NULL,
    issued_at TIMESTAMPTZ,
    effective_at TIMESTAMPTZ,
    payload JSONB NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Create indexes for common lookups
CREATE INDEX IF NOT EXISTS idx_weather_documents_location_id
ON weather_documents (location_id);

CREATE INDEX IF NOT EXISTS idx_weather_documents_source_type
ON weather_documents (source_type);

-- Verify the table was created
SELECT
    table_name,
    column_name,
    data_type
FROM information_schema.columns
WHERE table_name = 'weather_documents'
ORDER BY ordinal_position;
