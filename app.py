"""
Vector Weather Retrieval Service - Flask app.

- Reads/writes Lakebase (Databricks-managed Postgres) via lakebase.py
- Harvests NWS alerts + forecasts via weather_client.py, normalizes them
  into `weather_documents` (referencing `locations` for resolved NWS grid
  points), and exposes semantic search over `weather_embeddings` (pgvector).

Table DDL lives in sql/ and is applied manually against Lakebase (see
sql/01_setup_locations_table.sql, 02_setup_weather_documents_table.sql,
03_setup_weather_embeddings_table.sql) - this app does not auto-create
tables, since the pgvector column/index setup depends on the instance's
search_path configuration.

Run locally:
    python app.py
"""

import json
import logging
import os
import re

from flask import Flask, jsonify, render_template, request
from sentence_transformers import SentenceTransformer

import lakebase
from weather_client import WeatherClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("weather-app")

app = Flask(__name__)

DEFAULT_SYNC_LIMIT = 50
DEFAULT_TOP_K = 5
MIN_TOP_K = 1
MAX_TOP_K = 20

EMBEDDING_MODEL_NAME = os.environ.get(
    "WEATHER_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
# Loaded once at module level (not per-request) - reused by /weather/search.
_embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)


@app.route("/")
def index():
    """Simple UI to sync locations and run semantic search."""
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.route("/locations", methods=["GET"])
def list_locations():
    """Read already-synced locations from Lakebase, for the UI sidebar."""
    rows = lakebase.run_query(
        """
        SELECT id, raw_input, latitude, longitude, grid_office, grid_x, grid_y, resolved_at
        FROM locations
        ORDER BY raw_input ASC
        """
    )
    return jsonify(rows)


@app.errorhandler(Exception)
def handle_exception(err):
    """Ensure all unhandled errors return JSON (not an HTML error page)."""
    logger.exception("Unhandled exception while processing request")
    status_code = getattr(err, "code", 500)
    if not isinstance(status_code, int):
        status_code = 500
    return jsonify({"error": str(err)}), status_code


@app.route("/weather/sync", methods=["POST"])
def sync_weather():
    """
    Resolve each location to an NWS grid point, fetch its active alerts +
    forecast periods, and upsert them into locations / weather_documents.

    Body: {"locations": ["Chicago, IL", "Austin, TX"], "limit": 50}
    `limit` caps the number of documents upserted per location (alerts +
    forecast periods combined), applied after fetching.
    """
    body = request.json if request.is_json else {}
    raw_locations = body.get("locations") or []
    locations = [loc.strip() for loc in raw_locations if isinstance(loc, str) and loc.strip()]
    if not locations:
        return jsonify({"error": "locations must be a non-empty list of location strings"}), 400

    try:
        limit = int(body.get("limit", DEFAULT_SYNC_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_SYNC_LIMIT

    client = WeatherClient()
    total = 0
    synced_locations = []
    errors = {}

    for raw_input in locations:
        try:
            location = _upsert_location(client, raw_input)
            documents = client.fetch_documents_for_location(location)
            if limit:
                documents = documents[:limit]
            total += _upsert_documents(documents)
            synced_locations.append(raw_input)
        except Exception as exc:
            logger.exception("Failed to sync location %r", raw_input)
            errors[raw_input] = str(exc)

    response = {"synced": total, "locations": synced_locations}
    if errors:
        response["errors"] = errors
    return jsonify(response)


@app.route("/weather/search", methods=["POST"])
def search_weather():
    """
    Cosine-similarity search over weather_embeddings, deduped to the
    best-matching chunk per document so top_k returns distinct documents.

    Body: {"query": "risk of flooding near rivers", "top_k": 5}
    """
    body = request.json if request.is_json else {}
    query = body.get("query")
    if not isinstance(query, str) or not query.strip():
        return jsonify({"error": "query must be a non-empty string"}), 400

    try:
        top_k = int(body.get("top_k", DEFAULT_TOP_K))
    except (TypeError, ValueError):
        top_k = DEFAULT_TOP_K
    top_k = max(MIN_TOP_K, min(top_k, MAX_TOP_K))

    embedding = _embedding_model.encode(query.strip()).tolist()
    vector_literal = _to_vector_literal(embedding)

    rows = lakebase.run_query(
        """
        SELECT location, headline, chunk_text, similarity
        FROM (
            SELECT DISTINCT ON (e.document_id)
                e.document_id,
                l.raw_input AS location,
                d.headline,
                e.chunk_text,
                1 - (e.embedding <=> %s::vector) AS similarity
            FROM weather_embeddings e
            JOIN weather_documents d ON d.id = e.document_id
            JOIN locations l ON l.id = d.location_id
            ORDER BY e.document_id, e.embedding <=> %s::vector
        ) best_chunk_per_document
        ORDER BY similarity DESC
        LIMIT %s
        """,
        (vector_literal, vector_literal, top_k),
    )

    return jsonify(rows)


def _location_id(raw_input: str) -> str:
    """Stable slug primary key for `locations`, derived from the raw input
    string so re-syncing the same location updates rather than duplicates it."""
    slug = re.sub(r"[^a-z0-9]+", "-", raw_input.strip().lower()).strip("-")
    return slug


def _upsert_location(client: WeatherClient, raw_input: str) -> dict:
    """Resolve raw_input to lat/lon + NWS grid point, upsert into
    `locations`, and return the resolved fields plus its assigned id."""
    resolved = client.resolve_location(raw_input)
    location_id = _location_id(raw_input)

    lakebase.run_write(
        """
        INSERT INTO locations (
            id, raw_input, latitude, longitude, grid_office, grid_x, grid_y, resolved_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (id) DO UPDATE
            SET raw_input = EXCLUDED.raw_input,
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude,
                grid_office = EXCLUDED.grid_office,
                grid_x = EXCLUDED.grid_x,
                grid_y = EXCLUDED.grid_y,
                resolved_at = EXCLUDED.resolved_at
        """,
        (
            location_id,
            resolved["raw_input"],
            resolved["latitude"],
            resolved["longitude"],
            resolved["grid_office"],
            resolved["grid_x"],
            resolved["grid_y"],
        ),
    )

    resolved["id"] = location_id
    return resolved


def _upsert_documents(documents: list[dict]) -> int:
    """Upsert normalized weather_documents rows (see weather_client.py's
    normalize_alert / normalize_forecast_period for the row shape)."""
    if not documents:
        return 0

    count = 0
    with lakebase.get_connection() as conn:
        with conn.cursor() as cur:
            for doc in documents:
                cur.execute(
                    """
                    INSERT INTO weather_documents (
                        id, location_id, source_type, headline, narrative_text,
                        issued_at, effective_at, payload, synced_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (id) DO UPDATE
                        SET location_id = EXCLUDED.location_id,
                            source_type = EXCLUDED.source_type,
                            headline = EXCLUDED.headline,
                            narrative_text = EXCLUDED.narrative_text,
                            issued_at = EXCLUDED.issued_at,
                            effective_at = EXCLUDED.effective_at,
                            payload = EXCLUDED.payload,
                            synced_at = EXCLUDED.synced_at
                    """,
                    (
                        doc["id"],
                        doc["location_id"],
                        doc["source_type"],
                        doc["headline"],
                        doc["narrative_text"],
                        doc["issued_at"],
                        doc["effective_at"],
                        json.dumps(doc["payload"]),
                    ),
                )
                count += 1
            conn.commit()
    return count


def _to_vector_literal(embedding: list[float]) -> str:
    """Format a Python float list as a pgvector text literal, e.g. "[0.1,0.2,...]",
    for passing through psycopg2 and casting with ::vector in SQL."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


if __name__ == "__main__":
    host = os.getenv("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_RUN_PORT", 8000))
    app.run(debug=True, host=host, port=port)
