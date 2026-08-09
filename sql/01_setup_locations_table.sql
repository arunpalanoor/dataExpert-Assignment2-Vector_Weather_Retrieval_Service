-- Setup script for locations table
-- Run this manually in Lakebase Postgres database - 1st Script.
--
-- Created this additional table to store NWS grid-point resolution (GET /points/{lat},{lon}) for each location to normalize tables.This could be used as a list of saved locations on the final UI.

--- Set search path to custome schema as Lakebase SQL EDITOR defaults to public schema
SET search_path TO weather;

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
