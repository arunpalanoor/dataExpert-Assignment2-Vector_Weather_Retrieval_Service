"""
Lakebase (Databricks-managed Postgres) connection helper.

Connects using a single LAKEBASE_URL (a standard Postgres connection URL,
e.g. postgresql://role:password@host:5432/databricks_postgres?sslmode=require)
pointing at a native Postgres role with a static, non-expiring password.
This keeps setup to a single secret instead of five separate env vars.

Improved get_connection method to parse lakebase-url from secret scope properly if additional schema seach path is included
"""

import base64
import os
from contextlib import contextmanager
from urllib.parse import parse_qs, urlparse

import psycopg2
from databricks.sdk import WorkspaceClient
from psycopg2.extras import RealDictCursor
from sqlalchemy import create_engine

_w = WorkspaceClient()

_SCOPE = os.environ.get("LAKEBASE_SECRET_SCOPE", "weather")
_KEY = os.environ.get("LAKEBASE_SECRET_KEY", "lakebase-url")


def _lakebase_url() -> str:
    """Fetch and decode the Lakebase connection URL from the Databricks secret scope."""
    secret = _w.secrets.get_secret(scope=_SCOPE, key=_KEY)
    return base64.b64decode(secret.value).decode("utf-8")


@contextmanager
def get_connection():
    """Yield a raw psycopg2 connection with a RealDictCursor factory."""
    url = _lakebase_url()
    parsed = urlparse(url)
    
    # Build connection parameters dict
    conn_params = {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "user": parsed.username,
        "password": parsed.password,
        "database": parsed.path.lstrip("/"),
        "cursor_factory": RealDictCursor,
    }
    
    # Parse query parameters
    query_params = parse_qs(parsed.query)
    
    # Add sslmode if present
    if "sslmode" in query_params:
        conn_params["sslmode"] = query_params["sslmode"][0]
    
    # Add options if present (this handles search_path)
    if "options" in query_params:
        conn_params["options"] = query_params["options"][0]
    
    conn = psycopg2.connect(**conn_params)
    try:
        yield conn
    finally:
        conn.close()


def get_engine():
    """Return a SQLAlchemy engine for Lakebase."""
    return create_engine(_lakebase_url())


def run_query(sql: str, params: tuple | dict | None = None) -> list[dict]:
    """Run a read query against Lakebase and return rows as list[dict]."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def run_write(sql: str, params: tuple | dict | None = None) -> int:
    """Run an INSERT/UPDATE/DELETE against Lakebase, return affected row count."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount


if __name__ == "__main__":
    print("Testing Lakebase connection...")
    print("=" * 80)
    
    try:
        # Check current schema and search_path
        schema_info = run_query("""
            SELECT current_schema() as current_schema,
                   current_setting('search_path') as search_path
        """)
        print(f"\nCurrent schema: {schema_info[0]['current_schema']}")
        print(f"Search path: {schema_info[0]['search_path']}")
        
        # Get tables in current schema only (respects search_path)
        tables_query = """
            SELECT tablename 
            FROM pg_tables 
            WHERE schemaname = current_schema()
            ORDER BY tablename
        """
        
        tables = run_query(tables_query)
        print(f"\nFound {len(tables)} table(s) in current schema:\n")
        
        for table in tables:
            print(f"  - {table['tablename']}")
        
        print("\n" + "=" * 80)
        print("✅ Connection test successful!")
        
    except Exception as e:
        print(f"\n❌ Connection test failed: {e}")
        import traceback
        traceback.print_exc()
