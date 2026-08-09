-- Setup script for weather_embeddings table
-- Run this manually in your Lakebase Postgres database - 3rd Script.
--
-- Uses sentence-transformers/all-MiniLM-L6-v2 (384-dim)

--- Set search path to custome schema as Lakebase SQL EDITOR defaults to public schema
--- Added public to search path to use vector extension which was already set in public schema. It doesn't automatically get applied for all schemas and errors when used as a data type.
SET search_path TO weather, public;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS weather_embeddings (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES weather_documents (id),
    chunk_index INT NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding VECTOR(384) NOT NULL,
    model_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Create HNSW index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS idx_weather_embeddings_embedding
ON weather_embeddings
USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_weather_embeddings_document_id
ON weather_embeddings (document_id);

-- Verify the table was created
SELECT
    table_name,
    column_name,
    data_type,
    udt_name
FROM information_schema.columns
WHERE table_name = 'weather_embeddings'
ORDER BY ordinal_position;
