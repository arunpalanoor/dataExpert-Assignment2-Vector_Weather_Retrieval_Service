"""
Embedding ingestion script (Part 2): reads narrative_text from
weather_documents that don't yet have embeddings, chunks it, embeds each
chunk with sentence-transformers, and writes vectors into weather_embeddings
via psycopg2.

Plain Python script (not Spark, not a Databricks notebook) - run it
directly with the same LAKEBASE_URL secret / connection setup used by
app.py and lakebase.py.

Run:
    python notebooks/ingest_weather_embeddings.py
"""

import hashlib
import os
import sys

# Allow `python notebooks/ingest_weather_embeddings.py` from the repo root -
# Python only puts the script's own directory (notebooks/) on sys.path by
# default, and this script needs to `import lakebase` from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from psycopg2.extras import execute_values
from sentence_transformers import SentenceTransformer

import lakebase

EMBEDDING_MODEL_NAME = os.environ.get(
    "WEATHER_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
# Most NWS narrative text is short; chunking mainly matters for combined
# alert description + instruction text. Matches the reference project's
# sliding-window convention (CHUNK_SIZE=800, CHUNK_OVERLAP=100).
CHUNK_SIZE = int(os.environ.get("WEATHER_CHUNK_SIZE", 800))
CHUNK_OVERLAP = int(os.environ.get("WEATHER_CHUNK_OVERLAP", 100))
ENCODE_BATCH_SIZE = int(os.environ.get("WEATHER_EMBED_BATCH_SIZE", 32))


def fetch_unembedded_documents() -> list[dict]:
    """
    Documents with no existing weather_embeddings rows under the current
    model. This is an optimization to skip already-processed documents on
    repeat runs, not a strict correctness guarantee - if a document's
    narrative_text changes after being embedded (same document id, e.g. a
    re-issued forecast period), it won't be picked up again here since it
    already has at least one embedding row. Re-embedding on content change
    is a known limitation (see README_WEATHER.md).
    """
    return lakebase.run_query(
        """
        SELECT d.id, d.narrative_text
        FROM weather_documents d
        LEFT JOIN weather_embeddings e
            ON e.document_id = d.id AND e.model_name = %s
        WHERE e.id IS NULL
          AND d.narrative_text IS NOT NULL
          AND TRIM(d.narrative_text) != ''
        """,
        (EMBEDDING_MODEL_NAME,),
    )


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Sliding-window chunking - most inputs are shorter than chunk_size and
    produce exactly one chunk."""
    text = text.strip()
    if not text:
        return []

    chunks = []
    step = max(chunk_size - overlap, 1)
    for start in range(0, len(text), step):
        chunk = text[start : start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        if start + chunk_size >= len(text):
            break
    return chunks


def to_vector_literal(embedding) -> str:
    """Format a Python float list as a pgvector text literal for casting
    with ::vector in SQL (same helper as app.py's query-embedding path)."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


def main() -> None:
    documents = fetch_unembedded_documents()
    if not documents:
        print("No unembedded documents found - nothing to do.")
        return

    print(f"Found {len(documents)} document(s) to embed.")

    chunk_rows = []
    for doc in documents:
        for chunk_index, chunk in enumerate(chunk_text(doc["narrative_text"])):
            chunk_rows.append((doc["id"], chunk_index, chunk))

    if not chunk_rows:
        print("No chunks produced - nothing to embed.")
        return

    print(f"Loading embedding model {EMBEDDING_MODEL_NAME!r}...")
    model = SentenceTransformer(EMBEDDING_MODEL_NAME)

    print(f"Embedding {len(chunk_rows)} chunk(s) in batches of {ENCODE_BATCH_SIZE}...")
    chunk_texts = [row[2] for row in chunk_rows]
    embeddings = []
    for i in range(0, len(chunk_texts), ENCODE_BATCH_SIZE):
        batch = chunk_texts[i : i + ENCODE_BATCH_SIZE]
        vectors = model.encode(batch, show_progress_bar=False)
        embeddings.extend(vectors.tolist())
        print(f"  Embedded {min(i + ENCODE_BATCH_SIZE, len(chunk_texts))}/{len(chunk_texts)} chunks")

    insert_rows = [
        (
            hashlib.sha256(f"{document_id}:{chunk_index}".encode("utf-8")).hexdigest(),
            document_id,
            chunk_index,
            chunk_text_value,
            to_vector_literal(embedding),
            EMBEDDING_MODEL_NAME,
        )
        for (document_id, chunk_index, chunk_text_value), embedding in zip(chunk_rows, embeddings)
    ]

    print(f"Writing {len(insert_rows)} embedding row(s) into weather_embeddings...")
    with lakebase.get_connection() as conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO weather_embeddings (
                    id, document_id, chunk_index, chunk_text, embedding, model_name, created_at
                ) VALUES %s
                ON CONFLICT (id) DO UPDATE
                    SET chunk_text = EXCLUDED.chunk_text,
                        embedding = EXCLUDED.embedding,
                        model_name = EXCLUDED.model_name,
                        created_at = EXCLUDED.created_at
                """,
                insert_rows,
                template="(%s, %s, %s, %s, %s::vector, %s, now())",
                page_size=100,
            )
        conn.commit()

    print(f"Done. Inserted/updated {len(insert_rows)} embedding row(s).")


if __name__ == "__main__":
    main()
