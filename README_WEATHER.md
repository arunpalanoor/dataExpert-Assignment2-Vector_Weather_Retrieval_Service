# Weather Vector Retrieval Service

## Data source

- **National Weather Service API** (`api.weather.gov`)
- Locations (e.g. `"Chicago, IL"`) are geocoded to lat/lon via the **Open-Meteo Geocoding API**. This is then resolved to an NWS grid point via `GET /points/{lat},{lon}`.
- _Note: NWS only covers the US and its territories._

## Schema decisions

Three tables created directly on Lakebase inbuilt SQL EDITOR. Reference sql script added to (`/sql`) folder.The Lakebase URL was set as a secret within scope `weather`.

- **`locations`** - stores each location's NWS grid resolution (`grid_office`, `grid_x`, `grid_y`) to avoid repeated api calls and normilize tables. 
- **`weather_documents`** - one row per NWS alert or forecast period, referencing `locations.id`. `source_type` (`"alert"` / `"forecast"`)
- **`weather_embeddings`** - one row per chunk: `chunk_index`, `chunk_text`, `embedding vector(384)`, `model_name`. Kept `CHUNK_SIZE=800` / `CHUNK_OVERLAP=100` . Noted that most NWS narrative text is short enough to produce exactly one chunk; only long combined alert description+instruction text tends to split further.
**Embedding model**: `sentence-transformers/all-MiniLM-L6-v2` (384-dim)

## Running the pipeline end-to-end

1. Run SQL queries from `/sql` folder on Lakebase, in the order: `01_setup_locations_table.sql` → `02_setup_weather_documents_table.sql` → `03_setup_weather_embeddings_table.sql`.
2. Start the app: `python app.py` (or deploy via Databricks Apps - see `app.yaml`).
3. **Sync**: Add US locations eg. `Chicago, IL` and number of documents to sync eg. `20`. This writes into `locations` + `weather_documents` tables.
4. **Embedding**: Scheduled job runs `notebooks/ingest_weather_embeddings.ipynb` every 60 mins. Reads `weather_documents` rows with no embedding yet, chunks + embeds + writes into `weather_embeddings`.
5. **Search**: Query "risk of flooding near rivers", Set `"top_k": 5`, returns the `top_k` best-matching distinct documents ranked by cosine similarity.

## Limitations and future improvements

- **US-only coverage.** Weather API supports only US locations. While Open-Meteo is global and can cover Non-US locations. There is scope to further improve the app by including other location weather apis. Currently, non-us locations are ignored even if added to the sync list, that way the app doesn't fail.
- Query text doesn't consider the **Location**. Both Forecast and Alert text doesn't mentions the location name, and hence not part of the embeddings. So two locations with similar weather cannot be distinguished and the app ignores the location if part of Query. eg. If the query is- "Is it going to rain in Austin, TX this week?"; the app might return forecast and alerts from other locations. Future version can include location as part of chunk or metadata enrichment.
- **Add `source_type` filter on search.** Alerts and forecasts are currently searched together; Can utilize the column from `weather_documents`.
- **Auto trigger embeddings** for every location sync event from UI.
