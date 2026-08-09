-- Setup script for locations table
-- Run this manually in your Lakebase Postgres database before syncing weather data.
--
-- Caches the NWS grid-point resolution (GET /points/{lat},{lon}) for each
-- location so repeated syncs don't need to re-resolve office/gridX/gridY
-- every time.

CREATE TABLE IF NOT EXISTS locations (
    id TEXT PRIMARY KEY,
    raw_input TEXT NOT NULL,
    latitude NUMERIC NOT NULL,
    longitude NUMERIC NOT NULL,
    grid_office TEXT NOT NULL,
    grid_x INT NOT NULL,
    grid_y INT NOT NULL,
    resolved_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Verify the table was created
SELECT
    table_name,
    column_name,
    data_type
FROM information_schema.columns
WHERE table_name = 'locations'
ORDER BY ordinal_position;
