# Weather Vector Retrieval Service

## Data source

**National Weather Service API** (`api.weather.gov`) — free, no API key, generous rate limits, and rich narrative text (alert `description`/`instruction`, forecast `detailedForecast`). Free-text locations (e.g. `"Chicago, IL"`) are geocoded to lat/lon via the **Open-Meteo Geocoding API** (also free, no key) before being resolved to an NWS grid point via `GET /points/{lat},{lon}`. NWS only covers the US and its territories.

## Schema decisions

Three tables (DDL in `sql/`, applied manually):

- **`locations`** — caches each location's NWS grid resolution (`grid_office`, `grid_x`, `grid_y`) so repeat syncs don't re-hit `/points`. Split out into its own table rather than embedding location fields directly on `weather_documents` because grid resolution has an independent lifecycle: it's resolved once and reused across every document synced for that location. This differs from `weather_embeddings`, where a chunk and its embedding are produced together in the same pass and don't warrant a similar split.
- **`weather_documents`** — one row per NWS alert or forecast period, referencing `locations.id`. `source_type` (`"alert"` / `"forecast"`) distinguishes the two shapes NWS actually returns: alerts have `id`/`event`/`description`/`instruction`/`sent`/`effective`; forecast periods have none of that, just `name`/`startTime`/`endTime`/`detailedForecast`. This also drives how each type's `id` and `issued_at`/`effective_at` get populated during normalization.
- **`weather_embeddings`** — one row per chunk: `chunk_index`, `chunk_text`, `embedding vector(384)`, `model_name`. Sliding-window chunking at `CHUNK_SIZE=800` / `CHUNK_OVERLAP=100` (matches the reference ticker-news pipeline's convention) — most NWS narrative text is short enough to produce exactly one chunk; only long combined alert description+instruction text tends to split further.

**Embedding model**: `sentence-transformers/all-MiniLM-L6-v2` (384-dim), matching the existing ticker-news pipeline so both stay queryable with the same pgvector distance operator.

## Running the pipeline end-to-end

1. Run the DDL in `sql/` against Lakebase, in order: `01_setup_locations_table.sql` → `02_setup_weather_documents_table.sql` → `03_setup_weather_embeddings_table.sql`.
2. Start the app: `python app.py` (or deploy via Databricks Apps — see `app.yaml`).
3. **Sync**: `POST /weather/sync` with `{"locations": ["Chicago, IL", "Austin, TX"], "limit": 50}`, or use the UI at `/`. Writes into `locations` + `weather_documents`.
4. **Embed**: run `notebooks/ingest_weather_embeddings.ipynb` (as a Databricks Job/Workflow, or interactively). Reads `weather_documents` rows with no embedding yet, chunks + embeds + writes into `weather_embeddings`.
5. **Search**: `POST /weather/search` with `{"query": "risk of flooding near rivers", "top_k": 5}`, or use the UI. Returns the `top_k` best-matching distinct documents (deduped per document, best-scoring chunk kept) ranked by cosine similarity.

## Known limitations / future improvements

- **US-only coverage.** Non-US locations geocode fine (Open-Meteo is global) but fail at NWS grid resolution (`/points` returns 404). Handled per-location in `/weather/sync` so one bad location doesn't fail the whole batch.
- **Location isn't part of the embedded text.** Forecast `detailedForecast` text never mentions the location name, so two locations with similar weather can be hard for semantic search to tell apart. Fix: embed `"{location}: {narrative_text}"` instead of raw `narrative_text`, and re-embed existing documents.
- **No re-embedding on content change.** The ingestion notebook only picks up documents with zero existing embedding rows. If a forecast period's text is revised after being embedded (same synthesized document id), the stale embedding isn't refreshed until that logic is extended (e.g. a content hash comparison).
- **No `source_type` filter on search.** Alerts and forecasts are searched together; the column exists on `weather_documents` for future filtering but `/weather/search` doesn't expose it yet.
- **Not implemented (stretch goals)**: LLM-generated summary variant of search results, HNSW-vs-no-index latency benchmark.
